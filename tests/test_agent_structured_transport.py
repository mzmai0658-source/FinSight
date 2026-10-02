"""作品说明：离线验证适配器与预算回归，无需启动 Ollama。"""
from types import SimpleNamespace
import httpx
import pytest
from pydantic import BaseModel
from src.agent.semantic_planner import SemanticPlanner
from src.agent.turn_runtime import AgentFailure, TurnRuntime


class Payload(BaseModel):
    value: int


def adapter(response=None, error=None):
    calls = []
    class FakeModel:
        def bind(self, **options):
            calls.append(options)
            return self
        def invoke(self, messages):
            calls.append(messages)
            if error: raise error
            return response
    planner = object.__new__(SemanticPlanner)
    planner.model_factory = lambda **options: FakeModel()
    planner.model_options = {}
    planner.call_timeout = 30
    return planner, calls


def message(content, done_reason='stop'):
    return SimpleNamespace(content=content,response_metadata={'done_reason':done_reason},usage_metadata={})


def test_plain_schema_json_does_not_require_tool_call():
    planner,calls = adapter(message('{"value":3}'))
    assert planner.structured_call(Payload,'return JSON',{}).value == 3
    assert calls[0]['format']==Payload.model_json_schema()
    assert calls[0]['stream'] is False and 'tools' not in calls[0]


def test_current_question_is_last_user_message_separate_from_history():
    planner, calls = adapter(message('{"value":3}'))
    planner.structured_call(Payload, 'return JSON', {
        'question':'1', 'recent_dialogue':[{'role':'user','content':'今天吃什么'}]})
    messages = calls[1]
    assert messages[-1] == {'role':'user','content':'1'}
    assert '今天吃什么' in messages[-2]['content']
    assert '不是本轮提问' in messages[-2]['content']


def test_validation_error_preserves_field_path_and_retry_classification():
    planner,_ = adapter(message('{"wrong":3}'))
    with pytest.raises(AgentFailure) as failure:
        planner.structured_call(Payload,'return JSON',{})
    assert failure.value.code=='model_schema_invalid' and failure.value.retryable
    assert 'value' in str(failure.value) and 'missing' in str(failure.value)


def test_truncation_is_not_retried_with_identical_output_budget():
    planner,_ = adapter(message('{"value":',done_reason='length'))
    with pytest.raises(AgentFailure) as failure:
        planner.structured_call(Payload,'return JSON',{})
    assert failure.value.code=='model_output_truncated' and not failure.value.retryable


def test_offline_model_is_a_transport_failure_not_missing_user_conditions():
    planner,calls = adapter(error=httpx.ConnectError('offline'))
    with pytest.raises(AgentFailure) as failure:
        planner.structured_call(Payload,'return JSON',{})
    assert failure.value.code=='model_connection_failed' and not failure.value.retryable
    assert len(calls)==2


def test_budget_is_shared_across_planning_and_composition():
    budget=TurnRuntime(max_calls=3)
    for schema in ('TurnRequest','TurnRequest','AnswerDraft'): budget.start_call(schema)
    with pytest.raises(AgentFailure) as failure: budget.start_call('AnswerDraft')
    assert failure.value.code=='turn_budget_exhausted'
