"""将本轮限制、财务任务条件与明确的会话偏好分开保存。"""
from __future__ import annotations
from copy import deepcopy
import re
from .contracts import Conditions,MODIFICATION

TURN_DEFAULTS={'restrictions.no_query','restrictions.no_repeat'}


def explicit_lifetime(field,question):
    """只检查明确作用范围；按字段所在分句保留本轮与会话偏好的区别。"""
    patterns={
        'presentation.unit':r'亿元|万元|元|每股|百分|单位',
        'presentation.decimals':r'小数|[零一二两三四五六七八九十\d]+位',
        'presentation.format':r'表格|表|图|文字|输出形式',
        'presentation.chart_type':r'折线|柱状|饼图|散点',
        'presentation.order':r'升序|降序|从.{0,4}到|排名|排序',
        'presentation.limit':r'前[零一二两三四五六七八九十\d]+|数量',
        'presentation.include_inputs':r'基础数|原始数|两年.*数|输入',
        'restrictions.no_query':r'查|查询|取数',
        'restrictions.no_repeat':r'重复|金额|数字',
        'restrictions.no_chart':r'图|绘制',
    }
    if field not in patterns:return None
    active=None;observed=set()
    for clause in re.split(r'[，,。；;？?!！\n]',question):
        temporary=bool(re.search(r'(?:本|这|此)(?:一)?(?:轮|次|回)|临时|暂时',clause))
        persistent=bool(re.search(r'以后|今后|之后(?:都|一直)|后面(?:都|一直)|一直|每次|从现在起',clause))
        if temporary and persistent:active=None
        elif temporary:active='turn'
        elif persistent:active='session'
        if active and re.search(patterns[field],clause):observed.add(active)
    return next(iter(observed)) if len(observed)==1 else None


def is_reference_turn(understanding):
    """已登记的事实引用或禁止查数的来源追问，只改变本轮输入。"""
    if understanding.continuity not in {'continue','resume'} or not understanding.goals:
        return False
    if not all(g.kind in {'quote','sign'} or g.kind=='concept' and g.concept_mode=='implication'
               for g in understanding.goals):
        return False
    if any(ref.target=='fact' for ref in understanding.context_references):
        return True
    edits=[*understanding.modifications,*(e for g in understanding.goals for e in g.condition_edits)]
    return any(e.operation=='replace' and (
        e.field=='restrictions.no_query' and e.value is True or
        e.field=='restrictions' and 'no_query' in e.value.model_fields_set and e.value.no_query)
        for e in edits)

def validate_lifetime(edit, question):
    if edit.lifetime is None:return
    # 语义作用范围由同一次理解给出；枚举合法不等于已证明理解正确。
    # 不以几种固定措辞拒绝合法的本轮要求，且会话偏好仍限制字段范围。
    if edit.lifetime=='session' and not edit.field.startswith(('presentation.','restrictions.')):
        raise ValueError('Only presentation and restrictions may be session preferences')

def paths(edit):
    if edit.field in {'time','presentation','restrictions'}:
        return [edit.field+'.'+key for key in edit.value.model_fields_set]
    return [edit.field]

def get_path(raw,path):
    for part in path.split('.'):raw=raw[part]
    return deepcopy(raw)

def set_path(raw,path,value):
    parts=path.split('.')
    for part in parts[:-1]:raw=raw[part]
    raw[parts[-1]]=deepcopy(value)

def preferences_base(preferences):
    raw=Conditions().model_dump()
    for edit in preferences:
        value=edit.value
        if edit.operation=='clear':value=get_path(Conditions().model_dump(),edit.field)
        set_path(raw,edit.field,value)
    return Conditions.model_validate(raw)

