"""作品说明：核验门禁通过后，使用准确类型化数值生成财务正文。"""
from __future__ import annotations

from .catalog import METRICS,metric_definition
from .contracts import Conditions, Request
from .executor import ExecutionResult, PERIOD_LABELS, display_value
from .units import fact_output_unit


def escaped(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def table_only_content(content):
    pieces=[piece.strip() for piece in content.split('\n\n') if piece.strip()]
    tables=[piece for piece in pieces if piece.startswith('|')]
    messages=[piece for piece in pieces if not piece.startswith('|')]
    if messages:tables.append('| 说明 |\n|---|\n'+'\n'.join('| '+escaped(message)+' |' for message in messages))
    return '\n\n'.join(tables)


def render_fact_implication(fact):
    """作品说明：已核实数值的正负由会计定义解释；正负本身不构成经营原因证据。"""
    sign='正数' if fact.decimal>0 else '负数' if fact.decimal<0 else '零'
    metric=METRICS[fact.metric]
    profit={'net_profit','attributable_net_profit','deducted_attributable_net_profit','operating_profit','total_profit'}
    flows={'operating_cash_flow':'经营活动','investing_cash_flow':'投资活动','financing_cash_flow':'筹资活动','net_cash_change':'现金及现金等价物'}
    if fact.metric in profit:
        meaning='该指标口径下盈利' if fact.decimal>0 else '该指标口径下亏损' if fact.decimal<0 else '该指标口径下盈亏平衡'
        text=f'{metric.label}为{sign}，表示{meaning}。利润正负不能直接证明现金流或未来经营表现。'
    elif fact.metric in flows:
        meaning='净流入' if fact.decimal>0 else '净流出' if fact.decimal<0 else '净变动为零'
        text=f'{metric.label}为{sign}，表示{flows[fact.metric]}{meaning}，不能仅据此判定盈利或亏损；具体原因需要报告叙述证据。'
    elif fact.metric.endswith('_reported_yoy') or fact.metric.endswith('_reported_change_vs_year_end'):
        meaning='增加' if fact.decimal>0 else '下降' if fact.decimal<0 else '没有变化'
        text=f'{metric.label}为{sign}，表示按该指标登记的比较基期{meaning}；负增长不等同净利润为负。'
    else:
        text=f'{metric.label}为{sign}。应按该科目的会计定义理解，单个余额或比率的正负不能直接说明盈利亏损或经营原因。'
    return text+' '+metric.label+'：'+metric_definition(fact.metric,fact.scope)+'。'


def render_computed_implication(reference):
    if reference.decimal is None:
        return reference.label+'未定义，不能判为正数、负数或零。'+reference.detail+'。'
    sign='正数' if reference.decimal>0 else '负数' if reference.decimal<0 else '零'
    direction='增加' if reference.decimal>0 else '减少' if reference.decimal<0 else '没有变化'
    if reference.comparison_axis=='companies':direction='第一家公司高于第二家' if reference.decimal>0 else '第一家公司低于第二家' if reference.decimal<0 else '两家相等'
    return reference.label+'为'+sign+'，表示所比较指标'+direction+'。这是变化量；它的正负不能直接判定利润金额是盈利还是亏损，也不能单凭变化说明经营原因。'+reference.detail+'。'


def render_financial(request: Request, result: ExecutionResult, claimed_sign='none') -> str:
    if result.blocks:
        return '\n\n'.join(filter(None, [render_financial(selected, block, claimed_sign) for selected, block in result.blocks]))
    if request.goals and all(goal.kind=='quote' for goal in request.goals):
        return ''
    c = request.conditions
    pieces = []
    if claimed_sign != 'none' and len(result.facts) == 1 and c.calculation=='none':
        value = result.facts[0].decimal
        actual = 'positive' if value > 0 else 'negative' if value < 0 else 'zero'
        if actual != claimed_sign:
            pieces.append('先核对前提：这个已核实的数为' + {'positive': '正数', 'negative': '负数', 'zero': '零'}[actual] + '。')
    rows = []
    displayed=result.facts if c.calculation=='none' or c.presentation.include_inputs else []
    for f in displayed:
        value, unit = display_value(f.value, f.unit, fact_output_unit(c,f.unit), c.presentation.decimals)
        scope = '母公司' if f.scope == 'parent' else '合并'
        label = METRICS[f.metric].label
        if f.status == 'derived':
            label += '（计算值）'
        if f.metric in {'attributable_net_profit', 'deducted_attributable_net_profit'}:
            scope = '合并报表归属上市公司股东'
        rows.append([f.company, f'{f.year} {PERIOD_LABELS[f.period]}', label, scope, value, unit])
    if c.presentation.format != 'chart' and rows:
        if c.presentation.format == 'table' or len(rows) > 1 or any(g.kind == 'rank' for g in request.goals):
            pieces.append('| 公司 | 报告期 | 指标 | 口径 | 数值 | 单位 |\n|---|---|---|---|---:|---|\n' +
                '\n'.join('| ' + ' | '.join(escaped(cell) for cell in row) + ' |' for row in rows))
        else:
            company, period, label, scope, value, unit = rows[0]
            pieces.append(f'{company} {period}的{label}为 **{value} {unit}**（{scope}口径）。')
    if any(g.kind == 'sign' for g in request.goals) and c.presentation.format != 'chart':
        for f in result.facts:
            if f.metric in {'attributable_net_profit', 'deducted_attributable_net_profit', 'net_profit', 'operating_profit', 'total_profit'}:
                pieces.insert(0, f'{f.company}该报告期' + ('盈利。' if f.decimal > 0 else '亏损。' if f.decimal < 0 else '盈亏平衡。'))
            else:
                pieces.insert(0, f'{f.company}的{METRICS[f.metric].label}为' + ('正数。' if f.decimal > 0 else '负数。' if f.decimal < 0 else '零。'))
    if c.presentation.format!='chart':
        indexed={fact.id:fact for fact in result.facts}
        for comparison in result.comparisons:
            first,second=indexed[comparison['first']],indexed[comparison['second']]
            relation={'greater':'高于','less':'低于','equal':'等于'}[comparison['relation']]
            label=METRICS[first.metric].label
            if comparison['axis']=='companies':
                pieces.insert(0,f'{first.year} {PERIOD_LABELS[first.period]}，{first.company}的{label}{relation}{second.company}。')
            else:
                pieces.insert(0,f'{first.company}的{label}：{first.year} {PERIOD_LABELS[first.period]}{relation}{second.year} {PERIOD_LABELS[second.period]}。')
    for d in result.derived:
        if d['value'] is None:
            pieces.append(d['detail'] + '。')
            continue
        if d['unit'] == '百分点':
            value, _ = display_value(d['value'], '%', '%', c.presentation.decimals)
            unit = '个百分点'
        else:
            value, unit = display_value(d['value'], d['unit'], c.presentation.unit if d['unit'] == '元' else None, c.presentation.decimals)
        pieces.append(f'{d["company"]}的{d["label"]}为 **{value} {unit}**。' + (d['detail'] + '。' if d['detail'] else ''))
    if result.missing:
        pieces.append('以下请求项没有可发布的核实数据：' + '；'.join(
            f'{item["stock_code"]} {item.get("year", "")} {item.get("period", "")} {METRICS[item["metric"]].label if item.get("metric") in METRICS else "匹配报告期"}'
            for item in result.missing) + '。')
    if any(g.kind == 'rank' for g in request.goals):
        pieces.append(f'排名范围为本次选定的 {len(c.codes)} 家库内公司；缺失项不参与数值排名。')
    pieces.extend(result.notes)
    if c.presentation.format == 'table':
        # 作品说明：只要表格时，派生数值、缺失提示和前提纠正也以表格行呈现。
        tables = [piece for piece in pieces if piece.startswith('|')]
        messages = [piece for piece in pieces if not piece.startswith('|')]
        if messages:
            tables.append('| 说明 |\n|---|\n' + '\n'.join('| ' + escaped(message) + ' |' for message in messages))
        return '\n\n'.join(tables)
    return '\n\n'.join(pieces)


def render_rules(previous, question: str, goal=None, companies=None) -> str:
    if goal and goal.catalog_target=='capabilities':
        return render_catalog([], 'capabilities', Conditions())
    e = previous.last_execution
    if goal and goal.execution_ref:
        e = next((item for item in previous.executions if item.turn_id == goal.execution_ref),None)
        if not e: return '没有找到对应轮次的执行记录，不能根据旧回答推断曾查到数据。'
    if e:
        names = companies or {}
        selected = '；'.join(names.get(code,code) + '：' + '、'.join(f'{year} {PERIOD_LABELS[period]}' for year, period in pairs) for code, pairs in e.resolved_periods.items())
        detail = ''
        if e.conditions:
            detail = ' 指标为'+'、'.join(METRICS[m].label for m in e.conditions.metrics if m in METRICS)+'，'+('母公司' if e.conditions.scope=='parent' else '合并')+'口径。'
        state={'completed':'已完成','partial':'部分结果可用','no_data':'未取得核实数据','failed':'没有通过核验','cancelled':'已取消','clarification':'条件待明确'}[e.status]
        return f'该轮实际采用：{e.time_rule}。选取结果为 {selected}。{detail}执行状态：{state}。明确指定年份时不会用其他年份补齐。'
    return '没有明确公司且上下文不唯一时先询问；未指定年份取最新年报；泛称净利润默认归母并明示。公司比较和排名默认共同报告期，逐家查询默认各自最新。当前只支持报告披露的累计口径。'


def render_catalog(reports, target, conditions):
    if target=='capabilities':
        return ('可以查询库内公司的已核实财务数字、比较报告期、排名、画图、定位原文或解释财务概念。'
            '查询需要明确公司和指标；缺年份取最新年报。折线趋势至少需要两个核实数据点，单点不能构成趋势。'
            '柱状图可展示单点；不同单位分别展示，负值和缺失点保留。不支持单季、实时行情或保证收益。')
    if target == 'metrics':
        return '支持的指标：'+'、'.join(m.label+'（'+m.unit+'）' for m in METRICS.values())+'。金额、比率与每股指标分别保留单位；未核实或原件未披露的单元格不返回数字。'
    grouped={}
    for report in reports:
        if conditions.time.years and report['year'] not in conditions.time.years: continue
        company=report['company'];period=report['period']
        grouped.setdefault(company,{}).setdefault(period,[]).append(report['year'])
    if not grouped: return '在所指定公司和年份范围内，没有已登记的报告期。'
    rows=[]
    for company,periods in grouped.items():
        rows.append([company,*['、'.join(map(str,sorted(set(periods.get(period,[]))))) or '无登记原件' for period in ('FY','HY','Q1','Q3')]])
    return '| 公司 | 年报 | 半年报累计 | 一季度累计 | 前三季度累计 |\n|---|---|---|---|---|\n'+'\n'.join('| '+' | '.join(row)+' |' for row in rows)


def chart_compat(chart: dict) -> dict:
    series = chart['series']
    first = series[0] if series else {'values': [], 'name': ''}
    option = dict(tooltip={'trigger': 'axis'}, legend={'data': [s['name'] for s in series]},
        xAxis={'type': 'category', 'data': chart['x']}, yAxis={'type': 'value', 'name': chart['unit']},
        series=[dict(type=chart['chart_type'], name=s['name'], data=[dict(value=value,value_exact=s['values_exact'][index],
            value_formatted=s.get('values_formatted',['']*len(s['values']))[index]) for index,value in enumerate(s['values'])] if 'values_exact' in s else s['values'], connectNulls=False) for s in series])
    if chart['chart_type']=='pie':
        option=dict(tooltip={'trigger':'item'},legend={},series=[dict(type='pie',data=first['values'],radius='65%')])
    elif chart['chart_type']=='scatter':
        option=dict(tooltip={'trigger':'item'},xAxis={'type':'value','name':METRICS[chart['metrics'][0]].label+'（'+chart['axis_units'][0]+'）'},
            yAxis={'type':'value','name':METRICS[chart['metrics'][1]].label+'（'+chart['axis_units'][1]+'）'},
            series=[dict(type='scatter',name=first['name'],data=first['values'])])
    return {**chart, 'x_data': chart['x'], 'y_data': first['values'], 'y_label': chart['unit'],
        'series_name': first['name'], 'option': option, 'data_source': {'kind': 'canonical', 'label': '已核实的版本化事实', 'unit': chart['unit']}}


def verification_compat(verification):
    checks = [*verification.numeric, *verification.request, *verification.evidence]
    return {**verification.model_dump(mode='json'), 'status': 'warn' if verification.status == 'partial' else verification.status,
        'checks': [{**c.model_dump(), 'label': c.name, 'status': 'warn' if c.status in {'unknown', 'not_applicable'} else c.status} for c in checks],
        'unmatched_numbers': [], 'summary': {'passed': sum(c.status == 'pass' for c in checks),
            'warnings': sum(c.status == 'unknown' for c in checks), 'failed': sum(c.status == 'fail' for c in checks)}}
