"""作品说明：验证类型化模型决策与服务边界，独立于关键词路由。"""
import pytest
from src.agent.semantic_planner_legacy import TurnDecision, validate_decision
from src.agent.semantic_planner import conversation_state, resolve_request, FinancialRequest, CatalogRequest, TimeRequest, Origin
from src.agent.financial_query import FinancialQueryService
from src.agent.query_plan import QueryPlan
from src.agent.domain import CODE_TO_NAME_MAP, COMPANY_CODE_MAP

@pytest.fixture(autouse=True)
def registry(monkeypatch):
    for code,name in [('002082','万邦德'),('600085','同仁堂')]:
        monkeypatch.setitem(CODE_TO_NAME_MAP,code,name)
        monkeypatch.setitem(COMPANY_CODE_MAP,name,code)


def history():
    return [{'role':'assistant','content':'错误旧答案声称2025年有年报', 'metadata':{
        'response_kind':'financial','dialogue_state':{'version':1,
        'active_request':{'codes':['002082'],'metrics':['total_operating_revenue'],
                          'pairs':[[2023,'HY'],[2024,'HY']],'period':'HY','unit':'亿元'},
        'chart_eligibility':[{'status':'unavailable','reason':'insufficient_points'}]}}}]


@pytest.mark.parametrize('question', ['你需要满足什么条件才能画图','不是，我是在问你问题，你生成图表干嘛',
    '能画什么图，先别操作','为什么要画图','这是什么意思','别查了说说ROE'])
def test_model_help_decision_cannot_execute_financial_tools(question):
    decision=TurnDecision(intent='help',reply='趋势图需要至少两个同口径有效数据点。',chart=True,followup=True,inherit_fields=['time'])
    plan=validate_decision(question,decision,history())
    class Planner:
        def plan_turn(self,*_): return plan
    class ForbiddenRepository:
        def execute(self,*_): raise AssertionError('Help must not query SQL')
    result=list(FinancialQueryService(Planner(),ForbiddenRepository()).run(question,history()))[-1][1]['result']
    assert result['outcome']['status']=='answered'
    assert result['response_kind']=='conversation'
    assert not result['chart_data_list'] and not result['facts']
    assert result['dialogue_state']['active_request']['pairs']==[(2023,'HY'),(2024,'HY')]


def test_new_years_do_not_come_from_assistant_prose():
    d=TurnDecision(intent='facts',companies=['600085'],company_text='同仁堂',main_metrics=True,metric_text='指标',time_mode='latest',time_text='最近三年')
    p=validate_decision('同仁堂最近三年指标',d,history())
    assert p.codes==['600085'] and p.latest_count==3 and p.pairs==[]


def test_only_explicit_inheritance_is_used():
    d=TurnDecision(intent='facts',metrics=['net_profit'],metric_text='净利润',followup=True,inherit_fields=['companies','time','period','unit'])
    p=validate_decision('那净利润呢',d,history())
    assert p.codes==['002082'] and p.pairs==[(2023,'HY'),(2024,'HY')]
    assert p.unit=='亿元' and not p.chart
    d.followup=False
    assert validate_decision('那净利润呢',d,history()).intent=='clarify'


def test_correction_can_include_new_data_request():
    d=TurnDecision(intent='facts',correction=True,followup=True,time_mode='explicit',time_text='2024年',reports=[{'year':2024,'period':'FY'}],period='FY',inherit_fields=['companies','metrics'])
    p=validate_decision('不对，改成2024年年报',d,history())
    assert p.correction and p.intent=='facts' and p.pairs==[(2024,'FY')]


def test_old_unstructured_history_does_not_supply_scope():
    d=TurnDecision(intent='facts',followup=True,inherit_fields=['companies','time','metrics'])
    p=validate_decision('那它呢',d,[{'role':'assistant','content':'万邦德2024年收入1万元'}])
    assert p.intent=='clarify' and p.reason=='previous_scope_unavailable'


