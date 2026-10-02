"""作品说明：以异步LangGraph串联理解、请求检查、执行、证据审核和发布，财务正文在核验后交付。"""
from __future__ import annotations
from src.etl.release_profiles import release_profile

import asyncio
import time
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .catalog import METRICS,metric_definition
from .contracts import Check, Execution, FactReference, GoalResult, Verification
from .evidence import AUDIT, EXPLAIN, EvidenceReview, Explanations, NarrativeRetriever, numeric_quote, safe_narrative_claim
from .executor import ExecutionResult, execute
from .model import AsyncModel, Budget, ModelFailure
from .planner import Planner
from .rendering import chart_compat, render_financial, render_rules, render_catalog, render_fact_implication, verification_compat
from .repository import CanonicalRepository
from .state import InvalidUnderstanding, apply_understanding, read_state
from .verification import verify
from .value_references import revalidate_facts, revalidate_computed, computed_references, visible_fact_references
from .rendering import render_computed_implication,table_only_content
from .request_bindings import requirement_source_supported


@dataclass(frozen=True)
class AgentEvent:
    type: str
    data: dict


class GraphState(TypedDict, total=False):
    question: str
    turn_id: str
    history: list[dict]
    budget: Budget
    previous: object
    understanding: object
    request: object
    dialogue: object
    semantic: object
    repository: object
    executed: ExecutionResult
    references: list[dict]
    evidence_checks: list[Check]
    prose: list[str]
    verification: Verification
    result: dict
    emit: object
    context_facts: list
    context_computed: list
    context_reference_failed: bool
    data_query_executed: bool


