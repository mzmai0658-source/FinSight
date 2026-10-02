import pytest
from pydantic import ValidationError
from src.agent.v3.contracts import Conditions,DialogueState,Goal,GoalSelection,FactReference,Request
from src.agent.v3.proposals import IntentPlan,ConditionPlan,combine_plans,compile_proposal
from src.agent.v3.state import apply_understanding
from src.agent.v3.condition_updates import apply_edits

COMPANIES={'600085':'同仁堂','002082':'万邦德'}

def interpretation(question,edits=(),**intent):
    intent.pop('topic',None);intent.setdefault('goals',[dict(id='g',kind='lookup',text=question)])
    plan=ConditionPlan(edits=[dict(field=f,operation=op,value=value,text=question) for f,op,value in edits])
    return compile_proposal(combine_plans(IntentPlan(**intent),plan),question,COMPANIES)


def test_special_goal_modes_are_required_by_the_typed_intent():
    for kind in ('catalog','concept','quote'):
        with pytest.raises(ValidationError):IntentPlan(goals=[dict(id='g',kind=kind,text='目标')])
    assert IntentPlan(goals=[dict(id='g',kind='catalog',text='能力',catalog_target='capabilities')]).goals[0].catalog_target=='capabilities'


def test_concept_preserves_current_scope_and_table_format_without_inheriting_financial_units():
    q='解释母公司净利润，只要表格，不查数字'
    prior=DialogueState(topic='financial',conditions=Conditions(codes=['600085'],metrics=['operating_revenue'],presentation={'unit':'亿元'}))
    u=interpretation(q,[('metrics','replace',['net_profit']),('scope','replace','parent'),('presentation.format','replace','table')],
        continuity='new',goals=[dict(id='meaning',kind='concept',text=q,concept_mode='definition')])
    r,state=apply_understanding(q,'concept',u,prior,COMPANIES)
    assert r.conditions.scope=='parent' and r.conditions.presentation.format=='table'
    assert not r.conditions.codes and r.conditions.presentation.unit is None and r.conditions.restrictions.no_query
    assert state.conditions==prior.conditions


def test_definition_does_not_require_coverage_of_a_named_company():
    q='贵州茅台的毛利率是什么意思，不查数字'
    u=interpretation(q,[('metrics','replace',['gross_margin'])],continuity='new',unknown_companies=['贵州茅台'],
        goals=[dict(id='meaning',kind='concept',text=q,concept_mode='definition')])
    r,_=apply_understanding(q,'concept',u,DialogueState(),COMPANIES)
    assert not r.unsupported and not r.clarification and r.conditions.metrics==['gross_margin']


def test_explicit_topic_clear_cannot_keep_a_suspended_company_for_later_resume():
    q='忘掉刚才的财务任务，换个话题，解释净利率'
    prior=DialogueState(topic='financial',conditions=Conditions(codes=['600085'],metrics=['operating_revenue']),
        active_goals=[Goal(id='old',kind='lookup',text='财务查询')])
    u=interpretation(q,[('metrics','replace',['net_margin'])],continuity='clear',
        goals=[dict(id='meaning',kind='concept',text=q,concept_mode='definition')])
    _,state=apply_understanding(q,'clear',u,prior,COMPANIES)
    assert not state.active_goals and not state.goal_conditions and not state.conditions.codes and state.suspended is None


def test_memory_deletion_cannot_be_invented_from_a_metric_edit_or_retention_instruction():
    for question in ('再看毛利率','只解释毛利率','不要忘记刚才公司'):
        plan=IntentPlan(continuity='new',goals=[dict(id='g',kind='lookup',source_ref=question)],
            memory_edits=[dict(field='financial_context',text=question)])
        with pytest.raises(ValueError,match='explicit instruction'):
            compile_proposal(combine_plans(plan,ConditionPlan(edits=[])),question,COMPANIES)
    q='不延续刚才公司，解释毛利率'
    plan=IntentPlan(continuity='new',goals=[dict(id='g',kind='concept',concept_mode='definition',source_ref='解释毛利率')],
        memory_edits=[dict(field='company_context',text='不延续刚才公司')])
    assert compile_proposal(combine_plans(plan,ConditionPlan(edits=[])),q,COMPANIES).memory_edits[0].field=='company_context'
    plan.continuity='clear'
    with pytest.raises(ValueError,match='whole financial context'):
        compile_proposal(combine_plans(plan,ConditionPlan(edits=[])),q,COMPANIES)


