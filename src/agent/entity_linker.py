"""作品说明：从本轮用户原话构建已登记实体和统一条件框架。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import re

from .domain import CODE_TO_NAME_MAP
from .facts import MAIN_FINANCIAL_METRICS, YEAR_SPAN_RE, asks_main_financial_metrics, extract_report_periods, field_specs, metric_mentions, year_span_request
from .query_plan import explicit_codes, main_metrics, selected_years

# 作品说明：泛称可能对应多个登记指标，需要用户选择具体口径。
AMBIGUOUS_METRIC_TEXT = {
    '现金流': ['operating_cf_net_amount', 'investing_cf_net_amount', 'financing_cf_net_amount', 'net_cash_flow'],
}
# 作品说明：“近三年”等区间从明确上下文或最新报告解析，不直接按当前自然年倒推。
_RELATIVE_RANGE = YEAR_SPAN_RE
# 作品说明：否定图表请求只禁止画图，不能反向启动图表任务。
_DRAWING = re.compile(r'画|图|可视化|走势|趋势|折线|柱状')
_SUFFIXES = ('股份', '集团', '药业', '制药', '公司')
_ORG_TOKEN = re.compile(r'[\u4e00-\u9fff]{2,12}(?:公司|集团|股份|药业|制药)')
_ORG_SKIP = re.compile(r'^(?:这|那|哪|某|各|每|本|该|此|什么|哪个|一家|几家|两家|多家|十家|全部|所有).*(?:公司|集团)?$|^(?:公司|集团)$')
_CHINESE_SPAN = re.compile(r'[\u4e00-\u9fff]{2,6}')
_COLLECTIVE_ALL = re.compile(r'所有|全部|全库')
_COLLECTIVE_N = re.compile(r'(?:这)?([一二三四五六七八九十两\d]+)家(?:公司)?')
_SINGLE_QUARTER = re.compile(r'单季|非累计|第四季度|第二季度|(?<![A-Za-z])Q2(?![A-Za-z0-9])|(?<![A-Za-z])Q4(?![A-Za-z0-9])', re.I)
_SOURCE = re.compile(r'出处|原文')
_CAUSE = re.compile(r'为啥|原因|为什么|怎么解释|怎么说明')
# 作品说明：此类句子禁止执行财务查询，即使模型提出查询也须阻断。
_MUTATION = re.compile(r'(?i)(?:delete\s+from|drop\s+table|update\s+\w+\s+set|insert\s+into)|密码|别人的聊天')
_INVESTMENT_ADVICE = re.compile(r'股价|涨跌|会涨|会跌|荐股|全仓|投资建议|值得投资|买入|卖出|预测.{0,8}(?:股价|涨跌|涨|跌)')
_FABRICATION = re.compile(r'估个数|编造|编个(?:数|值)|随便造|猜一下|瞎猜')
_EXECUTION_SCOPE = re.compile(r'这轮.{0,8}查|实际查过|查了哪些|查过什么范围')
_EXPLAIN_ONLY = re.compile(r'不用查|不要查|别查了|只解释|只讲|讲概念|不要编|能不能.{0,16}顶上')
_CATALOG_REF = re.compile(r'这些公司')
_OVERVIEW = re.compile(r'怎么样|怎样')
_CN_COUNT = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}


@dataclass
class Mentions:
    """作品说明：只将登记目录精确命中加入已解析公司，近似名称保持待确认。"""
    companies: list[dict] = field(default_factory=list)
    excluded_companies: list[str] = field(default_factory=list)
    near_miss_companies: list[dict] = field(default_factory=list)
    unknown_organizations: list[str] = field(default_factory=list)
    metrics: list[dict] = field(default_factory=list)
    ambiguous_metrics: list[dict] = field(default_factory=list)
    main_metrics: bool = False
    overview: bool = False
    years: list[int] = field(default_factory=list)
    year_span: int = 0
    year_span_before: bool = False
    periods: list[str] = field(default_factory=list)
    single_quarter: bool = False
    company_scope: str | None = None
    drawing: bool = False
    evidence: str = 'none'
    # 作品说明：此字段标记不应执行财务查询的对话主题。
    non_query: str | None = None

    def company_codes(self):
        return [c['code'] for c in self.companies]

    def metric_ids(self):
        return list(dict.fromkeys(m['id'] for m in self.metrics))

    def frame_metrics(self):
        """作品说明：可发布指标来自明确选择或主指标集合，含糊表述不自动猜测。"""
        if self.main_metrics:
            return list(MAIN_FINANCIAL_METRICS)
        return self.metric_ids()

    def to_payload(self):
        return asdict(self)


def _company_text(text, code):
    name = CODE_TO_NAME_MAP.get(code, '')
    if name and name in text:
        return name
    if code in text:
        return code
    for suffix in _SUFFIXES:
        short = name.removesuffix(suffix)
        if short != name and len(short) >= 2 and short in text:
            return short
    return ''


def _name_variants(name):
    variants = {name}
    for suffix in ('股份', '集团', '药业', '制药', '公司'):
        if name.endswith(suffix) and len(name) - len(suffix) >= 2:
            variants.add(name[:-len(suffix)])
    return variants


def exact_company_codes(span: str) -> set[str]:
    """作品说明：仅绑定登记目录的精确身份；句中唯一简称可命中，拼写错误仍待确认。"""
    span = ''.join(str(span or '').split())
    if not span:
        return set()
    exact = {code for code, name in CODE_TO_NAME_MAP.items() if code in span or name in span}
    names = list(CODE_TO_NAME_MAP.values())
    for code, name in CODE_TO_NAME_MAP.items():
        for variant in _name_variants(name):
            if variant == name or len(variant) < 2 or variant not in span:
                continue
            if sum(other.startswith(variant) for other in names) == 1:
                exact.add(code)
    return exact


def fuzzy_company_match(span: str) -> tuple[str, float] | None:
    """作品说明：返回最接近的登记名称供确认，不直接当作已解析公司。"""
    span = ''.join(str(span or '').split())
    if not 2 <= len(span) <= 12 or not CODE_TO_NAME_MAP:
        return None
    if exact_company_codes(span):
        return None
    aliases = []
    for code, name in CODE_TO_NAME_MAP.items():
        aliases.extend((code, variant) for variant in _name_variants(name))
    ranked = sorted(
        ((max(SequenceMatcher(None, span, n).ratio() for c, n in aliases if c == code), code)
         for code in CODE_TO_NAME_MAP),
        reverse=True,
    )
    if ranked and ranked[0][0] >= 0.65 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.15):
        return ranked[0][1], ranked[0][0]
    return None


def _parse_count(raw: str) -> int | None:
    raw = str(raw or '')
    if raw.isdigit():
        return int(raw)
    if raw in _CN_COUNT:
        return _CN_COUNT[raw]
    if len(raw) == 2 and raw[0] == '十' and raw[1] in _CN_COUNT:
        return 10 + _CN_COUNT[raw[1]]
    if len(raw) == 2 and raw[1] == '十' and raw[0] in _CN_COUNT:
        return _CN_COUNT[raw[0]] * 10
    return None


def _collective_scope(text: str) -> str | None:
    if _COLLECTIVE_ALL.search(text):
        return 'all'
    registry_size = len(CODE_TO_NAME_MAP)
    if not registry_size:
        return None
    for match in _COLLECTIVE_N.finditer(text):
        count = _parse_count(match.group(1))
        if count == registry_size:
            return 'all'
    return None


def _covered_ranges(text: str, codes: list[str]) -> list[tuple[int, int]]:
    ranges = []
    for code in codes:
        span = _company_text(text, code)
        if not span:
            continue
        start = text.find(span)
        if start >= 0:
            ranges.append((start, start + len(span)))
    return ranges


def _span_covered(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(left <= start and end <= right for left, right in ranges)


def _non_query_topic(text: str, *, has_concrete_query: bool) -> str | None:
    """作品说明：判定句子是否禁止财务查询。带公司和指标的概念说明仍可包含查询；编造、数据库写入及询问上次查询过程不进入财务查询。"""
    if _MUTATION.search(text):
        return 'out_of_scope'
    if _INVESTMENT_ADVICE.search(text) and not re.search(r'投资活动|投资现金流', text):
        return 'investment_advice'
    if _FABRICATION.search(text):
        return 'refuse_fabrication'
    if _EXECUTION_SCOPE.search(text):
        return 'execution_scope'
    if _EXPLAIN_ONLY.search(text) and not has_concrete_query:
        return 'concept'
    return None


def link_entities(text: str) -> Mentions:
    """作品说明：复用现有识别器构建共享条件框架，避免增加第二套语义理解。"""
    text = str(text or '')
    mentions = Mentions()
    linked = [c for c in explicit_codes(text) if c in CODE_TO_NAME_MAP]
    spans = sorted(((text.find(_company_text(text, c)), c) for c in linked), key=lambda item: item[0])
    mentions.companies = [{'code': c, 'name': CODE_TO_NAME_MAP[c], 'text': _company_text(text, c)} for _, c in spans]
    named = {c for c, n in CODE_TO_NAME_MAP.items() if n in text or c in text}
    mentions.excluded_companies = sorted(named - set(linked))
    covered = _covered_ranges(text, linked)
    near_misses = []
    unknowns = []
    for match in _ORG_TOKEN.finditer(text):
        span = match.group()
        if _ORG_SKIP.match(span) or _collective_scope(span) or _COLLECTIVE_N.fullmatch(span):
            continue
        if _span_covered(match.start(), match.end(), covered):
            continue
        if exact_company_codes(span):
            continue
        fuzzy = fuzzy_company_match(span)
        if fuzzy:
            code, score = fuzzy
            near_misses.append({'code': code, 'name': CODE_TO_NAME_MAP[code], 'text': span, 'score': round(score, 3)})
        else:
            unknowns.append(span)
    for match in _CHINESE_SPAN.finditer(text):
        span = match.group()
        if _span_covered(match.start(), match.end(), covered):
            continue
        if any(span in item['text'] or item['text'] in span for item in near_misses):
            continue
        if any(span in item or item in span for item in unknowns):
            continue
        if exact_company_codes(span):
            continue
        fuzzy = fuzzy_company_match(span)
        if fuzzy:
            code, score = fuzzy
            near_misses.append({'code': code, 'name': CODE_TO_NAME_MAP[code], 'text': span, 'score': round(score, 3)})
    # 作品说明：每个候选代码保留最高匹配分数及最早出现位置。
    best: dict[str, dict] = {}
    for item in near_misses:
        prior = best.get(item['code'])
        if not prior or item['score'] > prior['score']:
            best[item['code']] = item
    mentions.near_miss_companies = list(best.values())
    mentions.unknown_organizations = list(dict.fromkeys(unknowns))
    labels = {k: s['label'] for k, s in field_specs().items()}
    for start, end, metric in metric_mentions(text):
        span = text[start:end]
        if span in AMBIGUOUS_METRIC_TEXT:
            mentions.ambiguous_metrics.append({'text': span, 'options': AMBIGUOUS_METRIC_TEXT[span]})
        elif metric in labels:
            mentions.metrics.append({'id': metric, 'label': labels[metric], 'text': span})
    # 作品说明：「扣非」单独出现时，可能是扣非净利润，也可能是扣非后的收益率。
    if (re.search(r'扣非', text) and not any('扣非' in item['text'] for item in mentions.metrics)
            and not mentions.metric_ids()):
        mentions.ambiguous_metrics.append({
            'text': '扣非',
            'options': ['net_profit_excl_non_recurring', 'roe_weighted_excl_non_recurring'],
        })
    broad = bool(main_metrics(text) or asks_main_financial_metrics(text))
    comparing = bool(re.search(r'相比|比较|对比|同比|(?:跟|和|与).{0,16}比|比一下', text))
    mentions.overview = bool(_OVERVIEW.search(text) and not comparing and not mentions.metric_ids() and not mentions.ambiguous_metrics)
    mentions.main_metrics = bool((broad or mentions.overview) and not comparing and not mentions.metric_ids() and not mentions.ambiguous_metrics)
    span = year_span_request(text)
    mentions.year_span = span[0] if span else 0
    mentions.year_span_before = bool(span and span[1] == 'before')
    mentions.years = selected_years(_RELATIVE_RANGE.sub('', text))
    mentions.periods = extract_report_periods(text)
    mentions.single_quarter = bool(_SINGLE_QUARTER.search(text))
    mentions.company_scope = 'catalog' if _CATALOG_REF.search(text) and not linked else _collective_scope(text)
    mentions.drawing = bool(_DRAWING.search(text))
    mentions.non_query = _non_query_topic(text, has_concrete_query=bool(linked and mentions.metric_ids()))
    if mentions.non_query:
        mentions.evidence = 'none'
    elif _CAUSE.search(text):
        mentions.evidence = 'cause'
    elif _SOURCE.search(text):
        mentions.evidence = 'source'
    else:
        mentions.evidence = 'none'
    return mentions


# 作品说明：该名称供规划器与比较流程共同使用。
build_turn_frame = link_entities