def test_company_must_match_named_registry_identity():
    d=TurnDecision(intent='facts',companies=['600085'],company_text='万邦德')
    p=validate_decision('万邦德24年收入',d,[])
    assert p.intent=='clarify' and p.reason=='company_identity_unconfirmed'


def test_out_of_question_year_anchor_is_rejected():
    d=TurnDecision(intent='facts',companies=['002082'],company_text='万邦德',metrics=['net_profit'],metric_text='净利润',time_mode='explicit',time_text='2024年',reports=[{'year':2025}])
    p=validate_decision('万邦德2024年净利润',d,[])
    assert p.intent=='clarify' and p.reason=='year_mismatch'


def test_invalid_persisted_state_is_not_inherited():
    h=history(); h[0]['metadata']['dialogue_state']['active_request']['pairs']=[['oops','BAD']]
    assert conversation_state(h)=={}


def test_model_failure_never_falls_back_to_keyword_rules():
    class Broken:
        def plan_turn(self,*_): raise TimeoutError()
    result=list(FinancialQueryService(Broken(),None).run('万邦德24年营收画图'))[-1][1]['result']
    assert result['outcome']['status']=='query_failed'
    assert 'intent_service_failed' in result['outcome']['reason_codes']
    assert not result['facts']


def test_pending_clarification_preserves_partial_validated_scope():
    class Planner:
        def plan_turn(self,*_): return QueryPlan(question='万邦德的', intent='clarify', codes=['002082'], reason='metric_required')
    result=list(FinancialQueryService(Planner(),None).run('万邦德的'))[-1][1]['result']
    h=[{'role':'assistant','metadata':{'dialogue_state':result['dialogue_state']}}]
    d=TurnDecision(intent='facts',followup=True,inherit_fields=['companies'],metrics=['total_operating_revenue'],metric_text='营收',time_mode='explicit',time_text='24年',reports=[{'year':2024}])
    p=resolve_request('24年营收',FinancialRequest(kind='financial', goal='查收入', standalone_question='万邦德2024年营收',
        relation='followup',company_scope='inherit',metrics=['total_operating_revenue'],
        time=TimeRequest(mode='explicit',years=[2024]),origins={'metrics':Origin(kind='current',text='营收')}),h)
    assert p.codes==['002082'] and p.intent=='facts'


def test_latest_followup_rediscovers_new_company_reports():
    h=history(); s=h[0]['metadata']['dialogue_state']['active_request']
    s.update(time_mode='latest',latest_count=3)
    d=TurnDecision(intent='facts',companies=['600085'],followup=True,inherit_fields=['time','period','metrics'])
    p=validate_decision('换同仁堂',d,h)
    assert p.codes==['600085'] and p.latest_count==3 and p.pairs==[]


def test_independent_known_metric_is_not_lost_to_another_ambiguous_metric():
    d=TurnDecision(intent='facts',companies=['002082'],metrics=['total_operating_revenue'],time_mode='explicit',reports=[{'year':2024}],clarification='现金流希望查看哪一类？')
    p=validate_decision('万邦德24年收入和现金流多少',d,[])
    assert p.intent=='facts' and p.metrics==['total_operating_revenue'] and p.clarification


def test_coverage_queries_preserve_company_for_period_followup():
    from tests.test_structured_financial_query import Repository, row
    class Planner:
        def plan_turn(self,*_): return QueryPlan('万邦德有哪些年报',intent='coverage_periods',codes=['002082'],coverage_period_filter='FY')
    result=list(FinancialQueryService(Planner(),Repository([row(2024)])).run('万邦德有哪些年报'))[-1][1]['result']
    h=[{'role':'assistant','metadata':{'dialogue_state':result['dialogue_state']}}]
    p=resolve_request('那半年报呢',CatalogRequest(kind='catalog',dimension='reports',goal='半年报范围',
        standalone_question='万邦德有哪些半年报',relation='followup',time=TimeRequest(mode='explicit',years=[2024],period='HY')),h)
    assert p.codes==['002082'] and p.coverage_period_filter=='HY'
