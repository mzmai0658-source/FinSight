"""保护格式合同、连续失败恢复和程序生成财务说明的边界。"""
import pytest
from jsonschema import Draft202012Validator

from src.agent.v3.contracts import Conditions, DialogueState, Goal, InterruptedRequest, Request
from src.agent.v3.executor import ExecutionResult
from src.agent.v3.model import sampling_schema
from src.agent.v3.model_wire import encode_schema
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.rendering import render_concept_definition, render_financial
from src.agent.v3.tasks import failure_context
from src.agent.v3.turn_transport import turn_guide, turn_schema
from src.agent.v3.state import apply_understanding


def transport(feedback=()):
    return encode_schema(turn_schema(sampling_schema(TurnPlan.model_json_schema()), {
        'question': '改为2022年，其余照旧',
        'companies': {'600085': '同仁堂'},
        'state': {'active_goals': [{'id': 'old', 'kind': 'lookup'}]},
        'correction_feedback': list(feedback),
    }))


@pytest.mark.parametrize('feedback', [(), ({'required_goal_kinds': ['lookup', 'concept']},)])
def test_model_sees_the_same_goal_shape_and_required_fields_as_the_validator(feedback):
    schema = transport(feedback)
    guide = turn_guide(schema)
    assert set(guide['required']) == set(schema['required'])
    assert set(guide['required'])=={'goals','continuity'}
    assert 'edits' in guide['goals']['items']['properties']
    assert guide['goals'] == schema['properties']['goals']
    assert guide['goals'] is not schema['properties']['goals']
    goal_rule = guide['goals']
    if goal_rule['type'] == 'array':
        validator = Draft202012Validator(goal_rule)
        assert not validator.is_valid([])
        assert not validator.is_valid([{}])
        assert not validator.is_valid([{'kind': goal_rule['items']['properties']['kind']['enum'][0], 'extra': True}])
    else:
        assert set(goal_rule['required']) == {'lookup', 'concept', 'remaining'}
        assert goal_rule['properties']['remaining']['type'] == 'array'


def confirmed_state():
    return DialogueState(topic='financial', conditions=Conditions(
        codes=['600085'], metrics=['operating_revenue'], time={'mode': 'explicit', 'years': [2024]},
        presentation={'unit': '万元', 'decimals': 2, 'format': 'table'}),
        active_goals=[Goal(id='old', kind='lookup', text='已确认任务')])


def failed(state, question, paths, approved=False, request=None):
    checkpoint = {'approved': approved, 'uncertain_paths': paths}
    if request:
        checkpoint['request'] = request.model_dump(mode='json')
    return DialogueState.model_validate(failure_context({
        'id': 'failure-' + str(state.revision), 'status': 'failed', 'question': question,
        'context': state.model_dump(mode='json'), 'request_checkpoint': checkpoint,
    }))


@pytest.mark.parametrize('first_field,first_question', [
    ('time.years', '改为2022年'), ('codes', '换家公司'), ('metrics', '改看毛利率'),
])
def test_a_second_format_failure_cannot_erase_an_unconfirmed_financial_change(first_field, first_question):
    state = failed(confirmed_state(), first_question, [first_field])
    state = failed(state, '以后都按亿元显示', ['presentation.unit'])
    assert set(state.interrupted_request.uncertain_paths) == {first_field, 'presentation.unit'}
    assert not state.recent_facts and not state.recent_computed


def test_a_failed_temporary_display_change_does_not_cancel_older_uncertainty():
    state = failed(confirmed_state(), '以后都用亿元', ['presentation.unit'])
    state = failed(state, '仅这一次按万元显示', ['presentation.unit'])
    assert state.interrupted_request.uncertain_paths == ['presentation.unit']


def test_legacy_unknown_context_also_keeps_a_new_failed_year_change():
    state = confirmed_state()
    state.interrupted_request = InterruptedRequest(turn_id='legacy', question='旧的失败请求', status='failed')
    state = failed(state, '改为2022年', ['time.years'])
    assert set(state.interrupted_request.uncertain_paths) == {'codes', 'metrics', 'time.years'}


