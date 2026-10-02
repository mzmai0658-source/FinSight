import asyncio,time
from decimal import Decimal
import pytest
from src.agent.v3.agent import V3Agent
from src.agent.v3.contracts import Understanding,Fact,MODIFICATION,Goal,Check,GoalSelection
from src.agent.v3.planner import SemanticReview
from src.agent.v3.proposals import IntentPlan,ConditionPlan,TurnPlan
from src.agent.v3.executor import ExecutionResult,build_charts,execute
from src.agent.v3.rendering import chart_compat
from src.agent.v3.model import Budget
from tests.test_v3_contracts import request,fact

class Repository:
    version='v';collection='accepted-index'
    def report_catalog(self,codes):return [dict(stock_code=c,year=2024,period='FY') for c in codes]
    def query(self,codes,selections,metrics,scope):
        return [Fact(id='value',data_version='v',stock_code='600085',company='同仁堂',year=2024,period='FY',metric='operating_revenue',scope='parent',value='4896408337.09',unit='元',status='verified')]
    def by_ids(self,ids):return []

class Model:
    async def structured(self,schema,system,payload,budget):
        budget.charge()
        assert schema is TurnPlan
        return TurnPlan(goals=[dict(id='lookup',kind='lookup',text=payload['question'])],
            edits=[dict(field=field,operation='replace',value=value,text=payload['question'])
            for field,value in [('codes',['600085']),('metrics',['operating_revenue']),('scope','parent'),
                ('time.mode','latest_each'),('presentation.unit','亿元'),('presentation.decimals',3),('presentation.format','table')]])



def test_financial_body_is_published_only_after_the_verification_gate(monkeypatch):
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda f:True)
    events=[]
    async def emit(kind,payload):events.append((kind,payload))
    agent=V3Agent(None,{'600085':'同仁堂'},model=Model(),repository_factory=lambda _:Repository())
    result=asyncio.run(agent.run('同仁堂母公司营收，亿元三位，只要表格',[],'t',time.time()+270,emit))
    assert result['outcome']['status']=='answered'
    assert result['verification_v3']['status']=='pass'
    assert '48.964' in result['answer']['content'] and '母公司' in result['answer']['content']
    assert result['facts'][0]['value_exact']=='4896408337.09'
    assert not any(kind in {'answer_delta','chart'} for kind,_ in events)
    assert result['chart_data_list']==[]
    assert result['diagnostics']['model_calls']==1


def test_program_audit_checkpoint_is_accepted_by_durable_task_store(monkeypatch,tmp_path):
    from src.agent.v3.tasks import TaskStore
    store=TaskStore(tmp_path/'turns.sqlite3')
    from inspect import signature
    allowed=set(signature(store.checkpoint).parameters)-{'id'}
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda f:True)
    checkpoints=[]
    async def emit(kind,payload):
        if '_request_checkpoint' in payload:
            checkpoint=payload['_request_checkpoint']
            assert set(checkpoint)<=allowed
            checkpoints.append(checkpoint)
    result=asyncio.run(V3Agent(None,{'600085':'同仁堂'},model=Model(),repository_factory=lambda _:Repository()).run(
        '同仁堂母公司营收，亿元三位，只要表格',[],'t',time.time()+270,emit))
    assert any(c['approved'] for c in checkpoints)
    assert result['diagnostics']['request_audit_mode']=='program'

def test_an_unverified_source_cannot_release_the_financial_body():
    async def emit(*args):pass
    agent=V3Agent(None,{'600085':'同仁堂'},model=Model(),repository_factory=lambda _:Repository())
    result=asyncio.run(agent.run('同仁堂母公司营收',[],'t',time.time()+270,emit))
    assert result['verification_v3']['status']=='fail'
    assert result['facts']==[] and result['outcome']['status']!='answered'
    assert '48.964' not in result['answer']['content']
    assert result['dialogue_state']['recent_facts']==[]


def test_graph_failure_retains_bounded_contract_diagnostics_without_driver_text():
    from src.agent.v3.model import ModelFailure
    class ExhaustedModel:
        async def structured(self,schema,system,payload,budget):
            budget.calls=6
            budget.timings.append({'step':'IntentPlan','seconds':1})
            budget.artifacts['structured_trace']=[{'call':1,'step':'IntentPlan','proposal':{'goals':[]}}]
            raise ModelFailure('model_call_budget_exhausted')
    async def emit(*args):pass
    agent=V3Agent(None,{},model=ExhaustedModel())
    with pytest.raises(ModelFailure) as captured:
        asyncio.run(agent.run('查询',[],'failed',time.time()+270,emit))
    diagnostics=captured.value.diagnostics
    assert diagnostics['model_calls']==6 and diagnostics['structured_trace'][0]['call']==1
    assert set(diagnostics)=={'model_calls','timings','structured_trace','correction_feedback'}