def test_setting_report_periods_cannot_be_published_as_an_execution_rule_answer():
    from src.agent.v3.request_bindings import goal_source_supported,requirement_source_supported
    from src.agent.v3.planner import GoalCoverage
    prior=DialogueState()
    for question in ('半年报和年报放一起，明确累计口径','把同仁堂去掉，只留万邦德','解释净利润为负的含义'):
        request=Request(turn_id='t',question=question,conditions=Conditions(),goals=[])
        goal=Goal(id='r',kind='rules',text=question,intent_source=question)
        assert not goal_source_supported(goal,request,prior)
        assert not requirement_source_supported(GoalCoverage(kind='rules',goal_ids=['r'],representation='represented',source_ref=question),request)
    question='你这次查的是哪家公司、哪一年、哪个指标？'
    request=Request(turn_id='t',question=question,conditions=Conditions(),goals=[])
    assert goal_source_supported(Goal(id='r',kind='rules',text=question,intent_source=question),request,prior)


def test_comparison_defaults_follow_the_actual_unique_object_dimension():
    q='比较同仁堂和万邦德2024年营业收入'
    u=interpretation(q,[('codes','replace',['600085','002082']),('metrics','replace',['operating_revenue']),('time.years','replace',[2024])],
        continuity='new',goals=[dict(id='compare',kind='compare',text=q)])
    r,_=apply_understanding(q,'compare',u,DialogueState(),COMPANIES)
    assert r.conditions.comparison_axis=='companies' and r.conditions.origins['comparison_axis'].kind=='default'


def test_failed_condition_change_cannot_make_a_year_only_followup_query_an_older_metric():
    prior=DialogueState(topic='financial',conditions=Conditions(codes=['600085'],metrics=['attributable_net_profit'],time={'mode':'explicit','years':[2024]}),
        active_goals=[Goal(id='old',kind='lookup',text='原先查询')],
        interrupted_request={'turn_id':'failed','question':'改看毛利率','status':'failed'})
    q='改成2023年，其他不变'
    r,state=apply_understanding(q,'followup',interpretation(q,[('time.years','replace',[2023])],continuity='continue'),prior,COMPANIES)
    assert r.clarification and r.goals[0].clarification and state.interrupted_request is not None
    assert not state.recent_facts and not state.recent_computed
    q='同仁堂，指标用毛利率'
    u=interpretation(q,[('codes','replace',['600085']),('metrics','replace',['gross_margin'])],continuity='continue')
    r,confirmed=apply_understanding(q,'confirmed',u,state,COMPANIES)
    assert not r.clarification and confirmed.interrupted_request is None
    assert r.conditions.metrics==['gross_margin'] and r.conditions.time.years==[2023]


def test_each_edit_preserves_a_literal_input_span_and_an_exact_operation():
    q='只留万邦德'
    u=interpretation(q,[('codes','keep',['002082'])],continuity='continue')
    prior=DialogueState(conditions=Conditions(codes=['600085','002082'],metrics=['operating_revenue'],time={'mode':'explicit','years':[2023]},presentation={'unit':'亿元','decimals':3,'format':'table'}))
    r,_=apply_understanding(q,'t',u,prior,COMPANIES)
    assert r.conditions.codes==['002082'] and r.conditions.time.years==[2023]
    assert r.conditions.presentation.decimals==3 and r.conditions.origins['codes'].text==q
    p=combine_plans(IntentPlan(goals=[dict(id='g',kind='lookup',text=q)]),ConditionPlan(edits=[dict(field='codes',operation='keep',value=['002082'],text='不存在的原话')]))
    with pytest.raises(ValueError):compile_proposal(p,q,COMPANIES)


def test_sparse_contract_has_no_redundant_mask_or_populated_default_object():
    with pytest.raises(ValidationError):ConditionPlan(edits=[],changed_fields=['codes'],changes={})
    q='万邦德2024年营收'
    u=interpretation(q,[('codes','replace',['002082']),('metrics','replace',['operating_revenue']),('time.years','replace',[2024])])
    r,_=apply_understanding(q,'t',u,DialogueState(),COMPANIES)
    assert r.conditions.codes==['002082'] and r.conditions.metrics==['operating_revenue'] and r.conditions.time.years==[2024] and not r.clarification


def test_shared_and_goal_edit_use_the_same_transition_policy():
    base=Conditions(codes=['600085'],metrics=['operating_revenue'],time={'mode':'explicit','years':[2023]},presentation={'unit':'亿元','decimals':3,'format':'table'})
    edits=ConditionPlan(edits=[dict(field='time.years',operation='replace',value=[2024],text='2024年'),dict(field='presentation.decimals',operation='replace',value=2,text='2位')]).edits
    q='改成2024年和2位小数'
    from src.agent.v3.condition_defaults import finalize_conditions
    direct=finalize_conditions(apply_edits(base,edits,q,'t'),['lookup'],'t')
    req=Request(turn_id='t',question=q,conditions=base,goals=[])
    g=Goal(id='g',kind='lookup',text='改条件',condition_edits=edits)
    assert req.for_goal(g).conditions==direct
    assert direct.time.mode=='explicit' and direct.time.years==[2024]
    assert direct.presentation.unit=='亿元' and direct.presentation.format=='table' and direct.presentation.decimals==2


