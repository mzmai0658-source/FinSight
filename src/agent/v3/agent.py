"""以异步LangGraph串联理解、请求检查、执行、证据审核和发布，财务正文在核验后交付。"""
from __future__ import annotations
from src.etl.release_profiles import release_profile

import asyncio
import re
import time
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .catalog import METRICS,metric_definition
from .contracts import Check, Execution, FactReference, GoalResult, Verification
from .evidence import AUDIT, EXPLAIN, EvidenceReview, Explanations, NarrativeRetriever, numeric_quote, safe_narrative_claim, verified_narrative_years
from .executor import ExecutionResult, execute
from .model import AsyncModel, Budget, ModelFailure
from .planner import Planner
from .rendering import chart_compat, render_financial, render_rules, render_catalog, render_fact_implication, verification_compat
from .repository import CanonicalRepository
from .state import InvalidUnderstanding, apply_understanding, read_state
from .verification import SourceAudit,verify
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
    prose_by_goal: dict[str,list[str]]
    verification: Verification
    result: dict
    emit: object
    context_facts: list
    context_computed: list
    context_reference_failed: bool
    data_query_executed: bool
    context_by_goal: dict
    context_computed_by_goal: dict
    pending_publication: object


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
            correction=dict(error=str(exc),repair_goals=getattr(exc,'repair_goals',False))
            for key,attribute in (('goal_kind_exclusions','rejected_goal_kinds'),('continuity_exclusions','rejected_continuities'),('required_goal_kinds','required_goal_kinds')):
                if values:=list(getattr(exc,attribute,())):correction[key]=values
            if correction.get('required_goal_kinds'):correction['minimum_goal_count']=getattr(exc,'minimum_goal_count',1)
            if wire_proposal:=s['budget'].artifacts.get('last_turn_wire_proposal'):
                correction['invalid_proposal']=wire_proposal
            understanding = await self.planner.understand(s['question'], previous, s['history'], s['budget'],
                [correction,
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
        """程序检查合同一致性；自由语义理解由提案与独立评测分别记录。"""
        from .request_validation import review_contract
        return review_contract(request, semantic)

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
            feedback=dict(defects=semantic.planner_defects)
            if proposal:=s['budget'].artifacts.get('last_turn_wire_proposal'):
                feedback['invalid_proposal']=proposal
            understanding=await self.planner.understand(s['question'],s['previous'],s['history'],s['budget'],
                [feedback])
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
        query_goals=[g for g in ready.goals if g.kind in {'lookup','compare','rank','chart','quote','cause','sign'} and not request.for_goal(g).conditions.restrictions.no_query]
        need_data = bool(query_goals)
        catalog_needed = any(g.kind == 'catalog' and g.catalog_target not in {'capabilities','greeting','closing','metrics'} for g in ready.goals)
        repository=None;result=ExecutionResult();checks=[];prose=[]
        if need_data or catalog_needed:
            repository=await asyncio.to_thread(self.repository_factory,self.engine)
        if need_data:
            await s['emit']('tool_call', {'tool': 'query_database', 'label': '读取核实事实', 'detail': '固定数据版本，参数化只读查询'})
            result=await asyncio.to_thread(execute,ready.model_copy(update={'goals':query_goals}),repository)
            await s['emit']('tool_result', {'tool': 'query_database', 'row_count': len(result.facts), 'status': 'success' if result.facts else 'empty'})
        result.goals.extend(waiting)
        contextual_goals=[g for g in ready.goals if
            g.kind=='concept' and g.concept_mode=='implication'
            or request.for_goal(g).conditions.restrictions.no_query and g.kind in {'quote','sign'}]
        by_goal={};computed_by_goal={};reference_failed=False
        if contextual_goals and s['understanding'].continuity in {'continue','resume'}:
            from .memory_updates import apply_memory_edits
            from .reference_selection import fact_matches,computed_matches
            retained=apply_memory_edits(s['previous'],request.memory_edits,request.question,request.turn_id)
            references=retained.recent_facts;computations=retained.recent_computed
            versions={ref.data_version for ref in [*references,*computations]}
            facts=[];computed=[]
            if len(versions)==1 and (repository is None or repository.version in versions):
                if repository is None:repository=await asyncio.to_thread(self.repository_factory,self.engine,version=next(iter(versions)))
                facts=await asyncio.to_thread(revalidate_facts,repository,[ref.id for ref in references])
                for ref in computations:
                    checked=await asyncio.to_thread(revalidate_computed,repository,ref)
                    if checked:computed.append(checked)
            valid=(len(versions)==1 and {f.id for f in facts}=={ref.id for ref in references} and len(computed)==len(computations))
            singular=any(ref.target=='fact' and ref.number=='singular' for ref in request.context_references)
            for goal in contextual_goals:
                selected=request.for_goal(goal);c=selected.conditions
                selected_facts=[f for f in facts if fact_matches(c,f)] if valid else []
                selected_computed=[v for v in computed if computed_matches(c,v,self.companies)] if valid else []
                count=len(selected_facts)+len(selected_computed)
                if not count or singular and count!=1:
                    detail='请明确所指的公司、指标和报告期，当前条件没有唯一的已核实事实。' if valid else '先前事实引用未通过来源核验，请重新查询所指数字。'
                    result.goals.append(GoalResult(id=goal.id,kind=goal.kind,status='no_data',detail=detail))
                    goal.clarification.append(detail);request.clarification.append(detail)
                    checks.append(Check(name=goal.id,status='unknown',detail=detail));prose.append(detail)
                    reference_failed=not valid
                    continue
                by_goal[goal.id]=selected_facts;computed_by_goal[goal.id]=selected_computed
                periods={}
                for f in selected_facts:periods.setdefault(f.stock_code,[]).append((f.year,f.period))
                block=ExecutionResult(input_kind='reference',facts=selected_facts,selections=periods)
                result.blocks.append((selected,block))
                result.facts.extend(selected_facts)
        result.facts=list({f.id:f for f in result.facts}.values())
        return dict(repository=repository,executed=result,data_query_executed=need_data,references=[],evidence_checks=checks,prose=prose,
            context_facts=list({f.id:f for values in by_goal.values() for f in values}.values()),
            context_computed=list({v.id:v for values in computed_by_goal.values() for v in values}.values()),
            context_by_goal=by_goal,context_computed_by_goal=computed_by_goal,context_reference_failed=reference_failed)

    async def _evidence(self, s):
        request, result = s['request'], s['executed']
        refs, checks, prose = [], list(s.get('evidence_checks',[])), list(s.get('prose',[]))
        prose_by_goal={}
        context_facts=s.get('context_facts',[])
        context_computed=s.get('context_computed',[])
        context_values=[*context_facts,*context_computed]
        # 这里只生成各目标的证据和含义；前提纠正在完整发布包按引用身份统一生成。
        for goal in request.goals:
            if goal.clarification:continue
            start=len(prose)
            goal_context=s.get('context_by_goal',{}).get(goal.id,context_facts)
            goal_computed=s.get('context_computed_by_goal',{}).get(goal.id,context_computed)
            if goal.kind=='sign' and request.for_goal(goal).conditions.restrictions.no_query and not any(g.id==goal.id for g in result.goals):
                from .rendering import render_sign
                if goal_context:
                    result.goals.append(GoalResult(id=goal.id,kind='sign',status='completed',fact_ids=[f.id for f in goal_context]))
                    checks.append(Check(name=goal.id,status='pass',detail='引用事实重新核验后，由程序生成正负结论'))
                else:
                    prose.append('没有唯一可核实的利润事实，尚不能判断盈利或亏损。')
                    result.goals.append(GoalResult(id=goal.id,kind='sign',status='no_data'))
            elif goal.kind == 'unsupported':
                result.goals.append(GoalResult(id=goal.id, kind=goal.kind, status='unsupported', detail=goal.text))
            elif goal.kind == 'rules':
                prose.append(render_rules(s['previous'], request.question, goal, self.companies))
                result.goals.append(GoalResult(id=goal.id, kind=goal.kind, status='completed'))
            elif goal.kind == 'catalog':
                reports = [] if goal.catalog_target in {'capabilities','greeting','closing','metrics'} else await asyncio.to_thread(s['repository'].report_catalog, request.for_goal(goal).conditions.codes)
                prose.append(render_catalog(reports, goal.catalog_target or 'companies', request.conditions))
                result.goals.append(GoalResult(id=goal.id, kind=goal.kind, status='completed'))
            elif goal.kind == 'quote':
                selected_result = result.for_goal(goal.id)
                contextual_quote=request.for_goal(goal).conditions.restrictions.no_query
                needed = goal_context if contextual_quote else selected_result.facts
                if contextual_quote and goal_computed:
                    computed_inputs=list(dict.fromkeys(id for ref in goal_computed for id in ref.inputs))
                    needed=[*needed,*await asyncio.to_thread(revalidate_facts,s['repository'],computed_inputs)]
                support = await asyncio.to_thread(s['repository'].by_ids, [id for f in needed for id in f.inputs]) if s['repository'] else []
                granularity='location' if goal.quote_mode=='location' else goal.quote_granularity or 'row'
                quotes = [dict(q,goal_ids=[goal.id],purpose='source_row' if q.get('granularity')=='row' else 'source_cell')
                    for f in [*needed,*support] if (q:=numeric_quote(f,granularity))]
                refs.extend(quotes)
                direct_quote_complete = bool(needed) and not selected_result.missing and all(f.source and any(q['fact_id']==f.id and
                    (granularity!='row' or q.get('granularity')=='row') for q in quotes) for f in needed)
                result.goals.append(GoalResult(id=goal.id, kind=goal.kind, status='completed' if direct_quote_complete else 'partial' if quotes else 'no_data',
                    fact_ids=[f.id for f in needed], evidence_ids=[q['id'] for q in quotes]))
                checks.append(Check(name=goal.id, status='pass' if direct_quote_complete else 'unknown', detail='从事实登记的文件、页码和表格单元格直接定位'))
                if any(f.status=='derived' for f in needed):
                    prose.append('所问指标是计算值，没有把它改写成报告原句；以下摘录为基础数字的原表单元格。')
                if contextual_quote and goal_computed:
                    prose.append('刚才的结果由程序计算，报告没有对应的逐字原句；这里提供已重新核验的基础数字原表。')
                if granularity == 'location' or request.for_goal(goal).conditions.restrictions.no_repeat:
                    prose.extend(dict.fromkeys(f'原件 PDF 第 {f.source.page} 页，{f.source.table}的“{f.source.row}”行。' for f in needed if f.source))
                else:
                    from .rendering import render_numeric_quote
                    prose.extend(render_numeric_quote(q) for q in quotes)
                    prose.extend(q['incomplete_reason'] for q in quotes if q.get('incomplete_reason'))
                if goal.quote_mode != 'location' and not request.conditions.restrictions.no_repeat and any(f.source and f.source.raw_unit != request.conditions.presentation.unit for f in needed) and request.conditions.presentation.unit:
                    prose.append('上面保留原表单位；答案数字另按所要求单位换算，摘录未改写。')
            if len(prose)>start:prose_by_goal[goal.id]=prose[start:]
        for goal in request.goals:
            if goal.clarification or goal.kind!='concept' or goal.concept_mode not in {'definition','difference'}:continue
            start=len(prose)
            conditions=request.for_goal(goal).conditions
            metrics=conditions.metrics
            valid=bool(metrics) and all(metric in METRICS for metric in metrics) and (goal.concept_mode!='difference' or len(set(metrics))>=2)
            if valid:
                from .rendering import render_concept_definition,render_concept_relation,system_sentence
                if goal.concept_mode=='difference':prose.append('它们是不同指标，定义如下：')
                prose.extend(system_sentence(f'{"母公司净利润" if metric=="net_profit" and conditions.scope=="parent" else METRICS[metric].label}：{render_concept_definition(metric,conditions.scope)}') for metric in metrics)
                if goal.concept_mode=='difference' and (relation:=render_concept_relation(metrics)):prose.append(relation)
            else:prose.append('概念目标没有明确对应到指标目录，尚未完成解释。')
            prose_by_goal[goal.id]=prose[start:]
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
            referenced=s.get('context_by_goal',{}).get(goal.id,context_facts) or result.for_goal(goal.id).facts
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
                    # 检索与查数分开处理，检索失败保留可靠数字，并说明经营原因尚未取得证据。
                    result.notes.append('原文检索暂不可用，经营原因未确认；保留已核实数字。')
            from .cause_evidence import cause_evidence_candidates,is_evidence_limit
            cause_metrics={metric for goal in explanation_goals if goal.kind=='cause' for metric in request.for_goal(goal).conditions.metrics}
            cause_trace=dict(retrieved=len(snippets),metrics=sorted(cause_metrics),goals={})
            snippets=cause_evidence_candidates(snippets,cause_metrics) if cause_metrics else snippets
            cause_trace['context_candidates']=len(snippets)
            cause_trace['metric_bound']=sum(x.get('support_status')!='unreviewed' for x in snippets)
            from .evidence import checked_narrative_literal
            snippets=[verified for snippet in snippets if (verified:=await asyncio.to_thread(checked_narrative_literal,snippet))]
            cause_trace['original_verified']=len(snippets)
            goal_snippets={}
            for goal in explanation_goals:
                selected=result.for_goal(goal.id)
                pairs=selected.selections
                versions={f.source.document_version for f in selected.facts if f.source}
                goal_snippets[goal.id]=[x['id'] for x in snippets if
                    (x.get('year'),x.get('period')) in pairs.get(x.get('stock_code'),[]) and
                    (not versions or x.get('document_version') in versions) and
                    x.get('narrative_scope','consolidated')==request.for_goal(goal).conditions.scope and
                    x.get('target_metric') in request.for_goal(goal).conditions.metrics] if goal.kind=='cause' else []
                cause_trace['goals'][goal.id]=dict(allowed_snippets=len(goal_snippets[goal.id]),claims=[])
            if s.get('budget') is not None:
                s['budget'].artifacts.setdefault('cause_evidence',[]).append(cause_trace)
            payload = dict(question=request.question, goals=[g.model_dump() for g in explanation_goals],
                goal_conditions={g.id:request.for_goal(g).conditions.model_dump(mode='json',exclude={'origins'}) for g in explanation_goals},
                verified_facts=[dict(id=f.id,metric=f.metric,company=f.company,year=f.year,period=f.period,scope=f.scope)
                    for f in [*result.facts,*context_facts]] if any(g.kind=='cause' for g in explanation_goals) else [], snippets=snippets,
                allowed_evidence_ids=goal_snippets,
                referenced_facts=[dict(metric=f.metric,company=f.company,year=f.year,period=f.period,
                    sign='positive' if f.decimal>0 else 'negative' if f.decimal<0 else 'zero') for f in context_facts],
                concept_definitions={g.id:{m:dict(name=METRICS[m].label,definition=metric_definition(m,request.for_goal(g).conditions.scope))
                    for m in request.for_goal(g).conditions.metrics if m in METRICS} for g in explanation_goals if g.kind=='concept'})
            explanation_failure=None
            try:
                if any(goal.kind!='cause' or goal_snippets[goal.id] for goal in explanation_goals):
                    explanation = await self.model.structured(Explanations, EXPLAIN, payload, s['budget'])
                    from .evidence import explanation_review_input
                    audit_prompt,audit_payload=explanation_review_input(explanation,payload)
                    review = await self.model.structured(EvidenceReview,audit_prompt,audit_payload,s['budget'])
                else:
                    explanation=Explanations(claims=[],incomplete_goals=[g.id for g in explanation_goals])
                    review=EvidenceReview(judgments=[])
            except ModelFailure as exc:
                explanation_failure=exc.reason
                cause_trace['model_failure']=exc.reason
                explanation, review = Explanations(claims=[], incomplete_goals=[g.id for g in explanation_goals]), EvidenceReview(judgments=[])
            for goal in explanation_goals:
                accepted = []
                for i, claim in enumerate(explanation.claims):
                    if claim.goal_id != goal.id:
                        continue
                    judgment = [j for j in review.judgments if j.claim_index == i and j.goal_id == goal.id]
                    ids = set(goal_snippets[goal.id]) if goal.kind=='cause' else {x['id'] for x in snippets}
                    audit_ok=len(judgment)==1 and judgment[0].supported
                    identity_ok=set(claim.evidence_ids)<=ids
                    cited=[x for x in snippets if x['id'] in claim.evidence_ids and x['id'] in ids]
                    source_years=verified_narrative_years(cited) if goal.kind=='cause' else {f.year for f in [*result.facts,*context_facts]}
                    quantity_ok=safe_narrative_claim(claim.text,source_years,[x['page'] for x in cited])
                    supported = audit_ok and identity_ok
                    if goal.kind=='concept':supported = supported and not claim.evidence_ids
                    supported = supported and quantity_ok
                    if goal.kind == 'cause':
                        supported = supported and bool(claim.evidence_ids) and not is_evidence_limit(claim.text)
                    cause_trace['goals'][goal.id]['claims'].append(dict(index=i,audit_supported=audit_ok,identity_valid=identity_ok,has_evidence=bool(claim.evidence_ids),quantity_valid=quantity_ok,accepted=supported))
                    checks.append(Check(name=f'{goal.id}_claim_{i}', status='pass' if supported else 'unknown', detail=judgment[0].detail if judgment else '未取得独立审核'))
                    if supported:
                        accepted.append(claim)
                        pages=sorted({x['page'] for x in cited}) if goal.kind=='cause' else []
                        prose.append(claim.text+('（原件 PDF 第 '+'、'.join(map(str,pages))+' 页）' if pages else ''))
                        for snippet in snippets:
                            if snippet['id'] in claim.evidence_ids:
                                refs.append(dict(id=snippet['id'], type='reference', text=snippet['text'],
                                    paper_path=snippet['source_path'], source_title=f'{snippet["company"]}{snippet["year"]}{snippet["period"]}报告',
                                    document_version=snippet['document_version'], page_start=snippet['page'], page_end=snippet['page']))
                complete = bool(accepted) and goal.id not in explanation.incomplete_goals
                if accepted and goal.kind=='concept':
                    # 会计目录提供固定定义和公式，模型组织语言，财务数值由程序呈现。
                    prose.extend(f'{definition["name"]}：{definition["definition"]}。'
                        for definition in payload['concept_definitions'].get(goal.id,{}).values())
                model_failed=bool(explanation_failure and (goal.kind!='cause' or goal_snippets[goal.id]))
                rejected=not accepted and any(claim.goal_id==goal.id for claim in explanation.claims)
                retained='，已核实数字保留。' if result.facts else '。'
                failure_detail=('解释未通过核验' if rejected else
                    '解释生成超时' if explanation_failure=='model_timeout' else '解释生成或审核暂未完成')+retained
                result.goals.append(GoalResult(id=goal.id, kind=goal.kind, status='completed' if complete else 'partial' if accepted else 'failed' if model_failed or rejected else 'no_data',
                    evidence_ids=[id for claim in accepted for id in claim.evidence_ids], detail='' if complete else failure_detail if model_failed or rejected else '解释依据尚未确认'))
                if not complete:
                    prose.append(failure_detail if model_failed or rejected else '经营原因尚未找到足够原文依据。' if goal.kind == 'cause' else '概念解释尚未通过核对。')
                    checks.append(Check(name=goal.id, status='unknown', detail='该解释目标未完成'))
        refs = list({r['id']: r for r in refs}.values())
        return dict(executed=result, references=refs, evidence_checks=checks, prose=prose,prose_by_goal=prose_by_goal)

    async def _verify(self, s):
        await s['emit']('plan', {'label': '核验结果', 'detail': '分别核对数字、要求和证据支持度'})
        from .publication import prepare_publication,verify_publication
        bundle=prepare_publication(s)
        policy=getattr(s.get('repository'),'numeric_source_policy','strict')
        known={f.id:f for f in [*bundle.execution.support_facts,
            *(f for _,block in bundle.execution.blocks for f in block.support_facts),
            *s.get('context_facts',[]),*bundle.execution.facts]}
        support_ids=({id for f in known.values() for id in f.inputs}
            | {id for item in bundle.execution.derived for id in item['inputs']}
            | {id for item in bundle.computed_references for id in item.inputs})-set(known)
        attempted=set(known)
        # 在限定深度内展开完整来源链，包含派生指标使用的基础事实。
        for _ in range(8):
            if not support_ids or not s.get('repository'): break
            if len(attempted | support_ids)>2000: break
            batch=await asyncio.to_thread(s['repository'].by_ids,sorted(support_ids))
            attempted.update(support_ids)
            known.update({f.id:f for f in batch})
            support_ids={id for f in batch for id in f.inputs}-attempted
        support=list(known.values())
        source_audit=SourceAudit(support,[*bundle.execution.derived,*bundle.computed_references])
        if policy=='ocr_trial':
            # 批次标签保留混合状态；本轮提示和每项计算、图表只看实际引用的完整来源链。
            for fact in bundle.execution.facts:
                bundle.bindings['facts'][fact.id]['source_audit_complete']=source_audit.complete(fact.id)
            for block in [bundle.execution,*[block for _,block in bundle.execution.blocks]]:
                for calculation in block.derived:
                    complete=source_audit.complete(calculation['id'])
                    calculation['source_audit_complete']=complete
                    if not complete and calculation['value'] is not None:calculation['status']='unverified'
                    bundle.bindings['calculations'][calculation['id']]['source_audit_complete']=complete
            for reference in bundle.computed_references:
                owners=[id for id,values in s.get('context_computed_by_goal',{}).items()
                    if any(value.id==reference.id for value in values)]
                bundle.bindings['calculations'][reference.id]=dict(goal_ids=owners,purpose='reference',
                    source_audit_complete=source_audit.complete(reference.id))
            for i,chart in enumerate(bundle.execution.charts):
                bundle.bindings['charts'][str(i)]['source_audit_complete']=source_audit.chart_complete(chart)
            affected_facts=[fact.id for fact in bundle.execution.facts if not source_audit.complete(fact.id)]
            affected_calculations=list(dict.fromkeys([item['id'] for item in bundle.execution.derived
                if not source_audit.complete(item['id'])]+[item.id for item in bundle.computed_references
                if not source_audit.complete(item.id)]))
            affected_charts=[str(i) for i,chart in enumerate(bundle.execution.charts) if not source_audit.chart_complete(chart)]
            if affected_facts or affected_calculations or affected_charts:
                notice='【OCR宽松入库试验】本轮涉及尚未完成原件行列核验的引用值或派生输入，相关数值仅用于验证处理和问答链路。'
                bundle.content=notice+'\n\n'+bundle.content
                if s['request'].goals and all(s['request'].for_goal(g).conditions.presentation.format=='table' for g in s['request'].goals):
                    bundle.content=table_only_content(bundle.content)
                bundle.bindings['data_notice']=dict(policy='ocr_trial',source_audit_complete=False,text=notice,
                    fact_ids=affected_facts,calculation_ids=affected_calculations,chart_ids=affected_charts)
        verification = verify(s['request'], bundle.execution, s['semantic'].checks, s['evidence_checks'], support,
                              numeric_source_policy=policy,computed_references=bundle.computed_references,
                              source_audit=source_audit)
        verification=verify_publication(s['request'],bundle,verification,s['previous'])
        bundle.seal()
        return dict(verification=verification,pending_publication=bundle)

    async def _publish(self, s):
        request, dialogue = s['request'], s['dialogue']
        from .condition_lifetimes import is_reference_turn,persist_pending
        reference_turn=is_reference_turn(s['understanding'])
        if s.get('context_reference_failed'):dialogue.recent_facts=[];dialogue.recent_computed=[]
        if request.clarification and 'executed' not in s:
            content = '\n\n'.join([*request.clarification, *request.unsupported])
            verification = Verification(numeric=[], request=s['semantic'].checks,
                evidence=[], status='partial')
            result, refs = ExecutionResult(), []
            if any(g.kind in {'lookup','compare','rank','chart','quote','cause','sign'} for g in request.goals):
                if not reference_turn:
                    dialogue.pending=persist_pending(request,s['understanding'],s['previous'],dialogue.session_preferences)
                dialogue.recent_facts = []
                dialogue.recent_computed = []
            outcome = 'needs_clarification' if request.clarification else 'unsupported'
        else:
            from copy import deepcopy
            bundle=s['pending_publication'];bundle.assert_sealed()
            result, refs, verification = deepcopy(bundle.execution),deepcopy(bundle.references),s['verification']
            if verification.status == 'fail':
                content = '本轮结果没有通过完整核验，尚未发布财务正文。'
                result.facts, result.charts, result.derived, result.comparisons, refs = [], [], [], [], []
                for goal in result.goals:
                    if goal.status=='completed':goal.status='failed';goal.detail='待发布内容未通过核验'
            else:
                content=bundle.content
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
            if not reference_turn and any(g.kind in {'lookup','compare','rank','chart','quote','cause','sign'} for g in request.goals):
                blocked=[goal for goal in request.goals if goal.clarification]
                dialogue.pending=persist_pending(request,s['understanding'],s['previous'],dialogue.session_preferences,goals=blocked)
        if request.conditions.presentation.format=='table' and ('executed' not in s or verification.status=='fail'):
            content=table_only_content(content)
        if getattr(s.get('repository'),'numeric_source_policy','strict')=='ocr_trial':
            chart_bindings=s['pending_publication'].bindings['charts'] if s.get('pending_publication') else {}
            charts=[chart_compat(chart,source_audit_complete=chart_bindings.get(str(i),{}).get('source_audit_complete',False))
                for i,chart in enumerate(result.charts)]
        else:
            charts=[chart_compat(chart) for chart in result.charts]
        fact_payload = []
        for f in result.facts:
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
                publication={'fingerprint':s['pending_publication'].fingerprint,'bindings':s['pending_publication'].bindings,
                    'published':verification.status!='fail'} if s.get('pending_publication') else None,
                structured_trace=s['budget'].artifacts.get('structured_trace',[]),
                model_wire_outputs=s['budget'].artifacts.get('model_wire_outputs',[]),
                literal_bindings=s['budget'].artifacts.get('literal_bindings',[]),
                cause_evidence=s['budget'].artifacts.get('cause_evidence',[]),
                semantic_audit=s['semantic'].model_dump(mode='json'),
                **({'transport_normalizations':s['budget'].artifacts['transport_normalizations']} if s['budget'].artifacts.get('transport_normalizations') else {}),
                **({'format_repairs':s['budget'].artifacts['format_repairs']} if s['budget'].artifacts.get('format_repairs') else {})))}

    async def run(self, question: str, history: list[dict], turn_id: str, deadline: float, emit):
        budget = Budget(deadline=min(deadline - 10, time.time() + 240))
        try:
            async with asyncio.timeout(max(0.001, budget.remaining())):
                result = await self.graph.ainvoke(dict(question=question, history=history, turn_id=turn_id,
                    budget=budget, emit=emit))
        except Exception as exc:
            # 诊断保留模型JSON与条件变化；不记录鉴权配置或驱动异常中的私有连接信息。
            exc.diagnostics={**(getattr(exc,'diagnostics',{}) if isinstance(exc,ModelFailure) else {}),**dict(model_calls=budget.calls,timings=budget.timings,
                structured_trace=budget.artifacts.get('structured_trace',[]),
                model_wire_outputs=budget.artifacts.get('model_wire_outputs',[]),
                correction_feedback=budget.artifacts.get('correction_feedback',[])),
                **({'transport_normalizations':budget.artifacts['transport_normalizations']} if budget.artifacts.get('transport_normalizations') else {}),
                **({'format_repairs':budget.artifacts['format_repairs']} if budget.artifacts.get('format_repairs') else {})}
            raise
        return result['result']
