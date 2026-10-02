"""作品说明：验证本机紧凑传输协议及保留的安全约束。"""
import asyncio,json,time
from types import SimpleNamespace
from jsonschema import Draft202012Validator
import pytest
from src.agent.v3.model import AsyncModel,Budget,ModelFailure,sampling_schema
from src.agent.v3.model_wire import translate_data,encode_schema
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.turn_transport import turn_schema,decode_turn

COMPANIES={'600085':'同仁堂','002082':'万邦德'}

def native(goals,edits=None,**kwargs):
    return translate_data(dict(goals=goals,edits=edits or {},continuity='new',assignments=[],clarification=[],**kwargs))

def schema(q,state=None):
    return encode_schema(turn_schema(sampling_schema(TurnPlan.model_json_schema()),dict(question=q,companies=COMPANIES,state=state or {})))

def test_native_protocol_restores_current_provenance_and_registered_identities_in_one_call(monkeypatch):
    monkeypatch.setattr('src.agent.v3.model.resolve_chat_provider',lambda:SimpleNamespace(provider='ollama',base_url='http://127.0.0.1:11434',model='transport-test'))
    q='同仁堂2024年营业收入用元表示'
    class Transport:
        def __init__(self,**kwargs):pass
        def bind(self,**kwargs):self.schema=kwargs['format'];return self
        async def ainvoke(self,messages):
            assert json.loads(messages[-1][1])['question']==q
            value=native([{'kind':'lookup'}],{k:[{'operation':'replace','value':v}] for k,v in
                [('codes',['600085']),('metrics',['operating_revenue']),('time.years',[2024]),('presentation.unit','元')]})
            assert Draft202012Validator(self.schema).is_valid(value)
            bad=json.loads(json.dumps(value));bad['edits']['codes'][0]['value']=['123456']
            assert not Draft202012Validator(self.schema).is_valid(bad)
            return SimpleNamespace(content=json.dumps(value),response_metadata={'model':'transport-test'})
    async def scenario():
        budget=Budget(time.time()+90)
        plan=await AsyncModel(factory=Transport).structured(TurnPlan,'test',dict(question=q,companies=COMPANIES),budget)
        assert budget.calls==1 and plan.goals[0].id=='g1' and plan.goals[0].source_ref==q
        assert all(e.text==q for e in plan.edits)
        assert plan.compile(q,COMPANIES).goals[0].kind=='lookup'
    asyncio.run(scenario())


def test_budget_reserves_time_for_verification_and_saving():
    budget=Budget(time.time()+9)
    with pytest.raises(ModelFailure,match='turn_deadline_exceeded'):budget.charge()
    assert budget.calls==0


def test_format_repair_has_one_owner_and_keeps_the_original_request(monkeypatch):
    monkeypatch.setattr('src.agent.v3.model.resolve_chat_provider',lambda:SimpleNamespace(provider='ollama',base_url='http://127.0.0.1:11434',model='transport-test'))
    seen=[];q='查财务数字'
    class Transport:
        def __init__(self,**kwargs):pass
        def bind(self,**kwargs):return self
        async def ainvoke(self,messages):
            seen.append(json.loads(messages[-1][1]))
            return SimpleNamespace(content=json.dumps(native([] if len(seen)==1 else [{'kind':'lookup'}])),response_metadata={})
    async def scenario():
        budget=Budget(time.time()+90)
        plan=await AsyncModel(factory=Transport).structured(TurnPlan,'test',dict(question=q,companies=COMPANIES),budget)
        assert plan.goals[0].source_ref==q and budget.calls==2
        assert all(v['question']==q for v in seen)
        assert budget.artifacts['proposal_repair_used']
        assert seen[1]['format_correction']['step']=='TurnPlan'
    asyncio.run(scenario())


@pytest.mark.parametrize('output',['null','{"goals":[null]}','{"edits":{"scope":[null]}}','{"edits":{},"assignments":[null]}','{"edits":{},"edits":{}}'])
def test_malformed_native_objects_are_model_format_failures(monkeypatch,output):
    monkeypatch.setattr('src.agent.v3.model.resolve_chat_provider',lambda:SimpleNamespace(provider='ollama',base_url='http://127.0.0.1:11434',model='transport-test'))
    class Transport:
        def __init__(self,**kwargs):pass
        def bind(self,**kwargs):return self
        async def ainvoke(self,messages):return SimpleNamespace(content=output,response_metadata={})
    with pytest.raises(ModelFailure,match='model_invalid_schema'):
        asyncio.run(AsyncModel(factory=Transport).structured(TurnPlan,'test',dict(question='查数',companies=COMPANIES),Budget(time.time()+90)))


def test_all_goal_kinds_remain_available_for_unseen_paraphrases():
    s=schema('先岔开一下')
    kinds=s['properties']['goals']['items']['properties']['kind']['enum']
    from src.agent.v3.model_wire import LABELS
    assert set(kinds)==set(LABELS['kind'][k] for k in ('lookup','compare','rank','chart','quote','cause','sign','concept','rules','catalog','unsupported'))


