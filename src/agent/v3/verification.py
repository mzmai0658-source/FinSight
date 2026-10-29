"""分别判定数字一致性、请求满足度和证据支持度，单项通过不代表整轮完成。"""
from __future__ import annotations

from decimal import Decimal

from .contracts import Check, Request, Verification
from .catalog import METRICS
from .evidence import checked_source
from .executor import ExecutionResult


class SourceAudit:
    """沿实际引用值及完整输入链核对原件审核状态，缺项与循环保留为未完成。"""

    def __init__(self, facts, calculations=()):
        self.facts = {fact.id: fact for fact in facts}
        self.calculations = {item['id'] if isinstance(item, dict) else item.id:
            item if isinstance(item, dict) else item.model_dump(mode='json') for item in calculations}
        self._complete = {}
        self._sources = {}

    def checked(self, fact):
        if fact.id not in self._sources:
            self._sources[fact.id] = checked_source(fact)
        return self._sources[fact.id]

    def complete(self, identifier, ancestors=frozenset()):
        if identifier in ancestors or len(ancestors)>8:
            return False
        if identifier in self._complete:
            return self._complete[identifier]
        fact = self.facts.get(identifier)
        if fact is not None:
            complete = fact.status=='verified' and self.checked(fact)
            if fact.status=='derived' or complete and fact.inputs:
                complete = self.inputs_complete(fact.inputs, ancestors | {identifier})
        else:
            item = self.calculations.get(identifier)
            complete = bool(item and len(item.get('inputs', []))==2 and
                self.inputs_complete(item['inputs'], ancestors | {identifier}))
        self._complete[identifier] = complete
        return complete

    def inputs_complete(self, identifiers, ancestors=frozenset()):
        return bool(identifiers) and all(self.complete(identifier, ancestors) for identifier in identifiers)

    def chart_complete(self, chart):
        return self.inputs_complete([*chart.get('source_fact_ids', []), *chart.get('source_derived_ids', [])])


