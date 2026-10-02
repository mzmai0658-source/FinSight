"""作品说明：只读审计财务问答发布链路，联合材料清单、追加式提取记录、详情、现用快照与历史数据库；仅执行 SELECT。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TABLES = (
    "core_performance_indicators_sheet",
    "balance_sheet",
    "income_sheet",
    "cash_flow_sheet",
)
IDENTITY = ("stock_code", "report_year", "report_period")
META_COLUMNS = {
    "serial_number", "stock_code", "stock_abbr", "report_year", "report_period",
    "created_at", "updated_at",
}
FINAL_JOURNAL_STATUSES = {"existing", "published", "extracted", "review_required", "failed"}
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<paren>\()?\s*(?P<sign>[-+\u2212]?)\s*"
    r"(?P<number>(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?)\s*%?\s*(?P<close>\))?"
)


def _identity(row: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(row.get("stock_code") or "").zfill(6),
        int(row.get("report_year") or 0),
        str(row.get("report_period") or ""),
    )


def _identity_dict(identity: tuple[str, int, str]) -> dict[str, Any]:
    return dict(zip(IDENTITY, identity))


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, Path)):
        return str(value)
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=_json_default)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_journal(path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], int]:
    """作品说明：读取可容忍中途写入的 JSONL 日志，每个文件摘要保留最后记录。"""
    if not path.exists():
        return [], {}, 0
    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    ignored_tail = 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            if index != len(lines) - 1:
                raise
            ignored_tail += 1
            continue
        if not isinstance(record, dict):
            raise ValueError(f"Journal line {index + 1} is not an object")
        records.append(record)
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        source_hash = str(record.get("source_sha256") or "")
        if source_hash:
            latest[source_hash] = record
    return records, latest, ignored_tail


def _payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {}
    return dict(value)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    negative_parentheses = text.startswith("(") and text.endswith(")")
    text = text.strip("() ").replace(",", "").replace("，", "").replace("−", "-")
    text = text.removesuffix("%").strip()
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return -number if negative_parentheses and number > 0 else number


def _same_number(left: Any, right: Any, *, reference: Any = None) -> bool:
    a, b = _to_decimal(left), _to_decimal(right)
    if a is None or b is None:
        return left == right
    tolerance = Decimal("0.00000001")
    ref = reference if isinstance(reference, Decimal) else None
    if ref is not None:
        tolerance = max(tolerance, Decimal(1).scaleb(ref.as_tuple().exponent) / 2)
    return abs(a - b) <= tolerance


def raw_value_appears(raw_value: Any, text: str) -> bool:
    """作品说明：判断来源值是否实际出现在 OCR 或 PDF 页文本中。"""
    if raw_value is None:
        return False
    expected = _to_decimal(raw_value)
    normalized = unicodedata.normalize("NFKC", text or "")
    if expected is not None:
        for match in NUMBER_RE.finditer(normalized):
            number = _to_decimal(
                ("(" if match.group("paren") else "")
                + (match.group("sign") or "")
                + match.group("number")
                + (")" if match.group("close") else "")
            )
            if number is not None and number == expected:
                return True
        return False
    needle = re.sub(r"[\s,，]", "", unicodedata.normalize("NFKC", str(raw_value)))
    haystack = re.sub(r"[\s,，]", "", normalized)
    return bool(needle) and needle in haystack


class PreparedPages:
    """作品说明：按需读取已准备页快照，并检查内容摘要。"""

    def __init__(self, directory: Path):
        self.directory = directory
        self._cache: dict[str, tuple[list[str], dict[str, Any]]] = {}

    def load(self, manifest_row: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        source_key = str(manifest_row.get("source_key") or "")
        if source_key in self._cache:
            return self._cache[source_key]
        path = self.directory / f"{source_key}.json.gz"
        if not source_key or not path.is_file():
            raise FileNotFoundError(f"Prepared page snapshot missing: {path}")
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        pages = payload.get("pages") or []
        if not isinstance(pages, list) or any(not isinstance(page, str) for page in pages):
            raise ValueError(f"Prepared pages have an invalid shape: {path}")
        metadata = {key: value for key, value in payload.items() if key != "pages"}
        self._cache[source_key] = (pages, metadata)
        return pages, metadata


def _page_text(pages: list[str], start: Any, end: Any) -> str | None:
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    if start < 1 or end < start or end > len(pages):
        return None
    return "\n".join(pages[start - 1:end])


def _formula_value(data: dict[str, Any], field: str) -> tuple[float | None, str | None, list[str]]:
    """作品说明：重新计算已知派生字段，不将其冒充 PDF 字面披露。"""
    def number(name: str) -> float | None:
        value = _to_decimal(data.get(name))
        return float(value) if value is not None else None

    cash_ratios = {
        "operating_cf_ratio_of_net_cf": "operating_cf_net_amount",
        "investing_cf_ratio_of_net_cf": "investing_cf_net_amount",
        "financing_cf_ratio_of_net_cf": "financing_cf_net_amount",
    }
    if field in cash_ratios:
        component, net_cash = number(cash_ratios[field]), number("net_cash_flow")
        if component is not None and net_cash not in (None, 0):
            # 作品说明：最终净现金流以元存储，报表组成项目采用万元。
            denominators = [net_cash, net_cash / 10000]
            target = number(field)
            for denominator in denominators:
                calculated = component / denominator * 100
                if target is not None and math.isclose(calculated, target, rel_tol=1e-8, abs_tol=1e-4):
                    return calculated, "cash_flow_component/net_cash_flow*100", [cash_ratios[field], "net_cash_flow"]
        return None, "cash_flow_component/net_cash_flow*100", [cash_ratios[field], "net_cash_flow"]

    growth_bases = {
        "operating_revenue_yoy_growth": "total_operating_revenue",
        "net_profit_yoy_growth": "net_profit",
        "asset_total_assets_yoy_growth": "asset_total_assets",
        "liability_total_liabilities_yoy_growth": "liability_total_liabilities",
        "net_cash_flow_yoy_growth": "net_cash_flow",
    }
    if field in growth_bases:
        base = growth_bases[field]
        current, previous, target = number(base), number(f"_{base}_previous_value"), number(field)
        if current is not None and previous not in (None, 0) and target is not None:
            candidates = [current]
            if field == "net_cash_flow_yoy_growth":
                candidates.append(current / 10000)
            for candidate in candidates:
                calculated = (candidate - previous) / abs(previous) * 100
                if math.isclose(calculated, target, rel_tol=1e-8, abs_tol=1e-4):
                    return calculated, "percentage_change(current,previous)", [base, f"_{base}_previous_value"]
        return None, "percentage_change(current,previous)", [base, f"_{base}_previous_value"]

    if field in {"operating_revenue_qoq_growth", "net_profit_qoq_growth"}:
        target = number(field)
        for values in (data.get("_quarter_qoq_backfill") or {}).values():
            if isinstance(values, dict) and target is not None and _same_number(values.get(field), target):
                return target, "percentage_change(current_quarter,previous_quarter)", [
                    "current_quarter_value", "previous_quarter_value",
                ]
        return None, "percentage_change(current_quarter,previous_quarter)", [
            "current_quarter_value", "previous_quarter_value",
        ]

    formula_specs: dict[str, tuple[str, list[str], Callable[..., float]]] = {
        "operating_cf_per_share": (
            "operating_cf_net_amount*10000/share_capital",
            ["operating_cf_net_amount", "share_capital"],
            lambda operating_cf, shares: operating_cf * 10000 / shares,
        ),
        "net_asset_per_share": (
            "equity_parent_attributable*10000/share_capital",
            ["equity_parent_attributable", "share_capital"],
            lambda equity, shares: equity * 10000 / shares,
        ),
        "asset_liability_ratio": (
            "liability_total_liabilities/asset_total_assets*100",
            ["liability_total_liabilities", "asset_total_assets"],
            lambda liabilities, assets: liabilities / abs(assets) * 100,
        ),
        "net_profit_margin": (
            "net_profit/total_operating_revenue*100",
            ["net_profit", "total_operating_revenue"],
            lambda profit, revenue: profit / revenue * 100,
        ),
        "gross_profit_margin": (
            "(total_operating_revenue-cost)/total_operating_revenue*100",
            ["total_operating_revenue", "operating_expense_cost_of_sales"],
            lambda revenue, cost: (revenue - cost) / revenue * 100,
        ),
    }
    if field in formula_specs:
        formula, inputs, callback = formula_specs[field]
        values = [number(name) for name in inputs]
        target = number(field)
        if target is not None and all(value is not None for value in values):
            try:
                calculated = callback(*values)
            except ZeroDivisionError:
                calculated = None
            if calculated is not None and math.isclose(calculated, target, rel_tol=1e-8, abs_tol=1e-4):
                return calculated, formula, inputs
        return None, formula, inputs
    return None, None, []


def _legacy_semantics(
    field: str,
    source: dict[str, Any],
    data: dict[str, Any],
    declared_derived: set[str],
) -> tuple[str, dict[str, Any]]:
    """作品说明：只对当前提取代码实际执行的计算推导语义。"""
    explicit = str(source.get("value_semantics") or "")
    if explicit:
        return explicit, {
            "derivation": source.get("derivation"),
            "derived_from": list(source.get("derived_from") or []),
            "legacy_inferred": False,
        }
    if field == "liability_advance_from_customers" and _to_decimal(data.get(field)) == 0:
        return "defaulted", {
            "derivation": "missing_source_value_defaulted_to_zero",
            "derived_from": [],
            "legacy_inferred": True,
        }
    calculated, formula, inputs = _formula_value(data, field)
    table_type = str(source.get("table_type") or "")
    extraction_mode = str(source.get("extraction_mode") or "")
    known_rule_calculation = (
        field in {
            "operating_cf_ratio_of_net_cf", "investing_cf_ratio_of_net_cf",
            "financing_cf_ratio_of_net_cf", "operating_revenue_qoq_growth",
            "net_profit_qoq_growth",
        }
        or (
            field.endswith("_yoy_growth")
            and table_type in {"primary_balance", "primary_income", "primary_cashflow"}
        )
        or field in declared_derived
    )
    if known_rule_calculation and (calculated is not None or field in declared_derived) and extraction_mode.startswith("rules"):
        return "derived", {
            "derivation": formula or "post_process_calculation",
            "derived_from": inputs,
            "legacy_inferred": True,
            "formula_verified": calculated is not None,
        }
    return "direct", {"legacy_inferred": False}


def _audit_sources(
    sources: dict[str, Any],
    fields: Iterable[str],
    pages: list[str],
    page_count: int,
    *,
    data: dict[str, Any] | None = None,
    declared_derived: Iterable[str] = (),
) -> dict[str, Any]:
    data = data or {}
    declared_derived_set = set(declared_derived)
    located = []
    unlocated = []
    invalid_pages = []
    raw_missing = []
    raw_uncheckable = []
    derived_fields = []
    defaulted_fields = []
    derived_formula_verified = []
    derived_formula_unverified = []
    scope_counts: Counter[str] = Counter()
    normalized_mismatches = []
    for field in sorted(set(fields)):
        source = sources.get(field)
        if not isinstance(source, dict):
            unlocated.append(field)
            continue
        start = source.get("page_start")
        end = source.get("page_end") or start
        normalized = source.get("normalized_value")
        if (
            not isinstance(start, int) or not isinstance(end, int)
            or start < 1 or end < start or end > page_count or normalized is None
        ):
            invalid_pages.append({"field": field, "page_start": start, "page_end": end})
            unlocated.append(field)
            continue
        located.append(field)
        scope = str(source.get("statement_scope") or "missing")
        scope_counts[scope] += 1
        raw = source.get("raw_value")
        page_text = _page_text(pages, start, end)
        raw_found = raw is not None and page_text is not None and raw_value_appears(raw, page_text)
        semantics, semantic_detail = _legacy_semantics(field, source, data, declared_derived_set)
        # 作品说明：旧详情缺少数值语义时，只在声明原值未出现时推断计算；可见原始字面值仍按直接披露处理。
        if not source.get("value_semantics") and raw_found:
            semantics, semantic_detail = "direct", {"legacy_inferred": False}
        if semantics == "derived":
            item = {"field": field, **semantic_detail}
            derived_fields.append(item)
            if semantic_detail.get("formula_verified") is False:
                derived_formula_unverified.append(item)
            elif semantic_detail.get("formula_verified") is True:
                derived_formula_verified.append(item)
            continue
        if semantics == "defaulted":
            defaulted_fields.append({"field": field, **semantic_detail})
            continue
        if raw is None or page_text is None:
            raw_uncheckable.append({"field": field, "page_start": start, "page_end": end})
        elif not raw_found:
            raw_missing.append({
                "field": field, "page_start": start, "page_end": end,
                "raw_value": raw,
            })
    return {
        "fields": len(set(fields)),
        "located": len(located),
        "unlocated": len(unlocated),
        "located_fields": located,
        "unlocated_fields": unlocated,
        "invalid_pages": invalid_pages,
        "raw_checked": len(located) - len(raw_uncheckable) - len(derived_fields) - len(defaulted_fields),
        "raw_found": len(located) - len(raw_uncheckable) - len(raw_missing) - len(derived_fields) - len(defaulted_fields),
        "raw_missing": raw_missing,
        "raw_uncheckable": raw_uncheckable,
        "derived": derived_fields,
        "defaulted": defaulted_fields,
        "derived_formula_verified": derived_formula_verified,
        "derived_formula_unverified": derived_formula_unverified,
        "statement_scope": dict(sorted(scope_counts.items())),
        "normalized_mismatches": normalized_mismatches,
    }


def audit_extraction_detail(
    result: dict[str, Any],
    manifest_row: dict[str, Any],
    pages: list[str],
    prepared_metadata: dict[str, Any],
) -> dict[str, Any]:
    issues: list[str] = []
    if result.get("status") != "success":
        issues.append("detail_not_success")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if _identity(data) != _identity(manifest_row):
        issues.append("detail_identity_mismatch")
    if data.get("source_is_summary"):
        issues.append("detail_classified_as_summary")
    document = data.get("_source_document") or {}
    if document.get("source_sha256") != manifest_row.get("sha256"):
        issues.append("detail_source_hash_mismatch")
    if document.get("source_path") != manifest_row.get("source_path"):
        issues.append("detail_source_path_mismatch")
    if document.get("page_count") != manifest_row.get("page_count"):
        issues.append("detail_page_count_mismatch")
    if prepared_metadata.get("sha256") != manifest_row.get("sha256"):
        issues.append("prepared_source_hash_mismatch")
    if prepared_metadata.get("ocr_sha256") != manifest_row.get("ocr_sha256"):
        issues.append("prepared_ocr_hash_mismatch")
    if len(pages) != manifest_row.get("text_page_count"):
        issues.append("prepared_text_page_count_mismatch")
    accepted = set(data.get("_accepted_fields") or [])
    accepted.discard("source_file")
    sources = data.get("_field_sources") if isinstance(data.get("_field_sources"), dict) else {}
    source_audit = _audit_sources(
        sources,
        accepted,
        pages,
        int(manifest_row.get("page_count") or 0),
        data=data,
        declared_derived=data.get("_derived_fields") or [],
    )
    for field in source_audit["located_fields"]:
        source = sources[field]
        if field in data and not _same_number(data.get(field), source.get("normalized_value")):
            source_audit["normalized_mismatches"].append(field)
    if source_audit["invalid_pages"]:
        issues.append("detail_invalid_page_locator")
    if source_audit["raw_missing"]:
        issues.append("detail_raw_value_not_on_source_page")
    if source_audit["normalized_mismatches"]:
        issues.append("detail_normalized_value_mismatch")
    warnings = list(data.get("_pre_save_review_warnings") or [])
    blockers = list(data.get("_pre_save_review_blockers") or [])
    missing = list(data.get("_missing_required_fields") or [])
    return {
        "review_status": data.get("_pre_save_review_status"),
        "warnings": warnings,
        "blockers": blockers,
        "missing_required_fields": missing,
        "accepted_fields": len(accepted),
        "source_fields_total": len(sources),
        "sources": source_audit,
        "issues": sorted(set(issues)),
    }


def load_database_snapshot(engine, *, require_provenance: bool = True) -> dict[str, Any]:
    """作品说明：只通过 SELECT 加载四张现用财务表及来源记录。"""
    from sqlalchemy import MetaData, Table, inspect, select

    available = set(inspect(engine).get_table_names())
    required = (*TABLES, "financial_report_provenance") if require_provenance else TABLES
    missing_tables = [name for name in required if name not in available]
    if missing_tables:
        raise ValueError(f"Financial audit tables missing: {missing_tables}")
    result: dict[str, Any] = {"tables": {}, "provenance": {}, "duplicates": {}}
    metadata = MetaData()
    with engine.connect() as connection:
        for name in TABLES:
            table = Table(name, metadata, autoload_with=engine)
            grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
            for row in connection.execute(select(table)).mappings():
                item = dict(row)
                grouped[_identity(item)].append(item)
            result["tables"][name] = {identity: rows[0] for identity, rows in grouped.items()}
            result["duplicates"][name] = {
                identity: len(rows) for identity, rows in grouped.items() if len(rows) != 1
            }
        if "financial_report_provenance" in available:
            provenance = Table("financial_report_provenance", metadata, autoload_with=engine)
            grouped_provenance: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
            for row in connection.execute(select(provenance)).mappings():
                item = dict(row)
                grouped_provenance[_identity(item)].append(_payload(item.get("payload")))
            result["provenance"] = {
                identity: rows[0] for identity, rows in grouped_provenance.items()
            }
            result["duplicates"]["financial_report_provenance"] = {
                identity: len(rows) for identity, rows in grouped_provenance.items() if len(rows) != 1
            }
    result["row_counts"] = {name: len(rows) for name, rows in result["tables"].items()}
    result["row_counts"]["financial_report_provenance"] = len(result["provenance"])
    return result


def audit_database_identity(
    snapshot: dict[str, Any],
    manifest_row: dict[str, Any],
    pages: list[str],
) -> dict[str, Any]:
    identity = _identity(manifest_row)
    rows = {name: snapshot["tables"][name].get(identity) for name in TABLES}
    present = [name for name, row in rows.items() if row is not None]
    payload = snapshot["provenance"].get(identity)
    issues: list[str] = []
    if present and len(present) != len(TABLES):
        issues.append("database_partial_four_table_snapshot")
    if present and payload is None:
        issues.append("database_rows_without_provenance")
    if payload is not None and not present:
        issues.append("database_provenance_without_rows")
    result: dict[str, Any] = {
        "table_presence": present,
        "has_provenance": payload is not None,
        "facts": 0,
        "source_located": 0,
        "source_unlocated": 0,
        "statement_scope": {},
        "missing_facts": [],
        "value_mismatches": [],
        "invalid_page_locators": [],
        "raw_checked": 0,
        "raw_found": 0,
        "raw_missing": [],
        "issues": issues,
    }
    if payload is None:
        return result
    if _identity(payload) != identity:
        issues.append("database_provenance_identity_mismatch")
    document = payload.get("document") or {}
    if document.get("source_sha256") != manifest_row.get("sha256"):
        issues.append("database_source_hash_mismatch")
    if document.get("source_path") != manifest_row.get("source_path"):
        # 作品说明：复用快照可能指向旧上传路径；字节相同构成可审计别名，而非证据冲突。
        issues.append("database_source_path_alias")
    if document.get("page_count") != manifest_row.get("page_count"):
        issues.append("database_page_count_mismatch")
    facts = payload.get("facts") or []
    result["facts"] = len(facts)
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    scopes: Counter[str] = Counter()
    for fact in facts:
        table, field = str(fact.get("table") or ""), str(fact.get("field") or "")
        by_key[(table, field)] = fact
        status = fact.get("status")
        if status != "source_located":
            result["source_unlocated"] += 1
            continue
        result["source_located"] += 1
        scope = str(fact.get("statement_scope") or "missing")
        scopes[scope] += 1
        start, end = fact.get("page_start"), fact.get("page_end")
        if (
            fact.get("source_sha256") != manifest_row.get("sha256")
            or not isinstance(start, int) or not isinstance(end, int)
            or start < 1 or end < start or end > int(manifest_row.get("page_count") or 0)
        ):
            result["invalid_page_locators"].append({"table": table, "field": field})
            continue
        raw = fact.get("raw_value")
        page_text = _page_text(pages, start, end)
        if raw is not None and page_text is not None:
            result["raw_checked"] += 1
            if raw_value_appears(raw, page_text):
                result["raw_found"] += 1
            else:
                result["raw_missing"].append({
                    "table": table, "field": field, "page_start": start,
                    "page_end": end, "raw_value": raw,
                })
    result["statement_scope"] = dict(sorted(scopes.items()))
    for table, row in rows.items():
        if row is None:
            continue
        for field, value in row.items():
            if field in META_COLUMNS or value is None:
                continue
            fact = by_key.get((table, field))
            if fact is None:
                result["missing_facts"].append({"table": table, "field": field})
            elif not _same_number(value, fact.get("value"), reference=value):
                result["value_mismatches"].append({
                    "table": table, "field": field,
                    "database_value": value, "fact_value": fact.get("value"),
                })
    if result["invalid_page_locators"]:
        issues.append("database_invalid_page_locator")
    if result["raw_missing"]:
        issues.append("database_raw_value_not_on_source_page")
    if result["missing_facts"]:
        issues.append("database_missing_fact")
    if result["value_mismatches"]:
        issues.append("database_fact_value_mismatch")
    result["issues"] = sorted(set(issues))
    return result


def compare_legacy(
    legacy: dict[str, Any],
    target: dict[str, Any],
    details: dict[tuple[str, int, str], dict[str, Any]],
) -> dict[str, Any]:
    """作品说明：将历史行与新提取详情、目标行独立对照。"""
    legacy_identities = set().union(*(set(legacy["tables"][name]) for name in TABLES))
    target_identities = set().union(*(set(target["tables"][name]) for name in TABLES))

    def compare(mode: str) -> dict[str, Any]:
        summary: Counter[str] = Counter()
        per_table: dict[str, Counter[str]] = {name: Counter() for name in TABLES}
        changes: list[dict[str, Any]] = []
        other_ids = set(details) if mode == "fresh_detail" else target_identities
        overlaps = sorted(legacy_identities & other_ids)
        for identity in overlaps:
            for table in TABLES:
                old = legacy["tables"][table].get(identity)
                if old is None:
                    continue
                if mode == "fresh_detail":
                    new = details[identity]
                else:
                    new = target["tables"][table].get(identity)
                    if new is None:
                        per_table[table]["missing_rows"] += 1
                        summary["missing_rows"] += 1
                        continue
                per_table[table]["rows"] += 1
                for field, legacy_value in old.items():
                    if field in META_COLUMNS:
                        continue
                    fresh_value = new.get(field)
                    if legacy_value is None and fresh_value is None:
                        continue
                    summary["compared_fields"] += 1
                    per_table[table]["compared_fields"] += 1
                    if legacy_value is not None and fresh_value is None:
                        kind = "fresh_missing"
                    elif legacy_value is None and fresh_value is not None:
                        kind = "fresh_added"
                    elif _same_number(legacy_value, fresh_value, reference=legacy_value):
                        summary["matching_fields"] += 1
                        per_table[table]["matching_fields"] += 1
                        continue
                    else:
                        kind = "value_changed"
                    summary[kind] += 1
                    per_table[table][kind] += 1
                    changes.append({
                        **_identity_dict(identity), "table": table, "field": field,
                        "legacy_value": legacy_value, "fresh_value": fresh_value,
                        "difference": kind,
                    })
        return {
            "overlap_identities": len(overlaps),
            **dict(summary),
            "per_table": {name: dict(counts) for name, counts in per_table.items()},
            "differences": changes,
        }

    return {
        "legacy_identities": len(legacy_identities),
        "legacy_row_counts": legacy.get("row_counts", {}),
        "legacy_duplicates": {
            name: [list(identity) for identity in values]
            for name, values in legacy.get("duplicates", {}).items() if values
        },
        "fresh_detail_comparison": compare("fresh_detail"),
        "serving_database_comparison": compare("serving_database"),
    }


def _verify_pdf(manifest_row: dict[str, Any]) -> dict[str, Any]:
    path = (ROOT / str(manifest_row.get("source_path") or "")).resolve()
    allowed = (ROOT / "data_root" / "financial_reports").resolve()
    if not path.is_relative_to(allowed) or path.suffix.lower() != ".pdf":
        return {"exists": False, "hash_matches": False, "error": "outside_financial_report_root"}
    if not path.is_file():
        return {"exists": False, "hash_matches": False, "error": "missing_pdf"}
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"exists": True, "sha256": digest, "hash_matches": digest == manifest_row.get("sha256")}


def build_audit(
    manifest: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    journal_records: list[dict[str, Any]],
    latest_journal: dict[str, dict[str, Any]],
    details_by_hash: dict[str, dict[str, Any]],
    target: dict[str, Any],
    legacy: dict[str, Any],
    pages_store: PreparedPages,
) -> dict[str, Any]:
    selected_by_identity = {_identity(row): row for row in selected}
    selected_hashes = {str(row.get("sha256")) for row in selected}
    target_identities = set().union(*(set(target["tables"][name]) for name in TABLES))
    target_identities |= set(target["provenance"])
    records = []
    counters: Counter[str] = Counter()
    detail_data_by_identity: dict[tuple[str, int, str], dict[str, Any]] = {}
    aggregate_scope: Counter[str] = Counter()
    aggregate_db_scope: Counter[str] = Counter()
    warning_types: Counter[str] = Counter()
    missing_fields: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    review_statuses: Counter[str] = Counter()
    total_detail_located = total_detail_unlocated = total_detail_invalid_pages = 0
    total_db_located = total_db_unlocated = total_db_invalid_pages = 0
    total_detail_raw_checked = total_detail_raw_found = 0
    total_db_raw_checked = total_db_raw_found = 0

    for row in selected:
        identity = _identity(row)
        source_hash = str(row.get("sha256") or "")
        journal = latest_journal.get(source_hash)
        detail = details_by_hash.get(source_hash)
        has_database = identity in target_identities
        needs_pages = detail is not None or has_database
        pages: list[str] = []
        prepared_metadata: dict[str, Any] = {}
        issues: list[str] = []
        if needs_pages:
            try:
                pages, prepared_metadata = pages_store.load(row)
            except Exception as error:
                issues.append("prepared_pages_unavailable")
                prepared_metadata = {"error": f"{type(error).__name__}: {error}"}
        detail_audit = None
        if detail is not None:
            detail_audit = audit_extraction_detail(detail, row, pages, prepared_metadata)
            issues.extend(detail_audit["issues"])
            detail_data_by_identity[identity] = detail.get("data") or {}
            total_detail_raw_checked += detail_audit["sources"]["raw_checked"]
            total_detail_raw_found += detail_audit["sources"]["raw_found"]
            total_detail_located += detail_audit["sources"]["located"]
            total_detail_unlocated += detail_audit["sources"]["unlocated"]
            total_detail_invalid_pages += len(detail_audit["sources"]["invalid_pages"])
            aggregate_scope.update(detail_audit["sources"]["statement_scope"])
            warning_types.update(detail_audit["warnings"])
            missing_fields.update(detail_audit["missing_required_fields"])
            review_statuses[str(detail_audit["review_status"] or "missing")] += 1
        database_audit = None
        if has_database:
            database_audit = audit_database_identity(target, row, pages)
            issues.extend(database_audit["issues"])
            total_db_raw_checked += database_audit["raw_checked"]
            total_db_raw_found += database_audit["raw_found"]
            total_db_located += database_audit["source_located"]
            total_db_unlocated += database_audit["source_unlocated"]
            total_db_invalid_pages += len(database_audit["invalid_page_locators"])
            aggregate_db_scope.update(database_audit["statement_scope"])
        pdf = None
        if needs_pages or journal is not None:
            pdf = _verify_pdf(row)
            if not pdf.get("hash_matches"):
                issues.append("pdf_hash_mismatch")
        journal_status = str(journal.get("status") or "") if journal else "pending"
        if journal:
            if _identity(journal) != identity:
                issues.append("journal_identity_mismatch")
            if journal_status not in FINAL_JOURNAL_STATUSES:
                issues.append("journal_unknown_status")
            if journal_status in {"extracted", "published", "review_required"} and detail is None:
                issues.append("journal_detail_missing")
            if journal_status == "failed":
                issues.append("extraction_failed")
        if detail is not None and journal is None:
            issues.append("orphan_extraction_detail")
        if journal_status in {"existing", "published"} and not has_database:
            issues.append("journal_published_but_database_missing")
        if journal_status == "extracted" and has_database:
            issues.append("extract_only_record_already_in_database")

        severe = any(
            token in issue
            for issue in issues
            for token in ("mismatch", "invalid", "missing_pdf", "hash", "raw_value", "failed", "unavailable")
        )
        has_review_findings = bool(
            (detail_audit and (
                detail_audit["warnings"] or detail_audit["blockers"]
                or detail_audit["missing_required_fields"]
                or detail_audit["sources"]["unlocated"]
                or detail_audit["sources"]["statement_scope"].get("unknown", 0)
                or detail_audit["sources"]["statement_scope"].get("missing", 0)
            ))
            or (database_audit and (
                database_audit["source_unlocated"]
                or database_audit["statement_scope"].get("unknown", 0)
                or database_audit["statement_scope"].get("missing", 0)
            ))
        )
        if journal_status == "pending" and not has_database and detail is None:
            audit_status = "pending"
        elif severe:
            audit_status = "blocked"
        elif issues or has_review_findings:
            audit_status = "warning"
        else:
            audit_status = "pass"
        counters[audit_status] += 1
        counters[f"journal_{journal_status}"] += 1
        issue_counts.update(set(issues))
        records.append({
            **_identity_dict(identity),
            "company": row.get("company"),
            "source_path": row.get("source_path"),
            "source_sha256": source_hash,
            "journal_status": journal_status,
            "journal_error": journal.get("error") if journal else None,
            "pdf": pdf,
            "extraction": detail_audit,
            "database": database_audit,
            "audit_status": audit_status,
            "issues": sorted(set(issues)),
        })

    orphan_journal_hashes = sorted(set(latest_journal) - selected_hashes)
    orphan_detail_hashes = sorted(set(details_by_hash) - selected_hashes)
    orphan_database = sorted(target_identities - set(selected_by_identity))
    journal_transitions: Counter[str] = Counter()
    last_status_by_hash: dict[str, str] = {}
    for record in journal_records:
        source_hash = str(record.get("source_sha256") or "")
        status = str(record.get("status") or "unknown")
        if source_hash in last_status_by_hash:
            journal_transitions[f"{last_status_by_hash[source_hash]}->{status}"] += 1
        last_status_by_hash[source_hash] = status

    legacy_audit = compare_legacy(legacy, target, detail_data_by_identity)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only",
        "summary": {
            "manifest_materials": len(manifest),
            "planned_documents": len(selected),
            "excluded_documents": len(excluded),
            "journal_lines": len(journal_records),
            "journal_documents": len(latest_journal),
            "extraction_details": len(details_by_hash),
            "database_identities": len(target_identities),
            "audit_statuses": {key: value for key, value in sorted(counters.items()) if not key.startswith("journal_")},
            "journal_statuses": {key.removeprefix("journal_"): value for key, value in sorted(counters.items()) if key.startswith("journal_")},
            "detail_raw_value": {
                "checked": total_detail_raw_checked,
                "found": total_detail_raw_found,
                "missing": total_detail_raw_checked - total_detail_raw_found,
            },
            "detail_source_location": {
                "located": total_detail_located,
                "unlocated": total_detail_unlocated,
                "invalid_page_locators": total_detail_invalid_pages,
            },
            "database_raw_value": {
                "checked": total_db_raw_checked,
                "found": total_db_raw_found,
                "missing": total_db_raw_checked - total_db_raw_found,
            },
            "database_source_location": {
                "located": total_db_located,
                "unlocated": total_db_unlocated,
                "invalid_page_locators": total_db_invalid_pages,
            },
            "detail_statement_scope": dict(sorted(aggregate_scope.items())),
            "database_statement_scope": dict(sorted(aggregate_db_scope.items())),
            "review_statuses": dict(sorted(review_statuses.items())),
            "review_warning_types": dict(sorted(warning_types.items())),
            "missing_required_fields": dict(sorted(missing_fields.items())),
            "issue_counts": dict(sorted(issue_counts.items())),
        },
        "journal_audit": {
            "duplicate_lines": len(journal_records) - len(latest_journal),
            "status_transitions": dict(sorted(journal_transitions.items())),
            "orphan_hashes": orphan_journal_hashes,
        },
        "selection_exclusions": excluded,
        "orphan_extraction_detail_hashes": orphan_detail_hashes,
        "orphan_database_identities": [list(identity) for identity in orphan_database],
        "database_row_counts": target.get("row_counts", {}),
        "database_duplicates": {
            name: {"|".join(map(str, identity)): count for identity, count in duplicates.items()}
            for name, duplicates in target.get("duplicates", {}).items() if duplicates
        },
        "legacy_121_audit": legacy_audit,
        "records": records,
    }


def _load_details(directory: Path) -> dict[str, dict[str, Any]]:
    result = {}
    if not directory.exists():
        return result
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        result[path.stem] = payload
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--manifest", default="data/runtime/real_validation/financial_library_1417/manifest.json")
    parser.add_argument("--rollout-dir", default="data/runtime/financial_qa_rollout")
    parser.add_argument("--legacy-database", default="financial_report")
    parser.add_argument("--output", default="data/runtime/financial_qa_rollout/audit.json")
    args = parser.parse_args()

    from dotenv import dotenv_values
    from sqlalchemy import create_engine
    from config.db_config import get_db_config
    from scripts.populate_financial_qa import select_documents

    os.environ.update({key: value for key, value in dotenv_values(args.env_file).items() if value is not None})
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    config = get_db_config()
    if config.database == args.legacy_database:
        raise ValueError("Serving and legacy databases must differ")
    manifest_path = (ROOT / args.manifest).resolve()
    rollout_dir = (ROOT / args.rollout_dir).resolve()
    output_path = (ROOT / args.output).resolve()
    for path in (manifest_path, rollout_dir, output_path):
        if not path.is_relative_to(ROOT):
            raise ValueError("Audit inputs and output must stay inside the project workspace")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected, excluded = select_documents(manifest)
    journal, latest, ignored_tail = read_journal(rollout_dir / "extraction.jsonl")
    details = _load_details(rollout_dir / "extracted")
    target_engine = create_engine(config.connection_string, pool_pre_ping=True)
    legacy_engine = create_engine(replace(config, database=args.legacy_database).connection_string, pool_pre_ping=True)
    try:
        target = load_database_snapshot(target_engine)
        legacy = load_database_snapshot(legacy_engine, require_provenance=False)
    finally:
        target_engine.dispose()
        legacy_engine.dispose()
    pages_store = PreparedPages(
        ROOT / "data/runtime/real_validation/financial_library_1417/prepared"
    )
    report = build_audit(
        manifest, selected, excluded, journal, latest, details, target, legacy, pages_store
    )
    report["serving_database"] = config.database
    report["legacy_database"] = args.legacy_database
    report["journal_audit"]["ignored_incomplete_tail_lines"] = ignored_tail
    _atomic_write_json(output_path, report)
    print(json.dumps({
        "output": str(output_path.relative_to(ROOT)),
        **report["summary"],
        "legacy_121": {
            "identities": report["legacy_121_audit"]["legacy_identities"],
            "fresh_overlap": report["legacy_121_audit"]["fresh_detail_comparison"]["overlap_identities"],
            "target_overlap": report["legacy_121_audit"]["serving_database_comparison"]["overlap_identities"],
        },
    }, ensure_ascii=False, default=_json_default))


if __name__ == "__main__":
    main()
