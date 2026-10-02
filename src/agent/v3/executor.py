"""作品说明：编译财务条件并用Decimal计算，使准确数值独立于模型撰写的语言。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP, localcontext

from .catalog import METRICS
from .contracts import Fact, GoalResult, Request
from .repository import identity_digest

PERIOD_LABELS = {'FY': '年报', 'HY': '上半年累计', 'Q1': '一季度累计', 'Q3': '前三季度累计'}
MONEY_UNITS = {'元': Decimal(1), '万元': Decimal(10000), '亿元': Decimal(100000000)}


@dataclass
class ExecutionResult:
    selections: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    time_rule: str = ''
    facts: list[Fact] = field(default_factory=list)
    support_facts: list[Fact] = field(default_factory=list)
    derived: list[dict] = field(default_factory=list)
    comparisons: list[dict] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    charts: list[dict] = field(default_factory=list)
    goals: list[GoalResult] = field(default_factory=list)
    blocks: list[tuple[Request, 'ExecutionResult']] = field(default_factory=list)

    def for_goal(self, goal_id):
        return next((result for request, result in self.blocks if any(g.id == goal_id for g in request.goals)), self)


def resolve_periods(request: Request, reports: list[dict]) -> tuple[dict, str]:
    c = request.conditions
    catalog = defaultdict(set)
    for r in reports:
        catalog[r['stock_code']].add((int(r['year']), r['period']))
    if c.time.pairs:
        return {code:sorted(set(c.time.pairs)) for code in c.codes}, '明确的逐项年份与报告期配对；不扩成其他组合'
    if c.time.mode == 'calendar_years' and c.time.span:
        from datetime import date
        latest = date.today().year-1
        return {code:[(y,p) for y in range(latest-c.time.span+1,latest+1) for p in c.time.periods] for code in c.codes}, f'最近 {c.time.span} 个已结束自然年，止于 {latest}；缺年不替换'
    if c.time.mode in {'explicit', 'calendar_years'}:
        selections = {code: [(year, period) for year in c.time.years for period in c.time.periods] for code in c.codes}
        return selections, '明确年份和报告期；缺失不换年补数'
    if c.time.span:
        candidates={code:{(year,period) for year,period in catalog[code] if period in c.time.periods} for code in c.codes}
        common=set.intersection(*(candidates[code] for code in c.codes)) if c.codes else set()
        selected={}
        for code in c.codes:
            years=common if c.time.mode=='latest_common' else candidates[code]
            selected[code]=sorted(years,key=lambda pair:(pair[0],{'Q1':1,'HY':2,'Q3':3,'FY':4}[pair[1]]))[-c.time.span:]
        return selected, f'选择{"共同拥有" if c.time.mode=="latest_common" else "各自已入库"}的最近 {c.time.span} 份指定报告；自然年查询另按完整日历年份处理'
    wanted = set(c.time.periods)
    candidates = {code: {(y, p) for y, p in catalog[code] if p in wanted} for code in c.codes}
    if c.time.mode == 'latest_common':
        common = set.intersection(*(candidates[code] for code in c.codes)) if c.codes else set()
        # 作品说明：报告期不能互换；各类期间分别选择最新数据，共同期为空时明确保留缺失。
        selected = [(max(y for y, pp in common if pp == p), p) for p in c.time.periods if any(pp == p for _, pp in common)]
        return {code: selected for code in c.codes}, '各公司共同拥有的最新报告期；无共同期时不改为各自最新'
    return {code: [(max(y for y, pp in values if pp == p), p) for p in c.time.periods if any(pp == p for _, pp in values)] for code, values in candidates.items()}, '各公司各自最新报告期；默认查询最新年报'


def display_value(value: str | Decimal, unit: str, output_unit: str | None, decimals: int | None) -> tuple[str, str]:
    target = output_unit or ('万元' if unit == '元' else unit)
    number = Decimal(value)
    if unit == '元':
        if target not in MONEY_UNITS:
            raise ValueError('Incompatible monetary unit')
        number /= MONEY_UNITS[target]
    elif target != unit:
        raise ValueError('Per-share and ratio units cannot be converted as total money')
    digits = decimals if decimals is not None else (4 if unit == '元/股' else 2)
    with localcontext() as context:
        context.prec = 60
        number = number.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    if number == 0:
        number = abs(number)
    return f'{number:,.{digits}f}', target


def calculation_targets(request, selections):
    """作品说明：明确相对某年计算同比时，基期只作为输入，不额外要求计算基期自身同比。"""
    import re
    c=request.conditions
    if c.calculation=='yoy' and c.comparison_axis=='years':
        relations=re.findall(r'(20\d{2})年[^。；]{0,80}?(?:相对|相比|相较|对比|比|较)(20\d{2})年',request.question)
        valid={(int(current),int(base)) for current,base in relations if int(current)==int(base)+1 and {int(current),int(base)}==set(c.time.years)}
        if len(valid)==1:
            current,_=next(iter(valid))
            return {code:[pair for pair in pairs if pair[0]==current] for code,pairs in selections.items()}
    return selections


def calculate(request: Request, result: ExecutionResult):
    c = request.conditions
    if c.calculation == 'none':
        return
    if any(m.endswith('_reported_yoy') for m in c.metrics):
        result.notes.append('不能将已披露增长率再次当作基础金额计算同比。')
        return
    groups = defaultdict(list)
    axis = c.comparison_axis
    if axis == 'none':
        result.notes.append('计算需要明确比较年份还是比较公司。')
        return
    explicit_cross_period=axis=='years' and c.calculation!='yoy' and len(c.time.pairs or [])==2
    for f in {f.id:f for f in [*result.support_facts,*result.facts]}.values():
        key = (f.metric, f.scope, None if explicit_cross_period else f.period, f.year if axis == 'companies' else f.stock_code)
        groups[key].append(f)
    with localcontext() as context:
        context.prec = 50
        for values in groups.values():
            if axis == 'companies':
                values.sort(key=lambda f: c.codes.index(f.stock_code))
                pairs = [(values[0], values[1])] if len(values) == 2 else []
            else:
                values.sort(key=lambda f: (f.year,{'Q1':1,'HY':2,'Q3':3,'FY':4}[f.period]))
                if c.calculation in {'yoy', 'relative_percent', 'percentage_points'}:
                    pairs = list(zip(values, values[1:]))
                else:
                    pairs = [(values[0], values[-1])] if len(values) >= 2 else []
            for a, b in pairs:
                if c.calculation=='yoy' and result.selections and (b.year,b.period) not in calculation_targets(request,result.selections).get(b.stock_code,[]):continue
                if a.unit!=b.unit or a.data_version!=b.data_version:continue
                if c.calculation == 'yoy' and axis == 'years' and b.year != a.year + 1:
                    result.notes.append(f'{b.company}{b.year}年缺少相邻基期，不能跨年补算同比。')
                    continue
                if c.calculation == 'percentage_points' and a.unit != '%':
                    result.notes.append('百分点变化仅适用于两个百分比指标。')
                    continue
                if c.calculation in {'yoy', 'relative_percent'}:
                    base,current=(b,a) if axis=='companies' else (a,b)
                    if base.decimal == 0:
                        result.derived.append(dict(id=identity_digest({'inputs': [a.id, b.id], 'formula': c.calculation}),
                            value=None, unit='%', inputs=[a.id, b.id], formula='(first-second)/abs(second)*100' if axis=='companies' else '(current-base)/abs(base)*100',
                            label='相对变化率', company=current.company, year=current.year, period=current.period, metric=current.metric,
                            detail='零基期，增长率未定义', status='undefined'))
                        continue
                    value = (current.decimal - base.decimal) / abs(base.decimal) * 100
                    detail = '按绝对基期计算' if base.decimal < 0 else ''
                    if base.decimal < 0 < current.decimal:
                        detail+='；本指标由负转正'
                        if current.metric in {'net_profit','attributable_net_profit','deducted_attributable_net_profit'}:
                            detail+='，本指标口径下扭亏为盈'
                    unit, label = '%', '同比增长率' if c.calculation == 'yoy' else '相对百分比变化'
                    formula = '(first-second)/abs(second)*100' if axis=='companies' else '(current-base)/abs(base)*100'
                else:
                    # 作品说明：跨公司差额按提问顺序计算第一家公司减第二家；跨年差额计算本期减基期。
                    value = a.decimal - b.decimal if axis == 'companies' else b.decimal - a.decimal
                    unit = '百分点' if c.calculation == 'percentage_points' else a.unit
                    label, formula, detail = '百分点变化' if unit == '百分点' else '差额', 'first-second' if axis == 'companies' else 'current-base', ''
                if a.period!=b.period:
                    detail+='；两个累计期间长度不同，本结果为所请求期间的直接变化，不能称为同比'
                scope_label='母公司' if b.scope=='parent' else '合并'
                label=scope_label+METRICS[b.metric].label+' '+label
                if axis=='years':detail+=f'；比较{b.year} {PERIOD_LABELS[b.period]}与{a.year} {PERIOD_LABELS[a.period]}，后期减基期'
                result.derived.append(dict(id=identity_digest({'inputs': [a.id, b.id], 'formula': formula}),
                    value=format(value, 'f'), unit=unit, inputs=[a.id, b.id], formula=formula,
                    label=label, company=b.company if axis == 'years' else a.company + '−' + b.company,
                    year=b.year, period=b.period, metric=b.metric, detail=detail, status='verified'))


def compare_values(request:Request,result:ExecutionResult):
    c=request.conditions
    if not any(g.kind=='compare' for g in request.goals):return
    if c.comparison_axis=='none':
        result.notes.append('比较需要明确比较公司还是比较年份。')
        return
    groups=defaultdict(list)
    for fact in result.facts:
        groups[(fact.metric,fact.scope,fact.period,fact.year if c.comparison_axis=='companies' else fact.stock_code)].append(fact)
    for values in groups.values():
        values.sort(key=lambda fact:c.codes.index(fact.stock_code) if c.comparison_axis=='companies' else fact.year)
        if len(values)<2:continue
        for first,second in zip(values,values[1:]):
            if first.unit!=second.unit or first.data_version!=second.data_version:continue
            relation='greater' if first.decimal>second.decimal else 'less' if first.decimal<second.decimal else 'equal'
            result.comparisons.append(dict(first=first.id,second=second.id,relation=relation,axis=c.comparison_axis))


def build_calculated_charts(request,result):
    c=request.conditions;kind=c.presentation.chart_type
    for metric in c.metrics:
        values=[item for item in result.derived if item['metric']==metric]
        if not values:continue
        raw_unit=values[0]['unit']
        unit=c.presentation.unit or '万元' if raw_unit=='元' else raw_unit
        def exact(item):
            if item['value'] is None:return None
            value=Decimal(item['value'])
            return format(value/MONEY_UNITS[unit] if raw_unit=='元' else value,'f')
        if kind=='scatter':
            continue
        labels=sorted({(year,period) for pairs in result.selections.values() for year,period in pairs},
            key=lambda pair:(pair[0],{'Q1':1,'HY':2,'Q3':3,'FY':4}[pair[1]]))
        title=METRICS[metric].label+' '+values[0]['label']
        refs=list(dict.fromkeys(id for item in values for id in item['inputs']))
        if kind=='pie':
            if any(item['value'] is None or Decimal(item['value'])<0 for item in values):
                result.notes.append('饼图不能表达缺失或负的计算结果；本次未生成饼图。');continue
            if not any(Decimal(item['value'])>0 for item in values):
                result.notes.append('计算结果全部为零，无法计算饼图占比。');continue
            points=[dict(name=f"{item['company']} {item['year']} {PERIOD_LABELS[item['period']]}",value=float(exact(item)),
                value_exact=exact(item),derived_id=item['id'],fact_ids=item['inputs']) for item in values]
            result.charts.append(dict(version=3,chart_type=kind,title=title,unit=unit,metric=metric,decimals=c.presentation.decimals,
                x=[point['name'] for point in points],series=[dict(name=title,values=points)],source_fact_ids=refs,source_derived_ids=[item['id'] for item in values]))
            continue
        series=[]
        for company in dict.fromkeys(item['company'] for item in values):
            selected={(item['year'],item['period']):item for item in values if item['company']==company}
            entries=[selected.get(pair) for pair in labels]
            exact_values=[exact(item) if item else None for item in entries]
            series.append(dict(name=company,values=[float(value) if value is not None else None for value in exact_values],
                values_exact=exact_values,derived_ids=[item['id'] if item else None for item in entries],
                fact_ids=[item['inputs'][-1] if item else None for item in entries],values_formatted=[
                    display_value(item['value'],'%' if raw_unit=='百分点' else raw_unit,'%' if raw_unit=='百分点' else unit,c.presentation.decimals)[0]+' '+unit
                    if item and item['value'] is not None else item['detail'] if item else '暂无核实值' for item in entries]))
        if kind=='line' and not any(sum(value is not None for value in item['values'])>=2 for item in series):
            result.notes.append('核实计算结果只有单点，尚不能构成趋势线。');continue
        result.charts.append(dict(version=3,chart_type=kind,title=title,unit=unit,metric=metric,decimals=c.presentation.decimals,
            x=[f'{year} {PERIOD_LABELS[period]}' for year,period in labels],series=series,source_fact_ids=refs,source_derived_ids=[item['id'] for item in values]))
    if kind=='scatter':
        if len(c.metrics)!=2:
            result.notes.append('计算结果散点图需要两个明确指标。');return
        mx,my=c.metrics;paired={}
        for item in result.derived:paired.setdefault((item['company'],item['year'],item['period']),{})[item['metric']]=item
        points=[];units=[]
        for key,entries in paired.items():
            if mx not in entries or my not in entries or any(entries[m]['value'] is None for m in (mx,my)):continue
            pair=[entries[m] for m in (mx,my)];current_units=[c.presentation.unit or '万元' if d['unit']=='元' else d['unit'] for d in pair]
            if units and units!=current_units:raise ValueError('Calculated scatter units changed across points')
            units=current_units;exact_values=[format(Decimal(d['value'])/MONEY_UNITS[u] if d['unit']=='元' else Decimal(d['value']),'f') for d,u in zip(pair,units)]
            points.append(dict(name=f'{key[0]} {key[1]} {PERIOD_LABELS[key[2]]}',value=[float(v) for v in exact_values],
                value_exact=exact_values,derived_ids=[d['id'] for d in pair],fact_ids=list(dict.fromkeys(id for d in pair for id in d['inputs']))))
        if points:result.charts.append(dict(version=3,chart_type='scatter',title='计算结果指标配对',unit=' / '.join(units),axis_units=units,
            metric=mx,metrics=[mx,my],decimals=c.presentation.decimals,x=[],series=[dict(name='计算结果',values=points)],
            source_fact_ids=list(dict.fromkeys(id for point in points for id in point['fact_ids'])),source_derived_ids=[d['id'] for d in result.derived]))


def build_charts(request: Request, result: ExecutionResult):
    c = request.conditions
    if c.restrictions.no_chart or c.restrictions.no_query or not any(g.kind == 'chart' for g in request.goals):
        return
    if c.calculation!='none':
        build_calculated_charts(request,result)
        if not result.charts:result.notes.append('没有可核验的所请求计算图；未用基础金额冒充计算结果。')
        return
    if c.presentation.chart_type == 'scatter':
        if len(c.metrics) != 2:
            result.notes.append('散点图需要两个明确指标，分别作为横轴和纵轴。')
            return
        mx,my=c.metrics
        lookup={(f.stock_code,f.year,f.period,f.metric):f for f in result.facts}
        units=[c.presentation.unit or '万元' if METRICS[m].dimension=='money' else METRICS[m].unit for m in (mx,my)]
        points=[]
        for code,pairs in result.selections.items():
            for year,period in pairs:
                a,b=lookup.get((code,year,period,mx)),lookup.get((code,year,period,my))
                if not a or not b: continue
                exact=[format(f.decimal/MONEY_UNITS[u] if f.unit=='元' else f.decimal,'f') for f,u in zip((a,b),units)]
                points.append(dict(name=f'{a.company} {year} {PERIOD_LABELS[period]}',value=[float(v) for v in exact],value_exact=exact,fact_ids=[a.id,b.id]))
                points[-1]['value_formatted']=[display_value(f.value,f.unit,u,c.presentation.decimals)[0]+' '+u for f,u in zip((a,b),units)]
        if points:
            result.charts.append(dict(version=3,chart_type='scatter',title=f'{METRICS[mx].label}与{METRICS[my].label}',
                unit=' / '.join(units),decimals=c.presentation.decimals,metric=mx,metrics=[mx,my],axis_units=units,x=[],series=[dict(name='指标配对',values=points)],
                source_fact_ids=[id for p in points for id in p['fact_ids']]))
        return
    for metric in c.metrics:
        values = [f for f in result.facts if f.metric == metric]
        if not values:
            continue
        chart_type = c.presentation.chart_type
        labels = sorted({f'{year} {PERIOD_LABELS[period]}' for code, periods in result.selections.items() for year, period in periods})
        if chart_type == 'line' and len(labels) < 2:
            result.notes.append('只有一个报告期，无法绘制趋势线；可改为柱状图。')
            continue
        if chart_type == 'pie' and any(f.decimal < 0 for f in values):
            result.notes.append('饼图不能表达负值占比；本次未生成饼图。')
            continue
        dimension = METRICS[metric].dimension
        unit = c.presentation.unit or ('万元' if dimension == 'money' else METRICS[metric].unit)
        if chart_type == 'pie':
            if not any(f.decimal > 0 for f in values):
                result.notes.append('所有数值为零，无法计算饼图占比。')
                continue
            points=[dict(name=f'{f.company} {f.year} {PERIOD_LABELS[f.period]}',
                value=float(f.decimal/MONEY_UNITS[unit] if dimension=='money' else f.decimal),
                value_exact=format(f.decimal/MONEY_UNITS[unit] if dimension=='money' else f.decimal,'f'),
                value_formatted=display_value(f.value,f.unit,unit,c.presentation.decimals)[0]+' '+unit,fact_id=f.id) for f in values]
            result.charts.append(dict(version=3,chart_type='pie',title=METRICS[metric].label,unit=unit,metric=metric,
                decimals=c.presentation.decimals,x=[p['name'] for p in points],series=[dict(name=METRICS[metric].label,values=points)],source_fact_ids=[f.id for f in values]))
            continue
        # 作品说明：不同指标分别绘图，不将不可比较的单位放入同一坐标轴。
        series = []
        for code in c.codes:
            selected = [f for f in values if f.stock_code == code]
            data, exact, refs, formatted = [], [], [], []
            for label in labels:
                f = next((f for f in selected if f'{f.year} {PERIOD_LABELS[f.period]}' == label), None)
                v = f.decimal / MONEY_UNITS[unit] if f and dimension == 'money' else f.decimal if f else None
                exact.append(format(v, 'f') if v is not None else None)
                data.append(float(v) if v is not None else None)
                refs.append(f.id if f else None)
                formatted.append(display_value(f.value,f.unit,unit,c.presentation.decimals)[0]+' '+unit if f else '暂无核实值')
            if selected:
                series.append(dict(name=selected[0].company, values=data, values_exact=exact, values_formatted=formatted, fact_ids=refs))
        if chart_type == 'line' and not any(sum(v is not None for v in s['values'])>=2 for s in series):
            result.notes.append('核实数据只有单点，尚不能构成趋势线。')
            continue
        result.charts.append(dict(version=3, chart_type=chart_type, title=METRICS[metric].label,
            unit=unit,decimals=c.presentation.decimals,metric=metric, x=labels, series=series, source_fact_ids=[f.id for f in values]))


def _execute_selection(request: Request, repository) -> ExecutionResult:
    result = ExecutionResult()
    if request.conditions.restrictions.no_query:
        return result
    result.selections, result.time_rule = resolve_periods(request, repository.report_catalog(request.conditions.codes))
    c = request.conditions
    result.facts = repository.query(c.codes, result.selections, c.metrics, c.scope)
    if c.calculation=='yoy' and c.comparison_axis=='years':
        baselines={code:sorted({(year-1,period) for year,period in pairs}) for code,pairs in calculation_targets(request,result.selections).items()}
        requested_ids={fact.id for fact in result.facts}
        result.support_facts=[fact for fact in repository.query(c.codes,baselines,c.metrics,c.scope) if fact.id not in requested_ids]
    identities = {(f.stock_code, f.year, f.period, f.metric, f.scope) for f in result.facts}
    for code in c.codes:
        if c.time.span and c.time.mode in {'latest','latest_each','latest_common'} and len(result.selections.get(code,[]))<c.time.span:
            result.missing.append(dict(stock_code=code,reason='insufficient_report_count',requested=c.time.span,available=len(result.selections.get(code,[]))))
        if not result.selections.get(code):
            result.missing.append(dict(stock_code=code, reason='no_matching_period'))
        for year, period in result.selections.get(code, []):
            for metric in c.metrics:
                if (code, year, period, metric, c.scope) not in identities:
                    result.missing.append(dict(stock_code=code, year=year, period=period,
                        metric=metric, scope=c.scope, reason='no_verified_fact'))
    ranking_valid = True
    if any(g.kind == 'rank' for g in request.goals):
        if len(c.metrics) != 1 or any(len(pairs) != 1 for pairs in result.selections.values()):
            ranking_valid = False
            result.notes.append('排名需要一个指标和每家公司一个明确报告期。')
        elif c.calculation=='none':
            result.facts.sort(key=lambda f: (f.decimal, f.stock_code), reverse=c.presentation.order == 'desc')
            result.facts = result.facts[:c.presentation.limit] if c.presentation.limit else result.facts
    calculate(request, result)
    if c.calculation=='yoy':
        computed={(d['company'],d['year'],d['period'],d['metric']) for d in result.derived}
        targets=calculation_targets(request,result.selections)
        for fact in result.facts:
            if (fact.year,fact.period) not in targets.get(fact.stock_code,[]):continue
            if (fact.company,fact.year,fact.period,fact.metric) not in computed:
                result.missing.append(dict(stock_code=fact.stock_code,year=fact.year-1,period=fact.period,metric=fact.metric,
                    scope=fact.scope,reason='no_verified_yoy_base',requested_year=fact.year))
    if ranking_valid and c.calculation!='none' and any(g.kind=='rank' for g in request.goals):
        undefined=[item for item in result.derived if item['value'] is None]
        if undefined:
            result.notes.append('零基期等未定义计算结果不参与数值排名：'+'、'.join(item['company'] for item in undefined))
            result.missing.extend(dict(stock_code=next((f.stock_code for f in result.facts if f.company==item['company']),''),
                year=item['year'],period=item['period'],metric=item['metric'],reason='undefined_ranking_value') for item in undefined)
        result.derived=[item for item in result.derived if item['value'] is not None]
        result.derived.sort(key=lambda item:(Decimal(item['value']),item['company']),reverse=c.presentation.order=='desc')
        if c.presentation.limit:result.derived=result.derived[:c.presentation.limit]
        chosen={id for item in result.derived for id in item['inputs']}
        result.facts=[fact for fact in result.facts if fact.id in chosen]
    compare_values(request,result)
    build_charts(request, result)
    for goal in request.goals:
        if goal.kind in {'concept', 'rules', 'catalog', 'quote', 'cause', 'unsupported'}:
            continue
        completed = bool(result.facts) and not result.missing
        if goal.kind == 'rank':
            completed = completed and ranking_valid
        if goal.kind == 'chart':
            completed = completed and bool(result.charts)
        if goal.kind=='compare':
            completed=completed and bool(result.comparisons)
        if c.calculation != 'none':
            expected = len(c.metrics) * (len(c.codes) if c.comparison_axis == 'years' else 1) * len(c.time.periods)
            if c.calculation=='yoy' and c.comparison_axis=='years':
                expected=len(c.metrics)*sum(len(pairs) for pairs in calculation_targets(request,result.selections).values())
            elif c.time.pairs and len(c.time.pairs)==2 and c.comparison_axis=='years':
                expected=len(c.metrics)*len(c.codes)
            elif c.calculation in {'relative_percent', 'percentage_points'} and c.comparison_axis == 'years':
                expected *= max(1, max((len(pairs) // len(c.time.periods) - 1 for pairs in result.selections.values()), default=1))
            if goal.kind=='rank' and ranking_valid:expected=min(c.presentation.limit or len(c.codes),len(c.codes))
            completed = completed and len(result.derived) == expected
        result.goals.append(GoalResult(id=goal.id, kind=goal.kind,
            status='completed' if completed else 'partial' if result.facts else 'no_data',
            fact_ids=[f.id for f in result.facts], detail='；'.join(result.notes)))
    return result


def execute(request: Request, repository) -> ExecutionResult:
    """作品说明：相同财务选择只编译一次，同时保存每个目标的实际执行结果。"""
    import json
    data_kinds = {'lookup', 'compare', 'rank', 'chart', 'quote', 'cause', 'sign'}
    groups = {}
    for goal in request.goals:
        if goal.kind not in data_kinds:
            continue
        selected = request.for_goal(goal)
        # 作品说明：排名仅裁剪自己的结果；独立要求的完整查询及来源继续保留，相同只读取数可共享缓存。
        key = json.dumps({'conditions':selected.conditions.model_dump(mode='json'),'ranking':goal.kind=='rank'}, sort_keys=True, ensure_ascii=False)
        if key not in groups:
            groups[key] = selected
        else:
            groups[key].goals.append(goal)
    result = ExecutionResult()
    for selected in groups.values():
        block = _execute_selection(selected, repository)
        result.blocks.append((selected, block))
        result.time_rule = block.time_rule
        for code, pairs in block.selections.items():
            result.selections[code] = sorted(set([*result.selections.get(code, []), *pairs]))
        result.facts.extend(block.facts)
        result.support_facts.extend(block.support_facts)
        result.derived.extend(block.derived)
        result.comparisons.extend(block.comparisons)
        result.missing.extend(block.missing)
        result.notes.extend(block.notes)
        result.goals.extend(block.goals)
        for chart in block.charts:
            chart['goal_ids'] = [g.id for g in selected.goals if g.kind == 'chart']
            result.charts.append(chart)
    result.facts = list({f.id: f for f in result.facts}.values())
    result.derived = list({d['id']: d for d in result.derived}.values())
    return result