def test_kind_owns_options_without_creating_extra_goals():
    raw=dict(goals=[dict(kind='lookup',concept_mode='definition',quote_mode='literal',catalog_target='companies')],edits={})
    plan=TurnPlan.model_validate(decode_turn(raw,'查数'))
    assert len(plan.goals)==1 and plan.goals[0].kind=='lookup'
    assert set(plan.goals[0].model_dump())=={'id','text','source_ref','context_goal_id','execution_ref','kind'}


def test_duplicate_replacements_cannot_hide_a_company():
    value=native([dict(kind='lookup')],{'codes':[dict(operation='replace',value=['600085']),dict(operation='replace',value=['002082'])]})
    value['continuity']=translate_data({'continuity':'continue'})['continuity']
    assert not Draft202012Validator(schema('换公司',{'active_goals':[{'id':'old','kind':'lookup'}]})).is_valid(value)
    value['edits']['codes']=[dict(operation='remove',value=['600085']),dict(operation='add',value=['002082'])]
    assert Draft202012Validator(schema('换公司',{'active_goals':[{'id':'old','kind':'lookup'}]})).is_valid(value)


def test_explicit_literal_bindings_cannot_be_dropped_or_approximated():
    q='同仁堂2024年母公司营业收入，用亿元，只给表格，保留两位小数。'
    s=schema(q);value=native([{'kind':'lookup'}],{k:[{'operation':'replace','value':v}] for k,v in [
        ('codes',['600085']),('metrics',['operating_revenue']),('scope','parent'),('time.years',[2024]),
        ('presentation.unit','亿元'),('presentation.format','table'),('presentation.decimals',2)]})
    validator=Draft202012Validator(s);assert validator.is_valid(value)
    for field,bad_value in [('scope','consolidated'),('presentation.unit','万元'),('presentation.decimals',3)]:
        bad=json.loads(json.dumps(value));bad['edits'][field][0]['value']=bad_value
        assert not validator.is_valid(bad)
    del value['edits']['presentation.unit'];assert not validator.is_valid(value)


def test_concept_metric_binding_does_not_create_a_query():
    q='归母净利润是什么意思，不查数，不画图'
    value=native([{'kind':'concept','concept_mode':'definition'}],{k:[{'operation':'replace','value':v}] for k,v in [
        ('metrics',['attributable_net_profit']),('restrictions.no_query',True),('restrictions.no_chart',True)]})
    assert Draft202012Validator(schema(q)).is_valid(value)
    plan=TurnPlan.model_validate(decode_turn(translate_data(value,decode=True),q))
    assert plan.goals[0].kind=='concept' and len(plan.goals)==1


def test_company_reference_can_be_declared_after_company_context_clear():
    q='它2023年营收多少'
    state={'active_goals':[{'id':'old','kind':'lookup'}],'conditions':{'codes':[],'metrics':['operating_revenue']}}
    value=native([{'kind':'lookup'}],{'time.years':[{'operation':'replace','value':[2023]}],'metrics':[{'operation':'replace','value':['operating_revenue']}]})
    value['continuity']=translate_data({'continuity':'continue'})['continuity'];value['context_references']=[{'text':'它','target':'company','number':'singular'}]
    assert Draft202012Validator(schema(q,state)).is_valid(value)


def test_memory_deletion_cannot_use_old_text_or_internal_number():
    for text in ('旧问句','s0'):
        q='不延续刚才公司'
        value=TurnPlan(goals=[dict(id='g1',kind='catalog',catalog_target='capabilities')],memory_edits=[dict(field='company_context',operation='clear',text=text)])
        compiled=value.compile(q,COMPANIES)
        assert all(edit.text==q for edit in compiled.memory_edits)
        with pytest.raises(ValueError):value.compile('有哪些公司',COMPANIES)


@pytest.mark.parametrize('question,fields',[
    ('同仁堂2024年第四季度单季营收',{'time.single_quarter','time.quarters','time.years','metrics','codes'}),
    ('最新三年营收',{'time.span','metrics'}),
])
def test_sampling_keeps_explicit_quarter_and_report_count(question,fields):
    assert fields<=set(schema(question)['properties']['edits']['required'])


def test_concept_choices_keep_the_whole_registered_metric_catalog():
    from src.agent.v3.catalog import METRICS
    data=schema('归母净利润与母公司净利润有什么区别，不查数')
    metric=data['$defs']['MetricEdit']['properties']['value']['items']['enum']
    assert set(metric)==set(METRICS)


def test_explicit_fact_reference_and_no_requery_are_required():
    state={'recent_fact_count':1,'active_goals':[{'id':'g1','kind':'lookup'}]}
    data=schema('这个数是负的？不重新查数，不重复金额',state)
    assert 'restrictions.no_query' in data['properties']['edits']['required']
    assert data['properties']['context_references']['minItems']==1
    assert data['properties']['claimed_sign']['enum']==['negative']
