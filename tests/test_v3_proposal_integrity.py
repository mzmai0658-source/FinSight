"""验证正确提案不被关键词改写，并隔离原文、临时条件和能力引导。"""
import asyncio,copy,time
import pytest
from jsonschema import Draft202012Validator,ValidationError
from src.agent.v3.contracts import Conditions,DialogueState,MODIFICATION
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.explicit_conditions import bind_explicit
from src.agent.v3.state import apply_understanding
from src.agent.v3.agent import V3Agent
from src.agent.v3.condition_lifetimes import persist_conditions

COMPANIES={'600085':'同仁堂','002082':'万邦德'}
def edit(field,value,operation='replace',**kw):return dict(field=field,operation=operation,value=value,text='',**kw)
def plan(question,metric='operating_revenue',scope='consolidated',kind='lookup',**kw):
    return TurnPlan(goals=[dict(id='g1',kind=kind,source_ref=question,**kw)],edits=[edit('codes',['600085']),edit('metrics',[metric]),edit('scope',scope),edit('time.years',[2023])])
def compile_plan(question,p,previous=None):
    p=p.model_copy(deep=True)
    for e in [*p.edits,*(e for a in p.assignments for e in a.edits)]:e.text=question
    bound,_=bind_explicit(p,question,COMPANIES)
    return apply_understanding(question,'turn',bound.compile(question,COMPANIES),previous or DialogueState(),COMPANIES)

@pytest.mark.parametrize('company',list(COMPANIES.values()))
@pytest.mark.parametrize('description,metric,scope',[
    ('扣除非经常性损益后的归母净利','deducted_attributable_net_profit','consolidated'),
    ('扣非后的归属于母公司股东的净利润','deducted_attributable_net_profit','consolidated'),
    ('只看母公司单体的净利润','net_profit','parent'),
    ('合并净利润','net_profit','consolidated'),
])
def test_complete_metric_is_not_overwritten_by_short_alias(company,description,metric,scope):
    q=f'{company}2023年{description}是多少？'
    p=plan(q,metric,scope);original=p.model_dump()
    bound,_=bind_explicit(p,q,COMPANIES)
    assert p.model_dump()==original
    assert next(e.value for e in bound.edits if e.field=='metrics')==[metric]
    assert next(e.value for e in bound.edits if e.field=='scope')==scope

def test_concept_pair_and_distinct_goal_assignments_remain_independent():
    q='只解释合并净利和归母净利的区别，不查数'
    p=TurnPlan(goals=[dict(id='g1',kind='concept',concept_mode='difference')],edits=[edit('metrics',['net_profit','attributable_net_profit'])])
    bound,_=bind_explicit(p,q,COMPANIES)
    assert bound.edits[0].value==['net_profit','attributable_net_profit']
    q='查同仁堂2023年营收，再解释万邦德2022年归母净利润变化原因'
    p=TurnPlan(goals=[dict(id='a',kind='lookup'),dict(id='b',kind='cause')],assignments=[
        dict(id='a',edits=[edit('codes',['600085']),edit('metrics',['operating_revenue']),edit('time.years',[2023])]),
        dict(id='b',edits=[edit('codes',['002082']),edit('metrics',['attributable_net_profit']),edit('time.years',[2022])])])
    bound,_=bind_explicit(p,q,COMPANIES)
    assert bound.model_dump()==p.model_dump()

@pytest.mark.parametrize('wording',['前六个月累计','头六个月累计','上半年累计'])
def test_positive_half_year_proposal_does_not_require_one_exact_alias(wording):
    q='同仁堂2023年'+wording+'营业收入是多少？';p=plan(q)
    p.edits.append(MODIFICATION.validate_python(edit('time.periods',['HY'])))
    request,_=compile_plan(q,p)
    assert request.conditions.time.periods==['HY'] and not request.clarification

@pytest.mark.parametrize('columns',['比较列','去年那一列','上期的比较数'])
def test_original_row_comparison_columns_do_not_create_query_tasks(columns):
    q=f'逐字摘录同仁堂2023年营业收入原表完整行，包括{columns}和物理页码'
    p=plan(q,kind='quote',quote_mode='literal',quote_granularity='row')
    request,_=compile_plan(q,p)
    from src.agent.v3.planner import SemanticReview
    review=SemanticReview(audit_mode='program',goal_requirements=[dict(kind='quote',goal_ids=['g1'],representation='represented')],satisfied=True,clarification=[],planner_defects=[],companies_correct=True,metrics_and_scope_correct=True,time_correct=True,goals_correct=True,constraints_correct=True,presentation_correct=True)
    checked=V3Agent(None,COMPANIES,model=object())._review_exact_bindings(request,review)
    assert checked.accepted,checked.planner_defects
    assert request.conditions.time.years==[2023] and [g.kind for g in request.goals]==['quote']