def test_period_only_and_empty_edit_list_preserve_year_scope_and_unit():
    old=DialogueState(conditions=Conditions(codes=['600085'],metrics=['operating_revenue'],scope='parent',time={'mode':'explicit','years':[2023]},presentation={'unit':'亿元','format':'table'}))
    for q,edits in [('改半年报',[('time.periods','replace',['HY'])]),('其他不变',[])]:
        r,_=apply_understanding(q,'t',interpretation(q,edits,continuity='continue'),old,COMPANIES)
        assert r.conditions.time.years==[2023] and r.conditions.scope=='parent' and r.conditions.presentation.unit=='亿元'
        assert r.conditions.presentation.format=='table'


def test_pending_known_year_and_metric_survive_a_company_answer():
    pending=Request(turn_id='first',question='2024年的营业收入是多少？',conditions=Conditions(metrics=['operating_revenue'],time={'mode':'explicit','years':[2024]}),goals=[Goal(id='g',kind='lookup',text='营收')],clarification=['请明确要查询哪家公司。'])
    u=interpretation('同仁堂',[('codes','replace',['600085'])],continuity='continue')
    r,s=apply_understanding('同仁堂','second',u,DialogueState(pending=pending),COMPANIES)
    assert not r.clarification and r.conditions.metrics==['operating_revenue'] and r.conditions.time.years==[2024] and s.pending is None


def test_partial_request_only_asks_for_missing_company():
    q='2024年的营业收入是多少？'
    r,_=apply_understanding(q,'t',interpretation(q,[('metrics','replace',['operating_revenue']),('time.years','replace',[2024])]),DialogueState(),COMPANIES)
    assert r.clarification==['请明确要查询哪家公司。']


def test_uncovered_name_with_registered_prefix_is_never_replaced():
    q='同仁堂国药营收'
    u=interpretation(q,[('codes','replace',['600085']),('metrics','replace',['operating_revenue'])],unknown_companies=['同仁堂国药'])
    r,s=apply_understanding(q,'t',u,DialogueState(),COMPANIES)
    assert r.goals[0].kind=='unsupported' and not r.conditions.codes and not s.conditions.codes


def test_keep_from_all_companies_narrows_and_records_exclusions():
    old=DialogueState(conditions=Conditions(codes=list(COMPANIES),all_companies=True,metrics=['operating_revenue']))
    q='只留万邦德'
    r,_=apply_understanding(q,'t',interpretation(q,[('codes','keep',['002082'])],continuity='continue'),old,COMPANIES)
    assert r.conditions.codes==['002082'] and not r.conditions.all_companies
    assert r.conditions.restrictions.excluded_codes==['600085']


def test_singular_reference_cannot_pick_from_two_confirmed_companies():
    q='它的营收呢'
    u=interpretation(q,[('codes','keep',['600085'])],continuity='continue',context_references=[dict(text='它',target='company')])
    old=DialogueState(conditions=Conditions(codes=list(COMPANIES),metrics=['operating_revenue']))
    r,_=apply_understanding(q,'t',u,old,COMPANIES)
    assert r.clarification and not r.conditions.codes


def test_contextual_implication_keeps_verified_binding_and_cannot_query():
    q='这个数为负说明什么'
    u=interpretation(q,topic='concept',continuity='continue',context_references=[dict(text='这个数',target='fact')],goals=[dict(id='g',kind='concept',text=q,concept_mode='implication')],claimed_sign='negative')
    old=DialogueState(topic='financial',conditions=Conditions(codes=['600085'],metrics=['operating_cash_flow'],time={'years':[2023],'mode':'explicit'}),recent_facts=[FactReference(id='f',data_version='v')])
    r,s=apply_understanding(q,'t',u,old,COMPANIES)
    assert r.conditions.codes==['600085'] and r.conditions.time.years==[2023] and r.conditions.restrictions.no_query
    assert s.conditions==old.conditions and not r.clarification


def test_year_set_operations_compose_and_retained_years_have_context_evidence():
    base=Conditions(time={'years':[2022,2023],'mode':'explicit'})
    q='加上2024年，去掉2022年'
    edits=ConditionPlan(edits=[dict(field='time.years',operation='add',value=[2024],text='加上2024年'),dict(field='time.years',operation='remove',value=[2022],text='去掉2022年')]).edits
    assert apply_edits(base,edits,q).time.years==[2023,2024]


def test_single_quarter_normalization_is_independent_of_edit_order():
    q='2024年第二季度单季'
    edits=[dict(field='time.periods',operation='replace',value=[],text=q),dict(field='time.quarters',operation='replace',value=[2],text=q),dict(field='time.single_quarter',operation='replace',value=True,text=q)]
    for values in (edits,list(reversed(edits))):
        c=apply_edits(Conditions(),ConditionPlan(edits=values).edits,q)
        assert c.time.periods==[] and c.time.single_quarter and c.time.quarters==[2]