def test_different_failed_turn_scopes_cannot_make_a_later_persistent_edit_temporary():
    state = failed(confirmed_state(), '改为2022年，这次临时用亿元', ['time.years', 'presentation.unit'])
    state = failed(state, '以后用亿元', ['presentation.unit'])
    q = '确定按2022年查，其他照旧'
    plan = TurnPlan(continuity='continue', goals=[dict(id='g1', kind='lookup', context_goal_id='old')],
        edits=[dict(field='time.years', operation='replace', value=[2022], text=q)])
    request, state = apply_understanding(q, 'confirm-year', plan.compile(q, {'600085':'同仁堂'}), state, {'600085':'同仁堂'})
    assert request.clarification and state.interrupted_request.uncertain_paths == ['presentation.unit']


def test_preferences_are_saved_during_recovery_but_an_unconfirmed_year_is_not_queried():
    companies = {'600085': '同仁堂'}
    state = failed(confirmed_state(), '改为2022年', ['time.years'])
    q = '以后都用亿元和三位小数，仅输出表格'
    plan = TurnPlan(continuity='continue', goals=[dict(id='g1', kind='lookup', context_goal_id='old')],
        edits=[dict(field=field, operation='replace', value=value, lifetime='session', text=q)
               for field, value in [('presentation.unit', '亿元'), ('presentation.decimals', 3), ('presentation.format', 'table')]])
    request, state = apply_understanding(q, 'preferences', plan.compile(q, companies), state, companies)
    assert request.clarification and '已记录' in request.clarification[-1]
    assert state.interrupted_request.uncertain_paths == ['time.years']
    assert state.conditions.time.years == [2024] and not state.recent_facts
    assert {e.field: e.value for e in state.session_preferences} == {
        'presentation.unit': '亿元', 'presentation.decimals': 3, 'presentation.format': 'table'}
    q = '年份确定用2022年，其他条件照旧'
    plan = TurnPlan(continuity='continue', goals=[dict(id='g1', kind='lookup', context_goal_id='g1')],
        edits=[dict(field='time.years', operation='replace', value=[2022], text=q)])
    request, state = apply_understanding(q, 'confirmed-year', plan.compile(q, companies), state, companies)
    assert not request.clarification and state.interrupted_request is None
    assert request.conditions.time.years == [2022]
    assert request.conditions.presentation.unit == '亿元'
    assert request.conditions.presentation.decimals == 3 and request.conditions.presentation.format == 'table'


def test_a_confirmed_checkpoint_does_not_resurrect_resolved_uncertainty():
    state = confirmed_state()
    request = Request(turn_id='failure-0', question='按2022年查', conditions=state.conditions,
                      goals=[Goal(id='g', kind='lookup', text='按2022年查')])
    result = failed(state, request.question, [], approved=True, request=request)
    assert result.interrupted_request.uncertain_paths == []


@pytest.mark.parametrize('metric', ['attributable_net_profit', 'deducted_attributable_net_profit', 'operating_revenue'])
def test_a_concept_is_explained_once_instead_of_appending_a_duplicate_catalog_definition(metric):
    text = render_concept_definition(metric, 'consolidated')
    if metric == 'attributable_net_profit':
        assert text.count('归属于母公司股东') == 1
        assert '少数股东' in text and '单体' in text and '子公司' in text
    elif metric == 'deducted_attributable_net_profit':
        assert text.count('非经常性损益') == 1
        assert '投资收益' in text and '母公司' in text
    else:
        assert '主营业务收入' in text and '营业总收入' in text


def test_parent_profit_concept_keeps_its_own_definition_without_a_consolidated_preface():
    text = render_concept_definition('net_profit', 'parent')
    assert '母公司单体' in text and '投资收益' in text
    assert '少数股东' not in text


def test_program_calculated_difference_uses_one_sentence_with_its_direction():
    request = Request(turn_id='difference', question='两年相差多少',
                      conditions=Conditions(presentation={'unit': '万元'}),
                      goals=[Goal(id='g', kind='lookup', text='两年相差多少')])
    result = ExecutionResult(derived=[dict(company='同仁堂', label='合并营业收入差额', value='10000',
        unit='元', detail='；比较2023 年报与2022 年报，后期减基期')])
    text = render_financial(request, result)
    assert '1.00 万元' in text and '后期减基期' in text
    assert '。；' not in text and '**；比较' in text
