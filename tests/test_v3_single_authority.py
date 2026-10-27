"""自然表达规范化、目标条件和记忆采用同一合同，避免按题目增加例外。"""
from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator

from src.agent.v3.contracts import Conditions, DialogueState, MODIFICATION
from src.agent.v3.condition_lifetimes import persist_conditions
from src.agent.v3.condition_updates import mentioned_years, validate_year_change
from src.agent.v3.model import sampling_schema
from src.agent.v3.model_wire import encode_schema, translate_data
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.state import apply_understanding
from src.agent.v3.turn_transport import decode_turn, turn_schema


COMPANIES = {'000333': '美的集团', '600887': '伊利股份', '300750': '宁德时代',
             '601088': '中国神华', '990101': '星河集团', '990102': '青禾股份'}


def schema(question, state=None):
    return turn_schema(sampling_schema(TurnPlan.model_json_schema()),
                       dict(question=question, companies=COMPANIES, state=state or {}))


def plan(question, codes, *, goals=None, edits=None):
    values = [('codes', codes), ('metrics', ['operating_revenue']), ('time.years', [2024])]
    return TurnPlan(goals=goals or [dict(id='g1', kind='lookup')], edits=[
        dict(field=field, operation='replace', value=value, text=question)
        for field, value in (edits if edits is not None else values)])


@pytest.mark.parametrize('code,name,short', [
    ('000333', '美的集团', '美的'), ('600887', '伊利股份', '伊利'),
    ('990101', '星河集团', '星河'), ('990102', '青禾股份', '青禾')])
def test_registered_unique_alias_is_equivalent_through_schema_compile_and_state(code, name, short):
    for company in (name, short, code):
        q = f'帮我列出{company} 2024年全年合并营业收入'
        s = schema(q)
        rule = s['properties']['edits']['properties']['codes']
        rule = rule.get('anyOf', [rule])[0]
        value = s['$defs'][rule['items']['$ref'].rsplit('/', 1)[-1]]['properties']['value']
        assert value.get('maxItems') != 0, '词面未命中不能禁止正确公司提案'
        request, saved = apply_understanding(q, 'alias', plan(q, [code]).compile(q, COMPANIES),
                                             DialogueState(), COMPANIES)
        assert not request.clarification
        assert request.conditions.codes == saved.conditions.codes == [code]


def test_alias_in_multiple_companies_does_not_delete_a_correct_selection():
    q = '宁德时代、伊利和中国神华2024年全年合并营收由少到多排，只留一家'
    p = plan(q, ['300750', '600887', '601088'])
    request, _ = apply_understanding(q, 'multiple', p.compile(q, COMPANIES), DialogueState(), COMPANIES)
    assert set(request.conditions.codes) == {'300750', '600887', '601088'}
    assert not request.clarification


def test_ambiguous_alias_is_not_silently_bound_to_one_registered_company():
    companies = {'990101': '星河集团', '990103': '星河股份'}
    q = '星河2024年全年营业收入多少'
    request, _ = apply_understanding(q, 'ambiguous', plan(q, ['990101']).compile(q, companies),
                                     DialogueState(), companies)
    assert request.clarification
    assert request.conditions.codes == []


def test_unmentioned_registered_company_is_not_authorized_by_catalog_membership():
    q = '宁德时代2024年营业收入是多少'
    request, _ = apply_understanding(q, 'wrong-company', plan(q, ['600887', '300750']).compile(q, COMPANIES),
                                     DialogueState(), COMPANIES)
    assert request.clarification
    assert '600887' not in request.conditions.codes


@pytest.mark.parametrize('year_text', ['2024', '２０２４', '二〇二四', '二零二四', '二千零二十四', '两千零二十四', '24'])
def test_equivalent_explicit_years_have_one_normalized_identity(year_text):
    q = f'查星河集团{year_text}年全年营业收入'
    assert mentioned_years(q) == {2024}
    validate_year_change(q, {'years': []}, {'years': [2024]})
    request, _ = apply_understanding(q, 'year', plan(q, ['990101']).compile(q, COMPANIES),
                                     DialogueState(), COMPANIES)
    assert request.conditions.time.years == [2024]


def test_unstated_year_is_still_rejected_instead_of_becoming_a_default():
    with pytest.raises(ValueError):
        validate_year_change('查星河集团营业收入', {'years': []}, {'years': [2024]})