def test_invalid_ranking_shape_is_not_marked_complete():
    r=request(codes=['600085'],metrics=['operating_revenue','net_profit'],scope='parent')
    r.goals=[Goal(id='rank',kind='rank',text='rank')]
    result=execute(r,Repository())
    assert result.goals[0].status!='completed'

def test_scatter_pairs_same_company_period_and_preserves_exact_values():
    r=request(codes=['600085'],metrics=['operating_revenue','attributable_net_profit'],presentation={'chart_type':'scatter','unit':'万元'})
    r.goals=[Goal(id='chart',kind='chart',text='scatter')]
    result=ExecutionResult(selections={'600085':[(2024,'FY')]},facts=[fact('600085',2024,'1234567.8901',metric='operating_revenue'),fact('600085',2024,'-98765.4321')])
    build_charts(r,result)
    assert len(result.charts)==1
    option=chart_compat(result.charts[0])['option']
    point=option['series'][0]['data'][0]
    assert point['value_exact']==['123.45678901','-9.87654321']
    assert option['xAxis']['type']=='value'

def test_one_verified_point_and_missing_year_do_not_fabricate_a_trend():
    r=request(codes=['600085'],metrics=['operating_revenue'])
    r.goals=[Goal(id='chart',kind='chart',text='trend')]
    result=ExecutionResult(selections={'600085':[(2023,'FY'),(2024,'FY')]},facts=[fact('600085',2023,'10',metric='operating_revenue')])
    build_charts(r,result)
    assert result.charts==[]


def test_independent_company_metric_goals_never_form_a_cartesian_product():
    calls=[]
    class IndependentRepository(Repository):
        def query(self,codes,selections,metrics,scope):
            calls.append((codes,metrics,scope))
            return [fact(codes[0],2024,'100',metric=metrics[0])]
    r=request(codes=['600085','002082'],metrics=['operating_revenue','attributable_net_profit'])
    r.goals=[Goal(id='income',kind='lookup',text='同仁堂营收',selection=GoalSelection(codes=['600085'],metrics=['operating_revenue'])),
        Goal(id='profit',kind='lookup',text='万邦德归母',selection=GoalSelection(codes=['002082'],metrics=['attributable_net_profit']))]
    result=execute(r,IndependentRepository())
    assert calls==[(['600085'],['operating_revenue'],'consolidated'),(['002082'],['attributable_net_profit'],'consolidated')]
    assert len(result.facts)==2 and all(g.status=='completed' for g in result.goals)
    assert result.for_goal('income').facts[0].metric=='operating_revenue'


def test_safe_lookup_is_executed_while_write_request_is_rejected(monkeypatch):
    class MixedModel(Model):
        async def structured(self,schema,system,payload,budget):
            value=await super().structured(schema,system,payload,budget)
            if isinstance(value,IntentPlan):
                value=TurnPlan.model_validate({**value.model_dump(),
                    'goals':[*[g.model_dump() for g in value.goals],dict(id='write',kind='unsupported',text='修改财务数据库')]})
            return value
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda f:True)
    async def emit(*args): pass
    result=asyncio.run(V3Agent(None,{'600085':'同仁堂'},model=MixedModel(),repository_factory=lambda _:Repository())
        .run('查同仁堂收入并修改数据库',[],'t',time.time()+270,emit))
    assert result['facts'] and '48.964' in result['answer']['content']
    assert result['outcome']['status']=='partial'
    assert {g['status'] for g in result['task_results']}=={'completed','unsupported'}


def test_table_only_preserves_undefined_growth_notice_inside_table():
    from src.agent.v3.rendering import render_financial
    r=request(presentation={'format':'table'},calculation='yoy',comparison_axis='years')
    result=ExecutionResult(derived=[dict(value=None,detail='零基期，增长率未定义')],notes=['按请求保留缺失项。'])
    body=render_financial(r,result)
    assert '零基期' in body and all(line.startswith('|') for line in body.splitlines() if line)


