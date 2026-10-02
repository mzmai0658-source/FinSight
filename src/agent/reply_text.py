"""作品说明：固定文本用于示例、定义、澄清与拒答；财务数值由事实生成，模板只解释含义及用法。"""
from __future__ import annotations

import re

from .domain import CODE_TO_NAME_MAP
from .facts import field_specs

_IDENTITY = re.compile(
    r'你是谁|你叫什么|你能做什么|你能干什么|你可以做什么|你会什么|你是做什么的?|'
    r'介绍一下你|你的功能|怎么用你|你好|您好|嗨|(?<![A-Za-z])hi(?![A-Za-z])|(?<![A-Za-z])hello(?![A-Za-z])',
    re.I,
)
_GREETING = re.compile(r'(你好|您好|嗨|hi|hello)[！!。\s]*', re.I)
_READING = re.compile(r'如何|怎么样|怎样|说明|比较|对比|谁高')

_MEANING = {
    'total_operating_revenue': '营业收入是公司在这一报告期确认的经营收入。',
    'net_profit': '净利润是收入扣掉成本、费用和税之后，归属于这一报告期的利润。',
    'gross_profit_margin': '销售毛利率是毛利占营业收入的比例，用来看卖出产品后留下的空间。',
    'roe': '净资产收益率是净利润相对净资产的比率，用来看股东权益的回报水平。',
    'operating_cf_net_amount': '经营活动现金流量净额是经营里收到的现金减去付出的现金，和利润不是同一个口径。',
    'investing_cf_net_amount': '投资活动现金流量净额是购建或处置资产、投资收支之后的现金净额。',
    'financing_cf_net_amount': '筹资活动现金流量净额是借款、还款、分红等融资活动之后的现金净额。',
    'net_cash_flow': '现金及现金等价物净增加额是各类现金流加总后，现金余额的变化。',
    'eps': '每股收益是这一报告期利润分到每股上的金额。',
}


def is_identity_question(question: str) -> bool:
    return bool(_IDENTITY.search(str(question or '')))


def is_greeting(question: str) -> bool:
    return bool(_GREETING.fullmatch(str(question or '').strip()))


def asks_for_reading(question: str) -> bool:
    """作品说明：数值之后给出简短的阅读或比较说明。"""
    return bool(_READING.search(str(question or '')))


def sample_companies(limit: int = 4) -> list[str]:
    """作品说明：示例使用登记公司名称，不固定偏向演示公司集合。"""
    picked = []
    for name in CODE_TO_NAME_MAP.values():
        if name and name not in picked:
            picked.append(name)
        if len(picked) >= limit:
            break
    if not picked:
        picked = ['这家公司']
    while len(picked) < min(limit, 3):
        picked.append(picked[-1])
    return picked[:limit]


def _company(index: int = 0) -> str:
    names = sample_companies()
    return names[min(index, len(names) - 1)]


def _year_text(year) -> str:
    return f'{int(year)}年' if year else ''


def metric_meaning(field: str) -> str:
    if field in _MEANING:
        return _MEANING[field]
    label = field_specs().get(field, {}).get('label') or '这个指标'
    return f'{label}是财报里的一项已入库指标。这里只说明查到的数值，不推断经营好坏。'


def follow_up_line(company: str, metric_label: str, year) -> str:
    company = company or _company()
    metric_label = metric_label or '营收'
    if year:
        return f'可以接着问：{company}{int(year)}年{metric_label}和{int(year) - 1}年比怎么样，或看原文在哪一页。'
    return f'可以接着问：{company}{metric_label}的原文在哪一页。'


def source_page_lines(rows: list[tuple]) -> str:
    """作品说明：每个原始页对应一条来源说明，包含公司、年度、期间、指标、文件和页码。"""
    lines = []
    for company, year, period, metric, _filename, page in rows:
        sentence = f'{company}{year}{period}{metric}的原文在该报告 PDF 第{page}页。'
        if sentence not in lines:
            lines.append(sentence)
    return '\n'.join(lines)