@pytest.mark.parametrize('years',['2023至2025年','二〇二三至二〇二五年','二千零二十三到两千零二十五年'])
def test_equivalent_year_ranges_keep_both_endpoints(years):
    assert mentioned_years('星河集团'+years+'营业收入') == {2023,2024,2025}


@pytest.mark.parametrize('years',['二〇二三、二〇二四年','二〇二三和二〇二四年','二〇二三、二〇二四以及二〇二五年'])
def test_equivalent_year_lists_share_the_final_year_suffix(years):
    expected={2023,2024,2025} if '二五' in years else {2023,2024}
    assert mentioned_years('星河集团'+years+'营业收入')==expected


def test_presentation_lifetime_is_optional_and_task_defaults_survive_clarification():
    q = '2024年全年合并营业收入是多少？请用亿元两位小数，不画图。'
    s = schema(q)
    for definition in s['$defs'].values():
        if 'lifetime' in definition.get('properties', {}):
            assert 'lifetime' not in definition.get('required', [])
    p = plan(q, [], edits=[('metrics', ['operating_revenue']), ('time.years', [2024]),
                          ('presentation.unit', '亿元'), ('presentation.decimals', 2),
                          ('restrictions.no_chart', True)])
    request, state = apply_understanding(q, 'missing-company', p.compile(q, COMPANIES), DialogueState(), COMPANIES)
    assert request.clarification and state.pending
    assert state.pending.conditions.presentation.unit == '亿元'
    assert state.pending.conditions.presentation.decimals == 2
    answer = '星河集团，其他条件不变'
    p = TurnPlan(continuity='continue', goals=[dict(id='g1', kind='lookup', context_goal_id='g1')],
                 edits=[dict(field='codes', operation='replace', value=['990101'], text=answer)])
    request, state = apply_understanding(answer, 'company-answer', p.compile(answer, COMPANIES), state, COMPANIES)
    assert not request.clarification
    assert request.conditions.presentation.unit == state.conditions.presentation.unit == '亿元'


def test_temporary_override_restores_prior_task_and_does_not_change_defaults():
    base = Conditions(presentation=dict(unit='万元', decimals=2))
    current = Conditions(presentation=dict(unit='亿元', decimals=4))
    edits = [MODIFICATION.validate_python(dict(field=f, operation='replace', value=v,
              lifetime='turn', text='暂时用亿元四位')) for f, v in [('presentation.unit', '亿元'), ('presentation.decimals', 4)]]
    saved = persist_conditions(current, base, edits, [])
    assert saved.presentation.unit == '万元' and saved.presentation.decimals == 2


def test_explicit_task_restriction_survives_unchanged_continuations():
    q='星河集团2024年营业收入，这项任务不要重复金额'
    p=plan(q,['990101'])
    p.edits.append(MODIFICATION.validate_python(dict(field='restrictions.no_repeat',operation='replace',
                                                    value=True,lifetime='task',text=q)))
    _,state=apply_understanding(q,'repeat-task',p.compile(q,COMPANIES),DialogueState(),COMPANIES)
    for index in range(2):
        q='保持原要求'
        p=TurnPlan(continuity='continue',goals=[dict(id='g1',kind='lookup',context_goal_id='g1')])
        request,state=apply_understanding(q,'unchanged-'+str(index),p.compile(q,COMPANIES),state,COMPANIES)
        assert request.conditions.restrictions.no_repeat and state.conditions.restrictions.no_repeat


def test_promoted_local_delta_survives_failure_checkpoint_without_being_applied_twice():
    from src.agent.v3.tasks import failure_context
    q='仅显示五行'
    base=Conditions(codes=['990101'],metrics=['operating_revenue'],time=dict(mode='explicit',years=[2024]),
                    presentation=dict(limit=2))
    previous=DialogueState(topic='financial',conditions=base,active_goals=[dict(id='old',kind='lookup',text='旧任务')],
                           goal_conditions={'old':base})
    wire=dict(continuity='continue',goals=[dict(kind='lookup',context_goal_id='old',
         edits={'presentation.limit':[dict(operation='replace',value=5)]})])
    p=TurnPlan.model_validate(decode_turn(wire,q))
    request,_=apply_understanding(q,'failed-local',p.compile(q,COMPANIES),previous,COMPANIES)
    assert request.conditions.presentation.limit==5
    restored=DialogueState.model_validate(failure_context(dict(id='failed-local',question=q,status='failed',
        context=previous.model_dump(mode='json'),request_checkpoint=dict(approved=False,request=request.model_dump(mode='json')))))
    assert 'presentation.limit' in restored.interrupted_request.uncertain_paths