class V3Agent:
    def __init__(self, engine, companies: dict[str, str], model=None, repository_factory=CanonicalRepository, retriever_factory=NarrativeRetriever):
        self.engine = engine
        self.companies = companies
        self.model = model or AsyncModel()
        self.planner = Planner(self.model, companies)
        self.repository_factory = repository_factory
        self.retriever_factory = retriever_factory
        graph = StateGraph(GraphState)
        for name, node in [('understand', self._understand), ('audit_request', self._review),
                           ('execute', self._execute), ('evidence', self._evidence),
                           ('verify', self._verify), ('publish', self._publish)]:
            graph.add_node(name, node)
        graph.add_edge(START, 'understand')
        graph.add_edge('understand', 'audit_request')
        graph.add_conditional_edges('audit_request', lambda s: 'publish' if s['request'].clarification and
            not any(not goal.clarification for goal in s['request'].goals) else 'execute')
        graph.add_edge('execute', 'evidence')
        graph.add_edge('evidence', 'verify')
        graph.add_edge('verify', 'publish')
        graph.add_edge('publish', END)
        self.graph = graph.compile()

    async def _understand(self, s):
        await s['emit']('plan', {'label': '理解本轮要求', 'detail': '整理目标和明确条件修改'})
        previous = read_state(s['history'])
        understanding=None
        try:
            understanding = await self.planner.understand(s['question'], previous, s['history'], s['budget'])
            request, dialogue = apply_understanding(s['question'], s['turn_id'], understanding, previous, self.companies)
        except InvalidUnderstanding as exc:
            if s['budget'].artifacts.get('proposal_repair_used'):raise
            s['budget'].artifacts['proposal_repair_used']=True
            understanding = await self.planner.understand(s['question'], previous, s['history'], s['budget'],
                [dict(error=str(exc),repair_goals=getattr(exc,'repair_goals',False),invalid_proposal=understanding.model_dump(mode='json') if understanding else getattr(exc,'request',None)),
                 *[str(e.get('loc')) + ': ' + e.get('msg','') for e in getattr(exc, 'details', [])]])
            try:
                request, dialogue = apply_understanding(s['question'], s['turn_id'], understanding, previous, self.companies)
            except InvalidUnderstanding as invalid:
                invalid.audit={'stage':'condition_compilation','message':str(invalid),'details':getattr(invalid,'details',[])}
                invalid.request=understanding.model_dump(mode='json')
                raise
        if len({g.id for g in request.goals}) != len(request.goals):
            raise InvalidUnderstanding('Duplicate goal IDs')
        await s['emit']('plan',{'label':'已整理请求，等待核对','_request_checkpoint':dict(request=request.model_dump(mode='json'),approved=False)})
        return dict(previous=previous, understanding=understanding, request=request, dialogue=dialogue)

    @staticmethod
    def _review_exact_bindings(request, semantic):
        """作品说明：每个模型提案都经过相同的程序条件检查，重做提案同样受约束。"""
        planned={goal.id:goal.kind for goal in request.goals}
        numeric={'lookup','compare','rank','sign','cause'}
        for requirement in semantic.goal_requirements:
            if semantic.audit_mode!='program' and not requirement_source_supported(requirement,request):
                requirement.covered=False;semantic.goals_correct=False
                semantic.planner_defects.append('审核目标缺少本轮原话依据：'+requirement.kind+'。')
            mapped={planned.get(id) for id in requirement.goal_ids}
            # 作品说明：目标覆盖表示计划已登记该要求；条件尚缺时保留待澄清目标，不能据此授权发布财务结果。
            waiting=bool(requirement.goal_ids) and all(any(g.id==id and g.clarification for g in request.goals) for id in requirement.goal_ids)
            if waiting and mapped=={requirement.kind} and requirement_source_supported(requirement,request):
                requirement.covered=True
            valid=all(id in planned for id in requirement.goal_ids) and requirement.covered
            if requirement.kind=='other':
                valid=valid and (not mapped or mapped=={'unsupported'})
            if requirement.kind not in {'greeting','other'}:
                compatible={requirement.kind} | (numeric | {'chart'} if requirement.kind=='lookup' else set())
                if requirement.kind=='rules' and any(g.id in requirement.goal_ids and g.kind=='catalog' and g.catalog_target=='capabilities' for g in request.goals):
                    compatible.add('catalog')
                valid=valid and bool(mapped & compatible)
            if not valid:
                requirement.covered=False
                semantic.goals_correct=False
                semantic.planner_defects.append('目标类型未被实际计划覆盖：'+requirement.kind+'；关联计划编号：'+','.join(requirement.goal_ids)+'。')
        required_codes={code for m in request.company_mentions if m.kind in {'explicit','context'} for code in m.codes}
        financial=[g for g in request.goals if g.kind in {'lookup','compare','rank','chart','quote','cause','sign'}]
        from .request_bindings import is_negated_span
        import re
        explicit_compare=any(not is_negated_span(request.question,m.start(),m.end()) for m in re.finditer(r'比较|对比|比一比|谁更(?:高|低|多|少)|哪家更(?:高|低|多|少)',request.question))
        if financial and explicit_compare and not any(g.kind=='compare' for g in financial) and not request.clarification:
            semantic.goals_correct=False
            semantic.planner_defects.append('原话明确要求财务比较，须登记compare目标并给出比较结论，不能只罗列数值。')
            if 'intent' not in semantic.repair_domains:semantic.repair_domains.append('intent')
        actual_codes={code for g in financial for code in request.for_goal(g).conditions.codes}
        from .request_bindings import explicit_presentation_bindings,explicit_collection_span
        bindings=explicit_presentation_bindings(request.question)
        for field,value in bindings.items():
            applicable=[g for g in financial if g.kind==('chart' if field.endswith('chart_type') else 'rank')]
            if field.endswith('chart_type') and financial and not applicable and not request.clarification:
                semantic.goals_correct=False
                semantic.planner_defects.append('原话明确指定图形，计划必须保留chart目标。')
            if applicable and any(getattr(request.for_goal(g).conditions.presentation,field.split('.')[-1])!=value for g in applicable):
                semantic.presentation_correct=False
                semantic.planner_defects.append('明确展示条件未保留：'+field+'='+str(value))
        if financial and explicit_collection_span(request.question) and not request.clarification and any(not request.for_goal(g).conditions.all_companies for g in financial):
            semantic.companies_correct=False
            semantic.planner_defects.append('明确全库范围必须保留all_companies，不能要求用户重复指定公司。')
        from .request_bindings import explicit_company_set_constraints,company_set_is_global
        registry={code:[m.text for m in request.company_mentions if m.kind=='explicit' and code in m.codes]
            for code in required_codes}
        company_sets=explicit_company_set_constraints(request.question,registry)
        exact_keep=company_sets['keep'] and company_set_is_global(request.question,registry,company_sets)
        wrong_keep=(actual_codes!=company_sets['keep'] if exact_keep else bool(company_sets['keep']-actual_codes))
        if financial and (wrong_keep or actual_codes & company_sets['exclude']) and not request.clarification:
            semantic.companies_correct=False
            semantic.planner_defects.append('明确仅保留或移除的公司没有按登记身份执行，不能反向删除要保留的公司。')
        removed={code for edit in request.modifications if edit.field=='codes' and edit.operation=='remove' for code in edit.value}
        retained=set().union(*(set(edit.value) for edit in request.modifications if edit.field=='codes' and edit.operation=='keep'))
        excluded=company_sets['exclude']|removed|set(request.conditions.restrictions.excluded_codes)
        positive_codes={code for mention in request.company_mentions if mention.kind=='explicit' and not any(
            is_negated_span(request.question,m.start(),m.end()) for m in re.finditer(re.escape(mention.text),request.question)) for code in mention.codes}-excluded
        if retained:positive_codes &= retained
        if financial and positive_codes and not actual_codes:
            semantic.planner_defects.append('公司身份已由原话明确识别，财务条件仍为空；必须把该身份写入条件。')
            semantic.companies_correct=False
        if financial and positive_codes-actual_codes and not request.clarification:
            semantic.companies_correct=False
            semantic.planner_defects.append('明确公司集合有遗漏，不能只保留最后一次公司替换。')
        from .catalog import ALIASES
        actual_metrics={m for g in financial for m in request.for_goal(g).conditions.metrics}
        if financial and not actual_metrics and any(alias in request.question for alias in ALIASES):
            semantic.planner_defects.append('原话已明确财务指标，条件中的指标为空；不能询问用户重复提供。')
            semantic.metrics_and_scope_correct=False
        from .request_bindings import metric_candidates
        explicit_metrics=metric_candidates(request.question,positive_only=True)
        all_metrics={m for g in request.goals for m in request.for_goal(g).conditions.metrics}
        if financial and explicit_metrics-all_metrics and not request.clarification:
            semantic.metrics_and_scope_correct=False
            semantic.planner_defects.append('明确指标未在请求中登记，不能用相近指标替代：'+','.join(sorted(explicit_metrics-all_metrics)))
        concept_goals=[g for g in request.goals if g.kind=='concept' and g.concept_mode in {'definition','difference'}]
        if concept_goals and any(alias in request.question for alias in ALIASES) and any(not request.for_goal(g).conditions.metrics for g in concept_goals):
            semantic.metrics_and_scope_correct=False
            semantic.planner_defects.append('概念已对应指标目录，定义目标不能遗漏该指标。')
        if any(g.concept_mode=='difference' and len(set(request.for_goal(g).conditions.metrics))<2 for g in concept_goals):
            semantic.goals_correct=False
            semantic.planner_defects.append('概念区别目标需要两个明确指标，单个指标不能冒充概念对比。')
            if 'intent' not in semantic.repair_domains:semantic.repair_domains.append('intent')
        from .request_bindings import explicit_output_unit,explicit_period_bindings,explicit_single_quarters,positive_period_mentions,positive_calendar_mentions
        unit=explicit_output_unit(request.question)
        if financial and unit and any(request.for_goal(goal).conditions.presentation.unit!=unit for goal in financial):
            semantic.presentation_correct=False
            semantic.planner_defects.append('明确输出单位未登记到条件：'+unit+'。')
        if financial and any(word in request.question for word in ('只要表格','只要表','只给表格','列个表','列成表','用表格')) and (
            any(request.for_goal(goal).conditions.presentation.format!='table' for goal in financial) or any(goal.kind=='chart' for goal in financial)):
            semantic.presentation_correct=False
            if any(goal.kind=='chart' for goal in financial):semantic.goals_correct=False
            semantic.planner_defects.append('表格要求必须保存在format=table，不能新增画图目标。')
        parent_explicit=bool(re.search(r'母公司(?:单体|口径|营业收入|净利润|利润表|报表)',request.question))
        if financial and parent_explicit and any(request.for_goal(g).conditions.scope!='parent' for g in financial) and not request.clarification:
            semantic.metrics_and_scope_correct=False
            semantic.planner_defects.append('明确母公司范围未保留到报表口径。')
        for pattern,field in ((r'(?:不|别|不要|先别)(?:重新|再次|再)?(?:查数|查数字|查询数字)','no_query'),
                              (r'(?:不|别|不要)(?:再)?(?:画图|生成图|绘图)','no_chart'),
                              (r'(?:不|别|不要)(?:再)?重复(?:金额|数字)','no_repeat')):
            if re.search(pattern,request.question) and any(not getattr(request.for_goal(g).conditions.restrictions,field) for g in request.goals):
                semantic.constraints_correct=False
                semantic.planner_defects.append('明确否定限制未保留：'+field)
        bound=explicit_period_bindings(request.question)
        named_periods={period for _,_,period in positive_period_mentions(request.question)}
        if financial and named_periods and not bound and any(set(request.for_goal(g).conditions.time.periods)!=named_periods for g in financial):
            semantic.time_correct=False
            semantic.planner_defects.append('明确累计报告期没有落实；修改报告种类须保留未修改年份。')
        if financial and bound:
            planned_periods=set()
            for goal in financial:
                time_selection=request.for_goal(goal).conditions.time
                planned_periods.update(time_selection.pairs or [(year,period) for year in time_selection.years for period in time_selection.periods])
            if planned_periods!=bound:
                semantic.time_correct=False
                semantic.planner_defects.append('原话明确绑定了年份与报告期，须逐项配对，不能遗漏或扩大为其他组合。')
        quarters=explicit_single_quarters(request.question)
        if financial and quarters and any(not request.for_goal(goal).conditions.time.single_quarter or
            set(request.for_goal(goal).conditions.time.quarters)!=quarters for goal in financial):
            semantic.time_correct=False
            semantic.planner_defects.append('明确单季身份必须保存季度编号及single_quarter，不能以累计或未指定季度代替。')
        from .condition_updates import mentioned_years
        from .request_bindings import explicit_report_count
        count=explicit_report_count(request.question)
        if financial and count is not None and any(request.for_goal(g).conditions.time.span!=count for g in financial):
            semantic.time_correct=False
            semantic.planner_defects.append('明确报告或年数未保留到time.span，不能只查一个最新年份。')
        if financial and count is not None and positive_calendar_mentions(request.question) and any(request.for_goal(g).conditions.time.mode!='calendar_years' for g in financial):
            semantic.time_correct=False
            semantic.planner_defects.append('明确自然年窗口必须使用calendar_years，不能改成最新入库年份。')
        years=mentioned_years(request.question)
        if financial and years:
            for goal in financial:
                selected=request.for_goal(goal).conditions
                planned_years={year for year,_ in selected.time.pairs} if selected.time.pairs else set(selected.time.years)
                if (len(years)==1 or selected.calculation=='none' and not bound) and not years<=planned_years:
                    semantic.time_correct=False
                    semantic.planner_defects.append('明确年份必须保存到正式条件，不能用当前最新报告期碰巧相同代替。')
                    break
        declared={id for requirement in semantic.goal_requirements for id in requirement.goal_ids}
        if any(goal.id not in declared and goal.kind!='unsupported' for goal in request.goals):
            semantic.goals_correct=False
            semantic.planner_defects.append('实际计划含有未被本轮要求覆盖的额外目标。')
        if semantic.planner_defects and 'conditions' not in semantic.repair_domains:semantic.repair_domains.append('conditions')
        if not semantic.accepted:
            semantic.satisfied=False
        return semantic

    async def _review(self, s):
        from .planner import SemanticReview,GoalCoverage
        def audit(request):
            review=SemanticReview(audit_mode='program',goal_requirements=[GoalCoverage(
                source_ref=g.intent_source or request.question,kind=g.kind,goal_ids=[g.id],representation='represented') for g in request.goals],
                satisfied=True,clarification=[],planner_defects=[],companies_correct=True,metrics_and_scope_correct=True,
                time_correct=True,goals_correct=True,constraints_correct=True,presentation_correct=True,continuity_correct=True)
            return self._review_exact_bindings(request,review)
        await s['emit']('plan',{'label':'程序核对请求条件','detail':'核对登记身份、条件修改和明确限制'})
        request=s['request'];dialogue=s['dialogue'];understanding=s['understanding']
        semantic=audit(request)
        if semantic.planner_defects:
            if s['budget'].artifacts.get('proposal_repair_used'):
                error=ModelFailure('explicit_request_check_failed');error.audit=semantic.model_dump(mode='json');error.request=request.model_dump(mode='json')
                raise error
            s['budget'].artifacts['proposal_repair_used']=True
            understanding=await self.planner.understand(s['question'],s['previous'],s['history'],s['budget'],
                [dict(defects=semantic.planner_defects,invalid_request=request.model_dump(mode='json'))])
            request,dialogue=apply_understanding(s['question'],s['turn_id'],understanding,s['previous'],self.companies)
            semantic=audit(request)
            if semantic.planner_defects:
                error=ModelFailure('explicit_request_check_failed');error.audit=semantic.model_dump(mode='json');error.request=request.model_dump(mode='json')
                raise error
        await s['emit']('plan',{'label':'请求条件已检查','_request_checkpoint':dict(
            request=request.model_dump(mode='json'),dialogue=dialogue.model_dump(mode='json'),approved=semantic.accepted)})
        return dict(request=request,semantic=semantic,understanding=understanding,dialogue=dialogue)

    async def _execute(self, s):
        request = s['request']
        ready=request.model_copy(update={'goals':[goal for goal in request.goals if not goal.clarification]})
        waiting=[GoalResult(id=goal.id,kind=goal.kind,status='no_data',detail='等待补充条件：'+'；'.join(goal.clarification))
            for goal in request.goals if goal.clarification]
        need_data = not request.conditions.restrictions.no_query and any(g.kind in {'lookup', 'compare', 'rank', 'chart', 'quote', 'cause', 'sign'} for g in ready.goals)
        catalog_needed = any(g.kind == 'catalog' for g in ready.goals)
        if not need_data and not catalog_needed:
            from .memory_updates import apply_memory_edits
            retained=apply_memory_edits(s['previous'],request.memory_edits,request.question,request.turn_id)
            references=retained.recent_facts
            computations=retained.recent_computed
            contextual_goals=[g for g in ready.goals if g.kind=='quote' or g.kind=='concept' and g.concept_mode=='implication']
            contextual_reference=s['understanding'].continuity in {'continue','resume'} and bool(contextual_goals)
            if contextual_reference and (references or computations):
                versions={ref.data_version for ref in [*references,*computations]}
                if len(versions)==1:
                    # 作品说明：重新核验登记的事实引用；旧回答文字不解析为新的财务数值。
                    repository=await asyncio.to_thread(self.repository_factory,self.engine,version=next(iter(versions)))
                    facts=await asyncio.to_thread(revalidate_facts,repository,[ref.id for ref in references])
                    checked=[]
                    for ref in computations:
                        validated=await asyncio.to_thread(revalidate_computed,repository,ref)
                        if validated:checked.append(validated)
                    if {f.id for f in facts}=={ref.id for ref in references} and len(checked)==len(computations):
                        return dict(repository=repository,context_facts=facts,context_computed=checked,
                            executed=ExecutionResult(goals=waiting),references=[],evidence_checks=[],prose=[])
                blocked=contextual_goals
                return dict(repository=None,context_facts=[],context_reference_failed=True,
                    executed=ExecutionResult(goals=[*waiting,*[GoalResult(id=g.id,kind=g.kind,status='no_data',
                        detail='先前事实引用未通过当前来源核验，不能解释为本轮已确认事实') for g in blocked]]),references=[],
                    evidence_checks=[Check(name=g.id,status='unknown',detail='引用的事实版本或来源未通过核验') for g in blocked],
                    prose=['先前引用未通过来源核验，请重新查询所指数字；本轮没有把旧回答当作事实。'])
            return dict(repository=None, executed=ExecutionResult(goals=waiting), references=[], evidence_checks=[], prose=[])
        await s['emit']('tool_call', {'tool': 'query_database', 'label': '读取核实事实', 'detail': '固定数据版本，参数化只读查询'})
        repository = await asyncio.to_thread(self.repository_factory, self.engine)
        result = await asyncio.to_thread(execute, ready, repository) if need_data else ExecutionResult()
        result.goals.extend(waiting)
        await s['emit']('tool_result', {'tool': 'query_database', 'row_count': len(result.facts), 'status': 'success' if result.facts else 'empty'})
        return dict(repository=repository, executed=result,data_query_executed=need_data,references=[], evidence_checks=[], prose=[])

    async def _evidence(self, s):
        request, result = s['request'], s['executed']
        refs, checks, prose = [], list(s.get('evidence_checks',[])), list(s.get('prose',[]))
        context_facts=s.get('context_facts',[])
        context_computed=s.get('context_computed',[])
        context_values=[*context_facts,*context_computed]
        if len(context_values)==1:
            number=context_values[0].decimal
            actual='undefined' if number is None else 'positive' if number>0 else 'negative' if number<0 else 'zero'
            if s['understanding'].claimed_sign not in {'none',actual}:
                prose.append('先核对前提：最近已核实的引用实际为'+{'positive':'正数','negative':'负数','zero':'零','undefined':'未定义'}[actual]+'，不能按相反正负解释本轮事实。')
            elif any(g.kind=='concept' and g.concept_mode=='implication' for g in request.goals):
                prose.append('先核对引用：最近已核实的这个数实际为'+{'positive':'正数','negative':'负数','zero':'零','undefined':'未定义'}[actual]+'。')
        for goal in request.goals:
            if goal.clarification:continue
            if goal.kind == 'unsupported':
                result.goals.append(GoalResult(id=goal.id, kind=goal.kind, status='unsupported', detail=goal.text))
            elif goal.kind == 'rules':
                prose.append(render_rules(s['previous'], request.question, goal, self.companies))
                result.goals.append(GoalResult(id=goal.id, kind=goal.kind, status='completed'))
            elif goal.kind == 'catalog':
                reports = await asyncio.to_thread(s['repository'].report_catalog, request.conditions.codes)
                prose.append(render_catalog(reports, goal.catalog_target or 'companies', request.conditions))
                result.goals.append(GoalResult(id=goal.id, kind=goal.kind, status='completed'))
            elif goal.kind == 'quote':
                selected_result = result.for_goal(goal.id)
                contextual_quote=request.for_goal(goal).conditions.restrictions.no_query
                needed = context_facts if contextual_quote else selected_result.facts
                if contextual_quote and context_computed:
                    computed_inputs=list(dict.fromkeys(id for ref in context_computed for id in ref.inputs))
                    needed=[*needed,*await asyncio.to_thread(revalidate_facts,s['repository'],computed_inputs)]
                support = await asyncio.to_thread(s['repository'].by_ids, [id for f in needed for id in f.inputs]) if s['repository'] else []
                quotes = [q for f in [*needed, *support] if (q := numeric_quote(f))]
                refs.extend(quotes)
                direct_quote_complete = bool(needed) and not selected_result.missing and all(f.source and numeric_quote(f) for f in needed)
                result.goals.append(GoalResult(id=goal.id, kind=goal.kind, status='completed' if direct_quote_complete else 'partial' if quotes else 'no_data',
                    fact_ids=[f.id for f in needed], evidence_ids=[q['id'] for q in quotes]))
                checks.append(Check(name=goal.id, status='pass' if direct_quote_complete else 'unknown', detail='从事实登记的文件、页码和表格单元格直接定位'))
                if any(f.status=='derived' for f in needed):
                    prose.append('所问指标是计算值，没有把它改写成报告原句；以下摘录为基础数字的原表单元格。')
                if contextual_quote and context_computed:
                    prose.append('刚才的结果由程序计算，报告没有对应的逐字原句；这里提供已重新核验的基础数字原表。')
                if goal.quote_mode == 'location' or request.conditions.restrictions.no_repeat:
                    prose.extend(dict.fromkeys(f'原件 PDF 第 {f.source.page} 页，{f.source.table}的“{f.source.row}”行。' for f in needed if f.source))
                else:
                    prose.extend(q['text'] for q in quotes)
                if any(f.source and f.source.raw_unit != request.conditions.presentation.unit for f in needed) and request.conditions.presentation.unit:
                    prose.append('上面保留原表单位；答案数字另按所要求单位换算，摘录未改写。')
        for goal in request.goals:
            if goal.clarification or goal.kind!='concept' or goal.concept_mode not in {'definition','difference'}:continue
            conditions=request.for_goal(goal).conditions
            metrics=conditions.metrics
            valid=bool(metrics) and all(metric in METRICS for metric in metrics) and (goal.concept_mode!='difference' or len(set(metrics))>=2)
            if valid:
                if goal.concept_mode=='difference':prose.append('它们是不同指标，定义如下：')
                prose.extend(f'{"母公司净利润" if metric=="net_profit" and conditions.scope=="parent" else METRICS[metric].label}：{metric_definition(metric,conditions.scope)}。' for metric in metrics)
            else:prose.append('概念目标没有明确对应到指标目录，尚未完成解释。')
            result.goals.append(GoalResult(id=goal.id,kind=goal.kind,status='completed' if valid else 'no_data',detail='' if valid else '概念指标身份不完整'))
            checks.append(Check(name=goal.id,status='pass' if valid else 'unknown',detail='定义和公式来自指标目录；请求条件由程序检查，自由语义仍可能理解失败'))
        implications={g.id for g in result.goals}
        for goal in request.goals:
            if goal.clarification or goal.kind!='concept' or goal.concept_mode!='implication' or goal.id in implications:continue
            if len(context_values)==1 and context_computed:
                prose.append(render_computed_implication(context_computed[0]))
                result.goals.append(GoalResult(id=goal.id,kind='concept',status='completed',fact_ids=list(context_computed[0].inputs)))
                checks.append(Check(name=goal.id,status='pass',detail='重新核验基础事实并复算引用结果，按实际变化解释正负'))
                continue
            referenced=context_facts or result.for_goal(goal.id).facts
            if len(referenced)==1:
                prose.append(render_fact_implication(referenced[0]))
                result.goals.append(GoalResult(id=goal.id,kind='concept',status='completed',fact_ids=[referenced[0].id]))
                checks.append(Check(name=goal.id,status='pass',detail='已核实事实的正负含义由统一指标定义与程序生成；经营原因没有据此推断'))
        blocked_ids={g.id for g in result.goals if g.kind=='concept' or g.status=='no_data'}
        explanation_goals = [g for g in request.goals if not g.clarification and g.id not in blocked_ids and (g.kind=='cause' or g.kind=='concept' and g.concept_mode not in {'definition','difference'})]
        if explanation_goals:
            await s['emit']('plan', {'label': '核对解释依据', 'detail': '经营原因使用对应原文，再独立审核结论'})
            snippets = []
            if any(g.kind == 'cause' for g in explanation_goals) and s['repository']:
                try:
                    retriever = await asyncio.to_thread(self.retriever_factory, s['repository'].collection)
                    snippets = await asyncio.to_thread(retriever.retrieve, request.question, result.selections)
                except Exception:
                    # 作品说明：检索与查数分开处理，检索失败保留可靠数字，并说明经营原因尚未取得证据。
                    result.notes.append('原文检索暂不可用，经营原因未确认；保留已核实数字。')
            goal_snippets={}
            for goal in explanation_goals:
                selected=result.for_goal(goal.id)
                pairs=selected.selections
                versions={f.source.document_version for f in selected.facts if f.source}
                goal_snippets[goal.id]=[x['id'] for x in snippets if
                    (x.get('year'),x.get('period')) in pairs.get(x.get('stock_code'),[]) and
                    (not versions or x.get('document_version') in versions)] if goal.kind=='cause' else []
            payload = dict(question=request.question, goals=[g.model_dump() for g in explanation_goals],
                verified_facts=[dict(id=f.id,metric=f.metric,company=f.company,year=f.year,period=f.period,scope=f.scope,
                    sign='positive' if f.decimal>0 else 'negative' if f.decimal<0 else 'zero')
                    for f in [*result.facts,*context_facts]] if any(g.kind=='cause' for g in explanation_goals) else [], snippets=snippets,
                allowed_evidence_ids=goal_snippets,
                referenced_facts=[dict(metric=f.metric,company=f.company,year=f.year,period=f.period,
                    sign='positive' if f.decimal>0 else 'negative' if f.decimal<0 else 'zero') for f in context_facts],
                concept_definitions={g.id:{m:dict(name=METRICS[m].label,definition=metric_definition(m,request.for_goal(g).conditions.scope))
                    for m in request.for_goal(g).conditions.metrics if m in METRICS} for g in explanation_goals if g.kind=='concept'})
            try:
                explanation = await self.model.structured(Explanations, EXPLAIN, payload, s['budget'])
                review = await self.model.structured(EvidenceReview, AUDIT,
                    {**payload, 'claims': [claim.model_dump() for claim in explanation.claims]}, s['budget'])
            except ModelFailure as exc:
                result.notes.append('解释尚未完成：' + exc.reason)
                explanation, review = Explanations(claims=[], incomplete_goals=[g.id for g in explanation_goals]), EvidenceReview(judgments=[])
            for goal in explanation_goals:
                accepted = []
                for i, claim in enumerate(explanation.claims):
                    if claim.goal_id != goal.id:
                        continue
                    judgment = [j for j in review.judgments if j.claim_index == i and j.goal_id == goal.id]
                    ids = set(goal_snippets[goal.id]) if goal.kind=='cause' else {x['id'] for x in snippets}
                    supported = len(judgment) == 1 and judgment[0].supported and set(claim.evidence_ids) <= ids
                    if goal.kind=='concept':supported = supported and not claim.evidence_ids
                    supported = supported and safe_narrative_claim(claim.text, [f.year for f in [*result.facts,*context_facts]])
                    if goal.kind == 'cause':
                        supported = supported and bool(claim.evidence_ids)
                    checks.append(Check(name=f'{goal.id}_claim_{i}', status='pass' if supported else 'unknown', detail=judgment[0].detail if judgment else '未取得独立审核'))
                    if supported:
                        accepted.append(claim); prose.append(claim.text)
                        for snippet in snippets:
                            if snippet['id'] in claim.evidence_ids:
                                refs.append(dict(id=snippet['id'], type='reference', text=snippet['text'],
                                    paper_path=snippet['source_path'], source_title=f'{snippet["company"]}{snippet["year"]}{snippet["period"]}报告',
                                    document_version=snippet['document_version'], page_start=snippet['page'], page_end=snippet['page']))
                complete = bool(accepted) and goal.id not in explanation.incomplete_goals
                if accepted and goal.kind=='concept':
                    # 作品说明：会计目录提供固定定义和公式，模型组织语言，财务数值由程序呈现。
                    prose.extend(f'{definition["name"]}：{definition["definition"]}。'
                        for definition in payload['concept_definitions'].get(goal.id,{}).values())
                result.goals.append(GoalResult(id=goal.id, kind=goal.kind, status='completed' if complete else 'partial' if accepted else 'no_data',
                    evidence_ids=[id for claim in accepted for id in claim.evidence_ids], detail='' if complete else '解释依据尚未确认'))
                if not complete:
                    prose.append('经营原因尚未找到足够原文依据。' if goal.kind == 'cause' else '概念解释尚未通过核对。')
                    checks.append(Check(name=goal.id, status='unknown', detail='该解释目标未完成'))
        refs = list({r['id']: r for r in refs}.values())
        return dict(executed=result, references=refs, evidence_checks=checks, prose=prose)

    async def _verify(self, s):
        await s['emit']('plan', {'label': '核验结果', 'detail': '分别核对数字、要求和证据支持度'})
        known={f.id for f in s['executed'].facts}
        support_ids=({id for f in s['executed'].facts for id in f.inputs} | {id for item in s['executed'].derived for id in item['inputs']})-known
        support=[]
        # 作品说明：在限定深度内展开完整来源链，包含派生指标使用的基础事实。
        for _ in range(8):
            if not support_ids: break
            if len(known | support_ids)>2000: break
            batch=await asyncio.to_thread(s['repository'].by_ids,sorted(support_ids))
            known.update(support_ids)
            support.extend(batch)
            support_ids={id for f in batch for id in f.inputs}-known
        verification = verify(s['request'], s['executed'], s['semantic'].checks, s['evidence_checks'], support)
        return dict(verification=verification)

    async def _publish(self, s):
        request, dialogue = s['request'], s['dialogue']
        if s.get('context_reference_failed'):dialogue.recent_facts=[];dialogue.recent_computed=[]
        if request.clarification and 'executed' not in s:
            content = '\n\n'.join([*request.clarification, *request.unsupported])
            verification = Verification(numeric=[], request=s['semantic'].checks,
                evidence=[], status='partial')
            result, refs = ExecutionResult(), []
            if any(g.kind in {'lookup','compare','rank','chart','quote','cause','sign'} for g in request.goals):
                dialogue.pending = request
                dialogue.recent_facts = []
                dialogue.recent_computed = []
            outcome = 'needs_clarification' if request.clarification else 'unsupported'
        else:
            result, refs, verification = s['executed'], s['references'], s['verification']
            if verification.status == 'fail':
                content = '本轮结果没有通过完整核验，尚未发布财务正文。'
                result.facts, result.charts, result.derived, result.comparisons, refs = [], [], [], [], []
            else:
                financial_body = render_financial(request, result, s['understanding'].claimed_sign)
                if request.conditions.restrictions.no_repeat:
                    previous_ids = {f.id for f in s['previous'].recent_facts}
                    if result.facts and all(f.id in previous_ids for f in result.facts) and not result.derived:
                        financial_body = ''
                content = '\n\n'.join(filter(None, [financial_body, *dict.fromkeys(s['prose']), *request.clarification,*request.unsupported]))
            if not content and not result.charts:
                verification.request.append(Check(name='published_deliverable',status='unknown',detail='没有可展示的回答、出处或图表；不能判为全部完成'))
                if verification.status=='pass': verification.status='partial'
                for goal in result.goals:
                    if goal.status=='completed':
                        goal.status='partial';goal.detail='执行后没有可展示的对应结果'
            if not content and not result.charts:
                content = '可以查询库内公司的财务数字、对比报告期或解释财务概念。' if not request.goals else '本轮没有取得可发布的结果。'
            complete = (not request.unsupported and verification.status == 'pass' and len(result.goals) == len(request.goals)
                and {g.id for g in result.goals} == {g.id for g in request.goals}
                and all(g.status == 'completed' for g in result.goals))
            outcome = 'answered' if complete else 'partial' if result.facts or s['prose'] else 'unsupported' if request.unsupported else 'no_data'
            version = s['repository'].version if s['repository'] else ''
            if result.facts and verification.status != 'fail':
                dialogue.recent_facts = [FactReference(id=f.id, data_version=version) for f in visible_fact_references(request,result)]
                dialogue.recent_computed = computed_references(request,result,version)
            if s.get('data_query_executed') and s['repository'] and any(g.kind in {'lookup','compare','rank','chart','quote','cause','sign'} for g in request.goals):
                dialogue.last_execution = Execution(turn_id=request.turn_id, data_version=version,
                    resolved_periods=result.selections, time_rule=result.time_rule,
                    fact_refs=[FactReference(id=f.id,data_version=version) for f in result.facts], status='failed' if verification.status=='fail' else 'completed' if complete else 'partial' if result.facts else 'no_data',
                    conditions=request.conditions,goal_conditions={g.id:request.for_goal(g).conditions for g in request.goals if g.kind in {'lookup','compare','rank','chart','quote','cause','sign'}})
                dialogue.executions=[*dialogue.executions,dialogue.last_execution][-12:]
            if any(g.kind in {'lookup','compare','rank','chart','quote','cause','sign'} for g in request.goals):
                blocked=[goal for goal in request.goals if goal.clarification]
                dialogue.pending=request.model_copy(update={'goals':blocked}) if blocked else None
        if request.conditions.presentation.format=='table':content=table_only_content(content)
        charts = [chart_compat(c) for c in result.charts]
        fact_payload = []
        for f in {fact.id:fact for fact in [*result.facts,*s.get('context_facts',[])]}.values():
            source = f.source.model_dump(mode='json') if f.source else {}
            if f.source:
                source.update(page_start=f.source.page, page_end=f.source.page, status=f.status)
            fact_payload.append({**f.model_dump(mode='json'), 'value_exact': f.value,
                'fact_id': f.id, 'row_id': f.id,
                'field': f.metric, 'label': METRICS[f.metric].label, 'stock_abbr': f.company,
                'report_year': f.year, 'report_period': f.period, 'source': source, 'query_id': 'canonical'})
        legacy_verification = verification_compat(verification)
        query_trace=getattr(s.get('repository'),'query_trace',[])
        return {'result': dict(version=3, task_id=request.turn_id, question=request.question,
            data_version=s['repository'].version if s.get('repository') else None,
            dataset_profile=release_profile(s['repository'].manifest).public_metadata() if s.get('repository') and hasattr(s['repository'],'manifest') else None,
            answer=dict(content=content, image=[], references=refs), sql='\n\n'.join(item['sql'] for item in query_trace if item['purpose']=='financial_facts'),query_trace=query_trace,
            request_contract=request.model_dump(mode='json'), dialogue_state=dialogue.model_dump(mode='json'),
            response_kind=s['understanding'].topic, query_plan=request.conditions.model_dump(mode='json'),
            facts=fact_payload, derived_facts=result.derived, comparisons=result.comparisons,evidence=refs, verification=legacy_verification,
            verification_v3=verification.model_dump(mode='json'), task_results=[g.model_dump(mode='json') for g in result.goals],
            execution_plan=[g.model_dump(mode='json') for g in request.goals],
            chart_data=charts[0] if charts else None, chart_data_list=charts,
            chart_format=request.conditions.presentation.chart_type if charts else '无',
            needs_clarification=bool(request.clarification), clarify_options=[],
            outcome=dict(status=outcome, reason_codes=[g.status for g in result.goals if g.status != 'completed']),
            validation=dict(status=legacy_verification['status'], verification=legacy_verification, facts=fact_payload, evidence=refs),
            diagnostics=dict(model_calls=s['budget'].calls, timings=s['budget'].timings,request_audit_mode='program',
                structured_trace=s['budget'].artifacts.get('structured_trace',[]),
                semantic_audit=s['semantic'].model_dump(mode='json'),
                **({'format_repairs':s['budget'].artifacts['format_repairs']} if s['budget'].artifacts.get('format_repairs') else {})))}

    async def run(self, question: str, history: list[dict], turn_id: str, deadline: float, emit):
        budget = Budget(deadline=min(deadline - 10, time.time() + 240))
        try:
            async with asyncio.timeout(max(0.001, budget.remaining())):
                result = await self.graph.ainvoke(dict(question=question, history=history, turn_id=turn_id,
                    budget=budget, emit=emit))
        except Exception as exc:
            # 作品说明：诊断只保留通过类型检查的提案和程序反馈，驱动异常、连接配置及模型原始输出不进入任务记录。
            exc.diagnostics={**(getattr(exc,'diagnostics',{}) if isinstance(exc,ModelFailure) else {}),**dict(model_calls=budget.calls,timings=budget.timings,
                structured_trace=budget.artifacts.get('structured_trace',[]),
                correction_feedback=budget.artifacts.get('correction_feedback',[])),
                **({'format_repairs':budget.artifacts['format_repairs']} if budget.artifacts.get('format_repairs') else {})}
            raise
        return result['result']
