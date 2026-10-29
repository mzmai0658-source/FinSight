"""验证字面条件、替换时的集合保留及执行失败后的清空行为。"""
import asyncio
import time
import pytest
from jsonschema import Draft202012Validator
from src.agent.v3.contracts import Conditions,DialogueState,Goal,Request,Presentation
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.state import apply_understanding,InvalidUnderstanding
from src.agent.v3.request_bindings import explicit_presentation_bindings,unregistered_company_literals
from src.agent.v3.turn_transport import turn_schema
from src.agent.v3.model import sampling_schema,ModelFailure
from src.agent.v3.agent import V3Agent
from src.agent.v3.planner import SemanticReview,GoalCoverage
from src.agent.v3.executor import ExecutionResult
from src.agent.v3.verification import verify
from src.agent.v3.tasks import TaskStore,TaskManager

COMPANIES={'600085':'同仁堂','002082':'万邦德','600129':'太极集团','002864':'盘龙药业'}

def plan(q,edits,**kw):
    return TurnPlan(goals=[dict(id='g1',kind=kw.pop('kind','lookup'))],
        edits=[dict(field=k,operation=op,value=v,text=q) for k,op,v in edits],**kw).compile(q,COMPANIES)

def prior():
    return DialogueState(topic='financial',conditions=Conditions(codes=['600085','600129'],metrics=['operating_revenue'],
        time={'mode':'explicit','years':[2024]},presentation={'unit':'亿元','decimals':3,'format':'table'}),
        active_goals=[Goal(id='old',kind='lookup',text='old')])

@pytest.mark.parametrize('q,expected',[
    ('最低的前三家',{'presentation.order':'asc','presentation.limit':3}),
    ('按升序取前2名',{'presentation.order':'asc','presentation.limit':2}),
    ('不要柱状图，改用折线图',{'presentation.chart_type':'line'}),
    ('最高和最低都看看',{}),
    ('画散点图',{'presentation.chart_type':'scatter'}),
])
def test_literal_display_values_and_negations(q,expected):
    assert explicit_presentation_bindings(q)==expected

def test_display_constraints_are_checked_after_sparse_conditions_are_bound():
    s=turn_schema(sampling_schema(TurnPlan.model_json_schema()),dict(question='库内公司最低前三家',companies=COMPANIES))
    assert {'all_companies','presentation.order','presentation.limit'}<=set(s['properties']['edits']['properties'])
    from tests.test_v3_generalization_boundaries import review
    request=Request(turn_id='missing-rank',question='库内公司2023年营业收入最低前三家',
        conditions=Conditions(codes=['600085'],metrics=['operating_revenue'],time={'mode':'explicit','years':[2023]}),
        goals=[Goal(id='g',kind='rank',text='库内公司2023年营业收入最低前三家')])
    from tests.request_expectations import mismatches
    assert {"all_companies","order","limit"}<=set(mismatches(request,all_companies=True,order="asc",limit=3))
    # 字面条件不能删去合法任务类别。
    assert len(s['properties']['goals']['items']['properties']['kind']['enum'])==11

def test_replacement_keeps_unmentioned_companies_and_rejects_bad_proposal():
    from tests.request_expectations import mismatches
    q='不是太极集团，是盘龙药业，其余不变。'
    for codes in (['002864'],['600129','002864']):
        request,_=apply_understanding(q,'t',plan(q,[('codes','replace',codes)],continuity='continue'),prior(),COMPANIES)
        assert 'codes' in mismatches(request,codes=['600085','002864'])
    r,s=apply_understanding(q,'t',plan(q,[('codes','remove',['600129']),('codes','add',['002864'])],continuity='continue'),prior(),COMPANIES)
    assert set(r.conditions.codes)=={'600085','002864'}
    assert r.conditions.presentation.unit=='亿元' and r.conditions.presentation.decimals==3
    assert r.conditions.time.years==[2024] and s.conditions==r.conditions

def test_company_clear_does_not_need_model_to_repeat_user_command():
    q='换个话题，不延续刚才公司。它2023年营收是多少？'
    previous=prior();previous.conditions.all_companies=True
    u=plan(q,[('metrics','replace',['operating_revenue']),('time.years','replace',[2023])],
        continuity='new',context_references=[dict(text='它',target='company',number='singular')])
    r,s=apply_understanding(q,'t',u,previous,COMPANIES)
    assert r.clarification and not r.conditions.codes and not s.conditions.codes
    assert not s.conditions.all_companies
    assert not any(c.codes or c.all_companies for c in s.goal_conditions.values())

