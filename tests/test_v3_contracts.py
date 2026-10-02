"""作品说明：验证新边界的实际行为，不依赖旧字段名称。"""
from decimal import Decimal
import json

import pytest
from pydantic import ValidationError

from src.agent.v3.catalog import ALIASES
from src.agent.v3.contracts import Conditions, DialogueState, Fact, Goal, MODIFICATION, Request, TimeSelection, Understanding
from src.agent.v3.executor import ExecutionResult, calculate, display_value, resolve_periods, build_charts
from src.agent.v3.state import InvalidUnderstanding, apply_understanding, read_state
from src.etl.canonical_audit import grid, original_has_value, select_current_column

COMPANIES = {'600085': 'A', '002082': 'B'}


def request(**kwargs):
    return Request(turn_id='turn', question='query', conditions=Conditions(**kwargs), goals=[Goal(id='g1', kind='lookup', text='query')])


def fact(code, year, value, metric='attributable_net_profit', unit='元', period='FY', scope='consolidated'):
    return Fact(id=code+str(year)+metric, data_version='v', stock_code=code, company=COMPANIES[code], year=year,
        period=period, metric=metric, scope=scope, value=value, unit=unit, status='verified')


def understanding(question, field, operation, value, **kwargs):
    return Understanding(topic='financial', continuity='continue', goals=[Goal(id='g1', kind='lookup', text=question)],
        modifications=[MODIFICATION.validate_python(dict(field=field, operation=operation, value=value, text=question))],
        clarification=[], unsupported=[], unknown_companies=[], **kwargs)


def test_accounting_aliases_do_not_merge_non_equivalent_values():
    assert ALIASES['毛利'] != ALIASES['毛利率']
    assert ALIASES['主营业务收入'] != ALIASES['营业收入'] != ALIASES['营业总收入']
    assert ALIASES['合并净利润'] != ALIASES['归母'] != ALIASES['扣非归母']
    assert '净利润' not in ALIASES  # 作品说明：默认归母口径属于显式请求规则。


def test_company_correction_and_time_delta_preserve_other_requirements():
    prior = DialogueState(topic='financial', conditions=Conditions(codes=['600085'], metrics=['operating_revenue'],
        presentation={'unit':'亿元', 'decimals':3, 'format':'table'}, time={'mode':'explicit','years':[2024],'periods':['FY']}))
    q = '不是A，是B'
    r, s = apply_understanding(q, 't1', understanding(q, 'codes', 'replace', ['002082']), prior, COMPANIES)
    assert r.conditions.codes == ['002082']
    assert r.conditions.metrics == ['operating_revenue']
    assert r.conditions.presentation.unit == '亿元'
    q = '改成2023年，其他不变'
    r, s = apply_understanding(q, 't2', understanding(q, 'time', 'replace', {'years':[2023]}), s, COMPANIES)
    assert r.conditions.time.years == [2023]
    assert r.conditions.presentation.decimals == 3
    assert r.conditions.presentation.format == 'table'


def test_delete_and_keep_companies_do_not_expand_the_scope():
    prior=DialogueState(conditions=Conditions(codes=list(COMPANIES),metrics=['operating_revenue']))
    q='去掉A，只留B'
    r,s=apply_understanding(q,'t',understanding(q,'codes','keep',['002082']),prior,COMPANIES)
    assert r.conditions.codes == ['002082']
    assert '600085' in r.conditions.restrictions.excluded_codes
    assert not r.clarification


def test_missing_company_is_a_clarification_not_a_catalog_query():
    q='查2024年营业收入'
    r,s=apply_understanding(q,'t',understanding(q,'metrics','replace',['operating_revenue']),DialogueState(),COMPANIES)
    assert r.clarification
    assert not r.conditions.all_companies


def test_origin_is_the_actual_input_not_a_model_invented_quote():
    u=understanding('not in the question','codes','replace',['600085'])
    from src.agent.v3.state import InvalidUnderstanding
    with pytest.raises(InvalidUnderstanding):apply_understanding('hello','t',u,DialogueState(),COMPANIES)
    u=understanding('同仁堂','codes','replace',['600085'])
    r,s=apply_understanding('查询同仁堂','t',u,DialogueState(),COMPANIES)
    assert r.conditions.origins['codes'].text=='同仁堂'
    assert s.modifications[0].text=='同仁堂'


def test_old_message_numbers_are_not_fact_memory():
    state=read_state([{'role':'assistant','content':'1,234.56万元', 'metadata':{'dialogue_state':{'version':2,'active_request':{'codes':['600085'],'metrics':['net_profit'],'value':1234.56}}}}])
    assert state.conditions.codes == ['600085']
    assert not state.conditions.metrics and not state.recent_facts


