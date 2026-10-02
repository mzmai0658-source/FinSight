"""作品说明：复用前只读审计历史财务行。公司及报告匹配只能帮助选择原件，不能证明字段来源；须重新提取并核对数值与页定位后才能发布。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TABLES = (
    "core_performance_indicators_sheet",
    "balance_sheet",
    "income_sheet",
    "cash_flow_sheet",
)
IDENTITY = ("stock_code", "report_year", "report_period")
IGNORED_COLUMNS = {"serial_number", "created_at", "updated_at"}


def _identity(row: dict[str, Any]) -> tuple[str, int, str]:
    return str(row["stock_code"]), int(row["report_year"]), str(row["report_period"])


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def _canonical_rows(rows: dict[str, list[dict[str, Any]]]) -> str:
    payload = {
        table: [
            {key: _json_value(value) for key, value in sorted(row.items())}
            for row in sorted(items, key=_identity)
        ]
        for table, items in sorted(rows.items())
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _load_rows(engine) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    from sqlalchemy import MetaData, Table, inspect, select

    inspector = inspect(engine)
    available = set(inspector.get_table_names())
    missing = [table for table in TABLES if table not in available]
    if missing:
        raise ValueError(f"Financial tables missing: {missing}")
    metadata = MetaData()
    reflected = {name: Table(name, metadata, autoload_with=engine) for name in TABLES}
    with engine.connect() as connection:
        rows = {
            name: [dict(row) for row in connection.execute(select(table)).mappings()]
            for name, table in reflected.items()
        }
    index: dict[str, dict[tuple[str, int, str], dict[str, Any]]] = {}
    duplicates: dict[str, list[list[Any]]] = {}
    for table, items in rows.items():
        grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
        for row in items:
            grouped[_identity(row)].append(row)
        duplicates[table] = [list(key) for key, values in grouped.items() if len(values) != 1]
        index[table] = {key: values[0] for key, values in grouped.items() if len(values) == 1}
    identity_sets = {table: set(items) for table, items in index.items()}
    union = set().union(*identity_sets.values())
    intersection = set.intersection(*identity_sets.values())
    audit = {
        "row_counts": {table: len(items) for table, items in rows.items()},
        "identity_counts": {table: len(items) for table, items in index.items()},
        "duplicates": duplicates,
        "identity_union": len(union),
        "identity_intersection": len(intersection),
        "identity_sets_equal": len(union) == len(intersection),
    }
    if any(duplicates.values()) or union != intersection:
        raise ValueError(f"Legacy financial identity audit failed: {audit}")
    return audit, rows


def _field_counts(rows: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, int]]:
    result = {}
    for table, items in rows.items():
        fields = [name for name in items[0] if name not in IGNORED_COLUMNS | set(IDENTITY) | {"stock_abbr"}]
        result[table] = {
            "fields": len(fields),
            "values": len(fields) * len(items),
            "non_null_values": sum(row.get(field) is not None for row in items for field in fields),
        }
    return result


def _target_state(engine) -> dict[str, Any]:
    from sqlalchemy import MetaData, Table, inspect, select

    available = set(inspect(engine).get_table_names())
    metadata = MetaData()
    state: dict[str, Any] = {"tables": {}, "provenance": {}, "materials": {}}
    with engine.connect() as connection:
        for name in TABLES:
            if name not in available:
                state["tables"][name] = {}
                continue
            table = Table(name, metadata, autoload_with=engine)
            values = [dict(row) for row in connection.execute(select(table)).mappings()]
            state["tables"][name] = {_identity(row): row for row in values}
        if "financial_report_provenance" in available:
            table = Table("financial_report_provenance", metadata, autoload_with=engine)
            for row in connection.execute(select(table)).mappings():
                item = dict(row)
                payload = item.get("payload")
                if isinstance(payload, str):
                    payload = json.loads(payload)
                state["provenance"][_identity(item)] = payload
        if "financial_report_material" in available:
            table = Table("financial_report_material", metadata, autoload_with=engine)
            selected = select(
                table.c.stock_code,
                table.c.report_year,
                table.c.report_period,
                table.c.report_kind,
                table.c.sha256,
                table.c.source_path,
                table.c.page_count,
                table.c.text_page_count,
                table.c.content_state,
            )
            for row in connection.execute(selected).mappings():
                state["materials"].setdefault(_identity(dict(row)), []).append(dict(row))
    return state


def _material_candidates(manifest: list[dict[str, Any]]) -> dict[tuple[str, int, str], list[dict[str, Any]]]:
    result: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in manifest:
        if row.get("identity_status") != "identified" or row.get("report_kind") != "full":
            continue
        result[_identity(row)].append(row)
    return result


def _same_number(left: Any, right: Any) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-8)
    except (TypeError, ValueError):
        return left == right


def _compare_legacy_with_target(
    source_rows: dict[str, list[dict[str, Any]]],
    target_tables: dict[str, dict[tuple[str, int, str], dict[str, Any]]],
    identity: tuple[str, int, str],
) -> dict[str, Any]:
    source_index = {
        table: {_identity(row): row for row in rows}
        for table, rows in source_rows.items()
    }
    changed = []
    matching = 0
    compared = 0
    for table in TABLES:
        legacy = source_index[table][identity]
        current = target_tables[table].get(identity)
        if current is None:
            continue
        for field, old_value in legacy.items():
            if field in IGNORED_COLUMNS | set(IDENTITY) | {"stock_abbr"} or field not in current:
                continue
            new_value = current[field]
            compared += 1
            if (old_value is None) != (new_value is None) or (
                old_value is not None and not _same_number(old_value, new_value)
            ):
                changed.append(
                    {
                        "table": table,
                        "field": field,
                        "legacy_value": _json_value(old_value),
                        "fresh_value": _json_value(new_value),
                    }
                )
            else:
                matching += 1
    return {"compared_fields": compared, "matching_fields": matching, "changed_fields": changed}


def _audit_target_provenance(
    payload: dict[str, Any] | None,
    target_tables: dict[str, dict[tuple[str, int, str], dict[str, Any]]],
    identity: tuple[str, int, str],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "document_hash_matches": False,
        "facts": 0,
        "source_located": 0,
        "source_unlocated": 0,
        "source_unlocated_fields": [],
        "unknown_statement_scope": 0,
        "unknown_statement_scope_fields": [],
        "missing_facts": [],
        "value_mismatches": [],
        "invalid_locators": [],
    }
    if not payload:
        return result
    document = payload.get("document") or {}
    result["document_hash_matches"] = document.get("source_sha256") == candidate.get("sha256")
    facts = payload.get("facts") or []
    result["facts"] = len(facts)
    by_field = {(fact.get("table"), fact.get("field")): fact for fact in facts}
    page_count = candidate.get("page_count")
    for fact in facts:
        status = fact.get("status")
        if status == "source_located":
            result["source_located"] += 1
            page_start = fact.get("page_start")
            page_end = fact.get("page_end")
            if (
                fact.get("source_sha256") != candidate.get("sha256")
                or not isinstance(page_start, int)
                or not isinstance(page_end, int)
                or not 1 <= page_start <= page_end <= page_count
            ):
                result["invalid_locators"].append(
                    {"table": fact.get("table"), "field": fact.get("field")}
                )
            if fact.get("statement_scope") in {None, "", "unknown"}:
                result["unknown_statement_scope"] += 1
                result["unknown_statement_scope_fields"].append(
                    {"table": fact.get("table"), "field": fact.get("field")}
                )
        else:
            result["source_unlocated"] += 1
            result["source_unlocated_fields"].append(
                {"table": fact.get("table"), "field": fact.get("field")}
            )
    for table in TABLES:
        row = target_tables[table].get(identity)
        if row is None:
            continue
        for field, value in row.items():
            if field in IGNORED_COLUMNS | set(IDENTITY) | {"stock_abbr"} or value is None:
                continue
            fact = by_field.get((table, field))
            if fact is None:
                result["missing_facts"].append({"table": table, "field": field})
            elif not _same_number(value, fact.get("value")):
                result["value_mismatches"].append({"table": table, "field": field})
    return result


def _verify_pdf(candidate: dict[str, Any]) -> dict[str, Any]:
    from pypdf import PdfReader

    path = (ROOT / candidate["source_path"]).resolve()
    financial_root = (ROOT / "data_root" / "financial_reports").resolve()
    if not path.is_relative_to(financial_root) or path.suffix.lower() != ".pdf":
        raise ValueError(f"Candidate outside financial report root: {path}")
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    actual_pages = len(PdfReader(path).pages)
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": digest,
        "sha256_matches": digest == candidate.get("sha256"),
        "actual_pages": actual_pages,
        "page_count_matches": actual_pages == candidate.get("page_count"),
        "text_complete": candidate.get("content_state") == "ready"
        and candidate.get("text_page_count") == candidate.get("page_count"),
    }


def build_preview(source_engine, target_engine, manifest: list[dict[str, Any]]) -> dict[str, Any]:
    source_audit, source_rows = _load_rows(source_engine)
    before_digest = _canonical_rows(source_rows)
    core_by_identity = {_identity(row): row for row in source_rows[TABLES[0]]}
    identities = sorted(core_by_identity)
    candidates = _material_candidates(manifest)
    target = _target_state(target_engine)
    records = []
    counters = Counter()
    for identity in identities:
        full = candidates.get(identity, [])
        issues = []
        pdf = None
        if len(full) != 1:
            issues.append("missing_full_pdf" if not full else "ambiguous_full_pdf")
        else:
            pdf = _verify_pdf(full[0])
            if not pdf["sha256_matches"]:
                issues.append("pdf_hash_mismatch")
            if not pdf["page_count_matches"]:
                issues.append("pdf_page_count_mismatch")
            if not pdf["text_complete"]:
                issues.append("text_snapshot_incomplete")
            catalogue = [
                row for row in target["materials"].get(identity, [])
                if row.get("report_kind") == "full" and row.get("sha256") == full[0].get("sha256")
            ]
            if len(catalogue) != 1:
                issues.append("serving_catalogue_mismatch")
        target_tables = [table for table in TABLES if identity in target["tables"][table]]
        if target_tables and len(target_tables) != len(TABLES):
            issues.append("partial_target_identity")
        target_payload = target["provenance"].get(identity)
        if target_tables and target_payload is None:
            issues.append("target_rows_without_provenance")
        if target_payload is not None and not target_tables:
            issues.append("target_provenance_without_rows")
        provenance = None
        comparison = None
        if len(target_tables) == len(TABLES) and target_payload is not None and len(full) == 1:
            provenance = _audit_target_provenance(target_payload, target["tables"], identity, full[0])
            if not provenance["document_hash_matches"]:
                issues.append("target_source_hash_mismatch")
            if provenance["missing_facts"]:
                issues.append("target_fact_missing")
            if provenance["value_mismatches"]:
                issues.append("target_fact_value_mismatch")
            if provenance["invalid_locators"]:
                issues.append("target_locator_invalid")
            comparison = _compare_legacy_with_target(source_rows, target["tables"], identity)
            counters["legacy_fresh_compared_identities"] += 1
            counters["legacy_fresh_changed_fields"] += len(comparison["changed_fields"])
            counters["target_source_located_facts"] += provenance["source_located"]
            counters["target_source_unlocated_facts"] += provenance["source_unlocated"]
            counters["target_unknown_scope_facts"] += provenance["unknown_statement_scope"]
        if issues:
            status = "blocked_source_validation"
        elif target_tables:
            status = (
                "already_published_with_full_provenance"
                if provenance
                and provenance["source_unlocated"] == 0
                and provenance["unknown_statement_scope"] == 0
                else "already_published_with_partial_provenance"
            )
        else:
            status = "ready_for_fresh_extraction"
        counters[status] += 1
        for issue in issues:
            counters[issue] += 1
        records.append(
            {
                "stock_code": identity[0],
                "report_year": identity[1],
                "report_period": identity[2],
                "stock_abbr": core_by_identity[identity].get("stock_abbr"),
                "legacy_rows": {table: 1 for table in TABLES},
                "full_pdf_candidates": len(full),
                "pdf": pdf,
                "target_tables": target_tables,
                "target_has_provenance": target_payload is not None,
                "target_provenance": provenance,
                "legacy_to_fresh_comparison": comparison,
                "status": status,
                "issues": issues,
                "direct_copy_allowed": False,
            }
        )
    after_audit, after_rows = _load_rows(source_engine)
    after_digest = _canonical_rows(after_rows)
    if after_audit != source_audit or after_digest != before_digest:
        raise RuntimeError("Legacy source database changed during read-only preview")
    return {
        "mode": "preview_only",
        "source_unchanged": True,
        "source_snapshot_sha256": before_digest,
        "source_audit": source_audit,
        "field_counts": _field_counts(source_rows),
        "summary": {
            "identities": len(identities),
            "companies": len({identity[0] for identity in identities}),
            **dict(counters),
            "direct_copy_allowed": 0,
        },
        "decision": (
            "Do not copy legacy values directly: the legacy database has no field-level PDF provenance. "
            "For each ready identity, run the current extractor against the verified full PDF, compare "
            "fresh values with legacy values for audit, and publish the fresh atomic snapshot only."
        ),
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--source-db", default="financial_report")
    parser.add_argument(
        "--manifest",
        default="data/runtime/real_validation/financial_library_1417/manifest.json",
    )
    parser.add_argument(
        "--output",
        default="data/runtime/financial_qa_rollout/legacy_preview.json",
    )
    args = parser.parse_args()

    from dotenv import dotenv_values

    os.environ.update({key: value for key, value in dotenv_values(args.env_file).items() if value is not None})
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    from config.db_config import get_db_config
    from sqlalchemy import create_engine

    config = get_db_config()
    if args.source_db != "financial_report":
        raise ValueError("This preview only accepts the legacy financial_report source database")
    if config.database in {args.source_db, "finsight_real_eval"}:
        raise ValueError("Serving database must differ from legacy and frozen evaluation databases")
    source_engine = create_engine(replace(config, database=args.source_db).connection_string)
    target_engine = create_engine(config.connection_string)
    manifest_path = (ROOT / args.manifest).resolve()
    output_path = (ROOT / args.output).resolve()
    if not manifest_path.is_relative_to(ROOT) or not output_path.is_relative_to(ROOT):
        raise ValueError("Manifest and output must stay inside the project workspace")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    preview = build_preview(source_engine, target_engine, manifest)
    preview["source_database"] = args.source_db
    preview["target_database"] = config.database
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**preview["summary"], "source_unchanged": True}, ensure_ascii=False))
    source_engine.dispose()
    target_engine.dispose()


if __name__ == "__main__":
    main()
