from copy import deepcopy
from decimal import Decimal
from tests.test_v3_contracts import fact,request
from src.agent.v3.contracts import Goal
from src.agent.v3.executor import execute,resolve_periods,calculate,ExecutionResult,build_charts
from src.agent.v3.rendering import render_financial
from src.agent.v3.verification import verify


class Repository:
    def __init__(self,values):self.values=values;self.queries=[]
    def report_catalog(self,codes):return [dict(stock_code=f.stock_code,year=f.year,period=f.period) for f in self.values]
    def query(self,codes,selections,metrics,scope):
        self.queries.append(deepcopy(selections))
        return [f for f in self.values if f.stock_code in codes and (f.year,f.period) in selections.get(f.stock_code,[]) and f.metric in metrics and f.scope==scope]


def test_yoy_loads_baseline_without_changing_the_requested_period_or_publishing_it_as_current():
    repo=Repository([fact('600085',2023,'-20'),fact('600085',2024,'10')])
    r=request(codes=['600085'],metrics=['attributable_net_profit'],time={'mode':'explicit','years':[2024]},calculation='yoy',comparison_axis='years',presentation={'unit':'%'})
    result=execute(r,repo)
    assert result.selections=={'600085':[(2024,'FY')]}
    assert [f.year for f in result.facts]==[2024] and [f.year for f in result.support_facts]==[2023]
    assert Decimal(result.derived[0]['value'])==150 and '扭亏为盈' in result.derived[0]['detail']
    assert result.goals[0].status=='completed' and len(repo.queries)==2
    assert '同比增长率' in render_financial(r,result) and '150.00 %' in render_financial(r,result)


def test_missing_yoy_base_does_not_turn_amount_or_nonadjacent_year_into_growth():
    repo=Repository([fact('600085',2022,'20'),fact('600085',2024,'10')])
    r=request(codes=['600085'],metrics=['attributable_net_profit'],time={'mode':'explicit','years':[2024]},calculation='yoy',comparison_axis='years')
    result=execute(r,repo)
    assert not result.derived and result.goals[0].status=='partial'
    assert result.missing[0]['reason']=='no_verified_yoy_base' and result.missing[0]['year']==2023


def test_zero_baseline_is_undefined_and_cross_company_relative_change_uses_the_second_company_as_base():
    r=request(codes=['600085','002082'],metrics=['attributable_net_profit'],calculation='relative_percent',comparison_axis='companies')
    result=ExecutionResult(facts=[fact('600085',2024,'150'),fact('002082',2024,'100')]);calculate(r,result)
    assert result.derived[0]['value']=='50.0' and result.derived[0]['formula']=='(first-second)/abs(second)*100'
    zero=ExecutionResult(facts=[fact('600085',2024,'150'),fact('002082',2024,'0')]);calculate(r,zero)
    assert zero.derived[0]['value'] is None and zero.derived[0]['status']=='undefined'


def test_cash_flow_turning_positive_does_not_claim_profit_turnaround():
    r=request(calculation='yoy',comparison_axis='years')
    result=ExecutionResult(facts=[fact('600085',2023,'-100',metric='operating_cash_flow'),fact('600085',2024,'100',metric='operating_cash_flow')])
    calculate(r,result)
    assert '由负转正' in result.derived[0]['detail'] and '扭亏' not in result.derived[0]['detail']


def test_explicit_cross_period_difference_has_a_cumulative_length_warning_and_is_not_yoy():
    a=fact('600085',2023,'100');b=fact('600085',2024,'75',period='HY')
    r=request(calculation='difference',comparison_axis='years',time={'mode':'explicit','pairs':[(2023,'FY'),(2024,'HY')]})
    result=ExecutionResult(facts=[a,b]);calculate(r,result)
    assert result.derived[0]['value']=='-25' and '累计期间长度不同' in result.derived[0]['detail']
    r.conditions.calculation='yoy';other=ExecutionResult(facts=[a,b]);calculate(r,other)
    assert not other.derived


def test_latest_reports_uses_available_reports_and_marks_insufficient_common_coverage():
    repo=Repository([fact('600085',2022,'20'),fact('600085',2024,'40'),fact('002082',2024,'30')])
    r=request(codes=['600085'],metrics=['attributable_net_profit'],time={'mode':'latest_each','span':2})
    assert resolve_periods(r,repo.report_catalog([]))[0]=={'600085':[(2022,'FY'),(2024,'FY')]}
    r.conditions.codes=['600085','002082'];r.conditions.time.mode='latest_common'
    result=execute(r,repo)
    assert result.selections=={'600085':[(2024,'FY')],'002082':[(2024,'FY')]}
    assert all(g.status=='partial' for g in result.goals)
    assert {item['reason'] for item in result.missing}=={'insufficient_report_count'}


def test_calculation_chart_uses_rates_preserves_negative_values_and_rejects_an_invented_point(monkeypatch):
    repo=Repository([fact('600085',2022,'100'),fact('600085',2023,'50'),fact('600085',2024,'75')])
    r=request(codes=['600085'],metrics=['attributable_net_profit'],time={'mode':'explicit','years':[2023,2024]},
        calculation='yoy',comparison_axis='years',presentation={'unit':'%','chart_type':'line'})
    r.goals=[Goal(id='chart',kind='chart',text='增长率趋势')]
    result=execute(r,repo);chart=result.charts[0]
    assert chart['unit']=='%' and chart['series'][0]['values_exact']==['-50.0','50.0']
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda _:True)
    assert verify(r,result,[],[],result.support_facts).status=='pass'
    chart['series'][0]['values'][0]=999
    assert verify(r,result,[],[],result.support_facts).status=='fail'


def test_growth_ranking_sorts_computed_rates_before_truncating():
    repo=Repository([fact('600085',2023,'1000'),fact('600085',2024,'1100'),fact('002082',2023,'10'),fact('002082',2024,'20')])
    r=request(codes=['600085','002082'],metrics=['attributable_net_profit'],time={'mode':'explicit','years':[2024]},
        calculation='yoy',comparison_axis='years',presentation={'unit':'%','order':'desc','limit':1})
    r.goals=[Goal(id='rank',kind='rank',text='增长率最高的一家')]
    result=execute(r,repo)
    assert result.derived[0]['company']=='B' and Decimal(result.derived[0]['value'])==100
    assert len(result.derived)==1 and [f.stock_code for f in result.facts]==['002082']
    assert result.goals[0].status=='completed'
