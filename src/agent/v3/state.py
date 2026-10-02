"""作品说明：按明确编辑更新状态，将确认的条件与未核实回答文字分开保存。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import re

from .catalog import ALIASES, AMBIGUOUS_TERMS, FAMILY_CHOICES, METRICS
from .contracts import ClarificationChoices, Conditions, DialogueState, Goal, Origin, Request, Understanding
from .condition_updates import merge_object,patch_values,apply_edits,validate_year_change


class InvalidUnderstanding(ValueError):
    pass


FINANCIAL_KINDS={'lookup','compare','rank','chart','quote','cause','sign'}


def financial_context_conditions(state: DialogueState):
    """作品说明：继承条件来自类型化请求或执行快照，旧回答正文不参与条件恢复。"""
    if state.pending:
        return {g.id:state.pending.for_goal(g).conditions for g in state.pending.goals if g.kind in FINANCIAL_KINDS}
    if state.goal_conditions:return {key:value.model_copy(deep=True) for key,value in state.goal_conditions.items()}
    # 作品说明：历史执行记录用于回查，不作为第二份记忆来源，避免概念插话后恢复已清除的公司。
    return {g.id:state.conditions.model_copy(deep=True) for g in state.active_goals if g.kind in FINANCIAL_KINDS}


def read_state(history: list[dict]) -> DialogueState:
    for item in reversed(history):
        if item.get('role') != 'assistant':
            continue
        raw = (item.get('metadata') or {}).get('dialogue_state')
        if isinstance(raw, dict) and raw.get('version') == 3:
            try:
                return DialogueState.model_validate(raw)
            except ValueError:
                # 作品说明：状态结构损坏时采用空状态，不把损坏内容转为财务事实。
                return DialogueState()
        if isinstance(raw, dict):
            # 作品说明：旧格式只迁移明确登记的身份，缺少口径信息时重新确认。
            old = raw.get('active_request') or {}
            codes = [c for c in old.get('codes', []) if isinstance(c, str) and len(c) == 6 and c.isdigit()]
            state = DialogueState()
            state.conditions.codes = codes
            state.legacy_scope_unknown = bool(codes)
            # 作品说明：旧指标、报表范围和回答数值不升级为当前确认条件。
            return state
    return DialogueState()


def apply_understanding(question: str, turn_id: str, understanding: Understanding,
                        previous: DialogueState, companies: dict[str, str]) -> tuple[Request, DialogueState]:
    from .memory_updates import apply_memory_edits
    previous=apply_memory_edits(previous,understanding.memory_edits,question,turn_id)
    state = previous.model_copy(deep=True)
    goal_context=financial_context_conditions(previous)
    confirmed_codes=set(previous.conditions.codes)
    if understanding.continuity in {'continue','resume'}:
        confirmed_codes.update(code for value in goal_context.values() for code in value.codes)
    if previous.pending:
        confirmed_codes.update(previous.pending.conditions.codes)
    if understanding.continuity=='resume' and previous.suspended:
        confirmed_codes.update(previous.suspended.codes)
    state.revision += 1
    for key, origin in state.conditions.origins.items():
        if origin.kind == 'current':
            origin.kind = 'context'
    if understanding.continuity in {'new', 'clear'}:
        state.conditions = Conditions()
        if understanding.topic=='financial' or understanding.continuity=='clear':state.interrupted_request=None
        if understanding.topic=='financial' or understanding.continuity=='clear':
            state.active_goals=[];state.goal_conditions={}
        state.legacy_scope_unknown = False
        state.pending = None
        if understanding.topic=='financial' or understanding.continuity=='clear':
            state.recent_facts = []
            state.recent_computed = []
            state.last_execution = None
        if understanding.continuity == 'clear':
            state.suspended = None
    elif understanding.continuity == 'resume' and state.suspended:
        state.conditions = state.suspended.model_copy(deep=True)
    elif state.pending and understanding.topic == 'financial':
        state.conditions = state.pending.conditions.model_copy(deep=True)
    for origin in state.conditions.origins.values():
        if origin.kind=='current' and origin.turn_id!=turn_id: origin.kind='context'

    if understanding.topic in {'concept', 'rules','catalog','greeting','other'} and previous.topic == 'financial' and understanding.continuity!='clear':
        state.suspended = previous.conditions.model_copy(deep=True)
    context_conditions=state.conditions.model_copy(deep=True)
    if understanding.goals and all(g.kind=='concept' and g.concept_mode in {'definition','difference'} for g in understanding.goals) and not understanding.context_references:
        # 作品说明：独立概念定义使用本轮指标集合，财务任务条件继续保留供明确恢复。
        state.conditions.metrics=[]
    independent_context=len({str(value.model_dump(exclude={'origins'})) for value in goal_context.values()})>1
    for goal in understanding.goals:
        if goal.context_goal_id:
            if understanding.continuity not in {'continue','resume'} or goal.context_goal_id not in goal_context:
                invalid=InvalidUnderstanding('目标引用没有对应的已确认财务条件。');invalid.repair_goals=True
                raise invalid
            goal.context_conditions=goal_context[goal.context_goal_id].model_copy(deep=True)
            for origin in goal.context_conditions.origins.values():
                if origin.kind=='current':origin.kind='context'
        elif goal.kind in FINANCIAL_KINDS and independent_context and understanding.continuity in {'continue','resume'}:
            invalid=InvalidUnderstanding('上一轮有分别绑定的独立目标，本轮须用context_goal_id明确沿用哪些目标；不能合并公司与指标。');invalid.repair_goals=True
            raise invalid
    if understanding.topic=='financial' and not understanding.goals:
        invalid=InvalidUnderstanding('A financial condition modification must retain an executable goal; an empty plan cannot fulfill a follow-up')
        invalid.repair_goals=True
        raise invalid
    if understanding.topic in {'rules','catalog'} and understanding.unsupported and not understanding.unknown_companies:
        raise InvalidUnderstanding('System rules and supported capabilities cannot be rejected for missing financial query conditions')
    try:
        state.conditions=apply_edits(state.conditions,understanding.modifications,question,turn_id)
        if any(edit.field=='scope' and edit.operation!='inherit' for edit in understanding.modifications):
            state.legacy_scope_unknown=False
    except ValueError as exc:
        if isinstance(exc,InvalidUnderstanding):raise
        error=InvalidUnderstanding('Condition value violates v3 contract: '+str(exc).split('\n')[0])
        error.details=exc.errors(include_input=False,include_context=False,include_url=False) if hasattr(exc,'errors') else []
        raise error from exc
    state.topic = understanding.topic
    state.modifications = deepcopy(understanding.modifications)
    c = state.conditions
    clarification = list(understanding.clarification)
    unsupported = list(understanding.unsupported)
    for reference in understanding.context_references:
        cleared_company=reference.target=='company' and any(edit.field in {'company_context','financial_context'} for edit in understanding.memory_edits)
        if reference.text not in question or understanding.continuity not in {'continue','resume'} and not cleared_company:
            raise InvalidUnderstanding('Context reference must bind to an explicit current span and retained context')
        if reference.target=='company' and reference.number=='singular' and len(confirmed_codes)!=1:
            clarification.append('这次“'+reference.text+'”指哪家公司？请明确公司名称或股票代码。')
            c.codes=[]
            for goal in understanding.goals:
                if goal.selection:goal.selection.codes=[]
        if reference.target=='fact' and reference.number=='singular' and len(previous.recent_facts)+len(previous.recent_computed)!=1:
            clarification.append('这次“'+reference.text+'”指哪个已核实数值？请明确公司、指标和报告期。')
        if reference.target=='financial_task' and not previous.active_goals and not previous.pending:
            clarification.append('当前没有唯一可恢复的财务任务，请明确公司、指标和报告期。')
    for mention in understanding.company_mentions:
        if mention.text not in question:
            raise InvalidUnderstanding('Company mention is not a literal span of the current input')
        matched={code for code,name in companies.items() if code in mention.text or name in mention.text}
        if mention.kind=='explicit' and set(mention.codes)!=matched:
            raise InvalidUnderstanding('Explicit company binding does not match the registered name/code in its source span')
        if mention.kind=='context' and (understanding.continuity not in {'continue','resume'} or not mention.codes or not set(mention.codes) <= confirmed_codes):
            raise InvalidUnderstanding('Company reference has no unique confirmed context')
        if mention.kind=='uncovered':
            if mention.text in companies or mention.text in companies.values() or mention.codes:
                raise InvalidUnderstanding('Uncovered company binding contradicts the registered identities')
            understanding.unknown_companies=list(dict.fromkeys([*understanding.unknown_companies,mention.text]))
        if mention.kind in {'ambiguous','candidate'}:
            clarification.append('请明确公司名称或股票代码。' if mention.kind=='ambiguous' else '请确认公司名称：'+mention.text+'。')
    if understanding.unknown_companies and any(g.kind in FINANCIAL_KINDS for g in understanding.goals):
        unsupported.append('当前库未覆盖：' + '、'.join(understanding.unknown_companies))
    invalid_codes = [code for code in c.codes if code not in companies]
    if invalid_codes:
        unsupported.append('当前库未覆盖公司代码：' + '、'.join(invalid_codes))
    invalid_metrics = [metric for metric in c.metrics if metric not in METRICS]
    if invalid_metrics:
        unsupported.append('当前未支持指标：' + '、'.join(invalid_metrics))
    financial_kinds = {'lookup', 'compare', 'rank', 'chart', 'quote', 'cause', 'sign'}
    financial = any(g.kind in financial_kinds for g in understanding.goals)
    shared_financial = any(g.kind in financial_kinds and not g.selection and not g.condition_edits and not g.context_conditions for g in understanding.goals)
    if not financial and understanding.goals and all(g.kind in {'rules','catalog'} for g in understanding.goals):
        # 作品说明：系统规则和目录不需要财务查询条件；模型提出的未来查询建议不是当前任务前提，引用歧义另行记录。
        unresolved_refs=[g for g in understanding.goals if g.execution_ref and not any(e.turn_id==g.execution_ref for e in previous.executions)]
        if not unresolved_refs: clarification=[]
    for term,candidates in AMBIGUOUS_TERMS.items():
        # 作品说明：指标目录建立身份与口径，不构成第二条语义路由；明确的已确认成员可继续沿用。
        explicit=any(alias in question for alias,metric in ALIASES.items() if metric in candidates)
        inherited=understanding.continuity in {'continue','resume'} and bool(context_conditions.metrics) and set(context_conditions.metrics)<=set(candidates)
        if financial and term in question and not explicit and not inherited:
            clarification.append('请明确现金流指标：经营活动、投资活动、筹资活动，还是现金及现金等价物净增加额？')
            c.metrics=[metric for metric in c.metrics if metric not in candidates]
            for goal in understanding.goals:
                if goal.selection and goal.selection.metrics:
                    goal.selection.metrics=[metric for metric in goal.selection.metrics if metric not in candidates]
    if financial and not c.restrictions.no_query and state.legacy_scope_unknown:
        clarification.append('旧会话没有可靠记录报表范围，请确认采用合并报表还是母公司报表。')
    if shared_financial and not c.restrictions.no_query and not unsupported:
        if not c.codes and not c.all_companies:
            clarification.append('请明确要查询哪家公司。')
        if not c.metrics and not any(term in question for term in AMBIGUOUS_TERMS):
            clarification.append('请明确要查询哪个财务指标。')
    if shared_financial and not c.restrictions.no_query and c.time.single_quarter:
        unsupported.append('当前只支持报告披露的累计口径，暂不支持单季查询。')
    if shared_financial and not c.restrictions.no_query and c.time.mode in {'explicit', 'calendar_years'} and not c.time.years and not c.time.pairs and not (c.time.mode=='calendar_years' and c.time.span):
        clarification.append('请明确需要的年份。')
    if shared_financial and not c.restrictions.no_query and c.scope == 'parent' and any(m in METRICS and 'parent' not in METRICS[m].scopes for m in c.metrics):
        clarification.append('归母、扣非归母和每股指标属于合并归属口径；是否改为母公司净利润？')
    if shared_financial and not c.restrictions.no_query and c.presentation.unit:
        from .units import presentation_unit_compatible
        for metric in c.metrics:
            m = METRICS.get(metric)
            if m and not presentation_unit_compatible(c,metric):
                clarification.append(f'{m.label}单位为{m.unit}，不能换算为{c.presentation.unit}。')
    if c.presentation.format == 'table':
        c.restrictions.no_chart = True
    if c.restrictions.no_chart and (c.presentation.format == 'chart' or any(g.kind == 'chart' for g in understanding.goals)):
        clarification.append('同时要求只要图和不画图，请明确输出形式。')
    if set(c.codes) & set(c.restrictions.excluded_codes) or set(c.metrics) & set(c.restrictions.excluded_metrics):
        clarification.append('选择条件与排除条件冲突，请明确保留哪些。')
    if c.all_companies:
        collection_basis=any(m.kind=='collection' and any(marker in m.text for marker in
            ('所有','全部','全库','库内','库里','库中','全体','数据库内','数据库里')) for m in understanding.company_mentions)
        if financial and not collection_basis and not (understanding.continuity in {'continue','resume'} and context_conditions.all_companies):
            raise InvalidUnderstanding('An all-company selection requires an explicit current collection or a confirmed inherited collection')
        c.codes = [code for code in companies if code not in c.restrictions.excluded_codes]
    from .condition_defaults import finalize_conditions
    c=finalize_conditions(c,[g.kind for g in understanding.goals],turn_id)
    state.conditions=c
    request_conditions = c.model_copy(deep=True)
    if not financial and understanding.topic in {'concept', 'rules'}:
        # 作品说明：概念或规则插话有独立的临时选择，财务公司、金额单位和范围留在挂起条件中。
        if any(ref.target=='fact' for ref in understanding.context_references):
            request_conditions=c.model_copy(deep=True)
            request_conditions.restrictions.no_query=True
            request_conditions.presentation.unit=None
            if not any(edit.field=='presentation.format' for edit in understanding.modifications):request_conditions.presentation.format='auto'
        else:
            request_conditions = Conditions(metrics=c.metrics, restrictions={'no_query':True},
                origins={key:origin for key,origin in c.origins.items() if key == 'metrics'})
            # 作品说明：保留插话本轮明确的口径和展示要求，同时保留原财务选择用于后续恢复。
            for key,origin in c.origins.items():
                if origin.kind!='current' or origin.turn_id!=turn_id:continue
                if key=='scope':request_conditions.scope=c.scope
                elif key.startswith('presentation.'):
                    field=key.split('.',1)[1];setattr(request_conditions.presentation,field,getattr(c.presentation,field))
                elif key.startswith('restrictions.'):
                    field=key.split('.',1)[1];setattr(request_conditions.restrictions,field,getattr(c.restrictions,field))
                else:continue
                request_conditions.origins[key]=origin.model_copy(deep=True)
            request_conditions.restrictions.no_query=True
    request = Request(turn_id=turn_id, question=question, continuity=understanding.continuity, conditions=request_conditions,
                      modifications=deepcopy(understanding.modifications),
                      memory_edits=deepcopy(understanding.memory_edits),
                      goals=deepcopy(understanding.goals), clarification=list(dict.fromkeys(clarification)),
                      context_references=understanding.context_references,
                      unsupported=list(dict.fromkeys(unsupported)),company_mentions=understanding.company_mentions)
    if shared_financial and not c.metrics:
        menus={menu.family:menu.model_copy(deep=True) for menu in (previous.pending.clarification_choices
            if previous.pending and understanding.continuity in {'continue','resume'} else [])}
        menus.update({family:ClarificationChoices(family=family,options=choices)
            for family,choices in FAMILY_CHOICES.items() if family in question})
        request.clarification_choices=list(menus.values())
    # 作品说明：同一有效财务选择成为当前对话条件；真正独立的例外保留目标绑定，避免局部条件在追问时丢失。
    financial_goals=[g for g in request.goals if g.kind in financial_kinds]
    selected=[request.for_goal(g).conditions for g in financial_goals]
    for goal in financial_goals:
        if goal.selection and goal.selection.time:
            validate_year_change(question,request.conditions.time.model_dump(),patch_values('time',goal.selection.time))
    if selected and all(value==selected[0] for value in selected) and any(g.selection or g.condition_edits or g.context_conditions for g in financial_goals):
        from .contracts import MODIFICATION
        promoted={}
        for goal in financial_goals:
            if goal.condition_edits:
                state.modifications.extend(deepcopy(goal.condition_edits))
            if not goal.selection: continue
            for key in goal.selection.model_fields_set:
                value=getattr(goal.selection,key)
                if value is None: continue
                if key in {'time','presentation'}:
                    promoted.setdefault(key,{}).update(patch_values(key,value))
                else: promoted[key]=value
        request.conditions=selected[0]
        state.conditions=selected[0].model_copy(deep=True)
        c=state.conditions
        for key,value in promoted.items():
            state.modifications.append(MODIFICATION.validate_python(dict(field=key,operation='replace',value=value,text=question)))
            c.origins[key]=Origin(kind='current',text=question,turn_id=turn_id)
            if key in {'time','presentation'}:
                for subfield in value: c.origins[key+'.'+subfield]=Origin(kind='current',text=question,turn_id=turn_id)
        if c.time.mode=='latest': c.time.mode='latest_common' if any(g.kind in {'compare','rank'} for g in financial_goals) else 'latest_each'
        if c.presentation.format=='table': c.restrictions.no_chart=True
        request.conditions=c.model_copy(deep=True)
        for goal in financial_goals: goal.selection=None;goal.condition_edits=[];goal.context_conditions=None
    elif selected:
        # 作品说明：仅一个集合维度不同时可记住公共选择；公司和指标都不同时继续分目标保存，避免扩大成交叉组合。
        for field in ('metrics','codes'):
            comparable=[value.model_dump(exclude={field,'origins'}) for value in selected]
            if all(value==comparable[0] for value in comparable):
                common=selected[0].model_copy(deep=True)
                setattr(common,field,list(dict.fromkeys(item for value in selected for item in getattr(value,field))))
                common.origins[field]=Origin(kind='current',text=question,turn_id=turn_id)
                request.conditions=common;state.conditions=common.model_copy(deep=True);c=state.conditions
                break
    if financial_goals:
        state.active_goals=deepcopy(financial_goals)
        state.topic='financial'
        if state.interrupted_request and understanding.continuity in {'continue','resume'}:
            from .request_bindings import metric_candidates
            named_codes={code for code,name in companies.items() if code in question or name in question}
            named_metrics=metric_candidates(question)
            actual_codes={code for g in financial_goals for code in request.for_goal(g).conditions.codes}
            actual_metrics={metric for g in financial_goals for metric in request.for_goal(g).conditions.metrics}
            confirmed=set(state.interrupted_request.confirmed_fields)
            if named_codes and actual_codes<=named_codes:confirmed.add('codes')
            if named_metrics and actual_metrics and actual_metrics<=named_metrics:confirmed.add('metrics')
            state.interrupted_request.confirmed_fields=sorted(confirmed)
            uncertain=state.interrupted_request.uncertain_paths
            if uncertain is None:
                missing={'codes','metrics'}-confirmed
            else:
                resolved={edit.field for edit in request.modifications if edit.operation!='inherit'}
                resolved.update(edit.field for goal in financial_goals for edit in goal.condition_edits if edit.operation!='inherit')
                missing=set(uncertain)-resolved
                state.interrupted_request.uncertain_paths=sorted(missing)
            if missing:
                labels={'codes':'公司','metrics':'指标','scope':'报表口径','all_companies':'公司范围','calculation':'计算要求','comparison_axis':'比较对象'}
                issue='上一轮修改尚未确认，请明确'+ '、'.join(dict.fromkeys(labels.get(path,'时间要求' if path.startswith('time.') else '展示要求' if path.startswith('presentation.') else '执行限制') for path in sorted(missing)))+'；其他已确认条件继续保留。'
                request.clarification.append(issue)
                for goal in financial_goals:goal.clarification.append(issue)
            else:state.interrupted_request=None
    # 作品说明：类型化能力检查保留独立可执行目标；不支持的选择作为明确任务结果，而非服务故障。
    for goal in request.goals:
        if goal.kind not in financial_kinds:
            continue
        gc = request.for_goal(goal).conditions
        unavailable = []
        if gc.time.single_quarter:
            unavailable.append('当前只支持报告披露的累计口径，暂不支持单季查询。')
        if any(code not in companies for code in gc.codes):
            unavailable.append('当前库未覆盖该目标的公司。')
        if any(metric not in METRICS for metric in gc.metrics):
            unavailable.append('当前未支持该目标的指标。')
        only_uncovered=bool(understanding.company_mentions) and all(m.kind=='uncovered' for m in understanding.company_mentions)
        if understanding.unknown_companies and (not gc.codes and not gc.all_companies or only_uncovered):
            unavailable.append('当前库未覆盖：'+'、'.join(understanding.unknown_companies))
        if unavailable:
            goal.kind = 'unsupported'
            request.unsupported.extend(unavailable)
    # 作品说明：能力结论依据登记身份；明确库外名称不能误报为需要用户再次指定公司。
    unknown_names=set(understanding.unknown_companies)
    request.clarification=[item for item in request.clarification if item not in unknown_names]
    if unknown_names and not any(g.kind in financial_kinds for g in request.goals) and not any(m.kind in {'ambiguous','candidate'} for m in understanding.company_mentions):
        request.clarification=[]
        if all(m.kind=='uncovered' for m in understanding.company_mentions):
            request.conditions.codes=[];state.conditions.codes=[]
    if request.unsupported and not any(g.kind=='unsupported' for g in request.goals):
        index=1
        while any(g.id==f'unsupported{index}' for g in request.goals):
            index+=1
        request.goals.append(Goal(id=f'unsupported{index}',kind='unsupported',text='；'.join(request.unsupported)))
    # 作品说明：未登记身份移出可执行和确认条件，原始代码保留在不支持说明中。
    request.conditions.codes=[code for code in request.conditions.codes if code in companies]
    state.conditions.codes=[code for code in state.conditions.codes if code in companies]
    for goal in request.goals:
        if goal.selection and goal.selection.codes is not None:
            goal.selection.codes=[code for code in goal.selection.codes if code in companies]
    # 作品说明：分目标公司、期间、指标和范围同样验证，不能将所有提及身份任意交叉查询。
    for goal in request.goals:
        if goal.kind not in financial_kinds:
            continue
        gc = request.for_goal(goal).conditions
        issues=goal.clarification
        if not gc.restrictions.no_query:
            prefix=f'{goal.id} ' if goal.selection else ''
            if not gc.codes and not gc.all_companies:
                issues.append(prefix+'请明确要查询哪家公司。')
            if not gc.metrics and not any(term in question for term in AMBIGUOUS_TERMS):
                issues.append(prefix+'请明确要查询哪个财务指标。')
        if any(code not in companies for code in gc.codes) or any(metric not in METRICS for metric in gc.metrics):
            raise InvalidUnderstanding('Goal selection contains an unknown registered identity')
        if gc.scope == 'parent' and any('parent' not in METRICS[m].scopes for m in gc.metrics):
            issues.append(f'{goal.id} 的指标不适用于母公司报表，请明确口径。')
        if gc.time.mode in {'explicit', 'calendar_years'} and not gc.time.years and not gc.time.pairs and not (gc.time.mode=='calendar_years' and gc.time.span):
            issues.append(f'{goal.id} 请明确需要的年份。')
        if set(gc.codes) & set(gc.restrictions.excluded_codes) or set(gc.metrics) & set(gc.restrictions.excluded_metrics):
            issues.append(f'{goal.id} 的选择与本轮排除条件冲突。')
        if gc.restrictions.no_chart and goal.kind == 'chart':
            issues.append(f'{goal.id} 与不画图的要求冲突。')
        from .units import presentation_unit_compatible
        if gc.presentation.unit and any(not presentation_unit_compatible(gc,m) for m in gc.metrics):
            issues.append(f'{goal.id} 的指标与输出单位不一致。')
    # 作品说明：公司选择须有登记身份或明确上下文依据；泛称公司不默认表示全库。
    old_codes=set(context_conditions.codes)
    old_metrics=set(context_conditions.metrics)
    if understanding.continuity in {'continue','resume'}:
        old_codes.update(code for value in goal_context.values() for code in value.codes)
        old_metrics.update(metric for value in goal_context.values() for metric in value.metrics)
    normalized=''.join(question.split()).casefold()
    # 作品说明：校验财务选择的身份依据，目标和指标含义仍由模型提案表达。
    financial_terms=(*ALIASES,'利润','收入','现金流','赚','盈利','亏损','资产','负债','收益')
    has_metric_basis=any(term.casefold() in normalized for term in financial_terms)
    selected_metrics={metric for g in request.goals if g.kind in financial_kinds for metric in request.for_goal(g).conditions.metrics}
    # 作品说明：用户可用已给出的有限选项短名回答澄清；只核对所选身份，不从仅年份回复猜指标。
    pending_choices={metric for menu in (previous.pending.clarification_choices if previous.pending else [])
        for label,metric in menu.options.items() if label in question}
    if len(pending_choices)==1 and selected_metrics==pending_choices:
        has_metric_basis=True
    inherited_metrics=understanding.continuity in {'continue','resume'} and selected_metrics <= old_metrics
    if selected_metrics and not has_metric_basis and not inherited_metrics:
        if selected_metrics <= set(previous.conditions.metrics) and understanding.continuity=='new':
            raise InvalidUnderstanding('A new task cannot select a metric only from previous context; a condition change requires continue')
        request.clarification.append('请明确需要的财务指标，例如营业收入、归母净利润或总资产。')
        request.conditions.metrics=[];state.conditions.metrics=[]
        for goal in request.goals:
            if goal.selection:goal.selection.metrics=[]
    explicit_codes={code for code,name in companies.items() if code in normalized or name.casefold() in normalized}
    selected_codes={code for g in request.goals if g.kind in financial_kinds for code in request.for_goal(g).conditions.codes}
    from .request_bindings import explicit_company_replacements
    replacements=explicit_company_replacements(question,companies)
    if replacements and financial:
        expected=set(old_codes)
        for removed,added in replacements:expected.discard(removed);expected.add(added)
        if selected_codes!=expected:
            raise InvalidUnderstanding('明确公司纠正必须只移除被纠正的公司并加入新公司，其余已确认公司保持不变；预期代码：'+','.join(sorted(expected)))
    supported_codes=explicit_codes | (old_codes if understanding.continuity in {'continue','resume'} else set())
    if not c.all_companies and selected_codes - supported_codes:
        if understanding.continuity=='new' and selected_codes <= set(previous.conditions.codes):
            raise InvalidUnderstanding('A new task cannot select a company only from previous context; a condition change requires continue')
        request.clarification.append('请明确公司名称或股票代码。')
        # 作品说明：没有确认的公司建议不进入待澄清条件，避免后续只改年份就执行猜测公司。
        request.conditions.codes=[code for code in request.conditions.codes if code in supported_codes]
        state.conditions.codes=[code for code in state.conditions.codes if code in supported_codes]
        for goal in request.goals:
            if goal.selection and goal.selection.codes is not None:
                goal.selection.codes=[code for code in goal.selection.codes if code in supported_codes]
    request.unsupported=list(dict.fromkeys(request.unsupported))
    request.clarification=list(dict.fromkeys(request.clarification))
    # 作品说明：共享歧义仅阻塞依赖它的财务目标；局部目标独立检查，概念说明可以在查数待澄清时交付。
    for goal in request.goals:
        shared_issue=goal.kind in financial_kinds and goal.selection is None and not goal.condition_edits and not goal.context_conditions
        fact_issue=goal.kind=='concept' and goal.concept_mode=='implication' and any(ref.target=='fact' for ref in request.context_references)
        if shared_issue or fact_issue:goal.clarification.extend(request.clarification)
        goal.clarification=list(dict.fromkeys(goal.clarification))
    request.clarification=list(dict.fromkeys([*request.clarification,*(issue for goal in request.goals for issue in goal.clarification)]))
    state.pending = request if request.clarification else None
    if financial_goals:
        state.goal_conditions={g.id:request.for_goal(g).conditions for g in request.goals if g.kind in FINANCIAL_KINDS}
    if request.clarification and understanding.continuity != 'clear':
        state.conditions = previous.conditions.model_copy(deep=True)
    if not financial and understanding.topic in {'concept', 'rules','catalog','greeting','other'} and understanding.continuity != 'clear':
        # 作品说明：插话中的不查数限制或概念指标不覆盖挂起的财务条件，明确返回时可恢复原任务。
        state.conditions = previous.conditions.model_copy(deep=True)
        state.pending=previous.pending.model_copy(deep=True) if previous.pending else None
    # 作品说明：本轮事实引用仅在执行及核验成功后更新。
    if financial and not c.restrictions.no_query or understanding.topic=='financial' and request.unsupported:
        state.recent_facts = []
        state.recent_computed = []
    return request, state