def verify(request: Request, result: ExecutionResult, semantic_checks: list[Check],
           evidence_checks: list[Check], support_facts=(), *, numeric_source_policy='strict',
           computed_references=(), source_audit=None) -> Verification:
    if numeric_source_policy not in {'strict', 'ocr_trial'}:
        raise ValueError('Unknown numeric source policy')
    c = request.conditions
    indexed = {f.id: f for f in [*support_facts, *result.support_facts,
        *(f for _,block in result.blocks for f in block.support_facts), *result.facts]}
    source_audit = source_audit or SourceAudit(indexed.values(), [*result.derived, *computed_references])
    verified={}
    def verify_fact(fact, ancestors=frozenset()):
        if fact.id in verified: return verified[fact.id]
        if fact.id in ancestors or len(ancestors)>8: return False
        metric=METRICS.get(fact.metric)
        ok=bool(metric and fact.unit==metric.unit and fact.scope in metric.scopes)
        if fact.status=='verified':
            ok=ok and source_audit.checked(fact)
        elif fact.status=='unverified' and numeric_source_policy=='ocr_trial':
            from src.etl.ocr_trial import trial_source_ok
            ok=ok and trial_source_ok(fact)
        elif fact.status=='derived' and metric and len(metric.formula)==2 and len(fact.inputs)==2:
            bases=[indexed.get(id) for id in fact.inputs]
            ok=ok and all(bases)
            if ok:
                a,b=bases
                ok=tuple(base.metric for base in bases)==metric.formula and all(
                    base.stock_code==fact.stock_code and base.year==fact.year and base.period==fact.period
                    and base.scope==fact.scope and base.data_version==fact.data_version and base.unit=='元'
                    and verify_fact(base,ancestors | {fact.id}) for base in bases)
                from decimal import localcontext
                with localcontext() as context:
                    context.prec=50
                    expected=a.decimal-b.decimal if fact.metric=='gross_profit' else a.decimal/b.decimal*100 if b.decimal else None
                formula=' - '.join(metric.formula) if fact.metric=='gross_profit' else ' / '.join(metric.formula)+' * 100'
                ok=ok and expected==fact.decimal and fact.formula==formula
                def source_versions(base,seen=frozenset()):
                    if base.id in seen: return set()
                    if base.source: return {base.source.document_version}
                    return set().union(*(source_versions(indexed[id],seen | {base.id}) for id in base.inputs if id in indexed))
                ok=ok and len(set().union(*(source_versions(base) for base in bases)))==1
        else:
            ok=False
        verified[fact.id]=ok
        return ok
    numeric = []
    published_ids=[*(fact.id for fact in result.facts), *(item['id'] for item in result.derived),
        *(reference.id for reference in computed_references)]
    if numeric_source_policy=='ocr_trial' and (any(not source_audit.complete(id) for id in published_ids)
            or any(not source_audit.chart_complete(chart) for chart in result.charts)):
        numeric.append(Check(name='original_source_audit',status='unknown',
            detail='本轮引用值或派生输入未完成原件行列核验，不代表数字已经核实'))
    for fact in result.facts:
        ok=verify_fact(fact)
        pending_source=ok and not source_audit.complete(fact.id)
        numeric.append(Check(name=fact.id, status='unknown' if pending_source else 'pass' if ok else 'fail',
            detail='OCR候选值或派生公式自洽，原件行列尚未完成核验' if pending_source else '事实原件或派生公式一致' if ok else '事实来源或派生公式未通过'))
    for d,reference in [*((item,None) for item in result.derived),
            *((item.model_dump(mode='json'),item) for item in computed_references)]:
        ok = len(d['inputs']) == 2 and all(id in indexed for id in d['inputs'])
        if ok:
            a, b = [indexed[id] for id in d['inputs']]
            ok=verify_fact(a) and verify_fact(b)
            from decimal import localcontext
            with localcontext() as context:
                context.prec = 50
                if d['formula'] == '(current-base)/abs(base)*100':
                    expected = (b.decimal - a.decimal) / abs(a.decimal) * 100 if a.decimal else None
                elif d['formula']=='(first-second)/abs(second)*100':
                    expected=(a.decimal-b.decimal)/abs(b.decimal)*100 if b.decimal else None
                elif d['formula'] == 'first-second':
                    expected = a.decimal - b.decimal
                else:
                    expected = b.decimal - a.decimal
            ok = ok and ((d['value'] is None and expected is None) or (expected is not None and Decimal(d['value']) == expected))
            selected = next((r.conditions for r, block in result.blocks if d in block.derived), c)
            axis=reference.comparison_axis if reference else selected.comparison_axis
            calculation=reference.calculation if reference else selected.calculation
            pairs=[(a.year,a.period),(b.year,b.period)] if reference else selected.time.pairs or []
            explicit_cross_period=axis=='years' and calculation!='yoy' and (len(pairs)==2 and {(a.year,a.period),(b.year,b.period)}==set(pairs)
                or not reference and bool(selected.calculation_inputs))
            ok = ok and a.metric == b.metric and a.scope == b.scope and (a.period==b.period or explicit_cross_period) and a.unit == b.unit and a.data_version == b.data_version
            ok = ok and (a.stock_code == b.stock_code if axis == 'years' else a.year == b.year)
            current=a if axis=='companies' else b
            unit='%' if calculation in {'yoy','relative_percent'} else '百分点' if calculation=='percentage_points' else a.unit
            formula=('(first-second)/abs(second)*100' if axis=='companies' else '(current-base)/abs(base)*100') if calculation in {'yoy','relative_percent'} else 'first-second' if axis=='companies' else 'current-base'
            company=b.company if axis=='years' else a.company+'−'+b.company
            # 算式正确仍可能贴错公司、期间、指标或单位，发布时分别从输入角色重建。
            ok=ok and (d.get('company'),d.get('year'),d.get('period'),d.get('metric'),d.get('unit'),d.get('formula'))==(
                company,current.year,current.period,current.metric,unit,formula)
            calculation_label={'yoy':'同比增长率','relative_percent':'相对百分比变化','difference':'差额','percentage_points':'百分点变化'}.get(calculation,'')
            label='相对变化率' if d['value'] is None else ('母公司' if current.scope=='parent' else '合并')+METRICS[current.metric].label+' '+calculation_label
            # 旧计算引用可使用原有的通用计算名称；公司、期间、指标和单位仍须逐项相符。
            ok=ok and (d.get('label')==label or reference is not None and d.get('label')==calculation_label)
            if not reference:
                ok=ok and all(fact.stock_code in selected.codes and fact.metric in selected.metrics and fact.scope==selected.scope for fact in (a,b))
            if calculation=='yoy' and axis=='years':ok=ok and b.year==a.year+1
            if not reference and selected.calculation_inputs:
                from .calculation_plan import explicit_pairs
                ok=ok and any((first.id,second.id)==(a.id,b.id) for first,second in explicit_pairs(selected,[a,b]))
            if reference:
                ok=ok and (reference.data_version,reference.metric,reference.scope)==(a.data_version,a.metric,a.scope)
        pending_source=ok and not source_audit.complete(d['id'])
        numeric.append(Check(name=d['id'], status='unknown' if pending_source else 'pass' if ok else 'fail',
            detail='派生公式自洽，输入原件未完整核验' if pending_source else '派生公式核对'))
    for comparison in result.comparisons:
        a,b=indexed.get(comparison['first']),indexed.get(comparison['second'])
        ok=bool(a and b)
        if ok:
            relation='greater' if a.decimal>b.decimal else 'less' if a.decimal<b.decimal else 'equal'
            ok=(verify_fact(a) and verify_fact(b) and comparison['relation']==relation and
                (a.metric,a.scope,a.period,a.unit,a.data_version)==(b.metric,b.scope,b.period,b.unit,b.data_version) and
                (a.year==b.year if comparison['axis']=='companies' else a.stock_code==b.stock_code))
        pending_source=ok and not source_audit.inputs_complete([a.id,b.id])
        numeric.append(Check(name='comparison_'+comparison['first']+'_'+comparison['second'],status='unknown' if pending_source else 'pass' if ok else 'fail',detail='比较结论与输入事实及比较对象一致；输入来源状态另行保留'))
    selections = [(selected.conditions, block.selections) for selected, block in result.blocks] or [(c, result.selections)]
    matching = all(any(f.stock_code in selected.codes and f.metric in selected.metrics and f.scope == selected.scope
        and (f.year, f.period) in periods.get(f.stock_code, []) for selected, periods in selections) for f in result.facts)
    # 排名排除缺失公司后仍须满足名次数量；独立查数的缺失不能借排名状态放行。
    coverage_complete=not result.missing
    if result.missing and result.blocks:
        coverage_complete=all(not block.missing or selected.goals and all(g.kind=='rank' for g in selected.goals)
            and all(g.status=='completed' for g in block.goals)
            and all(item['reason'] in {'no_verified_fact','report_not_in_catalog','no_verified_yoy_base','undefined_ranking_value'} for item in block.missing)
            for selected,block in result.blocks)
    request_checks = [*semantic_checks,
        Check(name='supported_components', status='unknown' if request.unsupported else 'pass', detail='；'.join(request.unsupported) or '请求没有未支持的组成部分'),
        Check(name='selected_conditions', status='pass' if matching else 'fail', detail='实际事实逐项符合公司、指标、范围和报告期'),
        Check(name='coverage', status='pass' if coverage_complete else 'unknown', detail='没有缺失项' if not result.missing else f'有 {len(result.missing)} 个请求项无核实事实，未补零或换年；排名仅纳入有核实数值的公司'),
        Check(name='negative_constraints', status='pass' if all(not (
            selected.conditions.restrictions.no_query and block.facts and block.input_kind!='reference'
            or selected.conditions.restrictions.no_chart and block.charts)
            for selected,block in result.blocks or [(request,result)]) else 'fail', detail='新查询与历史引用分别核对不查数、不画图等约束'),
    ]
    # 输出核验只对照已编译条件与实际内容；不再以全句词表重新解释图形、单位或引用方式。
    for chart in result.charts:
        selected = next((r.conditions for r, block in result.blocks if chart in block.charts), c)
        # 图表各轴单位由相同指标/计算展示规则重建，金额的单位不强制施加到百分比轴。
        ok = chart['chart_type'] == selected.presentation.chart_type
        ok = ok and all(id in indexed for id in chart['source_fact_ids'])
        request_checks.append(Check(name='chart_' + chart['metric'], status='pass' if ok else 'fail', detail='图表类型、单位和事实引用'))
    # 真实来源编号不能证明图点正确；用核实的十进制事实和公式重建各轴及点位逐项比较。
    from .executor import build_charts
    for selected,block in result.blocks or [(request,result)]:
        if block.input_kind=='reference':continue
        expected=ExecutionResult(selections=block.selections,facts=block.facts,derived=block.derived)
        build_charts(selected,expected)
        if result.blocks:
            for chart in expected.charts:chart['goal_ids']=[g.id for g in selected.goals if g.kind=='chart']
        request_checks.append(Check(name='chart_points',status='pass' if expected.charts==block.charts else 'fail',detail='逐点重建图表，核对原始精度、负数、空点和派生输入'))
    incomplete = [g for g in result.goals if g.status != 'completed']
    for goal in request.goals:
        if goal.clarification:continue
        block=result.for_goal(goal.id);selected=request.for_goal(goal)
        status=next((item for item in result.goals if item.id==goal.id),None)
        if status is None or status.status!='completed':continue
        delivered=True
        if goal.kind in {'lookup','compare','rank','chart','sign'}:delivered=bool(block.facts)
        if goal.kind=='chart':delivered=delivered and bool(block.charts)
        if goal.kind=='compare':delivered=delivered and (bool(block.comparisons) or block.value_ordered)
        if goal.kind in {'lookup','compare','rank','chart'} and selected.conditions.calculation!='none':
            delivered=delivered and bool(block.derived)
        if goal.kind in {'quote','cause'}:delivered=bool(status.evidence_ids)
        request_checks.append(Check(name='goal_deliverable_'+goal.id,status='pass' if delivered else 'unknown',detail='完成状态必须有对应事实、计算、图表或证据，空列表不算完成'))
    goal_ids_match = {g.id for g in result.goals} == {g.id for g in request.goals} and len(result.goals) == len(request.goals)
    request_checks.append(Check(name='goals', status='pass' if goal_ids_match and not incomplete else 'unknown', detail='逐项目标按ID和实际状态判定'))
    checks = [*numeric, *request_checks, *evidence_checks]
    status = 'fail' if any(ch.status == 'fail' for ch in checks) else 'partial' if any(ch.status == 'unknown' for ch in checks) else 'pass'
    return Verification(numeric=numeric, request=request_checks, evidence=evidence_checks, status=status)
