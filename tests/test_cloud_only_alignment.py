"""云端配置、零次重试与后台标题的离线协议验收。"""
import json
import time
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from src.agent.providers import resolve_chat_provider
from src.agent.llm_client import LLMClient
from src.api import readiness


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    for key in list(__import__('os').environ):
        if key.startswith(('LLM_', 'DEEPSEEK_', 'OLLAMA_')):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('LLM_API_KEY', 'private-offline-credential')


def test_cloud_defaults_do_not_depend_on_local_installation(monkeypatch):
    monkeypatch.delenv('LLM_API_KEY')
    config = resolve_chat_provider()
    assert config.provider == 'openai_compat'
    assert config.model == 'deepseek-flash'
    assert not config.is_configured
    assert config.timeout_seconds == 60
    assert config.max_retries == 0
    assert config.max_tokens == 3072
    assert config.describe()['expected_num_ctx'] is None


@pytest.mark.parametrize('provider', ['ollama', 'local', 'auto', 'unknown'])
def test_removed_or_unknown_providers_are_configuration_errors(monkeypatch, provider):
    monkeypatch.setenv('LLM_PROVIDER', provider)
    with pytest.raises(ValueError, match='LLM_PROVIDER'):
        resolve_chat_provider()


@pytest.mark.parametrize('model', ['qwen3.5:9b-q4_K_M', 'fin-r1:7b-q4'])
def test_local_model_names_are_rejected_before_http(monkeypatch, model):
    monkeypatch.setenv('LLM_MODEL', model)
    with pytest.raises(ValueError, match='LLM_MODEL'):
        resolve_chat_provider()


@pytest.mark.parametrize('key,value', [('LLM_TIMEOUT_SECONDS','bad'), ('LLM_TIMEOUT_SECONDS','0'),
    ('LLM_MAX_RETRIES','-1'), ('LLM_MAX_TOKENS','0')])
def test_invalid_cloud_numbers_do_not_silently_select_other_defaults(monkeypatch,key,value):
    monkeypatch.setenv(key,value)
    with pytest.raises(ValueError,match=key):resolve_chat_provider()


def test_legacy_cloud_key_fills_only_missing_canonical_value(monkeypatch):
    monkeypatch.delenv('LLM_API_KEY')
    monkeypatch.setenv('DEEPSEEK_API_KEY','private-legacy-credential')
    assert resolve_chat_provider().api_key == 'private-legacy-credential'
    monkeypatch.setenv('LLM_API_KEY','private-canonical-credential')
    config=resolve_chat_provider()
    assert config.api_key == 'private-canonical-credential'
    assert 'private-canonical-credential' not in repr(config)


