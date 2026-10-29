"""核验门禁通过后，使用准确类型化数值生成财务正文。"""
from __future__ import annotations

from .catalog import METRICS,metric_definition
from .contracts import Conditions, Request
from .executor import ExecutionResult, PERIOD_LABELS, display_value
from .units import fact_output_unit,result_output_unit


def escaped(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def system_sentence(text):
    """系统说明在句子边界统一收尾；逐字原文和完整回答不经过此函数。"""
    text=text.strip()
    if not text or text.endswith(('！','？','!','?')):return text
    return text.rstrip('。.')+'。'


def sign_reference_label(value):
    label=getattr(value,'label',None) or METRICS[value.metric].label
    return f'{value.company} {value.year} {PERIOD_LABELS[value.period]}的{label}'


def render_sign_correction(value,claimed_sign,label=None):
    """前提纠正只根据核验值生成，不解析用户措辞或推断经营原因。"""
    actual='undefined' if value is None else 'positive' if value>0 else 'negative' if value<0 else 'zero'
    if claimed_sign in {'none',actual}:return ''
    return '先核对前提：'+(label or '这个已核实的数')+'实际为'+{'positive':'正数','negative':'负数','zero':'零','undefined':'未定义'}[actual]+'。'


def render_numeric_quote(quote):
    """物理页定位与未改写的原表单元格同时展示，位置标注不冒充摘录。"""
    return f'原件 PDF 第 {quote["page_start"]} 页。\n\n'+quote['text']


def table_only_content(content):
    pieces=[piece.strip() for piece in content.split('\n\n') if piece.strip()]
    tables=[piece for piece in pieces if piece.startswith('|')]
    messages=[piece for piece in pieces if not piece.startswith('|')]
    if messages:tables.append('| 说明 |\n|---|\n'+'\n'.join('| '+escaped(message)+' |' for message in messages))
    return '\n\n'.join(tables)


def render_concept_definition(metric,scope):
    """将已登记会计科目解释为收入、成本与利润的关系；不修改查询身份或来源事实。"""
    explanations={
        'operating_revenue':'利润表中企业经营活动形成的收入，表示业务规模；尚未扣除营业成本、期间费用和所得税，不等同营业总收入或主营业务收入。',
        'total_operating_revenue':'企业营业收入与适用行业其他经营收入合计，具体构成以报告项目为准，不能自动等同营业收入。',
        'main_business_revenue':'企业主要经营业务形成的收入，不自动包含其他业务收入。',
        'net_profit':'收入扣除相关成本、费用、税费并计入其他损益后的最终利润；合并口径包含归母净利润和少数股东损益，收入较高仍可能亏损。',
        'attributable_net_profit':'合并净利润中归属于母公司股东的部分，不含少数股东损益，但包含合并范围内子公司的相应经营成果；不是母公司单体报表利润，也不等于实际分红。',
        'deducted_attributable_net_profit':'在归母净利润基础上扣除报告按规定认定的非经常性损益后的利润，帮助观察扣除这些事项后的盈利；不等于合并净利润或母公司单体利润，也不能自行把全部投资收益等项目一律排除。',
        'gross_profit':'营业收入减去营业成本的金额，尚未扣除期间费用和所得税；毛利为正不代表最终净利润为正。',
        'gross_margin':'毛利额占营业收入的比例，表示每单位收入扣除营业成本后留下的比例；它是比率，毛利额是金额。',
        'operating_cash_flow':'本期经营活动实际现金流入减去现金流出的净额；除销售收款外还受采购、工资、税费等付款影响，不能直接代表营业收入或净利润。',
    }
    if metric=='net_profit' and scope=='parent':return metric_definition(metric,scope)
    return explanations.get(metric) or metric_definition(metric,scope)


def render_concept_relation(metrics):
    """解释已登记指标之间的会计关系，不用经营原因或金额替代概念依据。"""
    if {'operating_revenue','operating_cash_flow'}<=set(metrics):
        return '营业收入反映当期确认的收入，不要求同一期间实际收款；经营现金流净额反映本期经营活动收付现金后的净额，还扣除采购、工资、税费等现金支出。赊销、收回以前期间的欠款和付款时点都会使二者不同，因此不能把经营现金流净额直接当作营业收入。'
    return ''


def render_fact_implication(fact):
    """已核实数值的正负由会计定义解释；正负本身不构成经营原因证据。"""
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
    return text+' '+system_sentence(metric.label+'：'+metric_definition(fact.metric,fact.scope))


def render_computed_implication(reference):
    if reference.decimal is None:
        return reference.label+'未定义，不能判为正数、负数或零。'+reference.detail+'。'
    sign='正数' if reference.decimal>0 else '负数' if reference.decimal<0 else '零'
    direction='增加' if reference.decimal>0 else '减少' if reference.decimal<0 else '没有变化'
    if reference.comparison_axis=='companies':direction='第一家公司高于第二家' if reference.decimal>0 else '第一家公司低于第二家' if reference.decimal<0 else '两家相等'
    return reference.label+'为'+sign+'，表示所比较指标'+direction+'。这是变化量；它的正负不能直接判定利润金额是盈利还是亏损，也不能单凭变化说明经营原因。'+reference.detail+'。'


def render_sign(fact):
    """盈亏结论来自已核实利润的正负；现金流等其他指标不冒充盈利判断。"""
    if fact.metric in {'attributable_net_profit','deducted_attributable_net_profit','net_profit','operating_profit','total_profit'}:
        return f'{fact.company}该报告期在{METRICS[fact.metric].label}口径下'+('盈利。' if fact.decimal>0 else '亏损。' if fact.decimal<0 else '盈亏平衡。')
    return f'{fact.company}的{METRICS[fact.metric].label}为'+('正数。' if fact.decimal>0 else '负数。' if fact.decimal<0 else '零。')+'该指标不能直接判定盈利或亏损。'


def render_financial(request: Request, result: ExecutionResult, claimed_sign='none', repeated_fact_ids=frozenset()) -> str:
    if result.blocks:
        return '\n\n'.join(filter(None, [render_financial(selected, block, claimed_sign,repeated_fact_ids) for selected, block in result.blocks]))
    if request.goals and all(goal.kind=='quote' for goal in request.goals):
        return ''
    c = request.conditions
    pieces = []
    if claimed_sign != 'none' and len(result.facts) == 1 and c.calculation=='none':
        correction=render_sign_correction(result.facts[0].decimal,claimed_sign)
        if correction:pieces.append(correction)
    rows = []
    # 计算缺少输入而无法完成时，保留已核实的可用部分，不能连可靠金额一起隐藏。
    incomplete_calculation=c.calculation!='none' and bool(result.missing) and not result.derived
    displayed=result.facts if c.calculation=='none' or c.presentation.include_inputs or incomplete_calculation else []
    for f in displayed:
        if c.restrictions.no_repeat and f.id in repeated_fact_ids:continue
        value, unit = display_value(f.value, f.unit, fact_output_unit(c,f.unit,f.metric), c.presentation.decimals)
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
            pieces.insert(0,render_sign(f))
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
            value, unit = display_value(d['value'], d['unit'], result_output_unit(c,d['unit'],d.get('metric')), c.presentation.decimals)
        detail=d['detail'].strip('；;。 ')
        pieces.append(f'{d["company"]}的{d["label"]}为 **{value} {unit}**' + ('；'+detail if detail else '')+'。')
    if result.missing:
        pieces.append('以下请求项没有可发布的核实数据：' + '；'.join(
            f'{item["stock_code"]} {item.get("year", "")} {item.get("period", "")} {METRICS[item["metric"]].label if item.get("metric") in METRICS else "匹配报告期"}'+('（当前库没有该报告原件）' if item.get('reason')=='report_not_in_catalog' else '（该指标尚无核实事实）' if item.get('reason')=='no_verified_fact' else '')
            for item in result.missing) + '。')
    if result.value_ordered or any(g.kind == 'rank' for g in request.goals):
        pieces.append(f'排名范围为本次选定的 {len(c.codes)} 家库内公司；缺失项不参与数值排名。')
    pieces.extend(result.notes)
    if c.presentation.format == 'table':
        # 只要表格时，派生数值、缺失提示和前提纠正也以表格行呈现。
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
    if target=='greeting':
        return '你好！我是FinSight财报助手。可以直接问我库内公司的财务数字、比较结果或原件出处。'
    if target=='closing':
        return '不客气，先到这里。需要查财报时再来问我。'
    if target=='capabilities':
        return ('你好，我是FinSight财报助手。可以查询库内公司的已核实财务数字、比较报告期、排名、画图、定位原文或解释财务概念。'
            '查询需要明确公司和指标；缺年份取最新年报。折线趋势至少需要两个核实数据点，单点不能构成趋势。'
            '柱状图可展示单点；不同单位分别展示，负值和缺失点保留。不支持单季、实时行情或保证收益。'
            '例如可以先问：“目前收录了哪些公司和报告期？”再按已收录范围查询指标和出处。')
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


def chart_compat(chart: dict, *, source_audit_complete=None) -> dict:
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
        'series_name': first['name'], 'option': option, 'data_source': {'kind': 'canonical',
        'label': '已核实的版本化事实' if source_audit_complete is not False else 'OCR宽松入库试验（未完整核验）',
        'unit': chart['unit'],**({} if source_audit_complete is None else {'source_audit_complete':source_audit_complete})}}


def verification_compat(verification):
    checks = [*verification.numeric, *verification.request, *verification.evidence]
    return {**verification.model_dump(mode='json'), 'status': 'warn' if verification.status == 'partial' else verification.status,
        'checks': [{**c.model_dump(), 'label': c.name, 'status': 'warn' if c.status in {'unknown', 'not_applicable'} else c.status} for c in checks],
        'unmatched_numbers': [], 'summary': {'passed': sum(c.status == 'pass' for c in checks),
            'warnings': sum(c.status == 'unknown' for c in checks), 'failed': sum(c.status == 'fail' for c in checks)}}
