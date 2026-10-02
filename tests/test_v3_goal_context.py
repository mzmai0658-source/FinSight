import pytest
from src.agent.v3.contracts import Conditions,DialogueState,Goal
from src.agent.v3.proposals import IntentPlan,ConditionPlan,combine_plans,compile_proposal
from src.agent.v3.state import apply_understanding,InvalidUnderstanding

COMPANIES={'600085':'同仁堂','002082':'万邦德'}


def prior_independent():
    bindings={
        'revenue':Conditions(codes=['600085'],metrics=['operating_revenue'],scope='parent',time={'mode':'explicit','years':[2024]},presentation={'unit':'亿元','format':'table'}),
        'profit':Conditions(codes=['002082'],metrics=['attributable_net_profit'],time={'mode':'explicit','years':[2024]},presentation={'unit':'万元','decimals':3}),
    }
    return DialogueState(topic='financial',active_goals=[Goal(id=key,kind='lookup',text=key) for key in bindings],goal_conditions=bindings)


def proposal(question,goals,edits=(),continuity='continue'):
    return compile_proposal(combine_plans(IntentPlan(continuity=continuity,goals=goals),ConditionPlan(edits=edits)),question,COMPANIES)


def test_year_change_keeps_independent_company_metric_scope_and_display_bindings():
    q='改成2023年，其他不变';old=prior_independent()
    u=proposal(q,[dict(id='a',kind='lookup',context_goal_id='revenue'),dict(id='b',kind='lookup',context_goal_id='profit')],
        [dict(field='time.years',operation='replace',value=[2023],text='2023年')])
    request,state=apply_understanding(q,'next',u,old,COMPANIES)
    assert not request.clarification
    selections=[request.for_goal(g).conditions for g in request.goals]
    assert [(c.codes,c.metrics,c.scope,c.time.years,c.presentation.unit) for c in selections]==[
        (['600085'],['operating_revenue'],'parent',[2023],'亿元'),
        (['002082'],['attributable_net_profit'],'consolidated',[2023],'万元')]
    assert selections[0].presentation.format=='table' and selections[1].presentation.decimals==3
    assert old.goal_conditions['revenue'].time.years==[2024]
    assert set(state.goal_conditions)=={'a','b'}


def test_unbound_multi_goal_followup_and_unknown_binding_are_rejected():
    old=prior_independent();q='改成2023年'
    for goal in [dict(id='g',kind='lookup'),dict(id='g',kind='lookup',context_goal_id='invented')]:
        with pytest.raises(InvalidUnderstanding):apply_understanding(q,'next',proposal(q,[goal]),old,COMPANIES)


def test_new_or_clear_request_cannot_borrow_old_goal_conditions():
    for continuity in ['new','clear']:
        q='不沿用刚才的公司'
        with pytest.raises(ValueError):
            u=proposal(q,[dict(id='g',kind='lookup',context_goal_id='revenue')],continuity=continuity)
            apply_understanding(q,'next',u,prior_independent(),COMPANIES)


def test_concept_aside_keeps_bindings_but_clear_discards_them():
    old=prior_independent()
    for continuity in ['new','clear']:
        q=('忘掉此前的财务任务，' if continuity=='clear' else '')+'毛利率是什么意思'
        u=proposal(q,[dict(id='definition',kind='concept',concept_mode='definition')],continuity=continuity)
        request,state=apply_understanding(q,'aside',u,old,COMPANIES)
        assert request.conditions.restrictions.no_query
        assert state.goal_conditions==({} if continuity=='clear' else old.goal_conditions)
    with pytest.raises(ValueError,match='explicit current-input instruction'):
        proposal('毛利率是什么意思',[dict(id='definition',kind='concept',concept_mode='definition')],continuity='clear')


def test_context_goal_comparison_defaults_apply_after_company_addition():
    old=DialogueState(topic='financial',conditions=Conditions(codes=['600085'],metrics=['operating_revenue'],
        time={'mode':'explicit','years':[2024]}),active_goals=[Goal(id='prior',kind='lookup',text='收入')])
    q='加上万邦德比较谁更高'
    u=proposal(q,[dict(id='compare',kind='compare',context_goal_id='prior')],
        [dict(field='codes',operation='add',value=['002082'],text='万邦德')])
    request,state=apply_understanding(q,'next',u,old,COMPANIES)
    assert not request.clarification
    assert request.for_goal(request.goals[0]).conditions.comparison_axis=='companies'
    assert state.goal_conditions['compare'].comparison_axis=='companies'


def test_latest_default_and_axis_recompute_after_goal_local_binding():
    from src.agent.v3.contracts import Request,GoalSelection
    r=Request(turn_id='t',question='同仁堂与万邦德比较',conditions=Conditions(codes=['600085'],
        metrics=['operating_revenue']),goals=[])
    goal=Goal(id='c',kind='compare',text=r.question,context_conditions=r.conditions,
        selection=GoalSelection(codes=['600085','002082']))
    resolved=r.for_goal(goal).conditions
    assert resolved.time.mode=='latest_common' and resolved.comparison_axis=='companies'
    assert r.conditions.time.mode=='latest' and r.conditions.codes==['600085']


def test_aside_explicit_company_clear_cannot_be_restored_from_execution():
    from src.agent.v3.contracts import Execution
    from src.agent.v3.state import financial_context_conditions
    old=prior_independent()
    old.conditions=old.goal_conditions['revenue'].model_copy(deep=True)
    old.last_execution=Execution(turn_id='previous',data_version='v',resolved_periods={'600085':[(2024,'FY')]},
        time_rule='explicit',fact_refs=[],status='completed',goal_conditions=old.goal_conditions)
    q='解释净利率，不用延续刚才公司'
    u=proposal(q,[dict(id='definition',kind='concept',concept_mode='definition')],
        [dict(field='metrics',operation='replace',value=['net_margin'],text='净利率')],continuity='new')
    from src.agent.v3.contracts import MemoryEdit
    u.memory_edits=[MemoryEdit(field='company_context',text='不用延续刚才公司')]
    _,state=apply_understanding(q,'aside',u,old,COMPANIES)
    assert not state.conditions.codes and not state.suspended.codes
    assert all(not value.codes for value in financial_context_conditions(state).values())
    assert state.last_execution.goal_conditions['revenue'].codes==['600085']


def test_concept_request_clear_does_not_delete_financial_memory():
    old=prior_independent();old.conditions=old.goal_conditions['revenue'].model_copy(deep=True)
    q='ROE是什么意思，只解释概念'
    u=proposal(q,[dict(id='definition',kind='concept',concept_mode='definition')],
        [dict(field='metrics',operation='replace',value=['roe_weighted'],text='ROE'),
         dict(field='time.years',operation='clear',value=[],text='只解释概念')],continuity='new')
    request,state=apply_understanding(q,'aside',u,old,COMPANIES)
    assert request.conditions.metrics==['roe_weighted'] and request.conditions.restrictions.no_query
    assert state.conditions==old.conditions and state.goal_conditions==old.goal_conditions