@pytest.mark.parametrize('wording',['这一轮','这一回','仅本次','只是现在这次'])
def test_turn_lifetime_is_not_rejected_by_a_phrase_allowlist(wording):
    from src.agent.v3.condition_updates import apply_edits
    previous=Conditions(presentation={'unit':'万元','decimals':2})
    edits=[MODIFICATION.validate_python(edit('presentation.unit','亿元',lifetime='turn')),
        MODIFICATION.validate_python(edit('presentation.decimals',3,lifetime='turn'))]
    current=apply_edits(previous,edits,wording+'用亿元三位小数')
    assert current.presentation.unit=='亿元'
    saved=persist_conditions(current,previous,edits,[])
    assert saved.presentation.unit=='万元' and saved.presentation.decimals==2

def test_temporary_table_does_not_leave_derived_no_chart_in_task():
    previous=Conditions();current=Conditions(presentation={'format':'table'},restrictions={'no_chart':True})
    edits=[MODIFICATION.validate_python(edit('presentation.format','table',lifetime='turn'))]
    saved=persist_conditions(current,previous,edits,[])
    assert saved.presentation.format=='auto' and not saved.restrictions.no_chart

def test_wire_requires_lifetime_only_on_present_display_or_restriction_edits():
    from src.agent.v3.turn_transport import turn_schema
    schema=turn_schema(TurnPlan.model_json_schema(),{'question':'介绍功能','companies':COMPANIES,'state':{}})
    rule=schema['properties']['edits']['properties']['presentation.unit']['items']
    sub={**schema['$defs'][rule['$ref'].split('/')[-1]],'$defs':schema['$defs']}
    validator=Draft202012Validator(sub)
    validator.validate({'operation':'replace','value':'万元'})
    with pytest.raises(ValidationError):validator.validate({'operation':'replace','value':'万元','lifetime':'forever'})
    validator.validate({'operation':'replace','value':'万元','lifetime':'task'})
    assert schema['properties']['edits']['required']==[]
    for field in ['codes','metrics','scope','time.years','calculation']:
        rules=schema['properties']['edits']['properties'][field]
        rule=rules.get('anyOf',[rules])[0]['items']
        definition=schema['$defs'][rule['$ref'].split('/')[-1]]
        assert 'lifetime' not in definition['properties']

@pytest.mark.parametrize('other',['写一篇游记','想个宠物名字','推荐晚餐'])
def test_supported_query_and_unrelated_goal_compile_independently(other):
    q='同仁堂2023年合并净利润有多少？另外'+other
    p=plan(q,metric='net_profit')
    from src.agent.v3.proposals import IntentGeneralGoal
    p.goals.append(IntentGeneralGoal(id='g2',kind='unsupported',source_ref=q))
    request,state=compile_plan(q,p)
    assert [g.kind for g in request.goals]==['lookup','unsupported']
    assert not request.goals[0].clarification and request.conditions.metrics==['net_profit']
    assert request.unsupported and state.conditions.codes==['600085']

@pytest.mark.parametrize('question,kind,target',[('你能做什么','catalog','capabilities'),('介绍支持的指标','catalog','metrics'),('写一首诗','unsupported',None)])
def test_informational_answers_have_no_report_database_dependency(question,kind,target):
    class Model:
        async def structured(self,schema,system,payload,budget):
            budget.charge();goal=dict(id='g1',kind=kind)
            if target:goal['catalog_target']=target
            return TurnPlan(goals=[goal])
    def unavailable(*args,**kw):raise AssertionError('No financial repository or retriever should be created')
    async def emit(*args):pass
    result=asyncio.run(V3Agent(None,COMPANIES,model=Model(),repository_factory=unavailable,retriever_factory=unavailable).run(question,[],'intro',time.time()+60,emit))
    assert not result['facts'] and not result['chart_data_list'] and not result['evidence']
    assert result['answer']['content'] and not result['needs_clarification']
    if target!='metrics':
        import re
        assert re.search(r'[“「"].+?[？?][”」"]',result['answer']['content'])
