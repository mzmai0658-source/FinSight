"""检验集合依据、纠错合同和云端协议，全部离线执行。"""
import asyncio
import copy
import json
from dataclasses import replace
from unittest.mock import patch
import httpx
import pytest
from jsonschema import Draft202012Validator

from src.agent.providers import ChatProviderConfig,resolve_chat_provider
from src.agent.v3.cloud_chat import CloudChat,CloudProviderFailure,cloud_request
from src.agent.v3.model import AsyncModel,Budget,ModelFailure,sampling_schema
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.turn_transport import turn_schema,encode_turn_proposal,decode_turn
from src.agent.v3.request_bindings import explicit_collection_span,collection_span_supported
from src.agent.v3.state import apply_understanding,InvalidUnderstanding
from src.agent.v3.contracts import DialogueState

COMPANIES={'600085':'同仁堂','600080':'金花股份','002082':'万邦德'}


def rank_plan(question,**kw):
    return TurnPlan(goals=[dict(id='g1',kind='rank',source_ref=question)],
        edits=[dict(field=field,value=value,operation='replace',text=question) for field,value in [
            ('all_companies',True),('metrics',['operating_revenue']),('time.years',[2022])]],**kw)


@pytest.mark.parametrize('scope',['库里','库中','库内','数据库里','数据库中','知识库里','知识库中的公司','数据库里的所有公司','已收录的公司'])
def test_catalog_scope_survives_binding_with_or_without_explicit_provenance(scope):
    q=f'按2022年营业收入给{scope}升序前两家'
    assert collection_span_supported(scope)
    for explicit in [None,scope]:
        request,_=apply_understanding(q,'test',rank_plan(q,collection_text=explicit).compile(q,COMPANIES),DialogueState(),COMPANIES)
        assert request.conditions.all_companies
        assert set(request.conditions.codes)==set(COMPANIES)
        assert not request.clarification


@pytest.mark.parametrize('text',['所有','数据库','库存','库存里的货物','同仁堂','排名','库里的所有人'])
def test_non_catalog_words_cannot_authorize_all_companies(text):
    assert not collection_span_supported(text)


def test_explicit_exclusion_does_not_restore_every_company():
    q='库里所有公司去掉金花股份，按2022年营业收入排名'
    raw=rank_plan(q).model_dump(mode='json')
    raw['edits'].append(dict(field='restrictions.excluded_codes',operation='replace',value=['600080'],text=q,lifetime='turn'))
    plan=TurnPlan.model_validate(raw)
    request,_=apply_understanding(q,'test',plan.compile(q,COMPANIES),DialogueState(),COMPANIES)
    assert set(request.conditions.codes)=={'600085','002082'}


def test_negative_catalog_mention_is_not_an_explicit_universe():
    assert explicit_collection_span('不要查询库里全部公司，只查同仁堂') is None
    q='同仁堂2022年营业收入按从高到低排名'
    from tests.request_expectations import mismatches
    request,_=apply_understanding(q,'test',rank_plan(q).compile(q,COMPANIES),DialogueState(),COMPANIES)
    assert mismatches(request,all_companies=False,codes=['600085'])


def test_correction_example_uses_actual_sparse_wire_contract():
    q='回到刚才的营收查询，改为2022年，用万元'
    plan=TurnPlan(continuity='continue',goals=[dict(id='g1',kind='lookup',source_ref=q,text=q,context_goal_id='old')],
        edits=[dict(field='time.years',value=[2022],operation='replace',text=q),
            dict(field='presentation.unit',value='万元',operation='replace',text=q,lifetime='task')],
        assignments=[dict(id='g1',edits=[dict(field='metrics',operation='replace',value=['operating_revenue'],text=q)])])
    original=plan.model_dump(mode='json')
    wire=encode_turn_proposal(plan)
    schema=turn_schema(sampling_schema(TurnPlan.model_json_schema()),dict(question=q,companies=COMPANIES,state={'active_goals':[{'kind':'lookup'}]}))
    Draft202012Validator(schema).validate(wire)
    assert plan.model_dump(mode='json')==original
    assert not {'id','text'}&set(wire['goals'][0])
    assert wire['goals'][0]['source_ref']==q and 'assignments' not in wire
    restored=TurnPlan.model_validate(decode_turn(copy.deepcopy(wire),q))
    assert restored.goals[0].context_goal_id=='old'
    assert restored.edits[0].value==[2022]
    assert restored.assignments[0].edits[0].value==['operating_revenue']


