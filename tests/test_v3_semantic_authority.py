"""正确的结构化提案不得被另一套句子规则改错，计算输入和展示各有明确身份。"""
from decimal import Decimal

import pytest

from src.agent.v3.agent import V3Agent
from src.agent.v3.contracts import Conditions, DialogueState, Goal, MODIFICATION, Request
from src.agent.v3.executor import calculation_targets, execute
from src.agent.v3.planner import GoalCoverage, SemanticReview
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.rendering import render_financial
from src.agent.v3.state import apply_understanding
from tests.test_v3_contracts import fact
from tests.test_v3_execution_math import Repository

COMPANIES = {'600085': '同仁堂', '002082': '万邦德'}


def audit(request):
    review = SemanticReview(audit_mode='program', goal_requirements=[GoalCoverage(kind=g.kind,
        source_ref=request.question, goal_ids=[g.id], representation='represented') for g in request.goals],
        satisfied=True, clarification=[], planner_defects=[], companies_correct=True,
        metrics_and_scope_correct=True, time_correct=True, goals_correct=True,
        constraints_correct=True, presentation_correct=True)
    return V3Agent._review_exact_bindings(request, review)


def task(question, **values):
    return Request(turn_id='test', question=question, goals=[Goal(id='g', kind='lookup', text=question)],
        conditions=Conditions(codes=['600085'], metrics=['operating_revenue'], **values))


@pytest.mark.parametrize('company,code', [(name, code) for code, name in COMPANIES.items()])
@pytest.mark.parametrize('negative', ['不要只取第三季度单季', '不做第三季度单季拆分'])
def test_cumulative_proposal_is_not_overruled_by_negated_single_quarter(company, code, negative):
    q = f'查{company}2024年前三季度累计营业收入，{negative}。'
    request = task(q, time={'mode': 'explicit', 'years': [2024], 'periods': ['Q3']})
    request.conditions.codes = [code]
    review = audit(request)
    assert review.accepted, review.planner_defects
    assert request.conditions.time.single_quarter is False


def test_ratio_result_does_not_override_money_input_display():
    q = '金额还是万元两位小数，增长率用百分比两位小数，只要表格。'
    request = task(q, time={'mode': 'explicit', 'years': [2024]}, calculation='yoy',
        comparison_axis='years', presentation={'unit': '万元', 'decimals': 2, 'format': 'table', 'include_inputs': True})
    review = audit(request)
    assert review.accepted, review.planner_defects
    result = execute(request, Repository([fact('600085', 2023, '1000000', metric='operating_revenue'),
        fact('600085', 2024, '1200000', metric='operating_revenue')]))
    body = render_financial(request, result)
    assert '120.00' in body and '万元' in body and '20.00 %' in body


@pytest.mark.parametrize('name,code', [(name, code) for code, name in COMPANIES.items()])
def test_forecast_boundary_is_not_forced_back_into_lookup(name, code):
    q = f'请预测{name}2027年营业收入。'
    plan = TurnPlan(goals=[dict(id='g', kind='unsupported')], edits=[
        dict(field=field, operation='replace', value=value, text=q) for field, value in (
            ('codes', [code]), ('metrics', ['operating_revenue']), ('time.years', [2027]))])
    understanding = plan.compile(q, COMPANIES)
    request, _ = apply_understanding(q, 'forecast', understanding, DialogueState(), COMPANIES)
    assert [g.kind for g in request.goals] == ['unsupported']
    assert request.unsupported and not request.clarification


def test_explicit_operand_roles_are_independent_of_word_order_and_no_2022_is_loaded():
    operands = [dict(current={'year': 2024}, base={'year': 2023})]
    for question in ('2023年和2024年原数也列出，再算2024年比2023年同比',
                     '计算同比，同时给出2023年和2024年原数'):
        request = task(question, time={'mode': 'explicit', 'years': [2023, 2024]},
            calculation='yoy', comparison_axis='years', calculation_inputs=operands,
            presentation={'unit': '万元', 'include_inputs': True})
        selections = {'600085': [(2023, 'FY'), (2024, 'FY')]}
        assert calculation_targets(request, selections) == {'600085': [(2024, 'FY')]}
        repo = Repository([fact('600085', 2023, '1000000', metric='operating_revenue'),
            fact('600085', 2024, '1200000', metric='operating_revenue')])
        result = execute(request, repo)
        assert len(result.derived) == 1 and Decimal(result.derived[0]['value']) == 20
        assert not result.missing and result.goals[0].status == 'completed'
        assert all(year != 2022 for query in repo.queries for pairs in query.values() for year, _ in pairs)