def test_explicit_empty_canonical_key_does_not_restore_a_legacy_credential(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', '')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'private-legacy-credential')
    assert resolve_chat_provider().is_configured is False
    monkeypatch.setenv('LLM_API_KEY', 'private-canonical-credential')
    assert resolve_chat_provider(api_key='').is_configured is False


@pytest.mark.parametrize('key', ['LLM_MODEL', 'LLM_BASE_URL', 'LLM_MAX_TOKENS'])
def test_explicit_empty_required_setting_is_a_configuration_error(monkeypatch, key):
    monkeypatch.setenv(key, '')
    with pytest.raises(ValueError, match=key):
        resolve_chat_provider()


def response(status=200):
    value=requests.Response();value.status_code=status;value.url='https://api.deepseek.com/chat/completions'
    value._content=json.dumps(dict(model='deepseek-flash',choices=[dict(message=dict(content='{}'))])).encode()
    return value


def fake_session(monkeypatch,client,statuses):
    calls=[]
    def post(url,**kwargs):
        calls.append(kwargs)
        return response(statuses[min(len(calls)-1,len(statuses)-1)])
    monkeypatch.setattr(client,'_get_session',lambda:SimpleNamespace(post=post))
    monkeypatch.setattr(client,'_reset_session',lambda:None)
    monkeypatch.setattr('src.agent.llm_client.time.sleep',lambda value:None)
    return calls


@pytest.mark.parametrize('method', ['chat','chat_json','chat_with_tools'])
def test_zero_retries_still_sends_one_cloud_request(monkeypatch,method):
    monkeypatch.setenv('LLM_MAX_RETRIES','0')
    client=LLMClient();calls=fake_session(monkeypatch,client,[200])
    kwargs=dict(messages=[dict(role='user',content='请返回JSON对象')])
    if method=='chat_with_tools':kwargs['tools']=[]
    result=getattr(client,method)(**kwargs)
    assert result is not None
    assert len(calls)==1
    body=calls[0]['json']
    assert body['model']=='deepseek-flash' and body['temperature']==0
    assert body['thinking']=={'type':'disabled'}
    assert body['max_tokens']==3072
    assert not {'num_ctx','num_predict','reasoning_effort'} & body.keys()


@pytest.mark.parametrize('status',[401,402,403,404])
def test_auth_balance_permission_and_model_errors_never_retry(monkeypatch,status):
    monkeypatch.setenv('LLM_MAX_RETRIES','2')
    client=LLMClient();calls=fake_session(monkeypatch,client,[status,200])
    assert client.chat([dict(role='user',content='hello')]) is None
    assert len(calls)==1


def test_retry_count_means_additional_attempts(monkeypatch):
    monkeypatch.setenv('LLM_MAX_RETRIES','1')
    client=LLMClient();calls=fake_session(monkeypatch,client,[503,200])
    assert client.chat([dict(role='user',content='hello')])=='{}'
    assert len(calls)==2


def test_health_checks_configured_model_instead_of_only_http_200(monkeypatch):
    def get(*args,**kwargs):
        value=response();value._content=b'{"data":[{"id":"other-model"}]}'
        return value
    monkeypatch.setattr(readiness,'_model_get',get)
    assert readiness.probe_llm()[0] is False


def test_title_budget_allows_real_model_admission(monkeypatch):
    import src.api.main as api
    monkeypatch.setenv('INTERNAL_API_TOKEN','private-offline-title-token-12345')
    calls=[]
    class TitleModel:
        async def structured(self,schema,system,payload,budget):
            budget.charge()
            calls.append(budget.finalization_reserve)
            return schema(title='金花营业收入')
    monkeypatch.setattr(api,'_get_agent',lambda:SimpleNamespace(model=TitleModel()))
    result=TestClient(api.app).post('/internal/title',
        headers={'X-Internal-Token':'private-offline-title-token-12345'},
        json=dict(question='金花股份2023年营业收入是多少？',answer='已核验回答'))
    assert result.status_code==200
    assert result.json()['title']=='金花营业收入'
    assert calls==[1.0]


def test_sampling_keeps_named_properties_that_also_are_schema_annotations():
    from src.agent.v3.model import sampling_schema
    from src.api.schemas import InternalTitleResponse
    from jsonschema import Draft202012Validator
    sampled = sampling_schema(InternalTitleResponse.model_json_schema())
    assert 'title' in sampled['properties']
    Draft202012Validator(sampled).validate({'title': '金花营业收入'})
    assert 'title' not in sampled


def test_title_uses_cloud_json_contract_and_is_audited_as_title(monkeypatch, tmp_path):
    import asyncio
    import httpx
    from src.agent.v3.model import AsyncModel, Budget
    from src.agent.v3.cloud_chat import CloudChat
    from src.api.schemas import InternalTitleResponse
    calls=[]
    def reply(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'model':'deepseek-flash',
            'choices':[{'message':{'content':'{"title":"财报查询"}'}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5}})
    target=tmp_path/'model-audit.jsonl'
    monkeypatch.setenv('FINSIGHT_MODEL_AUDIT_PATH',str(target))
    model=AsyncModel(factory=lambda config: CloudChat(config,transport=httpx.MockTransport(reply)))
    async def run():
        budget=Budget(time.time()+12,finalization_reserve=1)
        response=await model.structured(InternalTitleResponse,'请生成简短标题。',{'question':'财报查询'},budget)
        assert response.title=='财报查询'
        assert budget.calls==1
    asyncio.run(run())
    assert len(calls)==1
    assert calls[0]['response_format']=={'type':'json_object'}
    rows=[json.loads(line) for line in target.read_text('utf-8').splitlines()]
    assert rows[0]['kind']=='title'


def test_model_audit_records_usage_without_inputs_or_credentials(monkeypatch,tmp_path):
    from src.agent import cloud_protocol
    target=tmp_path/'calls.jsonl'
    monkeypatch.setenv('FINSIGHT_MODEL_AUDIT_PATH',str(target))
    token=cloud_protocol.audit_start('deepseek-flash','title')
    cloud_protocol.audit_end(token,model='deepseek-flash',usage={'prompt_tokens':5,'completion_tokens':2})
    rows=[json.loads(line) for line in target.read_text(encoding='utf-8').splitlines()]
    assert [r['event'] for r in rows]==['start','end']
    assert rows[1]['model']=='deepseek-flash' and rows[1]['usage']['prompt_tokens']==5
    assert 'private-offline-credential' not in target.read_text(encoding='utf-8')
    assert not any({'headers','api_key','messages','question'} & r.keys() for r in rows)


@pytest.mark.parametrize('reason,word',[
    ('cloud_api_key_required','密钥'),
    ('cloud_authentication_or_balance_failed','认证'),
    ('cloud_rate_limited','限流'),
])
def test_cloud_failures_save_specific_guidance_without_financial_answers(tmp_path,reason,word):
    import asyncio
    from src.agent.v3.tasks import TaskManager,TaskStore,terminal_result
    from src.agent.v3.model import ModelFailure
    class FailedAgent:
        async def run(self,*args,**kwargs):raise ModelFailure(reason)
    async def run():
        store=TaskStore(tmp_path/'tasks.sqlite3');manager=TaskManager(FailedAgent(),store)
        await manager.submit('task','request','session','查询财报',[],time.time()+60)
        events=[event async for event in manager.stream('task')]
        result=terminal_result(store.get('task'))
        assert word in result['answer']['content']
        assert result['facts']==[]
        assert store.get('task')['status']=='failed'
        await manager.close()
    asyncio.run(run())


def test_extraction_json_prompt_uses_cloud_json_mode(monkeypatch):
    from src.etl.etl_worker import ETLWorker
    client=LLMClient();calls=fake_session(monkeypatch,client,[200])
    ETLWorker(client)._extract_metadata_with_llm('上市公司2024年年度报告')
    assert len(calls)==1
    assert calls[0]['json']['response_format']=={'type':'json_object'}