def test_definition_aside_has_only_current_metrics_and_preserves_financial_context():
    q='先岔开一下，解释毛利额和毛利率，不查数。'
    u=TurnPlan(continuity='continue',goals=[dict(id='g',kind='concept',concept_mode='difference')],
        edits=[dict(field='metrics',operation='add',value=['gross_profit','gross_margin'],text=q)]).compile(q,COMPANIES)
    r,s=apply_understanding(q,'t',u,prior(),COMPANIES)
    assert set(r.conditions.metrics)=={'gross_profit','gross_margin'}
    assert s.conditions.metrics==['operating_revenue'] and s.conditions.codes==prior().conditions.codes

def test_unknown_literal_name_is_not_substituted_and_non_company_prose_is_not_captured():
    q='贵州茅台2024年营业收入是多少？'
    # 名称由模型明确登记；程序不再从年份前的任意中文推测公司。
    assert unregistered_company_literals(q,COMPANIES)==[]
    assert unregistered_company_literals('6005192024年营业收入',COMPANIES)==['600519']
    assert not unregistered_company_literals('营业收入100000元够吗',COMPANIES)
    for text in ('同仁堂和万邦德2024年营业收入','母公司股东2024年净利润','库内公司2024年归母净利润','不要贵州茅台2024年营业收入','查2024年营业收入','再查2024年营业收入','然后查2024年营业收入','同仁堂2023年和2024年营业收入，用亿元画柱状图'):
        assert not unregistered_company_literals(text,COMPANIES)
    r,_=apply_understanding(q,'t',plan(q,[('metrics','replace',['operating_revenue']),('time.years','replace',[2024])],unknown_companies=['贵州茅台']),DialogueState(),COMPANIES)
    assert r.unsupported and not r.conditions.codes and not r.clarification

def test_alternative_collection_name_is_recorded_and_accepted():
    q='库里的公司2024年归母净利润最低前三家'
    u=plan(q,[('all_companies','replace',True),('metrics','replace',['attributable_net_profit']),('time.years','replace',[2024])],kind='rank')
    r,_=apply_understanding(q,'t',u,DialogueState(),COMPANIES)
    assert r.conditions.all_companies and set(r.conditions.codes)==set(COMPANIES) and not r.clarification

def test_wrong_chart_type_fails_both_request_review_and_final_verification():
    from tests.request_expectations import mismatches
    q='同仁堂2024年营业收入画柱状图'
    r=Request(turn_id='t',question=q,conditions=Conditions(codes=['600085'],metrics=['operating_revenue']),goals=[Goal(id='g',kind='chart',text=q)])
    assert 'chart_type' in mismatches(r,chart='bar')
    # 缺图、假完成和图点篡改继续由实际交付核验，原话类型由独立预期判定。
    checked=verify(r,ExecutionResult(),[],[])
    assert checked.status!='pass'

def test_company_deletion_survives_model_failure(tmp_path):
    class FailedAgent:
        companies=COMPANIES
        async def run(self,*args):raise ModelFailure('model_empty_output')
    async def scenario():
        manager=TaskManager(FailedAgent(),TaskStore(tmp_path/'tasks.db'))
        previous=prior();previous.conditions.all_companies=True;previous.suspended=previous.conditions.model_copy(deep=True)
        history=[dict(role='assistant',metadata={'dialogue_state':previous.model_dump(mode='json')})]
        await manager.submit('t','c','s','不延续刚才公司',history,time.time()+270)
        await manager.runners['t']
        result=manager.store.get('t')['result'];state=DialogueState.model_validate(result['dialogue_state'])
        assert not state.conditions.codes and not state.conditions.all_companies
        assert not state.suspended.codes and not state.suspended.all_companies
        await manager.close()
    asyncio.run(scenario())


def test_legal_metric_is_not_silently_replaced_or_claimed_as_semantically_certified():
    q='同仁堂2024年母公司营业收入，用亿元'
    # 目录合法仅说明可执行；自然语言理解错误由独立预期评分，程序不得默改成另一个指标。
    request,_=apply_understanding(q,'t',plan(q,[('codes','replace',['600085']),
            ('metrics','replace',['total_operating_revenue']),('scope','replace','parent'),
            ('time.years','replace',[2024]),('presentation.unit','replace','亿元')]),DialogueState(),COMPANIES)
    assert request.conditions.metrics==['total_operating_revenue']
    from tests.test_v3_generalization_boundaries import review
    assert all('自由语义未由独立模型认证' in c.detail for c in review(request).checks if c.status=='pass')