def test_typed_memory_clear_survives_a_failure_before_approval():
    from src.agent.v3.tasks import failure_context
    q='把上一段谈的东西全忘了'
    previous=DialogueState(conditions=Conditions(codes=['990101'],metrics=['operating_revenue']))
    p=TurnPlan(continuity='clear',memory_edits=[dict(field='financial_context',text=q)],
               goals=[dict(id='g1',kind='catalog',catalog_target='capabilities')])
    request,_=apply_understanding(q,'clear-failed',p.compile(q,COMPANIES),previous,COMPANIES)
    restored=DialogueState.model_validate(failure_context(dict(id='clear-failed',question=q,status='failed',
       context=previous.model_dump(mode='json'),request_checkpoint=dict(approved=False,request=request.model_dump(mode='json')))))
    assert not restored.conditions.codes and not restored.conditions.metrics


def test_waiting_goal_does_not_disable_checks_for_an_independent_ready_goal():
    from src.agent.v3.contracts import Request,Goal
    from tests.test_v3_semantic_authority import audit
    c=Conditions(codes=['990101','990102'],metrics=['operating_revenue'],time=dict(mode='explicit',years=[2023,2024]),
                 calculation='difference',comparison_axis='none')
    request=Request(turn_id='mixed',question='独立目标',conditions=c,
        goals=[Goal(id='ready',kind='lookup',text='可执行目标'),Goal(id='waiting',kind='lookup',text='待明确目标',clarification=['请明确公司'])],
        clarification=['请明确公司'])
    assert not audit(request).accepted


def test_explicit_conflict_waits_for_the_user_without_a_format_repair_loop():
    from tests.test_v3_semantic_authority import audit
    q='星河集团2024年营业收入画图，同时不要画图，请先确认。'
    p=plan(q,['990101'],goals=[dict(id='g1',kind='chart')])
    p.edits.append(MODIFICATION.validate_python(dict(field='restrictions.no_chart',operation='replace',value=True,text=q)))
    request,_=apply_understanding(q,'conflict',p.compile(q,COMPANIES),DialogueState(),COMPANIES)
    assert request.goals[0].clarification
    assert audit(request).accepted


def test_each_wire_goal_owns_its_conditions_without_parallel_assignment_ids():
    q = '星河集团2024年合并净利润和母公司净利润分开列出，用万元。'
    raw = dict(continuity='new', edits={}, goals=[
        dict(kind='lookup', edits={'codes': [dict(operation='replace', value=['990101'])],
             'metrics': [dict(operation='replace', value=['net_profit'])],
             'scope': [dict(operation='replace', value=scope)],
             'time.years': [dict(operation='replace', value=[2024])],
             'presentation.unit': [dict(operation='replace', value='万元')]})
        for scope in ('consolidated', 'parent')])
    wire = translate_data(raw)
    validator = Draft202012Validator(encode_schema(schema(q)))
    assert validator.is_valid(wire), list(validator.iter_errors(wire))
    decoded = decode_turn(translate_data(deepcopy(wire), decode=True), q)
    p = TurnPlan.model_validate(decoded)
    request, _ = apply_understanding(q, 'scopes', p.compile(q, COMPANIES), DialogueState(), COMPANIES)
    assert not request.clarification
    assert [request.for_goal(g).conditions.scope for g in request.goals] == ['consolidated', 'parent']
    assert all(request.for_goal(g).conditions.codes == ['990101'] for g in request.goals)


def test_goal_local_all_company_query_expands_the_same_published_catalog():
    q='库内全部公司2024年合并营业收入从高到低列前三家'
    wire=dict(continuity='new',goals=[dict(kind='rank',edits={
        'all_companies':[dict(operation='replace',value=True)],
        'metrics':[dict(operation='replace',value=['operating_revenue'])],
        'time.years':[dict(operation='replace',value=[2024])]})])
    p=TurnPlan.model_validate(decode_turn(wire,q))
    request,state=apply_understanding(q,'universe',p.compile(q,COMPANIES),DialogueState(),COMPANIES)
    assert set(request.conditions.codes)==set(COMPANIES)
    assert set(request.for_goal(request.goals[0]).conditions.codes)==set(COMPANIES)
    restored=type(request).model_validate(request.model_dump(mode='json'))
    assert set(restored.for_goal(restored.goals[0]).conditions.codes)==set(COMPANIES)
    assert '_catalog_codes' not in request.model_dump(mode='json')