def persist_conditions(current,base,edits,preferences):
    """从本轮有效条件生成唯一的持久条件；本轮值不反写主任务。"""
    raw=current.model_dump();old=base.model_dump();defaults=preferences_base(preferences).model_dump()
    lifetimes={path:edit.lifetime or ('turn' if path in TURN_DEFAULTS else 'task')
        for edit in edits if edit.operation!='inherit' for path in paths(edit)}
    for path in TURN_DEFAULTS:
        retained=(raw['origins'].get(path) or {}).get('lifetime')
        if lifetimes.get(path,retained) not in {'task','session'}:
            set_path(raw,path,get_path(defaults,path));raw['origins'].pop(path,None)
    for path,lifetime in lifetimes.items():
        if lifetime=='turn':
            set_path(raw,path,get_path(old,path))
            raw['origins'].pop(path,None)
            if path in old['origins']:raw['origins'][path]=old['origins'][path]
    if lifetimes.get('presentation.format')=='turn' and 'restrictions.no_chart' not in lifetimes:
        set_path(raw,'restrictions.no_chart',get_path(old,'restrictions.no_chart'))
        raw['origins'].pop('restrictions.no_chart',None)
        if 'restrictions.no_chart' in old['origins']:raw['origins']['restrictions.no_chart']=old['origins']['restrictions.no_chart']
    if raw['presentation']['format']=='table':raw['restrictions']['no_chart']=True
    return Conditions.model_validate(raw)

def update_preferences(previous,edits):
    result={edit.field:edit.model_copy(deep=True) for edit in previous}
    for edit in edits:
        if edit.lifetime!='session' or edit.operation=='inherit':continue
        if edit.operation=='clear':result.pop(edit.field,None)
        else:result[edit.field]=edit.model_copy(deep=True)
    return list(result.values())


def persist_pending(request,understanding,previous,preferences,*,goals=None,base=None,goal_context=None):
    """编译与发布共用待澄清任务保存规则，临时限制不经错误出口变成长久条件。"""
    selected=request.goals if goals is None else goals
    if not selected:
        return None
    if base is None:
        if understanding.continuity in {'new','clear'}:
            base=preferences_base(preferences)
        elif understanding.continuity=='resume' and previous.suspended:
            base=previous.suspended
        else:
            base=previous.pending.conditions if previous.pending else previous.conditions
    if goal_context is None:
        goal_context=({g.id:previous.pending.for_goal(g).conditions for g in previous.pending.goals}
                      if previous.pending else previous.goal_conditions)
    original_edits={g.id:g.condition_edits for g in understanding.goals}
    pending=request.model_copy(deep=True,update={'goals':[g.model_copy(deep=True) for g in selected]})
    pending.conditions=persist_conditions(request.conditions,base,understanding.modifications,preferences)
    pending.resolved_goal_conditions={g.id:persist_conditions(request.for_goal(g).conditions,
        goal_context.get(g.context_goal_id,base),
        [*understanding.modifications,*original_edits.get(g.id,[])],preferences) for g in selected}
    values=list(pending.resolved_goal_conditions.values())
    if values and all(value==values[0] for value in values):
        pending.conditions=values[0].model_copy(deep=True)
    return pending

def migrate_state(state, *, legacy=False):
    """旧状态的临时禁止项不升级为会话偏好，旧回答不参与迁移。"""
    state=state.model_copy(deep=True)
    if legacy:
        def clean(value):
            value=value.model_copy(deep=True)
            value.restrictions.no_query=False;value.restrictions.no_repeat=False
            for path in TURN_DEFAULTS:value.origins.pop(path,None)
            return value
        state.conditions=clean(state.conditions)
        if state.suspended:state.suspended=clean(state.suspended)
        state.goal_conditions={key:clean(value) for key,value in state.goal_conditions.items()}
        if state.pending:
            bindings={goal.id:clean(state.pending.for_goal(goal).conditions) for goal in state.pending.goals}
            state.pending.conditions=clean(state.pending.conditions)
            state.pending.resolved_goal_conditions=bindings
    state.state_revision=2
    return state
