"""作品说明：将删除对话记忆与修改当前请求分开，明确记录各自的作用范围。"""
from .contracts import Conditions,MODIFICATION


def apply_memory_edits(previous,edits,question,turn_id):
    from .condition_updates import apply_edits
    state=previous.model_copy(deep=True)
    for edit in edits:
        if not edit.text or edit.text not in question:
            from .state import InvalidUnderstanding
            error=InvalidUnderstanding('删除记忆必须有本轮逐字依据。');error.repair_goals=True
            raise error
        if edit.field=='financial_context':
            state.conditions=Conditions();state.active_goals=[];state.goal_conditions={}
            state.suspended=None;state.pending=None;state.legacy_scope_unknown=False
            state.interrupted_request=None
        else:
            clears=[MODIFICATION.validate_python(dict(field=field,operation='clear',value=[],text=edit.text))
                for field in ('codes','restrictions.excluded_codes')]
            def clean(value):
                value=apply_edits(value,clears,question,turn_id)
                value=apply_edits(value,[MODIFICATION.validate_python(dict(field='all_companies',operation='replace',value=False,text=edit.text))],question,turn_id)
                return value
            state.conditions=clean(state.conditions)
            if state.suspended:state.suspended=clean(state.suspended)
            state.goal_conditions={key:clean(value) for key,value in state.goal_conditions.items()}
            if state.pending:
                bindings={goal.id:clean(state.pending.for_goal(goal).conditions) for goal in state.pending.goals}
                state.pending.conditions=clean(state.pending.conditions);state.pending.modifications=[]
                for goal in state.pending.goals:
                    goal.context_conditions=bindings[goal.id];goal.selection=None;goal.condition_edits=[]
        state.recent_facts=[];state.recent_computed=[]
    # 作品说明：执行历史保持可读，但不用于重建已删除的公司或财务条件。
    return state