def test_explicit_reverse_roles_preserve_the_requested_direction():
    request = task('以2024为基期看2023的相对变化', time={'mode': 'explicit', 'years': [2023, 2024]},
        calculation='relative_percent', comparison_axis='years',
        calculation_inputs=[dict(current={'year': 2023}, base={'year': 2024})])
    result = execute(request, Repository([fact('600085', 2023, '50', metric='operating_revenue'),
        fact('600085', 2024, '100', metric='operating_revenue')]))
    assert result.derived[0]['year'] == 2023 and Decimal(result.derived[0]['value']) == -50


@pytest.mark.parametrize('order,limit,expected', [('desc', 2, ['002082', '600085']), ('asc', 1, ['600085'])])
def test_order_and_limit_execute_for_company_comparison(order, limit, expected):
    request = task('比较同一年收入并按数值排序，仅保留所需条数', time={'mode': 'explicit', 'years': [2024]},
        comparison_axis='companies', presentation={'order': order, 'limit': limit})
    request.conditions.codes = list(COMPANIES)
    request.goals = [Goal(id='g', kind='compare', text=request.question)]
    result = execute(request, Repository([fact('600085', 2024, '10', metric='operating_revenue'),
        fact('002082', 2024, '20', metric='operating_revenue')]))
    assert [f.stock_code for f in result.facts] == expected
    assert result.goals[0].status == 'completed'


def test_mixed_dimensions_use_metric_units_without_money_conversion_of_eps():
    request = task('收入用亿元，每股收益用元每股', time={'mode': 'explicit', 'years': [2024]},
        presentation={'metric_units': {'operating_revenue': '亿元', 'eps_basic': '元/股'}})
    request.conditions.metrics = ['operating_revenue', 'eps_basic']
    result = execute(request, Repository([fact('600085', 2024, '123000000', metric='operating_revenue'),
        fact('600085', 2024, '1.23', metric='eps_basic', unit='元/股')]))
    body = render_financial(request, result)
    assert '| 1.23 | 亿元 |' in body and '| 1.2300 | 元/股 |' in body


def test_complete_new_selection_can_leave_an_unrelated_failed_pending_request():
    old = Conditions(codes=['002082'], metrics=['attributable_net_profit'], calculation='yoy',
        comparison_axis='years', time={'mode': 'explicit', 'years': [2025]})
    pending = Request(turn_id='failed', question='旧比较未完成', conditions=old,
        clarification=['旧的比较和展示尚未确认'], goals=[Goal(id='old', kind='compare', text='旧比较')])
    previous = DialogueState(topic='financial', conditions=old, pending=pending,
        active_goals=pending.goals, interrupted_request=dict(turn_id='failed', question='旧比较未完成',
        status='failed', uncertain_paths=['calculation', 'comparison_axis', 'presentation.unit'], uncertain_paths_normalized=True))
    q = '接着查同仁堂2023年营业收入即可'
    plan = TurnPlan(continuity='continue', goals=[dict(id='new', kind='lookup')], edits=[
        dict(field=field, operation='replace', value=value, text=q) for field, value in (
            ('codes', ['600085']), ('metrics', ['operating_revenue']), ('time.years', [2023]))])
    request, saved = apply_understanding(q, 'new', plan.compile(q, COMPANIES), previous, COMPANIES)
    assert not request.clarification and not request.goals[0].clarification
    assert request.conditions.calculation == 'none'
    assert saved.interrupted_request is None


def test_invalid_metric_display_unit_is_still_rejected():
    request = task('单位错误', time={'mode': 'explicit', 'years': [2024]},
        presentation={'metric_units': {'operating_revenue': '元/股'}})
    from src.agent.v3.units import presentation_unit_compatible
    assert not presentation_unit_compatible(request.conditions, 'operating_revenue')


@pytest.mark.parametrize('roles', [
    [dict(current={'year':2025},base={'year':2024})],
    [dict(current={'year':2024},base={'code':'999999','year':2023})],
    [dict(current={'year':2024},base={'year':2024,'period':'HY'})],
])
def test_invalid_or_missing_operand_identity_cannot_be_reported_complete(roles):
    request=task('计算所需变化',time={'mode':'explicit','years':[2024]},calculation='yoy',
        comparison_axis='years',calculation_inputs=roles)
    result=execute(request,Repository([fact('600085',2024,'100',metric='operating_revenue')]))
    assert not result.derived and result.goals[0].status!='completed'
    assert result.missing or result.notes


def test_chart_rate_unit_is_independent_of_money_input_and_verified_by_reconstruction(monkeypatch):
    request=task('同比图和金额原数',time={'mode':'explicit','years':[2023,2024]},calculation='yoy',
        comparison_axis='years',presentation={'unit':'万元','chart_type':'line','include_inputs':True})
    request.goals=[Goal(id='g',kind='chart',text=request.question)]
    result=execute(request,Repository([fact('600085',year,value,metric='operating_revenue')
        for year,value in ((2022,'1000000'),(2023,'1200000'),(2024,'1800000'))]))
    assert result.charts[0]['unit']=='%'
    from src.agent.v3.verification import verify
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda _:True)
    assert verify(request,result,[],[],result.support_facts).status=='pass'
    result.charts[0]['unit']='万元'
    assert verify(request,result,[],[],result.support_facts).status=='fail'