def test_latest_policy_and_missing_natural_year_are_distinct():
    reports=[dict(stock_code='600085',year=2024,period='FY'),dict(stock_code='600085',year=2023,period='FY'),dict(stock_code='002082',year=2023,period='FY')]
    each,_=resolve_periods(request(codes=list(COMPANIES),time={'mode':'latest_each'}), reports)
    common,_=resolve_periods(request(codes=list(COMPANIES),time={'mode':'latest_common'}),reports)
    assert each == {'600085':[(2024,'FY')],'002082':[(2023,'FY')]}
    assert common == {'600085':[(2023,'FY')],'002082':[(2023,'FY')]}
    explicit,_=resolve_periods(request(codes=['002082'],time={'mode':'calendar_years','years':[2024]}),reports)
    assert explicit['002082'] == [(2024,'FY')]


def test_cross_company_difference_does_not_require_two_years():
    r=request(codes=list(COMPANIES),metrics=['operating_revenue'],calculation='difference',comparison_axis='companies')
    result=ExecutionResult(facts=[fact('600085',2024,'18597281604.93','operating_revenue'),fact('002082',2024,'1443365200','operating_revenue')])
    calculate(r,result)
    assert Decimal(result.derived[0]['value']) == Decimal('17153916404.93')


def test_negative_and_zero_base_growth_never_produce_fake_numbers():
    r=request(codes=['600085'],metrics=['attributable_net_profit'],calculation='yoy',comparison_axis='years')
    result=ExecutionResult(facts=[fact('600085',2023,'-42890600'),fact('600085',2024,'74611300')])
    calculate(r,result)
    assert Decimal(result.derived[0]['value']).quantize(Decimal('.0001')) == Decimal('273.9572')
    assert '扭亏为盈' in result.derived[0]['detail']
    zero=ExecutionResult(facts=[fact('600085',2023,'0'),fact('600085',2024,'1')])
    calculate(r,zero)
    assert zero.derived[0]['value'] is None and zero.derived[0]['status']=='undefined'


def test_relative_percentage_is_not_percentage_points():
    values=[fact('600085',2023,'47.2923','gross_margin','%'),fact('600085',2024,'43.9643','gross_margin','%')]
    r=request(codes=['600085'],metrics=['gross_margin'],calculation='relative_percent',comparison_axis='years')
    result=ExecutionResult(facts=values);calculate(r,result)
    assert Decimal(result.derived[0]['value']).quantize(Decimal('.0001')) == Decimal('-7.0371')
    r.conditions.calculation='percentage_points';result=ExecutionResult(facts=values);calculate(r,result)
    assert Decimal(result.derived[0]['value']) == Decimal('-3.3280')
    assert result.derived[0]['unit']=='百分点'


def test_decimal_formatting_retains_raw_value_and_per_share_identity():
    assert display_value('18597281604.93','元','亿元',3)==('185.973','亿元')
    assert display_value('1.113','元/股',None,3)==('1.113','元/股')
    with pytest.raises(ValueError): display_value('11.49','%','亿元',2)


def test_no_chart_and_missing_points_are_preserved():
    r=request(codes=['600085'],metrics=['operating_revenue'],restrictions={'no_chart':True})
    r.goals=[Goal(id='g1',kind='chart',text='chart')]
    result=ExecutionResult(selections={'600085':[(2023,'FY'),(2024,'FY')]},facts=[fact('600085',2023,'-100','operating_revenue')])
    build_charts(r,result);assert not result.charts
    r.conditions.restrictions.no_chart=False;r.conditions.presentation.chart_type='bar';build_charts(r,result)
    assert result.charts[0]['series'][0]['values_exact']==['-0.01',None]


def test_ocr_grid_and_original_cells_preserve_number_boundaries():
    assert grid('<table><tr><td rowspan="2">项目</td><td colspan="2">本年</td></tr><tr><td>调整前</td><td>调整后</td></tr></table>')[1]==['项目','调整前','调整后']
    assert original_has_value('营业收入 1,859,728.16 1,786,089.15','1,859,728.16')
    assert not original_has_value('营业收入 11,859,728.16','1,859,728.16')
    assert select_current_column(['项目','2023年1月1日','2022年末'],2023,'FY','total_assets') is None


def test_contract_rejects_float_and_unrecognized_fields():
    with pytest.raises(ValidationError): fact('600085',2024,1.23)
    with pytest.raises(ValidationError): Conditions(unit_guess='万元')
