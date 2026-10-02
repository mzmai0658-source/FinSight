from datetime import date

import pytest

from src.agent.v3.condition_updates import merge_object
from src.agent.v3.contracts import Conditions, DialogueState, Goal, Understanding
from src.agent.v3.executor import resolve_periods
from src.agent.v3.state import InvalidUnderstanding, apply_understanding
from tests.test_v3_contracts import request, understanding


def test_calendar_window_cannot_be_anchored_to_echoed_database_years():
    selected,_=resolve_periods(request(codes=['600085'],time={'mode':'calendar_years','span':3,'years':[2022,2023,2024]}),[])
    assert selected['600085']==[(year,'FY') for year in range(date.today().year-3,date.today().year)]
    result=merge_object('time',Conditions(time={'mode':'explicit','years':[2023]}).time.model_dump(),{'mode':'calendar_years','span':3})
    assert result['years']==[] and result['pairs'] is None


def test_latest_mode_cannot_silently_erase_explicit_years_in_the_same_patch():
    with pytest.raises(ValueError,match='contradictory'):
        merge_object('time',Conditions().time.model_dump(),{'years':[2024],'mode':'latest_each'})


def test_period_change_cannot_reset_a_confirmed_year_to_latest():
    from src.agent.v3.condition_updates import apply_edits
    from src.agent.v3.contracts import MODIFICATION
    old=Conditions(codes=['600080'],metrics=['operating_cash_flow'],time={'mode':'explicit','years':[2023],'periods':['HY']})
    bad=[MODIFICATION.validate_python(dict(field=field,operation='replace',value=value,text='改成年报'))
        for field,value in [('time.mode','latest_each'),('time.periods',['FY'])]]
    with pytest.raises(ValueError,match='不能清除已确认年份'):apply_edits(old,bad,'改成年报','t')
    changed=apply_edits(old,bad[1:],'改成年报','t')
    assert changed.time.years==[2023] and changed.time.periods==['FY'] and changed.time.mode=='explicit'


def test_negated_report_names_do_not_become_positive_period_choices():
    from src.agent.v3.request_bindings import positive_period_mentions,positive_latest_mentions
    assert {p for _,_,p in positive_period_mentions('不是半年报，是年报')}=={'FY'}
    assert {p for _,_,p in positive_period_mentions('半年报和年报一起')}=={'FY','HY'}
    assert not positive_latest_mentions('不是最新入库')
    from src.agent.v3.request_bindings import explicit_period_bindings
    assert explicit_period_bindings('2023和2024年年报')=={(2023,'FY'),(2024,'FY')}
    assert explicit_period_bindings('不是2023年半年报，是2024年年报')=={(2024,'FY')}
    assert explicit_period_bindings('2023年年报和2024年半年报')=={(2023,'FY'),(2024,'HY')}


def test_latest_windows_respect_each_company_or_common_period_policy():
    reports=[dict(stock_code=code,year=year,period='FY') for code,year in [('600085',2024),('600085',2023),('002082',2023),('002082',2022)]]
    each,_=resolve_periods(request(codes=['600085','002082'],time={'mode':'latest_each','span':2}),reports)
    common,_=resolve_periods(request(codes=['600085','002082'],time={'mode':'latest_common','span':2}),reports)
    assert each=={'600085':[(2023,'FY'),(2024,'FY')],'002082':[(2022,'FY'),(2023,'FY')]}
    # 作品说明：共同期间只有 2023 年，不能把缺失的 2022 年报告宣称为共同期间。
    assert common=={'600085':[(2023,'FY')],'002082':[(2023,'FY')]}


def test_cash_flow_family_is_not_a_unique_metric_or_a_trusted_pending_guess():
    query='金花股份2023年现金流多少'
    u=understanding(query,'metrics','replace',['operating_cash_flow'])
    u.modifications.append(understanding(query,'codes','replace',['600080']).modifications[0])
    r,s=apply_understanding(query,'first',u,DialogueState(),{'600080':'金花股份'})
    assert r.clarification and r.conditions.metrics==[] and not s.pending.conditions.metrics
    q='经营现金流'
    r,_=apply_understanding(q,'second',understanding(q,'metrics','replace',['operating_cash_flow']),s,{'600080':'金花股份'})
    assert not r.clarification and r.conditions.codes==['600080']


def test_short_choice_only_binds_to_a_previously_issued_metric_family_menu():
    q='金花股份2023年现金流多少'
    u=understanding(q,'metrics','replace',['operating_cash_flow'])
    u.modifications.append(understanding(q,'codes','replace',['600080']).modifications[0])
    _,pending=apply_understanding(q,'first',u,DialogueState(),{'600080':'金花股份'})
    q='经营活动那个，半年报'
    r,_=apply_understanding(q,'second',understanding(q,'metrics','replace',['operating_cash_flow']),pending,{'600080':'金花股份'})
    assert r.conditions.metrics==['operating_cash_flow'] and not r.clarification
    r,_=apply_understanding('2024年呢','third',understanding('2024年呢','metrics','replace',['operating_cash_flow']),pending,{'600080':'金花股份'})
    assert r.clarification and not r.conditions.metrics


def test_capability_rule_has_no_financial_selection_prerequisites():
    u=Understanding(topic='rules',continuity='new',goals=[Goal(id='g',kind='rules',text='图形能力',catalog_target='capabilities')],modifications=[],clarification=['请先指定公司和指标'],unsupported=[],unknown_companies=[])
    r,_=apply_understanding('系统图形能力是什么','t',u,DialogueState(),{})
    assert not r.clarification and r.conditions.restrictions.no_query
    from src.agent.v3.rendering import render_rules
    answer=render_rules(DialogueState(),'系统图形能力是什么',r.goals[0],{})
    assert '柱状图' in answer and '单点' in answer


def test_empty_followup_plan_is_a_model_defect_and_not_an_answer():
    with pytest.raises(InvalidUnderstanding,match='executable goal'):
        apply_understanding('改成年报','t',Understanding(topic='financial',continuity='continue',goals=[],modifications=[],clarification=[],unsupported=[],unknown_companies=[]),DialogueState(),{})


def test_system_capabilities_cannot_be_rejected_for_missing_query_conditions():
    with pytest.raises(InvalidUnderstanding,match='supported capabilities'):
        apply_understanding('系统能做什么','t',Understanding(topic='catalog',continuity='new',goals=[Goal(id='a',kind='catalog',text='功能',catalog_target='capabilities')],modifications=[],clarification=[],unsupported=['缺少公司不能解释功能'],unknown_companies=[]),DialogueState(),{})


def test_clarification_menu_survives_a_year_only_reply_and_binds_later_choice():
    q='金花股份2023年现金流多少'
    u=understanding(q,'codes','replace',['600080'])
    _,pending=apply_understanding(q,'first',u,DialogueState(),{'600080':'金花股份'})
    assert pending.pending.clarification_choices[0].options['经营活动']=='operating_cash_flow'
    q='改成2024年'
    _,pending=apply_understanding(q,'second',understanding(q,'time','replace',{'years':[2024]}),pending,{'600080':'金花股份'})
    assert pending.pending.clarification_choices
    q='经营活动那个'
    r,_=apply_understanding(q,'third',understanding(q,'metrics','replace',['operating_cash_flow']),pending,{'600080':'金花股份'})
    assert not r.clarification and r.conditions.time.years==[2024] and r.conditions.codes==['600080']