def test_actual_agent_correction_does_not_teach_internal_fields():
    import time
    from src.agent.v3.agent import V3Agent
    class Capture:
        calls=0
        async def structured(self,schema,system,payload,budget):
            self.calls+=1
            q=payload['question']
            edits=[dict(field='metrics',operation='replace',value=['operating_revenue'],text=q),
                dict(field='time.years',operation='replace',value=[2024],text=q)]
            if self.calls==1:
                return TurnPlan(continuity='new',context_references=[dict(text='它',target='company',number='singular')],
                    goals=[dict(id='g1',kind='lookup',source_ref=q)],edits=edits)
            previous=payload['correction_feedback'][0]['invalid_proposal']
            Draft202012Validator(turn_schema(sampling_schema(TurnPlan.model_json_schema()),payload)).validate(previous)
            assert not {'id','text'}&set(previous['goals'][0])
            assert 'assignments' not in previous
            return TurnPlan(continuity='new',unresolved_reference='它',goals=[dict(id='g1',kind='lookup',source_ref=q)],edits=edits)
    async def run():
        capture=Capture()
        async def emit(*args):pass
        agent=V3Agent(None,COMPANIES,model=capture)
        result=await agent._understand(dict(question='它2024年营业收入是多少？',history=[],turn_id='correction',budget=Budget(time.time()+60),emit=emit))
        assert result['request'].clarification and not result['request'].conditions.codes
        assert result['request'].conditions.time.years==[2024]
        assert result['request'].conditions.metrics==['operating_revenue']
        assert capture.calls==2
    asyncio.run(run())


def config():
    return ChatProviderConfig(provider='openai_compat',base_url='https://api.deepseek.com',model='deepseek-flash',
        api_key='private-test-key',timeout_seconds=60,max_retries=0,retry_backoff_seconds=0,max_tokens=3072)


def response(content='{"ok":true}',**kwargs):
    return httpx.Response(200,json=dict(model='deepseek-flash',choices=[{'message':{'content':content}}],
        usage={'prompt_tokens':12,'completion_tokens':4},**kwargs))


def test_cloud_defaults_require_credentials_instead_of_silent_local_fallback(monkeypatch):
    for key in ['LLM_PROVIDER','LLM_API_KEY','LLM_MODEL','DEEPSEEK_API_KEY','DEEPSEEK_MODEL']:
        monkeypatch.delenv(key,raising=False)
    resolved=resolve_chat_provider()
    assert resolved.provider=='openai_compat' and resolved.model=='deepseek-flash'
    assert not resolved.is_configured
    with pytest.raises(ModelFailure,match='cloud_api_key_required'):AsyncModel()
    assert 'private-test-key' not in repr(config())


def test_cloud_json_identity_usage_and_request_are_exact():
    seen=[]
    async def handler(request):
        seen.append(json.loads(request.content))
        assert request.headers['Authorization']=='Bearer private-test-key'
        return response()
    async def run():
        result=await CloudChat(config(),transport=httpx.MockTransport(handler)).bind(format={'type':'object'}).ainvoke([
            ('system','按合同填字段'),('human','财务问题')])
        assert result.content=='{"ok":true}'
        assert result.response_metadata==dict(model='deepseek-flash',prompt_eval_count=12,eval_count=4)
    asyncio.run(run())
    assert seen[0]['temperature']==0 and seen[0]['max_tokens']==3072
    assert seen[0]['thinking']=={'type':'disabled'}
    assert seen[0]['response_format']=={'type':'json_object'}
    assert 'JSON' in seen[0]['messages'][0]['content']
    assert 'Authorization' not in json.dumps(seen)


@pytest.mark.parametrize('status',[400,401,402,403,429,500,503])
def test_cloud_error_is_safe_and_not_automatically_retried(status):
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(status,text='private-test-key sensitive server detail')
    async def run():
        with pytest.raises(CloudProviderFailure) as exc:
            await CloudChat(config(),transport=httpx.MockTransport(handler)).ainvoke([('human','JSON')])
        assert 'private-test-key' not in str(exc.value)
    asyncio.run(run())
    assert len(seen)==1


def test_cloud_never_accepts_an_unexpected_response_model():
    def handler(request):
        return httpx.Response(200,json={'model':'different-model','choices':[{'message':{'content':'{}'}}]})
    async def run():
        with pytest.raises(CloudProviderFailure,match='model_mismatch'):
            await CloudChat(config(),transport=httpx.MockTransport(handler)).ainvoke([('human','JSON')])
    asyncio.run(run())


def test_cloud_cancellation_reaches_http_transport():
    async def run():
        entered=asyncio.Event();cancelled=asyncio.Event()
        async def handler(request):
            try:entered.set();await asyncio.Event().wait()
            except asyncio.CancelledError:cancelled.set();raise
        task=asyncio.create_task(CloudChat(config(),transport=httpx.MockTransport(handler)).ainvoke([('human','JSON')]))
        await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert cancelled.is_set()
    asyncio.run(run())


def test_same_cloud_provider_serves_all_structured_steps(monkeypatch):
    from pydantic import BaseModel,ConfigDict
    import time
    from src.agent.v3 import model as model_module
    class Output(BaseModel):
        model_config=ConfigDict(extra='forbid')
        ok:bool
    sent=[]
    def handler(request):sent.append(json.loads(request.content));return response()
    monkeypatch.setattr(model_module,'resolve_chat_provider',config)
    def client(c):return CloudChat(c,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(model_module,'CloudChat',client)
    async def run():
        model=AsyncModel()
        budget=Budget(deadline=time.time()+270)
        for _ in range(3):assert (await model.structured(Output,'只输出JSON',{'question':'测试合同'},budget)).ok
        assert budget.calls==3 and len(budget.timings)==3
        assert all(t['model']=='deepseek-flash' for t in budget.timings)
    asyncio.run(run())
    assert len(sent)==3 and all(b['model']=='deepseek-flash' for b in sent)