def test_operand_direction_is_checked_against_request_even_if_mutated_math_is_consistent(monkeypatch):
    request=task('相对变化',time={'mode':'explicit','years':[2023,2024]},calculation='relative_percent',
        comparison_axis='years',calculation_inputs=[dict(current={'year':2024},base={'year':2023})])
    result=execute(request,Repository([fact('600085',2023,'50',metric='operating_revenue'),
        fact('600085',2024,'100',metric='operating_revenue')]))
    from src.agent.v3.verification import verify
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda _:True)
    assert verify(request,result,[],[]).status=='pass'
    result.derived[0]['inputs'].reverse();result.derived[0]['value']='-50'
    assert verify(request,result,[],[]).status=='fail'


def test_lookup_quantity_is_never_silently_ignored_when_axis_was_not_set():
    request=task('数值降序仅一条',time={'mode':'explicit','years':[2024]},
        presentation={'order':'desc','limit':1})
    request.conditions.codes=list(COMPANIES)
    result=execute(request,Repository([fact('600085',2024,'10',metric='operating_revenue'),
        fact('002082',2024,'20',metric='operating_revenue')]))
    assert len(result.facts)==1 and result.facts[0].stock_code=='002082'


def test_single_company_year_values_can_be_sorted_and_limited_without_rank_kind():
    request=task('收入按数值从高到低仅保留一项',time={'mode':'explicit','years':[2022,2023,2024]},
        comparison_axis='years',presentation={'order':'desc','limit':1})
    result=execute(request,Repository([fact('600085',year,value,metric='operating_revenue')
        for year,value in ((2022,'10'),(2023,'50'),(2024,'20'))]))
    assert len(result.facts)==1 and result.facts[0].year==2023 and result.goals[0].status=='completed'


def test_metric_unit_dimension_error_is_stopped_before_execution():
    q='同仁堂2024年营业收入用元每股'
    plan=TurnPlan(goals=[dict(id='g',kind='lookup')],edits=[
        dict(field=field,operation='replace',value=value,text=q) for field,value in (
            ('codes',['600085']),('metrics',['operating_revenue']),('time.years',[2024]),
            ('presentation.metric_units',{'operating_revenue':'元/股'}))])
    request,_=apply_understanding(q,'units',plan.compile(q,COMPANIES),DialogueState(),COMPANIES)
    assert request.clarification or request.goals[0].clarification


def test_percent_metric_mapping_for_a_growth_result_never_converts_raw_amounts():
    request=task('同比和原数',time={'mode':'explicit','years':[2024]},calculation='yoy',comparison_axis='years',
        presentation={'metric_units':{'operating_revenue':'%'},'include_inputs':True})
    result=execute(request,Repository([fact('600085',2023,'1000000',metric='operating_revenue'),
        fact('600085',2024,'1200000',metric='operating_revenue')]))
    body=render_financial(request,result)
    assert '万元' in body and '20.00 %' in body


@pytest.mark.parametrize('field,bad', [('company','错误公司'),('year',2020),('metric','attributable_net_profit'),
    ('unit','元'),('period','HY'),('formula','unregistered_formula'),('label','归母净利润')])
def test_derived_identity_unit_and_formula_are_verified_independently_of_correct_arithmetic(monkeypatch,field,bad):
    request=task('计算同比',time={'mode':'explicit','years':[2024]},calculation='yoy',comparison_axis='years')
    result=execute(request,Repository([fact('600085',2023,'100',metric='operating_revenue'),
        fact('600085',2024,'120',metric='operating_revenue')]))
    from src.agent.v3.verification import verify
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda _:True)
    assert verify(request,result,[],[]).status=='pass'
    result.derived[0][field]=bad
    assert verify(request,result,[],[]).status=='fail'


@pytest.mark.parametrize('current,base',[(2024,2023),(2023,2024)])
def test_explicit_operand_recipe_survives_saved_reference_revalidation(monkeypatch,current,base):
    from tests.test_v3_value_references import ReferenceRepository
    from src.agent.v3.value_references import computed_references,revalidate_computed
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda _:True)
    repo=ReferenceRepository([fact('600085',2023,'100',metric='operating_revenue'),
        fact('600085',2024,'120',metric='operating_revenue')])
    request=task('保存本期与基期',time={'mode':'explicit','years':[2023,2024]},calculation='relative_percent',
        comparison_axis='years',calculation_inputs=[dict(current={'year':current},base={'year':base})])
    reference=computed_references(request,execute(request,repo),repo.version)[0]
    assert revalidate_computed(repo,reference)==reference
    changed=reference.model_copy(update={'value':'999'})
    assert revalidate_computed(repo,changed) is None
