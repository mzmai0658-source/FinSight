import asyncio
import time

import pytest

from src.agent.v3.contracts import Conditions, DialogueState, Execution, Goal, Request
from src.agent.v3.tasks import TaskManager, TaskStore, failure_context, terminal_result
from src.agent.v3.state import apply_understanding
from src.agent.v3.planner import GoalCoverage, confirmed_task_view
from src.agent.v3.request_bindings import requirement_source_supported, goal_source_supported
from src.agent.v3.proposals import IntentPlan,ConditionPlan,combine_plans,compile_proposal

COMPANIES={'600085':'同仁堂','002082':'万邦德'}

def interpretation(question,edits=(),**intent):
    intent.setdefault('goals',[dict(id='g',kind='lookup',text=question)])
    plan=ConditionPlan(edits=[dict(field=f,operation=op,value=value,text=question) for f,op,value in edits])
    return compile_proposal(combine_plans(IntentPlan(**intent),plan),question,COMPANIES)


def current_state():
    return DialogueState(topic='financial',conditions=Conditions(codes=['600085'],metrics=['operating_revenue'],
        time={'mode':'explicit','years':[2024]}),active_goals=[Goal(id='old',kind='lookup',text='已确认查询')])


def test_only_the_failed_metric_edit_needs_confirmation():
    store_record=dict(id='failed',status='failed',context=current_state().model_dump(),question='改看毛利率',
        request_checkpoint={'approved':False,'uncertain_paths':['metrics']})
    previous=DialogueState.model_validate(failure_context(store_record))
    q='改成2023年，其他不变'
    waiting,state=apply_understanding(q,'year',interpretation(q,[('time.years','replace',[2023])],continuity='continue'),previous,COMPANIES)
    assert waiting.clarification and state.interrupted_request.uncertain_paths==['metrics']
    assert '公司' not in waiting.clarification[-1]
    q='再看毛利率'
    ready,state=apply_understanding(q,'metric',interpretation(q,[('metrics','replace',['gross_margin'])],continuity='continue'),state,COMPANIES)
    assert not ready.clarification and state.interrupted_request is None
    assert ready.conditions.codes==['600085'] and ready.conditions.time.years==[2023]
    assert ready.conditions.metrics==['gross_margin']


def test_unconfirmed_year_cannot_be_replaced_by_an_old_year_on_a_chart_followup():
    record=dict(id='failed',status='failed',context=current_state().model_dump(),question='改成2023年',
        request_checkpoint={'approved':False,'uncertain_paths':['time.years']})
    previous=DialogueState.model_validate(failure_context(record))
    q='画个柱状图'
    request,state=apply_understanding(q,'chart',interpretation(q,[('presentation.chart_type','replace','bar')],continuity='continue',
        goals=[dict(id='chart',kind='chart',text=q)]),previous,COMPANIES)
    assert request.clarification and '时间要求' in request.clarification[-1]
    assert state.interrupted_request.uncertain_paths==['time.years']


@pytest.mark.parametrize('end',['sql_failure','cancel','restart'])
def test_confirmed_conditions_survive_failure_cancel_and_restart_without_any_fact_reuse(tmp_path,end):
    async def scenario():
        confirmed=asyncio.Event();release=asyncio.Event()
        class Agent:
            companies=COMPANIES
            async def run(self,q,history,id,deadline,emit):
                request,state=apply_understanding(q,id,interpretation(q,[('metrics','replace',['gross_margin'])],continuity='continue'),current_state(),COMPANIES)
                await emit('plan',{'label':'confirmed','_request_checkpoint':dict(request=request.model_dump(),dialogue=state.model_dump(),approved=True)})
                confirmed.set()
                if end=='sql_failure':raise RuntimeError('private-driver-error')
                await release.wait()
        store=TaskStore(tmp_path/'tasks.db');manager=TaskManager(Agent(),store)
        await manager.submit('task','client','session','改看毛利率',[],time.time()+270)
        await confirmed.wait()
        if end=='cancel':await manager.cancel('task','session')
        elif end=='restart':await manager.close();store.recover_restart()
        events=[event async for event in manager.stream('task')]
        record=store.get('task');result=terminal_result(record)
        state=DialogueState.model_validate(result['dialogue_state'])
        assert state.conditions.metrics==['gross_margin'] and state.conditions.codes==['600085']
        assert state.interrupted_request.uncertain_paths==[]
        assert not state.recent_facts and not state.recent_computed
        assert state.last_execution.status in {'failed','cancelled'}
        assert not state.last_execution.fact_refs and not state.last_execution.resolved_periods
        assert all('_request_checkpoint' not in event.data for event in events)
        assert 'private-driver-error' not in str(result)
        assert not store.checkpoint('task',record['request_checkpoint']['request'],current_state().model_dump(),approved=True)
        followup='改成2023年，其他不变'
        request,_=apply_understanding(followup,'next',interpretation(followup,[('time.years','replace',[2023])],continuity='continue'),state,COMPANIES)
        assert not request.clarification and request.conditions.metrics==['gross_margin']
        await manager.close()
    asyncio.run(scenario())


def test_failed_rules_question_retains_actual_financial_execution():
    state=current_state()
    state.last_execution=Execution(turn_id='old',data_version='version',resolved_periods={'600085':[(2024,'FY')]},time_rule='明确报告期',fact_refs=[],status='completed')
    request=Request(turn_id='rules',question='为什么选这些年份',conditions=Conditions(),goals=[Goal(id='r',kind='rules',text='为什么选这些年份')])
    record=dict(id='rules',status='failed',question=request.question,context=state.model_dump(),request_checkpoint={'request':request.model_dump(),'approved':False})
    result=DialogueState.model_validate(failure_context(record))
    assert result.last_execution.turn_id=='old' and result.interrupted_request.uncertain_paths==[]


def test_auditor_cannot_label_a_company_edit_as_greeting():
    q='只留万邦德'
    request=Request(turn_id='task',question=q,conditions=Conditions(),goals=[])
    assert not requirement_source_supported(GoalCoverage(kind='greeting',goal_ids=[],representation='missing',source_ref=q),request)
    q='为什么选这几个年份？'
    assert goal_source_supported(Goal(id='r',kind='rules',text=q,intent_source=q),request,current_state())


def test_readable_conditions_are_typed_projection_not_answer_numbers():
    view=confirmed_task_view(current_state(),COMPANIES)
    assert view['当前条件']['公司']==['同仁堂']
    assert view['当前条件']['指标']==['营业收入'] and view['当前条件']['年份']==[2024]
    assert 'recent_facts' not in str(view) and 'answer' not in str(view)