def test_concept_prose_has_no_financial_values_and_formula_is_program_generated():
    from src.agent.v3.evidence import Explanations,EvidenceReview
    class ConceptModel:
        async def structured(self,schema,system,payload,budget):
            assert payload['verified_facts']==[]
            assert 'gross_margin' in payload['concept_definitions']['concept']
            if schema is Explanations:
                return Explanations(claims=[dict(goal_id='concept',text='毛利率反映收入扣除营业成本后所剩的比例。',evidence_ids=[])],incomplete_goals=[])
            assert schema is EvidenceReview
            return EvidenceReview(judgments=[dict(goal_id='concept',claim_index=0,supported=True,detail='定义一致')])
    async def emit(*args):pass
    r=request(metrics=['gross_margin']);r.goals=[Goal(id='concept',kind='concept',text='解释毛利率')]
    state=dict(request=r,executed=ExecutionResult(facts=[fact('600085',2024,'999')]),previous=None,
        repository=None,budget=None,emit=emit)
    result=asyncio.run(V3Agent(None,{},model=ConceptModel())._evidence(state))
    assert result['executed'].goals[0].status=='completed'
    assert any('× 100' in line for line in result['prose'])
    assert not any('999' in line for line in result['prose'])


def test_concept_cannot_cite_a_financial_fact_even_when_model_audit_approves():
    from src.agent.v3.evidence import Explanations,EvidenceReview
    class ConceptModel:
        async def structured(self,schema,system,payload,budget):
            if schema is Explanations:
                return Explanations(claims=[dict(goal_id='concept',text='这是收入指标。',evidence_ids=['financial-fact'])],incomplete_goals=[])
            return EvidenceReview(judgments=[dict(goal_id='concept',claim_index=0,supported=True,detail='模型自报通过')])
    async def emit(*args):pass
    r=request(metrics=['operating_revenue']);r.goals=[Goal(id='concept',kind='concept',text='解释营收')]
    result=asyncio.run(V3Agent(None,{},model=ConceptModel())._evidence(dict(request=r,executed=ExecutionResult(),
        previous=None,repository=None,budget=None,emit=emit)))
    assert result['executed'].goals[0].status=='no_data'
    assert '这是收入指标。' not in result['prose']


def test_registered_definition_is_delivered_without_financial_query_or_numeric_prose_model():
    class NoModel:
        async def structured(self,*args):raise AssertionError('Definition must come from the registered catalog')
    async def emit(*args):pass
    r=request(metrics=['net_margin','gross_margin']);r.goals=[Goal(id='concept',kind='concept',text='区别',concept_mode='difference')]
    result=asyncio.run(V3Agent(None,{},model=NoModel())._evidence(dict(request=r,executed=ExecutionResult(),
        previous=None,repository=None,budget=None,emit=emit)))
    assert result['executed'].goals[0].status=='completed'
    assert result['prose'][0]=='它们是不同指标，定义如下：'
    assert any('净利润合计' in line for line in result['prose']) and any('毛利额' in line for line in result['prose'])


def test_same_company_time_goals_remember_both_metrics_but_different_pairs_stay_bound():
    from src.agent.v3.state import apply_understanding
    from src.agent.v3.contracts import DialogueState
    from tests.test_v3_contracts import understanding
    q='同仁堂2024年净利率毛利率各是多少'
    u=understanding(q,'codes','replace',['600085'])
    u.goals=[Goal(id='net',kind='lookup',text='净利率',selection=GoalSelection(metrics=['net_margin'])),
        Goal(id='gross',kind='lookup',text='毛利率',selection=GoalSelection(metrics=['gross_margin']))]
    _,state=apply_understanding(q,'t',u,DialogueState(),{'600085':'同仁堂'})
    assert state.conditions.metrics==['net_margin','gross_margin'] and len(state.active_goals)==2
    q='同仁堂营业收入，万邦德归母净利润'
    u=understanding(q,'codes','replace',['600085','002082'])
    u.goals=[Goal(id='income',kind='lookup',text='营收',selection=GoalSelection(codes=['600085'],metrics=['operating_revenue'])),
        Goal(id='profit',kind='lookup',text='归母',selection=GoalSelection(codes=['002082'],metrics=['attributable_net_profit']))]
    _,state=apply_understanding(q,'t2',u,DialogueState(),{'600085':'同仁堂','002082':'万邦德'})
    assert state.conditions.metrics==[]
    assert [(goal.selection.codes,goal.selection.metrics) for goal in state.active_goals]==[(['600085'],['operating_revenue']),(['002082'],['attributable_net_profit'])]