def reading_suffix(metrics: list[str], company: str, year, metric_label: str) -> str:
    lines = []
    for field in metrics:
        sentence = metric_meaning(field)
        if sentence not in lines:
            lines.append(sentence)
    lines.append(follow_up_line(company, metric_label, year))
    return '\n'.join(lines)


def capability_reply(*, greeting: bool = False) -> str:
    first, second, third = (_company(0), _company(1), _company(2))
    opening = '你好，我是财报学习助手。' if greeting else '我是财报学习助手。'
    return '\n'.join([
        opening,
        '回答基于已经入库的财报数字和研报原文，不构成投资建议。',
        '下面提到的公司只是已入库名单里的例子。',
        '我能做这几件事：',
        f'查某个数字。比如：{first}2024年营收多少。',
        f'比公司或比年份。比如：{second}和{first}2024年营收谁高。',
        f'看主要指标或画趋势。比如：{third}最近怎么样；把{first}近几年营收画成趋势图。',
        f'找原文在哪一页。比如：{third}2024年营收的原文在哪一页。',
        f'有些事我不会做。不编数字，也不估个数。与其说“帮我估一下营收”，可以改问：{first}2024年营收多少。',
        f'也不回答和财报无关的问题。想查数时，直接说出公司、年份和指标即可。',
        '公司可以用简称。年份没说时，可以补“2024年”，或说“最近一年”。',
    ])


def clarify_metric(company: str = '', year=None) -> str:
    who = f'{company}{_year_text(year)}' if company else '这次'
    example_company = company or _company()
    example_year = _year_text(year) or '2024年'
    return (
        f'已经知道你想看{who}的财报，还缺具体指标。'
        f'可以说营业收入、净利润，或直接说主要指标。'
        f'例如：{example_company}{example_year}营业收入多少。'
    )


def clarify_company(metric_label: str = '', year=None) -> str:
    metric = metric_label or '营收'
    example_year = _year_text(year) or '2024年'
    company = _company()
    return (
        f'指标已经清楚，是{metric}。还缺公司。'
        f'可以说公司全称、简称或股票代码。'
        f'例如：{company}{example_year}{metric}多少。'
    )


def clarify_cash(company: str = '', year=None) -> str:
    company = company or _company()
    if isinstance(year, str) and year:
        when = year if year.endswith('年') else f'{year}年'
    else:
        when = _year_text(year) or '2024年'
    return (
        f'现金流需要明确种类：想看经营、投资还是筹资活动现金流量净额？我不会替你选经营现金流。'
        f'可以说：{company}{when}经营活动现金流量净额多少；'
        f'或：{company}{when}投资活动现金流量净额多少；'
        f'或：{company}{when}筹资活动现金流量净额多少。'
    )


def clarify_pronoun() -> str:
    company = _company()
    return (
        '这句里的“它”或“这家”对不上上一轮记下的公司。'
        f'请直接说出公司名。例如：{company}2024年营收多少。'
    )


def clarify_near_miss(options: list[str]) -> str:
    shown = '、'.join(options) or _company()
    example = options[0] if options else _company()
    return (
        f'这个名字没有精确对上登记公司。你是说{shown}吗？'
        f'请确认全称、简称或股票代码。例如：{example}2024年营收多少。'
    )


def clarify_request() -> str:
    company = _company()
    return (
        '我还没确定你的意思。你想查询财报数据，还是了解系统功能？'
        f'例如：{company}2024年营收多少。也可以直接问：你能干什么。'
    )


def clarify_time() -> str:
    return '这次想看哪一年或哪几个报告期？例如：2024年全年，或2024年前三季度累计。只说年份时按全年年报查询。'


def no_data_reply(company: str = '', year=None, period_label: str = '', metric_label: str = '') -> str:
    company = company or '这家公司'
    when = f'{_year_text(year)}{period_label}' if year or period_label else '这一报告期'
    metric = metric_label or '这个指标'
    return (
        f'{company}{when}的{metric}没有可用记录。这不是查询失败，是当前数字库里没有这一条。'
        f'可以换一年、换报告期，或问这家有哪些报告。'
        f'例如：{company}有哪些年份的报告。'
    )


