"""作品说明：财报回答的确定性验证与统一证据构建。

本模块只处理本轮已经产生的 SQL 行、RAG 命中和图表数据，不访问网络、数据库
或模型，便于离线单测与评测框架复用。
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .facts import build_facts, evidence_id, explicit_period, metadata_matches, metric_mentions, request_scope
from .domain import COMPANY_CODE_MAP


_NUMBER_WITH_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<value>[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*(?P<unit>个百分点|亿元|万元|％|%|亿|万|元)"
)
_PERCENT_KEY_MARKERS = (
    "ratio", "rate", "margin", "roe", "yoy", "同比", "增长率", "变动率", "占比", "率",
)
_NON_METRIC_KEYS = (
    "serial", "report_year", "year", "stock_code", "code", "id", "index",
)
_PERIOD_ORDER = {"Q1": 1, "HY": 2, "Q3": 3, "FY": 4}
_FAIL_NOTICE = "⚠️ 以下数字未能通过数据核验，请结合证据栏复核。"


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _close(actual: float, expected: float, *, percent: bool = False, tolerance: float = 0.0) -> bool:
    return abs(actual - expected) <= tolerance + max(1e-8, abs(expected) * 1e-10)


def extract_numeric_claims(answer_text: str) -> List[Dict[str, Any]]:
    """作品说明：提取带金额或百分比单位的财务数字；年份、序号和股票代码不会误入。"""
    claims: List[Dict[str, Any]] = []
    for match in _NUMBER_WITH_UNIT_RE.finditer(str(answer_text or "")):
        raw_value = float(match.group("value").replace(",", ""))
        unit = match.group("unit")
        if unit in {"%", "％", "个百分点"}:
            normalized_values = [raw_value]
            kind = "percentage"
            normalized_unit = "%"
        elif unit in {"亿元", "亿"}:
            normalized_values = [raw_value * 10000.0]
            kind = "amount"
            normalized_unit = "万元"
        elif unit in {"万元", "万"}:
            normalized_values = [raw_value]
            kind = "amount"
            normalized_unit = "万元"
        else:
            # 作品说明：数据库大额字段以万元计，但 EPS 等字段本身以元计；两种尺度都核对。
            normalized_values = [raw_value, raw_value / 10000.0]
            kind = "amount"
            normalized_unit = "元/万元"
        claims.append({
            "raw": match.group(0),
            "value": raw_value,
            "unit": unit,
            "kind": kind,
            "normalized_unit": normalized_unit,
            "normalized_values": normalized_values,
        })
    return claims


def _is_metric_key(key: str) -> bool:
    lowered = str(key or "").lower()
    return not any(marker in lowered for marker in _NON_METRIC_KEYS)


def _is_percent_key(key: str) -> bool:
    lowered = str(key or "").lower()
    return any(marker in lowered for marker in _PERCENT_KEY_MARKERS)


def _derived_yoy(facts):
    """作品说明：同比基期核验要求完整身份及相同报告期间。"""
    index = defaultdict(list)
    for fact in facts:
        if not all(fact.get(key) for key in ("stock_code", "report_year", "report_period")):
            continue
        if fact["unit"] == "%":
            continue
        index[(fact["stock_code"], fact["field"], fact["report_period"], int(fact["report_year"]))].append(fact)
    output = []
    for (code, field, period, year), current in index.items():
        previous = index.get((code, field, period, year - 1), [])
        if len({f["value"] for f in current}) != 1 or len({f["value"] for f in previous}) != 1:
            continue
        if not previous[0]["value"]:
            continue
        fact = dict(current[0])
        fact.update(unit="%", value=(current[0]["value"] - previous[0]["value"]) / abs(previous[0]["value"]) * 100,
                    calculation="yoy", source_fact_ids=[previous[0]["fact_id"], current[0]["fact_id"]])
        fact["fact_id"] = evidence_id("fact", *fact["source_fact_ids"], "yoy")
        output.append(fact)
    return output


def _claim_context(answer, match, question, rows):
    # 作品说明：句子边界核验保留逗号前的身份上下文。
    start = max([answer.rfind(delimiter, 0, match.start()) for delimiter in ("。", "；", ";", "\n", "！", "？")]) + 1
    prefix = answer[start:match.start()]
    mentions = metric_mentions(prefix)
    field = mentions[-1][2] if mentions else None
    question_scope = request_scope(question, rows)
    local_scope = request_scope(prefix, rows)
    context = {}
    for output, key in (("stock_code", "stock_codes"), ("report_year", "report_years")):
        values = local_scope[key] or question_scope[key]
        if output == "stock_code":
            nearby = [
                (prefix.rfind(name), str(code))
                for name, code in COMPANY_CODE_MAP.items()
                if name and name in prefix
            ]
            nearby.extend(
                (match.start(), match.group(1))
                for match in re.finditer(r"(?<!\d)(\d{6})(?!\d)", prefix)
            )
            if nearby:
                values = [max(nearby, key=lambda item: item[0])[1]]
        if output == "report_year" and local_scope[key]:
            values = [int(re.findall(r"(?<!\d)(20\d{2})(?!\d)", prefix)[-1])]
        context[output] = values[0] if len(values) == 1 else None
    context["report_period"] = explicit_period(prefix) or question_scope["report_period"] or ("FY" if context["report_year"] else None)
    if field is None:
        question_fields = {item[2] for item in metric_mentions(question)}
        field = next(iter(question_fields)) if len(question_fields) == 1 else None
    tail = prefix[mentions[-1][1]:] if mentions else prefix
    context.update(field=field, calculation="yoy" if "同比" in tail else None,
                   ambiguous=bool(re.search(r"不是|并非|不等于|超过|不足|至少|至多|约束", tail)),
                   negative=bool(re.search(r"下降|减少|降低", tail)))
    return context


def verify_numbers(answer_text: str, sql_rows: Sequence[Mapping[str, Any]], *, question: str = "") -> Dict[str, Any]:
    claims = extract_numeric_claims(answer_text)
    facts = build_facts(sql_rows)
    facts += _derived_yoy(facts)
    unmatched, unverified, matched_claims = [], [], []
    scope = request_scope(question, sql_rows)
    for claim, match in zip(claims, _NUMBER_WITH_UNIT_RE.finditer(str(answer_text or ""))):
        context = _claim_context(str(answer_text), match, question, sql_rows)
        enriched = {**claim, **context}
        if context["ambiguous"] or not all(context.get(key) for key in ("stock_code", "report_year", "report_period", "field")):
            unverified.append({**enriched, "reason": "无法唯一识别公司、指标、期间或断言含义"})
            continue
        if not metadata_matches(context, scope):
            unmatched.append({**enriched, "reason": "断言主体或期间与问题不一致"})
            continue
        unit = claim["unit"]
        value = claim["value"]
        if context["negative"] and value > 0:
            value = -value
        candidates = []
        for fact in facts:
            if any(str(fact.get(key) or "") != str(context[key]) for key in ("stock_code", "report_year", "report_period")):
                continue
            expected_field = context["field"]
            field_match = fact["field"] == expected_field
            if context["calculation"] == "yoy":
                field_match = (field_match and fact.get("calculation") == "yoy") or fact["field"] in {
                    expected_field + "_yoy_growth", expected_field + "_yoy",
                    "operating_revenue_yoy_growth" if expected_field == "total_operating_revenue" else ""}
            elif fact.get("calculation"):
                field_match = False
            if not field_match:
                continue
            normalized = value
            decimals = len(match.group("value").partition(".")[2])
            rounding_tolerance = 0.5 * 10 ** (-decimals)
            compatible = False
            if unit in {"%", "％"}:
                compatible = fact["unit"] == "%"
            elif unit == "个百分点":
                # 作品说明：百分点变化属于明确差额事实，不能当作增长率。
                compatible = fact.get("calculation") == "percentage_point_difference"
            elif unit in {"亿元", "亿", "万元", "万", "元"} and fact["unit"] in {"元", "万元"}:
                yuan = value * ({"亿元": 1e8, "亿": 1e8, "万元": 1e4, "万": 1e4, "元": 1}[unit])
                normalized = yuan / (1e4 if fact["unit"] == "万元" else 1)
                rounding_tolerance *= {"亿元": 1e8, "亿": 1e8, "万元": 1e4, "万": 1e4, "元": 1}[unit] / (1e4 if fact["unit"] == "万元" else 1)
                compatible = True
            if compatible and _close(normalized, fact["value"], percent=fact["unit"] == "%", tolerance=rounding_tolerance):
                candidates.append(fact)
        if candidates:
            matched_claims.append({**enriched, "fact_ids": [fact["fact_id"] for fact in candidates]})
        else:
            unmatched.append({**enriched, "reason": "对应公司、指标、期间与单位的事实不支持该数值"})
    status = "fail" if unmatched else "warn" if unverified or not claims else "pass"
    return {"name": "numbers_grounded", "label": "财务事实核验", "status": status,
            "detail": f"已关联 {len(matched_claims)}/{len(claims)} 项财务事实；未核验 {len(unverified)} 项" if claims else "没有可自动核验的带单位财务断言",
            "total": len(claims), "matched": len(matched_claims), "unmatched": unmatched,
            "unverified": unverified, "claims": matched_claims}


def verify_references(references: Sequence[Mapping[str, Any]], rag_results: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    unmatched, unverified = [], []
    for reference in references:
        identity = (reference.get("document_id"), reference.get("chunk_id"))
        if not all(identity):
            unverified.append(str(reference.get("source_title") or "未知来源"))
            continue
        originals = [item for item in rag_results if (item.get("document_id"), item.get("chunk_id")) == identity]
        excerpt = _normalize_text(reference.get("text")).rstrip("…").strip()
        if not excerpt or not any(
            excerpt in _normalize_text(item.get("text")) and all(
                str(reference.get(key) or "") == str(item.get(key) or "")
                for key in ("document_version", "source_sha256", "stock_code", "report_year", "report_period", "page_start", "source_title"))
            for item in originals):
            unmatched.append(str(reference.get("source_title") or identity[0]))
    status = "fail" if unmatched else "warn" if unverified or (rag_results and not references) else "pass"
    return {"name": "references_supported", "label": "原文与来源一致性", "status": status,
            "detail": "仅核对具体片段、来源身份和摘录；不证明解释性结论受到支持",
            "total": len(references), "matched": len(references) - len(unmatched) - len(unverified),
            "unmatched": unmatched, "unverified": unverified}


def _normalize_label(value: Any) -> str:
    text = _normalize_text(value).replace("年度", "").replace("年", "")
    if re.fullmatch(r"[-+]?\d+\.0+", text):
        text = text.split(".", 1)[0]
    return text.lower()


def bind_chart_source(chart: Mapping[str, Any], sql_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """作品说明：每个图表点须唯一对应字段、查询和数据行。"""
    source = dict(chart.get("data_source") or {})
    if source.get("kind") != "sql_result":
        return source
    facts = build_facts(sql_rows)
    query_id = source.get("query_id")
    available_queries = {fact.get("query_id") for fact in facts if fact.get("query_id")}
    if not query_id and len(available_queries) == 1:
        query_id = next(iter(available_queries))
    text = " ".join(str(chart.get(key) or "") for key in ("y_label", "series_name", "title"))
    fields = {item[2] for item in metric_mentions(text)}
    field = source.get("y_field") or (next(iter(fields)) if len(fields) == 1 else None)
    x_field = source.get("x_field") or "report_year"
    label = str(chart.get("y_label") or "")
    unit = source.get('unit') or ("亿元" if "亿" in label else "万元" if "万" in label else "%" if "%" in label or "％" in label else "元" if "元" in label else None)
    source.update(query_id=query_id, x_field=x_field, y_field=field, unit=unit)
    points = []
    for x, y in zip(chart.get("x_data") or [], chart.get("y_data") or []):
        if y is None:
            points.append({'x':x,'y':None,'missing':True})
            continue
        candidates = []
        for fact in facts:
            if fact["query_id"] != query_id or fact["output_field"] != field and fact["field"] != field:
                continue
            row = next((row for row in sql_rows if row.get("row_id") == fact["row_id"]), {})
            if _normalize_label(row.get(x_field)) != _normalize_label(x):
                continue
            candidates.append(fact)
        if len(candidates) == 1:
            points.append({"row_id": candidates[0]["row_id"], "fact_id": candidates[0]["fact_id"], "x": x, "y": y})
    source["points"] = points
    return source


def verify_charts(charts: Sequence[Mapping[str, Any]], sql_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    failures, warnings = [], []
    facts = {fact["fact_id"]: fact for fact in build_facts(sql_rows)}
    rows = {row.get("row_id"): row for row in sql_rows}
    for index, chart in enumerate(charts, 1):
        x_data, y_data = list(chart.get("x_data") or []), list(chart.get("y_data") or [])
        source = chart.get("data_source") or {}
        if not x_data or len(x_data) != len(y_data):
            failures.append(f"图表 {index} 的 x/y 数据为空或长度不一致")
            continue
        if source.get("kind") != "sql_result":
            warnings.append(f"图表 {index} 为显式输入，尚未核实来源")
            continue
        if not all(source.get(key) for key in ("query_id", "x_field", "y_field", "unit")):
            warnings.append(f"图表 {index} 缺少查询、字段或单位绑定")
            continue
        points = source.get("points") or []
        if len(points) != len(x_data):
            failures.append(f"图表 {index} 数据点不能唯一关联 SQL 行")
            continue
        title_scope = request_scope(" ".join(str(chart.get(k) or "") for k in ("title", "series_name")), sql_rows)
        label_fields = {item[2] for item in metric_mentions(str(chart.get("y_label") or ""))}
        for x, raw_y, point in zip(x_data, y_data, points):
            if raw_y is None:
                if not point.get('missing') or point.get('fact_id') or point.get('row_id') or point.get('x')!=x:
                    failures.append(f"图表 {index} 缺失点标记无效")
                continue
            fact, row = facts.get(point.get("fact_id")), rows.get(point.get("row_id"))
            value = _finite_number(raw_y)
            if not fact or not row or value is None or fact["row_id"] != point.get("row_id") or fact["query_id"] != source["query_id"]:
                failures.append(f"图表 {index} 数据点来源绑定无效")
                continue
            if not all(fact.get(key) for key in ("stock_code", "report_year", "report_period")):
                warnings.append(f"图表 {index} 来源行缺少完整主体期间")
            scale = 10000 if source['unit']=='亿元' and fact['unit']=='万元' else 0.0001 if source['unit']=='元' and fact['unit']=='万元' else 1
            valid_unit = source["unit"] == fact["unit"] or source["unit"] in {'亿元','元'} and fact["unit"] == "万元" or source['unit']=='元/股' and fact['field']=='eps'
            expected = value * scale
            if not (valid_unit and _close(expected, fact["value"], percent=fact["unit"] == "%")
                    and _normalize_label(x) == _normalize_label(row.get(source["x_field"]))
                    and source["y_field"] in {fact["field"], fact["output_field"]}
                    and (not label_fields or label_fields == {fact["field"]}) and metadata_matches(fact, title_scope)):
                failures.append(f"图表 {index} 数据点 {x} → {raw_y} 的主体、指标、单位或期间不一致")
    return {"name": "charts_consistent", "label": "图表事实核验", "status": "fail" if failures else "warn" if warnings else "pass",
            "detail": (failures or warnings or [f"{len(charts)} 张图表按查询、字段及数据行核验"])[0],
            "total": len(charts), "failures": failures, "warnings": warnings}


def verify_turn(
    answer_text: str,
    sql_rows: Sequence[Mapping[str, Any]],
    references: Sequence[Mapping[str, Any]],
    rag_results: Sequence[Mapping[str, Any]],
    charts: Sequence[Mapping[str, Any]],
    *,
    degraded: bool = False,
    needs_clarification: bool = False,
    question: str = "",
) -> Dict[str, Any]:
    """作品说明：执行三类核验并汇总为稳定的 pass/warn/fail 契约。"""
    reference_check = verify_references(references, rag_results)
    quote_count, quote_errors = 0, []
    def bound_quote(match):
        nonlocal quote_count
        index = int(match.group(1)) - 1
        if 0 <= index < len(references):
            ref = references[index]
            period = {'FY': '全年', 'HY': '上半年', 'Q1': '一季度', 'Q3': '前三季度'}.get(ref.get('report_period'), ref.get('report_period'))
            header = f"{ref.get('stock_code')}，{ref.get('report_year')}年{period}，PDF 第 {ref.get('page_start')} 页"
            quote = re.sub(r'(?m)^> ?', '', match.group(3)).strip()
            if (reference_check['status'] == 'pass' and ref.get('source_sha256')
                    and metadata_matches(ref, request_scope(question, sql_rows))
                    and match.group(2) == header and _normalize_text(quote) == _normalize_text(ref.get('text'))):
                quote_count += 1
                return ''
        quote_errors.append('正文摘引的文字、引用编号、公司、报告期或页码与来源不一致')
        return match.group(0)
    narrative = re.sub(r'原文摘引【(\d+)】（([^）]+)）：\s*\n\n(>[^\n]*(?:\n>[^\n]*)*)', bound_quote, answer_text)
    checks = [
        verify_numbers(narrative, sql_rows, question=question),
        reference_check,
        verify_charts(charts, sql_rows),
    ]
    if quote_count or quote_errors:
        checks.append({'name': 'literal_quotes', 'label': '正文原文摘引', 'status': 'fail' if quote_errors else 'pass',
                       'matched': quote_count, 'detail': quote_errors[0] if quote_errors else '正文摘引逐字匹配已绑定来源；摘引内数字属于原文披露，不作为独立计算结论'})
        if quote_count and not quote_errors and not extract_numeric_claims(narrative):
            checks[0].update(status='pass', detail='摘引内数字已核对原文；摘引外没有财务数值断言')
    if charts and not extract_numeric_claims(answer_text):
        checks[0].update(status="pass", detail="本轮数值通过图表逐点核验，正文不含财务金额")
    if rag_results or any(word in question for word in ("原因", "为什么", "归因", "解释")):
        checks.append({"name": "explanation_support", "label": "解释性结论", "status": "warn",
                       "detail": "自动检查仅覆盖结构化财务事实与摘录身份；解释是否被原文支持尚需人工核对"})
    if not str(answer_text or "").strip():
        checks.append({"name": "answer_state", "status": "fail", "label": "回答状态", "detail": "回答为空"})
    if needs_clarification:
        checks.append({
            "name": "turn_state",
            "label": "回答状态",
            "status": "warn",
            "detail": "正在等待用户补充信息，本轮尚未形成可核验答案",
        })
    if degraded:
        checks.append({
            "name": "execution_mode",
            "label": "运行模式",
            "status": "warn",
            "detail": "本轮使用规则降级模式，数字仍按 SQL 结果核验",
        })

    statuses = {str(check.get("status")) for check in checks}
    status = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    unmatched_numbers = [
        item
        for check in checks
        if check.get("name") == "numbers_grounded"
        for item in (check.get("unmatched") or [])
    ]
    return {
        "status": status,
        "scope": "可识别的公司、指标、报告期、单位、数值及证据身份；不证明所有自然语言结论",
        "checks": checks,
        "unmatched_numbers": unmatched_numbers,
        "summary": {
            "passed": sum(1 for check in checks if check.get("status") == "pass"),
            "warnings": sum(1 for check in checks if check.get("status") == "warn"),
            "failed": sum(1 for check in checks if check.get("status") == "fail"),
        },
    }


def prepend_failure_notice(answer_text: str, verification: Mapping[str, Any]) -> str:
    content = str(answer_text or "").strip()
    if verification.get("status") != "fail" or content.startswith(_FAIL_NOTICE):
        return content
    return f"{_FAIL_NOTICE}\n\n当前证据无法支持所请求的结论，暂不提供未经核实的数字。请查看查询结果与核验详情，或补充报告。"


def build_evidence(
    sql_events: Sequence[Mapping[str, Any]],
    references: Sequence[Mapping[str, Any]],
    charts: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """作品说明：把三类既有结果整理为同一列表；旧展示字段仍由调用方保留。"""
    evidence: List[Dict[str, Any]] = []
    for index, event in enumerate(sql_events, start=1):
        evidence.append({
            "id": event.get("query_id") or f"sql-{index}",
            "query_id": event.get("query_id"),
            "type": "sql",
            "source": {"kind": "database", "label": "MySQL 财报结构化数据"},
            "sql": str(event.get("sql") or ""),
            "status": str(event.get("status") or ""),
            "row_count": event.get("row_count"),
            "columns": list(event.get("columns") or []),
            "rows": list(event.get("rows") or [])[:50],
        })
    for index, reference in enumerate(references, start=1):
        evidence.append({
            "id": reference.get("chunk_id") or f"reference-{index}",
            **{key: reference.get(key) for key in ("document_id", "chunk_id", "document_version", "page_start", "section_title", "stock_code", "report_year", "report_period")},
            "type": "reference",
            "source": {
                "kind": "document",
                "title": reference.get("source_title"),
                "path": reference.get("paper_path"),
            },
            "text": str(reference.get("text") or ""),
            "score": reference.get("score"),
            "paper_image": reference.get("paper_image"),
        })
    for index, chart in enumerate(charts, start=1):
        evidence.append({
            "id": f"chart-{index}",
            "type": "chart",
            "source": dict(chart.get("data_source") or {}),
            "chart_data": dict(chart),
        })
    return evidence