@pytest.mark.parametrize('independent_goal',[False,True])
def test_goal_local_catalog_expands_before_comparison_defaults(independent_goal):
    q='比较库内全部公司2024年营业收入差额，并单独看星河集团净利润'
    goals=[dict(kind='compare',edits={
        'all_companies':[dict(operation='replace',value=True)],
        'metrics':[dict(operation='replace',value=['operating_revenue'])],
        'time.years':[dict(operation='replace',value=[2024])],
        'calculation':[dict(operation='replace',value='difference')]})]
    if independent_goal:
        goals.append(dict(kind='lookup',edits={
            'codes':[dict(operation='replace',value=['990101'])],
            'metrics':[dict(operation='replace',value=['net_profit'])],
            'time.years':[dict(operation='replace',value=[2024])]}))
    p=TurnPlan.model_validate(decode_turn(dict(continuity='new',goals=goals),q))
    directory={'990101':'星河集团','990102':'青禾股份'}
    request,_=apply_understanding(q,'comparison-universe',p.compile(q,directory),DialogueState(),directory)
    resolved=request.for_goal(request.goals[0]).conditions
    assert set(resolved.codes)==set(directory)
    assert resolved.comparison_axis=='companies'


def test_clear_company_memory_removes_session_exclusions_before_new_task():
    from src.agent.v3.contracts import MemoryEdit
    from src.agent.v3.memory_updates import apply_memory_edits
    q='删除之前全部公司选择和排除条件'
    preference=MODIFICATION.validate_python(dict(field='restrictions.excluded_codes',operation='replace',
        value=['990101'],text='以后都不看星河集团',lifetime='session'))
    display=MODIFICATION.validate_python(dict(field='presentation.unit',operation='replace',
        value='万元',text='以后都用万元',lifetime='session'))
    state=DialogueState(conditions=dict(codes=['990102'],restrictions=dict(excluded_codes=['990101'])),
        session_preferences=[preference,display])
    state=apply_memory_edits(state,[MemoryEdit(field='company_context',text=q)],q,'clear-company')
    assert [edit.field for edit in state.session_preferences]==['presentation.unit']
    next_q='库内所有公司2024年营业收入'
    p=plan(next_q,[],edits=[('all_companies',True),('metrics',['operating_revenue']),('time.years',[2024])])
    directory={'990101':'星河集团','990102':'青禾股份'}
    request,_=apply_understanding(next_q,'new-task',p.compile(next_q,directory),state,directory)
    assert set(request.conditions.codes)==set(directory)
    assert request.conditions.restrictions.excluded_codes==[]
    assert request.conditions.presentation.unit=='万元'


def test_transport_contract_does_not_reinterpret_user_operations():
    from typing import get_args
    from src.agent.v3.contracts import GoalKind
    first = schema('先别用旧公司，这回只看青禾，图免了')
    second = schema('清空公司后查询青禾股份，不画图')
    assert set(first['properties']['goals']['items']['properties']['kind']['enum']) == set(get_args(GoalKind))
    assert first['properties']['edits']['properties']['codes'] == second['properties']['edits']['properties']['codes']


@pytest.mark.parametrize('kind,question,extra,goal_mode', [
    ('lookup', '比较星河集团2023年与2024年营业收入，给出差额。',
     [('time.years', [2023, 2024]), ('calculation', 'difference'), ('comparison_axis', 'years')], {}),
    ('quote', '把星河集团2024年营业收入在报告里的那一行原样列出来。', [],
     dict(quote_mode='literal', quote_granularity='row'))])
def test_typed_goal_is_not_reclassified_by_another_phrase_list(kind, question, extra, goal_mode):
    from tests.test_v3_semantic_authority import audit
    edits = [('codes', ['990101']), ('metrics', ['operating_revenue'])]
    edits += extra or [('time.years', [2024])]
    p = plan(question, [], goals=[dict(id='g1', kind=kind, **goal_mode)], edits=edits)
    request, _ = apply_understanding(question, 'typed', p.compile(question, COMPANIES), DialogueState(), COMPANIES)
    assert not request.clarification
    assert audit(request).accepted


