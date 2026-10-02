"""作品说明：提供稳定证据标识与类型化事实，不由模型推测身份。"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any, Mapping, Sequence

from .domain import COMPANY_CODE_MAP, KEYWORD_FIELD_MAP, get_table_fields


MAIN_FINANCIAL_METRICS: tuple[str, ...] = (
    "total_operating_revenue",
    "net_profit",
    "gross_profit_margin",
    "roe",
)
_MAIN_METRICS_RE = re.compile(r"主要(?:财务|会计)?(?:数据|指标|数字)|核心(?:财务|业绩)?指标|财务概况|关键财务数字")
_PERIOD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Q1", r"一季度|第一季度|一季报|(?<![A-Za-z])Q1(?![A-Za-z0-9])"),
    ("HY", r"上半年|半年度|半年报|中报|中期|半年(?!度|报)|(?<![A-Za-z])HY(?![A-Za-z0-9])|(?<![A-Za-z])H1(?![A-Za-z0-9])"),
    ("Q3", r"前三季度|三季度|第三季度|三季报|(?<![A-Za-z])Q3(?![A-Za-z0-9])"),
    # 作品说明：半年报、半年度里的「年报」「年度」不是全年。
    ("FY", r"全年|(?<!半)年报|(?<!半)年度|(?<![A-Za-z])FY(?![A-Za-z0-9])"),
)
_CN_YEAR_DIGIT = str.maketrans({
    "零": "0", "〇": "0", "○": "0", "一": "1", "二": "2", "三": "3",
    "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9",
})


def evidence_id(kind: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return f"{kind}-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def field_specs() -> dict[str, dict[str, str]]:
    specs = {}
    for fields in get_table_fields().values():
        for field, label in fields.items():
            unit = "%" if "%" in label else "万元" if "万元" in label else "元" if "元)" in label or "元）" in label else ""
            if unit:
                # 作品说明：括号可承载扣非等业务口径，不能全部删除，否则会把调整后指标混同原指标。
                business_label = re.sub(r'[（(](?:万元|亿元|元(?:/股)?|%|％)[）)]\s*$', '', label)
                specs.setdefault(field, {"label": business_label, "unit": unit})
    return specs


def metric_mentions(text: str) -> list[tuple[int, int, str]]:
    aliases = dict(KEYWORD_FIELD_MAP)
    for field, spec in field_specs().items():
        aliases[field] = field
        aliases.setdefault(spec["label"], field)
    matches = [(m.start(), m.end(), field) for alias, field in aliases.items()
               for m in re.finditer(re.escape(alias), text, re.I)]
    # 作品说明：扣非净利润或销售净利率内部的短名称不能再识别为另一个指标。
    return sorted([item for item in matches if not any(
        other[0] <= item[0] and other[1] >= item[1] and other[1] - other[0] > item[1] - item[0]
        for other in matches)], key=lambda item: (item[0], item[1]))


def requested_metric_fields(text: str) -> set[str]:
    """作品说明：仅对明确支持的宽泛摘要请求展开主指标集合。"""
    fields = {item[2] for item in metric_mentions(str(text or ""))}
    if _MAIN_METRICS_RE.search(str(text or "")):
        fields.update(MAIN_FINANCIAL_METRICS)
    return fields


def asks_main_financial_metrics(text: str) -> bool:
    return bool(_MAIN_METRICS_RE.search(str(text or "")))


def _arabic_year_spellings(source: str) -> str:
    """作品说明：支持中文完整年度及两位年度，分别规范为对应的四位年份。"""
    source = re.sub(r"[零〇○一二三四五六七八九]{4}", lambda m: m.group(0).translate(_CN_YEAR_DIGIT), source)
    return re.sub(r"[零〇○一二三四五六七八九]{2}(?=年)", lambda m: m.group(0).translate(_CN_YEAR_DIGIT), source)


def _listed_years(source: str) -> set[int]:
    """作品说明：用户列举的每个年份均须保留，即使只有末项带年字。"""
    token = re.compile(r"(?<!\d)((?:20)?\d{2})(?!\d)")
    gap = re.compile(r"\s*年?\s*(?:和|与|及|、|，|,|/|\s)\s*")
    current: list[int] = []
    pos = 0
    found: set[int] = set()

    def add(raw: str) -> int:
        return int(raw) if len(raw) == 4 else 2000 + int(raw)

    for match in token.finditer(source):
        year = add(match.group(1))
        if current and gap.fullmatch(source[pos:match.start()]):
            current.append(year)
        else:
            if len(current) >= 2:
                found.update(current)
            current = [year]
        pos = match.end()
    if len(current) >= 2:
        found.update(current)
    return found


def extract_report_years(text: str, *, current_year: int | None = None, report_anchor: int | None = None) -> list[int]:
    """作品说明：支持完整年份、口语两位年份、年份区间及相对年份。"""
    source = _arabic_year_spellings(str(text or ""))
    years = {int(year) for year in re.findall(r"(?<!\d)(20\d{2})(?!\d)", source)}
    years.update(_listed_years(source))

    for first, last in re.findall(r"(20\d{2})\s*(?:年)?\s*[-—~至到]\s*(20\d{2})", source):
        if 0 <= int(last) - int(first) <= 30:
            years.update(range(int(first), int(last) + 1))

    def full(short: str) -> int:
        return 2000 + int(short)

    short_ranges = re.findall(
        r"(?<!\d)(\d{2})\s*(?:年)?\s*[-—~至到]\s*(\d{2})\s*(?:年|这几年|几年)",
        source,
    )
    for first, last in short_ranges:
        start, end = full(first), full(last)
        if 0 <= end - start <= 30:
            years.update(range(start, end + 1))

    for first, last in re.findall(
        r"(?<!\d)(\d{2})\s*(?:年)?\s*(?:和|与|及|、|,|，|/)\s*(\d{2})\s*(?:年|两年)",
        source,
    ):
        years.update((full(first), full(last)))

    # 作品说明：两个简写年度可共享同一报告期间，例如 23/24 全年。
    for first, last in re.findall(r"(?<!\d)(\d{2})\s*/\s*(\d{2})(?!\d)", source):
        years.update((full(first), full(last)))

    # 作品说明：紧凑报告编号的期间应绑定邻近短年份。
    years.update(full(value) for value in re.findall(
        r"(?<!\d)(\d{2})(?=(?:Q1|HY|Q3|FY)(?![A-Za-z0-9]))", source, re.I))

    years.update(full(value) for value in re.findall(r"(?<!\d)(\d{2})\s*(?:财)?年", source))

    calendar = int(current_year or datetime.now().year)
    # 作品说明：今年始终按今天。去年、前年在追问里可以改锚到正在看的报告年。
    relative = int(report_anchor) if report_anchor else calendar
    if "今年" in source:
        years.add(calendar)
    if "去年" in source:
        years.add(relative - 1)
    if "前年" in source:
        years.add(relative - 2)
    # 作品说明：旁边的「这三年」只是在确认已经列出的那一串，不另按今天倒推。
    recent_match = re.search(rf"(?:最近|近|过去)({_YEAR_COUNT_WORD})年", source)
    if recent_match and not _listed_years(source):
        count = _year_count(recent_match.group(1))
        if count:
            years.update(range(calendar - count, calendar))
    return sorted(years)


_YEAR_COUNT_WORD = r"[两二三四五2-5]|\d"
YEAR_SPAN_RE = re.compile(rf"(最近|过去|近|这|前)({_YEAR_COUNT_WORD})年")


def _year_count(raw: str) -> int:
    """作品说明：中文两与二表示相同数量，数字本身保持不变。"""
    named = {"二": 2, "两": 2, "三": 3, "四": 4, "五": 5}
    if raw in named:
        return named[raw]
    return int(raw) if str(raw).isdigit() else 0


def year_span_request(text: str) -> tuple[int, str] | None:
    """作品说明：用户未逐一列年时解析年度数量；已有年份列表时不重复生成窗口。窗口分为截至当前条件的最近若干年与之前若干年。"""
    source = _arabic_year_spellings(str(text or ""))
    if _listed_years(source):
        return None
    match = YEAR_SPAN_RE.search(source)
    if not match:
        return None
    count = _year_count(match.group(2))
    if not 2 <= count <= 10:
        return None
    kind = "before" if match.group(1) == "前" else "ending"
    return count, kind


def extract_report_periods(text: str) -> list[str]:
    """作品说明：按原话出现顺序保留全部期间。"""
    found: list[tuple[int, str]] = []
    for period, pattern in _PERIOD_PATTERNS:
        found.extend((match.start(), period) for match in re.finditer(pattern, str(text or ""), re.I))
    result: list[str] = []
    for _, period in sorted(found):
        if period not in result:
            result.append(period)
    return result


def explicit_period(text: str) -> str | None:
    periods = extract_report_periods(text)
    return periods[0] if periods else None


def extract_year_period_pairs(text: str) -> list[tuple[int, str]]:
    """作品说明：将相邻年度与期间绑定，避免错误生成笛卡尔积。"""
    source = str(text or "")
    years = extract_report_years(source)
    periods = extract_report_periods(source)
    if not years:
        return []
    if not periods:
        return [(year, "FY") for year in years]
    if len(periods) == 1:
        return [(year, periods[0]) for year in years]

    period_words = (
        r"一季度|第一季度|上半年|半年度|半年报|中报|中期|"
        r"前三季度|三季度|第三季度|三季报|全年|年报|年度|Q1|HY|Q3|FY"
    )
    direct: list[tuple[int, str]] = []
    for match in re.finditer(
        rf"(?<!\d)((?:20)?\d{{2}})(?!\d)\s*年?\s*({period_words})",
        source,
        re.I,
    ):
        raw_year, raw_period = match.groups()
        year = int(raw_year) if len(raw_year) == 4 else 2000 + int(raw_year)
        period = explicit_period(raw_period)
        if period and (year, period) not in direct:
            direct.append((year, period))
    return direct


def request_scope(text: str, rows: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    names = dict(COMPANY_CODE_MAP)
    names.update({str(row["stock_abbr"]): str(row["stock_code"]) for row in rows
                  if row.get("stock_abbr") and row.get("stock_code")})
    codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", text))
    codes.update(str(code) for name, code in names.items() if name and name in text)
    years = extract_report_years(text)
    periods = extract_report_periods(text)
    if years and not periods:
        periods = ["FY"]
    pairs = extract_year_period_pairs(text)
    return {
        "stock_codes": sorted(codes),
        "report_years": years,
        "report_period": periods[0] if len(periods) == 1 else None,
        "report_periods": periods,
        "year_periods": pairs,
    }


def scoped_metadata(meta: Mapping[str, Any]) -> dict[str, Any]:
    """作品说明：缺失身份只从文件名或报告标题补充，不能借正文中的其他期间填充。"""
    result = dict(meta)
    header = str(meta.get("source_title") or meta.get("doc_name") or meta.get("source") or "").replace("\\", "/").split("/")[-1]
    inferred = request_scope(header)
    if not result.get("stock_code") and len(inferred["stock_codes"]) == 1:
        result["stock_code"] = inferred["stock_codes"][0]
    if not result.get("report_year") and len(inferred["report_years"]) == 1:
        result["report_year"] = inferred["report_years"][0]
    if not result.get("report_period"):
        result["report_period"] = explicit_period(header) or ("FY" if meta.get("report_kind") == "annual" else None)
    return result


def metadata_matches(meta: Mapping[str, Any], scope: Mapping[str, Any]) -> bool:
    if meta.get("publication_status") == "staged":
        return False
    codes, years = scope.get("stock_codes") or [], scope.get("report_years") or []
    try:
        year = int(meta.get("report_year") or 0)
    except (TypeError, ValueError):
        return False
    period = str(meta.get("report_period") or "")
    pairs = {(int(item[0]), str(item[1])) for item in (scope.get("year_periods") or [])}
    periods = set(scope.get("report_periods") or ([] if not scope.get("report_period") else [scope["report_period"]]))
    return (
        (not codes or str(meta.get("stock_code") or "") in codes)
        and (not pairs or (year, period) in pairs)
        and (bool(pairs) or not years or year in years)
        and (bool(pairs) or not periods or period in periods)
    )


def bind_sql_rows(sql: str, rows: Sequence[Mapping[str, Any]], scope: Mapping[str, Any] | None = None,
                  lineage: Mapping[str, Any] | None = None) -> tuple[str, list[dict[str, Any]]]:
    query_id = evidence_id("query", sql.strip())
    bound = []
    for index, original in enumerate(rows):
        row = dict(original)
        for key in ("stock_code", "stock_abbr", "report_year", "report_period"):
            if row.get(key) is None and (scope or {}).get(key) is not None:
                row[key] = scope[key]
        row["query_id"] = query_id
        row["row_id"] = evidence_id("row", query_id, index, original)
        if lineage is not None:
            row["_column_lineage"] = dict(lineage)
        bound.append(row)
    return query_id, bound


def build_facts(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    specs, facts = field_specs(), []
    for index, row in enumerate(rows):
        lineage = row.get("_column_lineage")
        for output_field, raw in row.items():
            info = (lineage or {}).get(output_field)
            # 作品说明：只接受明确来源映射，未登记输出不能伪装成业务字段。
            if isinstance(lineage, Mapping) and not info:
                continue
            field = str(info.get("field") or output_field) if isinstance(info, Mapping) else output_field
            if field not in specs or isinstance(raw, bool):
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            row_id = row.get("row_id") or evidence_id("row", index, row)
            facts.append({"fact_id": evidence_id("fact", row_id, field), "query_id": row.get("query_id"),
                          "row_id": row_id, "stock_code": row.get("stock_code"), "stock_abbr": row.get("stock_abbr"),
                          "report_year": row.get("report_year"), "report_period": row.get("report_period"),
                          "field": field, "output_field": output_field, "unit": specs[field]["unit"], "value": value,
                          "source": (row.get("_provenance") or {}).get(output_field) or {"status": "source_unlocated"}})
    return facts


def document_identity(item: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(item)
    path = str(item.get("paper_path") or item.get("source") or "").replace("\\", "/")
    version = str(item.get("document_version") or item.get("source_sha256") or "unversioned")
    document_id = str(item.get("document_id") or evidence_id("document", path))
    result.update(document_id=document_id, document_version=version,
                  chunk_id=str(item.get("chunk_id") or evidence_id("chunk", document_id, version,
                               item.get("page_start"), item.get("section_title"), item.get("text"))))
    return result
