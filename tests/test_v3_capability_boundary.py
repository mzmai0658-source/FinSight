from src.agent.v3.contracts import Conditions, DialogueState, Goal, GoalSelection, Understanding
from src.agent.v3.state import apply_understanding
from src.agent.v3.executor import ExecutionResult
from src.agent.v3.verification import verify

COMPANIES={'600085':'同仁堂','002082':'万邦德'}

def proposal(**changes):
    return Understanding(topic='financial',continuity='new',goals=[Goal(id='g1',kind='lookup',text='查数')],
        modifications=[],clarification=[],unsupported=[],unknown_companies=[],**changes)

def test_unknown_company_is_an_unsupported_goal_without_query_or_clarification():
    u=proposal();u.unknown_companies=['贵州茅台']
    r,s=apply_understanding('贵州茅台2024年营业收入','t',u,DialogueState(),COMPANIES)
    assert r.goals[0].kind=='unsupported' and not r.clarification
    assert any('贵州茅台' in item for item in r.unsupported)

def test_single_quarter_selection_cannot_reach_executor_as_a_lookup():
    u=proposal();u.continuity='continue'
    prior=DialogueState(conditions=Conditions(codes=['600085'],metrics=['operating_revenue'],time={'single_quarter':True}))
    r,_=apply_understanding('第二季度单季','t',u,prior,COMPANIES)
    assert r.goals[0].kind=='unsupported' and r.unsupported and not r.clarification

def test_unsupported_goal_does_not_block_independent_supported_company_query():
    u=proposal();u.continuity='continue'
    u.goals=[Goal(id='fy',kind='lookup',text='查年报'),Goal(id='quarter',kind='lookup',text='查单季',
        selection=GoalSelection(time={'single_quarter':True}))]
    prior=DialogueState(conditions=Conditions(codes=['600085'],metrics=['operating_revenue']))
    r,_=apply_understanding('同仁堂年报和第二季度单季','t',u,prior,COMPANIES)
    assert [g.kind for g in r.goals]==['lookup','unsupported']
    assert not r.clarification

def test_generic_company_phrase_cannot_select_previous_companies_in_a_new_task():
    u=proposal();u.continuity='continue'
    prior=DialogueState(conditions=Conditions(codes=list(COMPANIES),metrics=['operating_revenue']))
    # 作品说明：新查询没有可授权沿用的公司上下文。
    u.continuity='new'
    from src.agent.v3.contracts import MODIFICATION
    u.modifications=[MODIFICATION.validate_python({'field':'codes','operation':'replace','value':list(COMPANIES)}),
        MODIFICATION.validate_python({'field':'metrics','operation':'replace','value':['operating_revenue']})]
    import pytest
    from src.agent.v3.state import InvalidUnderstanding
    with pytest.raises(InvalidUnderstanding,match='new task cannot select a company'):
        apply_understanding('医药公司2024年营业收入','t',u,prior,COMPANIES)
    # 作品说明：合法的缺公司提案保持用户歧义，不能把模型擅选的无关历史公司当作建议。
    u.modifications[0]=MODIFICATION.validate_python({'field':'codes','operation':'replace','value':[]})
    r,_=apply_understanding('医药公司2024年营业收入','t',u,prior,COMPANIES)
    assert r.clarification and not r.conditions.codes and not r.conditions.all_companies

def test_unsupported_notice_cannot_receive_full_verification_even_without_goal_result():
    from tests.test_v3_contracts import request
    r=request();r.unsupported=['不能修改数据库。']
    v=verify(r,ExecutionResult(),[],[])
    assert v.status=='partial'
    assert any(c.name=='supported_components' and c.status=='unknown' for c in v.request)

def test_an_unknown_financial_query_clears_old_fact_memory():
    from src.agent.v3.contracts import FactReference
    prior=DialogueState(recent_facts=[FactReference(id='old',data_version='v')])
    u=proposal();u.unknown_companies=['贵州茅台'];u.goals[0].kind='unsupported'
    _,s=apply_understanding('贵州茅台营收','t',u,prior,COMPANIES)
    assert s.recent_facts==[]

def test_explicit_null_clears_only_optional_presentation_fields():
    from src.agent.v3.contracts import MODIFICATION
    prior=DialogueState(conditions=Conditions(codes=['600085'],metrics=['operating_revenue'],presentation={'unit':'亿元','decimals':3,'format':'table'}))
    u=proposal();u.continuity='continue'
    u.modifications=[MODIFICATION.validate_python({'field':'presentation','operation':'replace','value':{'unit':None}})]
    r,_=apply_understanding('取消金额单位要求，其他不变','t',u,prior,COMPANIES)
    assert r.conditions.presentation.unit is None
    assert r.conditions.presentation.decimals==3 and r.conditions.presentation.format=='table'

def test_returning_to_cumulative_period_clears_single_quarter_identity():
    from src.agent.v3.contracts import MODIFICATION
    prior=DialogueState(conditions=Conditions(codes=['600085'],metrics=['operating_revenue'],time={'single_quarter':True,'quarters':[2]}))
    u=proposal();u.continuity='continue'
    u.modifications=[MODIFICATION.validate_python({'field':'time','operation':'replace','value':{'single_quarter':False,'periods':['HY']}})]
    r,_=apply_understanding('改为上半年累计','t',u,prior,COMPANIES)
    assert not r.conditions.time.single_quarter and r.conditions.time.quarters==[]
    assert r.goals[0].kind=='lookup' and not r.unsupported

def test_grounded_uncovered_company_is_capability_failure_even_if_name_was_in_clarification():
    from src.agent.v3.contracts import CompanyMention
    u=proposal();u.company_mentions=[CompanyMention(text='贵州茅台',kind='uncovered',codes=[])]
    u.clarification=['贵州茅台']
    r,_=apply_understanding('贵州茅台2024年营业收入','t',u,DialogueState(),COMPANIES)
    assert not r.clarification and r.unsupported and r.goals[0].kind=='unsupported'

def test_single_goal_selection_becomes_confirmed_followup_conditions():
    u=proposal();u.goals[0].selection=GoalSelection(codes=['600085'],metrics=['operating_revenue'],scope='parent',
        time={'mode':'explicit','years':[2024]},presentation={'unit':'亿元','decimals':3,'format':'table'})
    r,s=apply_understanding('同仁堂2024年母公司营业收入，亿元三位，只要表格','t',u,DialogueState(),COMPANIES)
    assert r.goals[0].selection is None
    assert r.conditions.codes==s.conditions.codes==['600085']
    assert s.conditions.scope=='parent' and s.conditions.presentation.unit=='亿元'
    assert s.conditions.time.years==[2024] and s.modifications

def test_promoted_selection_gets_the_same_unit_validation_as_shared_conditions():
    u=proposal();u.goals[0].selection=GoalSelection(codes=['600085'],metrics=['gross_margin'],presentation={'unit':'亿元'})
    r,_=apply_understanding('同仁堂毛利率用亿元','t',u,DialogueState(),COMPANIES)
    assert r.clarification