@pytest.mark.parametrize('q,kind,metrics,extra', [
    ('只列星河集团2024年合并口径净利润，别用母公司净利润替代。', 'lookup', ['net_profit'], {}),
    ('解释营业收入变动的含义，不查数字。', 'concept', ['operating_revenue'], dict(concept_mode='definition'))])
def test_binding_and_review_do_not_turn_excluded_scope_or_concept_into_another_task(q, kind, metrics, extra):
    from src.agent.v3.explicit_conditions import bind_explicit
    from tests.test_v3_semantic_authority import audit
    edits = [('metrics', metrics)]
    if kind == 'lookup': edits += [('codes', ['990101']), ('time.years', [2024]), ('scope', 'consolidated')]
    p = plan(q, [], goals=[dict(id='g1', kind=kind, **extra)], edits=edits)
    p, _ = bind_explicit(p, q, COMPANIES)
    request, _ = apply_understanding(q, 'scope-and-concept', p.compile(q, COMPANIES), DialogueState(), COMPANIES)
    assert not request.clarification
    assert audit(request).accepted


def test_typed_memory_and_collection_instructions_do_not_require_a_phrase_whitelist():
    q = '请把上一段谈的东西全忘了'
    p = TurnPlan(continuity='clear', memory_edits=[dict(field='financial_context', text=q)],
                 goals=[dict(id='g1', kind='catalog', catalog_target='capabilities')])
    p.compile(q, COMPANIES)
    q = '在已收录的上市企业里全部找一下，2024年营业收入由高到低列两家'
    p = plan(q, [], edits=[('all_companies', True), ('metrics', ['operating_revenue']), ('time.years', [2024])])
    p.collection_text = '已收录的上市企业里全部'
    request, _ = apply_understanding(q, 'collection', p.compile(q, COMPANIES), DialogueState(), COMPANIES)
    assert request.conditions.all_companies
    assert set(request.conditions.codes) == set(COMPANIES)
    assert not request.clarification


def test_lifetime_of_one_goal_does_not_spread_into_another_goal():
    q = '归母净利润这回用亿元；营业收入用万元。'
    edits = [MODIFICATION.validate_python(dict(field='presentation.unit', operation='replace', value='万元',
                                              text=q, lifetime='task'))]
    from src.agent.v3.condition_updates import apply_edits
    changed = apply_edits(Conditions(), edits, q)
    assert changed.presentation.unit == '万元'


def test_explicit_time_policy_edit_is_not_rejected_by_a_second_latest_keyword_list():
    from src.agent.v3.condition_updates import apply_edits
    q = '换成刚出炉那份年报'
    previous = Conditions(time=dict(mode='explicit', years=[2023]))
    edit = MODIFICATION.validate_python(dict(field='time.mode', operation='replace', value='latest_each', text=q))
    changed = apply_edits(previous, [edit], q)
    assert changed.time.mode == 'latest_each' and changed.time.years == []


@pytest.mark.parametrize('expected_kind,actual_kind,extra', [
    ('compare', 'lookup', dict(calculation='yoy', comparison_axis='years')),
    ('rank', 'compare', dict(comparison_axis='companies', presentation=dict(order='asc', limit=1)))])
def test_scoring_matches_deliverable_operators_instead_of_internal_goal_labels(expected_kind, actual_kind, extra):
    from eval.cloud_upload200 import match_goals
    q = '同一目标允许等价的内部分类'
    conditions = Conditions(codes=['990101'], metrics=['operating_revenue'],
                            time=dict(mode='explicit', years=[2024]), **extra).model_dump(mode='json')
    request = dict(question=q, conditions=conditions, goals=[dict(id='g1', kind=actual_kind)])
    expectation = dict(kind=expected_kind, codes=['990101'], metrics=['operating_revenue'], years=[2024],
                       periods=['FY'], scope='consolidated')
    if expected_kind == 'compare': expectation.update(calculation='yoy', axis='years')
    else: expectation.update(axis='companies', order='asc', limit=1)
    _, errors = match_goals([expectation], request)
    assert not errors
    wrong = deepcopy(request)
    wrong['conditions']['time']['years'] = [2023]
    assert match_goals([expectation], wrong)[1]
    wrong = deepcopy(request)
    wrong['conditions']['codes'] = ['990102']
    assert match_goals([expectation], wrong)[1]
