"""作品说明：分别判定数字一致性、请求满足度和证据支持度，单项通过不代表整轮完成。"""
from __future__ import annotations

from decimal import Decimal

from .contracts import Check, Request, Verification
from .catalog import METRICS
from .evidence import checked_source
from .executor import ExecutionResult


def verify(request: Request, result: ExecutionResult, semantic_checks: list[Check],
           evidence_checks: list[Check], support_facts=()) -> Verification:
    c = request.conditions
    indexed = {f.id: f for f in [*support_facts, *result.facts]}
    verified={}
    def verify_fact(fact, ancestors=frozenset()):
        if fact.id in verified: return verified[fact.id]
        if fact.id in ancestors or len(ancestors)>8: return False
        metric=METRICS.get(fact.metric)
        ok=bool(metric and fact.unit==metric.unit and fact.scope in metric.scopes)
        if fact.status=='verified':
            ok=ok and checked_source(fact)
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
    for fact in result.facts:
        ok=verify_fact(fact)
        numeric.append(Check(name=fact.id, status='pass' if ok else 'fail', detail='事实原件或派生公式一致' if ok else '事实来源或派生公式未通过'))
    for d in result.derived:
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
            explicit_cross_period=selected.comparison_axis=='years' and selected.calculation!='yoy' and len(selected.time.pairs or [])==2 and {(a.year,a.period),(b.year,b.period)}==set(selected.time.pairs)
            ok = ok and a.metric == b.metric and a.scope == b.scope and (a.period==b.period or explicit_cross_period) and a.unit == b.unit and a.data_version == b.data_version
            ok = ok and (a.stock_code == b.stock_code if selected.comparison_axis == 'years' else a.year == b.year)
        numeric.append(Check(name=d['id'], status='pass' if ok else 'fail', detail='派生公式核对'))
    for comparison in result.comparisons:
        a,b=indexed.get(comparison['first']),indexed.get(comparison['second'])
        ok=bool(a and b)
        if ok:
            relation='greater' if a.decimal>b.decimal else 'less' if a.decimal<b.decimal else 'equal'
            ok=(verify_fact(a) and verify_fact(b) and comparison['relation']==relation and
                (a.metric,a.scope,a.period,a.unit,a.data_version)==(b.metric,b.scope,b.period,b.unit,b.data_version) and
                (a.year==b.year if comparison['axis']=='companies' else a.stock_code==b.stock_code))
        numeric.append(Check(name='comparison_'+comparison['first']+'_'+comparison['second'],status='pass' if ok else 'fail',detail='比较结论与准确事实及比较对象一致'))
    selections = [(selected.conditions, block.selections) for selected, block in result.blocks] or [(c, result.selections)]
    matching = all(any(f.stock_code in selected.codes and f.metric in selected.metrics and f.scope == selected.scope
        and (f.year, f.period) in periods.get(f.stock_code, []) for selected, periods in selections) for f in result.facts)
    request_checks = [*semantic_checks,
        Check(name='supported_components', status='unknown' if request.unsupported else 'pass', detail='；'.join(request.unsupported) or '请求没有未支持的组成部分'),
        Check(name='selected_conditions', status='pass' if matching else 'fail', detail='实际事实逐项符合公司、指标、范围和报告期'),
        Check(name='coverage', status='pass' if not result.missing else 'unknown', detail='没有缺失项' if not result.missing else f'有 {len(result.missing)} 个请求项无核实事实，未补零或换年'),
        Check(name='negative_constraints', status='pass' if not (c.restrictions.no_query and result.facts or c.restrictions.no_chart and result.charts) else 'fail', detail='不查数字、不画图等约束'),
    ]
    from .request_bindings import explicit_presentation_bindings
    for field,value in explicit_presentation_bindings(request.question).items():
        applicable=[g for g in request.goals if g.kind==('chart' if field.endswith('chart_type') else 'rank')]
        if not applicable and not any(g.kind in {'lookup','compare','rank','chart','quote','cause','sign'} for g in request.goals):continue
        ok=all(getattr(request.for_goal(g).conditions.presentation,field.split('.')[-1])==value for g in applicable)
        if field.endswith('chart_type'):ok=ok and (bool(applicable) or bool(request.clarification))
        request_checks.append(Check(name='literal_'+field,status='pass' if ok else 'fail',detail='最终展示条件对照用户原话'))
    for chart in result.charts:
        selected = next((r.conditions for r, block in result.blocks if chart in block.charts), c)
        ok = chart['chart_type'] == selected.presentation.chart_type and (not selected.presentation.unit or chart['unit'] == selected.presentation.unit or
            chart['chart_type']=='scatter' and all(u==selected.presentation.unit for u in chart['axis_units']))
        ok = ok and all(id in indexed for id in chart['source_fact_ids'])
        request_checks.append(Check(name='chart_' + chart['metric'], status='pass' if ok else 'fail', detail='图表类型、单位和事实引用'))
    # 作品说明：真实来源编号不能证明图点正确；用核实的十进制事实和公式重建各轴及点位逐项比较。
    from .executor import build_charts
    for selected,block in result.blocks or [(request,result)]:
        expected=ExecutionResult(selections=block.selections,facts=block.facts,derived=block.derived)
        build_charts(selected,expected)
        if result.blocks:
            for chart in expected.charts:chart['goal_ids']=[g.id for g in selected.goals if g.kind=='chart']
        request_checks.append(Check(name='chart_points',status='pass' if expected.charts==block.charts else 'fail',detail='逐点重建图表，核对原始精度、负数、空点和派生输入'))
    incomplete = [g for g in result.goals if g.status != 'completed']
    goal_ids_match = {g.id for g in result.goals} == {g.id for g in request.goals} and len(result.goals) == len(request.goals)
    request_checks.append(Check(name='goals', status='pass' if goal_ids_match and not incomplete else 'unknown', detail='逐项目标按ID和实际状态判定'))
    checks = [*numeric, *request_checks, *evidence_checks]
    status = 'fail' if any(ch.status == 'fail' for ch in checks) else 'partial' if any(ch.status == 'unknown' for ch in checks) else 'pass'
    return Verification(numeric=numeric, request=request_checks, evidence=evidence_checks, status=status)