def _example_company(question: str = '', company: str = '') -> tuple[str, str]:
    """作品说明：优先沿用页面当前公司，登记名称仅作为示例。"""
    from .entity_linker import exact_company_codes
    named = [CODE_TO_NAME_MAP[code] for code in CODE_TO_NAME_MAP if code in exact_company_codes(question)]
    shown = company or (named[0] if named else '')
    if shown:
        return shown, '例如：'
    return _company(), '例如，以已入库公司为例：'


def unsupported_reply(reason: str, question: str = '', company: str = '') -> str:
    shown, lead = _example_company(question, company)
    if reason == 'single_quarter_not_supported':
        return (
            '单季的数字现在不能直接查。当前只有年报、一季度、上半年和前三季度累计。'
            f'可以把问题改成累计口径。{lead}{shown}2024年前三季度累计营收多少。'
        )
    if reason == 'investment_advice':
        return (
            '我不能预测股价，也不能建议买入或卖出。这里的回答不构成投资建议。'
            f'可以改问已经入库的财报数字。{lead}{shown}2024年营收多少。'
        )
    if reason == 'refuse_fabrication':
        return (
            '我不能编造或估计财务数字。没有入库记录时，不会给一个大概的数。'
            f'可以改成查实际数字。{lead}{shown}2024年营收多少。'
        )
    if reason == 'unknown_company':
        shown, lead = _example_company('', company)
        return (
            '当前数字库里没有对上这家公司，所以不能查它的财报数字。'
            f'可以查库里已有的公司、比较同一指标，或看原文在哪一页。'
            f'{lead}{shown}2024年营收多少。'
        )
    shown, lead = _example_company('', company)
    return (
        '这个问题不在财报查询范围内。我不回答饮食、天气或其他和生活无关的问题，也不改数据库。'
        f'我可以查财报数字、比较报告期、画趋势，以及找原文。'
        f'{lead}{shown}2024年营收多少。'
    )


def catalog_company_intro() -> str:
    return '下面是当前数字问答库里能直接查数字的公司。研报 PDF 的覆盖可能更宽，没出现在表里不代表没有原文。'


def catalog_company_outro(first_name: str = '') -> str:
    company = first_name or _company()
    return f'可以接着问表里的某一家。例如：{company}2024年营收多少。'


def catalog_report_note() -> str:
    return '年份是报告所属年度。全年是年报，上半年、一季度和前三季度累计是同一年内的累计口径，不是单独一个季度。'


def catalog_metric_note(company: str = '') -> str:
    company = company or _company()
    return (
        '表里是能查的指标类型。某一家、某一年有没有数，需要再点名查一次。'
        f'例如：{company}2024年营业收入多少。'
    )


def execution_scope_reply(queries: list[dict] | None) -> str:
    if not queries:
        return ('这段会话没有保存可核对的执行记录，不能据此确认之前实际查过哪些范围。'
                '实际执行过的查询请查看右侧执行过程。')
    lines = ['上一轮实际执行过的查询如下，只包括已经跑过的范围，不是猜测。']
    for index, query in enumerate(queries, 1):
        scope = query.get('requested_scope') or {}
        names = '、'.join(CODE_TO_NAME_MAP.get(code, code) for code in (scope.get('codes') or [])) or '未记下公司'
        pairs = scope.get('pairs') or []
        period_names = {'FY': '全年', 'HY': '上半年', 'Q1': '一季度', 'Q3': '前三季度累计'}
        when = '、'.join(
            f'{year}年{period_names.get(period, period)}' for year, period in pairs
        ) or '未记下年份'
        status = query.get('status') or '未知'
        lines.append(f'{index}. {names}，报告期 {when}，状态 {status}。')
    lines.append('可以接着问其中某一家、某一年。例如：把上面这家公司的营收再查一次。')
    return '\n'.join(lines)