def test_rule_aside_publication_does_not_erase_pending_financial_conditions():
    from src.agent.v3.contracts import DialogueState,Verification
    from tests.test_v3_contracts import understanding
    from src.agent.v3.state import apply_understanding
    q='万邦德最近怎么样'
    _,previous=apply_understanding(q,'first',understanding(q,'codes','replace',['002082']),DialogueState(),{'002082':'万邦德'})
    assert previous.pending.conditions.codes==['002082']
    u=Understanding(topic='rules',continuity='continue',goals=[Goal(id='rules',kind='rules',text='默认年份')],modifications=[],clarification=[],unsupported=[],unknown_companies=[])
    r,dialogue=apply_understanding('为什么默认这个年份','aside',u,previous,{'002082':'万邦德'})
    state=dict(request=r,dialogue=dialogue,previous=previous,understanding=u,repository=None,references=[],
        executed=ExecutionResult(),prose=['默认选最新年报。'],
        verification=Verification(numeric=[],request=[],evidence=[],status='pass'),budget=Budget(time.time()+100),
        semantic=SemanticReview(goal_requirements=[dict(kind='rules',goal_ids=['rules'],covered=True)],satisfied=True,clarification=[],planner_defects=[],companies_correct=True,metrics_and_scope_correct=True,time_correct=True,goals_correct=True,constraints_correct=True,presentation_correct=True))
    result=asyncio.run(V3Agent(None,{},model=Model())._publish(state))['result']
    assert result['dialogue_state']['pending']['conditions']['codes']==['002082']


def test_known_definition_is_delivered_while_only_the_financial_goal_waits_for_company():
    class PartialModel(Model):
        async def structured(self,schema,system,payload,budget):
            budget.charge()
            assert schema is TurnPlan
            return TurnPlan(goals=[dict(id='definition',kind='concept',text='解释ROE',concept_mode='definition'),
                    dict(id='lookup',kind='lookup',text='2024年营收')],
                edits=[dict(field='metrics',operation='replace',value=['operating_revenue'],text='营收'),
                    dict(field='time.years',operation='replace',value=[2024],text='2024年')],
                assignments=[dict(id='definition',edits=[dict(field='metrics',operation='replace',value=['roe_weighted'],text='ROE')])])


    async def emit(*args):pass
    def no_repository(*args,**kwargs):raise AssertionError('A missing-company lookup cannot run')
    result=asyncio.run(V3Agent(None,{'600085':'同仁堂'},model=PartialModel(),repository_factory=no_repository)
        .run('解释ROE，再查2024年的营收',[],'partial',time.time()+270,emit))
    assert result['outcome']['status']=='partial' and result['verification_v3']['status']=='partial'
    statuses={goal['id']:goal['status'] for goal in result['task_results']}
    assert statuses=={'definition':'completed','lookup':'no_data'}
    assert '加权平均净资产收益率' in result['answer']['content'] and '哪家公司' in result['answer']['content']
    assert [g['id'] for g in result['dialogue_state']['pending']['goals']]==['lookup']


def test_comparison_conclusion_uses_decimal_and_cannot_compare_different_periods():
    from src.agent.v3.rendering import render_financial
    class CompareRepository:
        def report_catalog(self,codes):return [dict(stock_code=c,year=2024,period='FY') for c in codes]
        def query(self,codes,selections,metrics,scope):
            return [fact('600085',2024,'100000000000.0001'),fact('002082',2024,'100000000000.0000')]
    r=request(codes=['600085','002082'],metrics=['attributable_net_profit'],comparison_axis='companies')
    r.goals=[Goal(id='compare',kind='compare',text='谁更高')]
    result=execute(r,CompareRepository())
    assert result.comparisons[0]['relation']=='greater'
    assert '高于' in render_financial(r,result) and result.goals[0].status=='completed'
    from src.agent.v3.executor import compare_values
    missing=ExecutionResult(facts=[fact('600085',2023,'10'),fact('002082',2024,'20')])
    compare_values(r,missing)
    assert not missing.comparisons


def test_invalid_prior_source_cannot_be_explained_as_a_verified_fact(monkeypatch):
    from src.agent.v3.contracts import DialogueState,FactReference,Understanding
    class OldRepository(Repository):
        def by_ids(self,ids):return [fact('600085',2024,'100')]
    class NoModel:
        async def structured(self,*args):raise AssertionError('An invalid fact reference must stop the implication goal')
    monkeypatch.setattr('src.agent.v3.evidence.checked_source',lambda _:False)
    async def emit(*args):pass
    r=request();r.goals=[Goal(id='meaning',kind='concept',text='这个数说明什么',concept_mode='implication')]
    previous=DialogueState(recent_facts=[FactReference(id='old',data_version='v')])
    state=dict(request=r,previous=previous,understanding=Understanding(topic='concept',continuity='continue',
        goals=r.goals,modifications=[],clarification=[],unsupported=[],unknown_companies=[]),emit=emit,budget=Budget(time.time()+60))
    agent=V3Agent(None,{},model=NoModel(),repository_factory=lambda *a,**kw:OldRepository())
    async def scenario():
        executed=await agent._execute(state)
        assert executed['context_reference_failed'] and executed['context_facts']==[]
        result=await agent._evidence({**state,**executed})
        assert result['executed'].goals[0].status=='no_data'
        assert '未通过来源核验' in result['prose'][0]
    asyncio.run(scenario())
