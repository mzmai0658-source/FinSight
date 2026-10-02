"""作品说明：将可追溯财务事实写入明确选择的数据库。默认只生成选择计划；--apply --extract-only 运行真实提取并保存审计结果，不修改 MySQL；发布还须指定 --expected-database。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IDENTITY = ("stock_code", "report_year", "report_period")
FINANCIAL_TABLES = (
    "core_performance_indicators_sheet", "balance_sheet", "income_sheet", "cash_flow_sheet",
    "financial_report_provenance", "financial_report_versions",
)
_EXPLICIT_REPORT_RE = re.compile(
    r"(?:年度报告|半年度报告|季度报告|第一季度报告|第三季度报告|一季度报告|三季度报告)"
)
_SAFE_PUBLICATION_WARNING_PATTERNS = (
    re.compile(r"^llm_extraction_disabled_skipped\([a-z_,]+\)$"),
    re.compile(r"^optional_llm_supplement_disabled\([a-z_,]+\)$"),
    re.compile(r"^(?:balance|income|cashflow)_critical_missing$"),
    re.compile(r"^(?:balance|income|cashflow)_incomplete\(\d+/\d+\)$"),
)


def _identity(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    return tuple(row.get(key) for key in IDENTITY)


def filter_documents_by_stock_codes(
    documents: list[dict[str, Any]],
    raw_stock_codes: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not raw_stock_codes:
        return documents, []
    requested = []
    for value in raw_stock_codes.split(","):
        code = value.strip()
        if not code:
            continue
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError(f"Invalid stock code: {code}")
        if code not in requested:
            requested.append(code)
    available = {str(row.get("stock_code") or "") for row in documents}
    missing = [code for code in requested if code not in available]
    if missing:
        raise ValueError("Requested stock codes have no selected full reports: " + ", ".join(missing))
    requested_set = set(requested)
    return [row for row in documents if str(row.get("stock_code") or "") in requested_set], requested


def load_stock_codes_file(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("companies") if isinstance(payload, dict) else payload
    if not isinstance(entries, list) or not entries:
        raise ValueError("Stock-code file must contain a non-empty companies list")
    codes = []
    for entry in entries:
        value = entry.get("stock_code") if isinstance(entry, dict) else entry
        codes.append(str(value or ""))
    return ",".join(codes)


def _is_full_report(row: dict[str, Any]) -> bool:
    """作品说明：只修正明显的长名称目录误识别。"""
    name = str(row.get("file_name") or "")
    if "摘要" in name:
        return False
    if str(row.get("report_kind") or "") == "full":
        return True
    try:
        page_count = int(row.get("page_count") or 0)
    except (TypeError, ValueError):
        page_count = 0
    return page_count >= 20 and bool(_EXPLICIT_REPORT_RE.search(name))


def select_documents(manifest: Iterable[dict[str, Any]]):
    """作品说明：每家公司、年度和期间选取一份身份无歧义的完整报告。"""
    groups: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    excluded: list[dict[str, Any]] = []
    for row in manifest:
        key = _identity(row)
        if any(value in (None, "") for value in key):
            excluded.append({"identity": key, "reason": "unresolved_identity", "files": [row.get("file_name")]})
        else:
            groups[key].append(row)
    selected: list[dict[str, Any]] = []
    for key in sorted(groups, key=lambda value: tuple(map(str, value))):
        rows = groups[key]
        full = [
            row for row in rows if _is_full_report(row)
            and not any(mark in str(row.get("file_name") or "") for mark in ("英文版", "更正前", "更新前"))
        ]
        corrected = [
            row for row in full
            if any(mark in str(row.get("file_name") or "") for mark in ("更正后", "更新后"))
        ]
        candidates = corrected or full
        if not candidates:
            excluded.append({"identity": key, "reason": "no_full_report", "files": [r.get("file_name") for r in rows]})
            continue
        # 作品说明：不能因 OCR 缓存可用而选择旧修订版；新光 2022 年两份修订 PDF 存在实际 ROE 更正。
        if len({row.get("sha256") for row in candidates}) > 1:
            excluded.append({"identity": key, "reason": "conflicting_full_versions", "files": [r.get("file_name") for r in candidates]})
            continue
        selected.append(sorted(candidates, key=lambda row: str(row.get("source_path") or ""))[0])
    return selected, excluded


def _json_default(value: Any):
    if isinstance(value, Decimal):
        return str(value)
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


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines) - 1:
                raise
    return records


def _payload(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def audit_extraction(result: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """作品说明：发布前核验身份、原件字节与页级证据。"""
    if result.get("status") != "success":
        raise ValueError(result.get("message") or "Extraction failed")
    data = result.get("data") or {}
    if _identity(data) != _identity(row):
        raise ValueError("Extracted identity differs from validated catalogue")
    if data.get("source_is_summary"):
        raise ValueError("Selected full report classified as summary")
    document = data.get("_source_document") or {}
    if document.get("source_sha256") != row.get("sha256"):
        raise ValueError("Extraction source hash differs from selected PDF")
    page_count = document.get("page_count")
    if not isinstance(page_count, int) or page_count < 1:
        raise ValueError("Extraction has no valid source page count")
    accepted = set(data.get("_accepted_fields") or [])
    sources = dict(data.get("_field_sources") or {})
    invalid: list[str] = []
    located: list[str] = []
    for field in sorted(accepted.intersection(sources)):
        source = sources.get(field) or {}
        start = source.get("page_start")
        end = source.get("page_end") or start
        if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end <= page_count or source.get("normalized_value") is None:
            invalid.append(field)
        else:
            located.append(field)
    if invalid:
        raise ValueError("Invalid page evidence for fields: " + ", ".join(invalid))
    review = str(data.get("_pre_save_review_status") or "")
    if review not in {"pass", "warn"}:
        raise ValueError("Extraction did not pass pre-save review")
    if not located:
        raise ValueError("Extraction contains no page-located financial facts")
    return {
        "review": review,
        "warnings": list(data.get("_pre_save_review_warnings") or []),
        "missing_fields": list(data.get("_missing_required_fields") or []),
        "optional_missing_fields": list(data.get("_optional_llm_missing_fields") or []),
        "llm_skipped_table_types": list(data.get("_llm_extraction_skipped_table_types") or []),
        "accepted_fields": len(accepted),
        "source_fields": len(located),
    }


def warning_is_safe_for_partial_publication(warning: str) -> bool:
    """作品说明：只允许明确描述数据缺失的警告。部分快照可发布已找到字段，缺失保留为空；勾稽失败、不合理数值等异常不能自动发布。"""
    return any(pattern.fullmatch(str(warning)) for pattern in _SAFE_PUBLICATION_WARNING_PATTERNS)


def publication_decision(
    extraction_audit: dict[str, Any],
    rollout_audit: dict[str, Any] | None,
    *,
    publish_warnings: bool,
) -> tuple[bool, str]:
    if not rollout_audit:
        return False, "No matching post-extraction rollout audit"
    if rollout_audit.get("audit_status") == "blocked":
        return False, "Post-extraction audit blocked this document"
    extraction = rollout_audit.get("extraction") or {}
    if extraction.get("issues"):
        return False, "Post-extraction audit found source-evidence issues"

    review = extraction_audit.get("review")
    if review == "pass":
        return True, "pass"
    warnings = [str(value) for value in extraction_audit.get("warnings") or []]
    if not publish_warnings:
        return False, "Pre-save warnings require --publish-warnings"
    unsafe = [warning for warning in warnings if not warning_is_safe_for_partial_publication(warning)]
    if unsafe:
        return False, "Unsafe review warnings: " + "; ".join(unsafe)
    return True, "safe_partial"


def _snapshot_database(engine, database: str, path: Path) -> None:
    """作品说明：为本轮发布创建持久化的发布前备份。"""
    from sqlalchemy import text
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("target_database") != database:
            raise ValueError("Existing batch backup belongs to another database")
        return
    tables: dict[str, list[dict[str, Any]]] = {}
    with engine.connect() as connection:
        for name in FINANCIAL_TABLES:
            tables[name] = [dict(row) for row in connection.execute(text(f"SELECT * FROM {name}")).mappings()]
    _atomic_write_json(path, {
        "target_database": database,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": tables,
    })


@contextmanager
def _database_lock(engine, database: str):
    """作品说明：同一现用数据库禁止并发运行发布脚本。"""
    from sqlalchemy import text
    name = ("finsight-financial-qa-" + database)[:64]
    connection = engine.connect()
    try:
        if connection.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": name}).scalar() != 1:
            raise RuntimeError(f"Another financial QA rollout is active for {database}")
        connection_id = connection.execute(text("SELECT CONNECTION_ID()")) .scalar_one()

        def heartbeat() -> None:
            owner = connection.execute(text("SELECT IS_USED_LOCK(:name)"), {"name": name}).scalar()
            if owner != connection_id:
                raise RuntimeError("Financial QA rollout database lock was lost")
        yield heartbeat
    finally:
        try:
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})
        finally:
            connection.close()


def _read_existing(engine) -> dict[tuple[Any, Any, Any], dict[str, Any]]:
    from sqlalchemy import text
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT stock_code, report_year, report_period, payload FROM financial_report_provenance"
        )).mappings()
        return {_identity(row): _payload(row["payload"]) for row in rows}


def _verify_published(engine, row: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import text
    with engine.connect() as connection:
        stored = connection.execute(text(
            "SELECT payload FROM financial_report_provenance "
            "WHERE stock_code=:stock_code AND report_year=:report_year AND report_period=:report_period"
        ), {key: row[key] for key in IDENTITY}).scalar_one_or_none()
    if stored is None:
        raise RuntimeError("Published financial snapshot cannot be read back")
    payload = _payload(stored)
    if (payload.get("document") or {}).get("source_sha256") != row.get("sha256"):
        raise RuntimeError("Published financial snapshot has an unexpected source hash")
    located = [fact for fact in payload.get("facts", []) if fact.get("status") == "source_located"]
    # 作品说明：母公司净利润、股本等可追溯提取字段未全部进入四张兼容宽表；不能直接以提取字段总数比较存储事实数。
    if not located:
        raise RuntimeError("Published snapshot contains no page-located evidence")
    page_count = (payload.get("document") or {}).get("page_count")
    for fact in located:
        if fact.get("source_sha256") != row.get("sha256"):
            raise RuntimeError("Published fact points to an unexpected source")
        start, end = fact.get("page_start"), fact.get("page_end")
        if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end <= page_count:
            raise RuntimeError("Published fact has invalid page evidence")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--manifest", default="data/runtime/real_validation/financial_library_1417/manifest.json")
    parser.add_argument("--output", default="data/runtime/financial_qa_rollout")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--extract-only", action="store_true", help="save audited results without changing MySQL")
    parser.add_argument("--expected-database", help="required for publication and must match DB_NAME")
    parser.add_argument(
        "--publish-warnings",
        action="store_true",
        help="publish only audited partial records whose warnings describe missing/skipped fields",
    )
    parser.add_argument(
        "--audit-file",
        help="required for publication; read-only audit produced after the latest extraction run",
    )
    parser.add_argument("--limit", type=int, default=0)
    cohort_group = parser.add_mutually_exclusive_group()
    cohort_group.add_argument(
        "--stock-codes",
        help="comma-separated six-digit company allowlist for a reproducible demo cohort",
    )
    cohort_group.add_argument(
        "--stock-codes-file",
        help="JSON file containing a companies list with stock_code values",
    )
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--refresh-extracted",
        action="store_true",
        help="extract prepared records again and append refreshed audit artifacts; extract-only mode only",
    )
    parser.add_argument(
        "--disable-optional-llm-supplement",
        action="store_true",
        help=(
            "keep rule-extracted values when only optional profit fields are missing; "
            "the result is marked review_required instead of calling the LLM"
        ),
    )
    parser.add_argument(
        "--rules-only",
        "--disable-llm-extraction",
        dest="rules_only",
        action="store_true",
        help=(
            "disable every LLM extraction call; rule-empty candidate chunks are "
            "recorded as review warnings"
        ),
    )
    args = parser.parse_args()
    if args.extract_only and not args.apply:
        parser.error("--extract-only requires --apply")
    if args.refresh_extracted and not args.extract_only:
        parser.error("--refresh-extracted is allowed only with --extract-only")
    if args.limit < 0:
        parser.error("--limit must be zero or positive")
    if args.apply and not args.extract_only and not args.audit_file:
        parser.error("publication requires --audit-file from the latest extraction run")

    from dotenv import dotenv_values
    os.environ.update({key: value for key, value in dotenv_values(args.env_file).items() if value is not None})
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    from sqlalchemy import create_engine
    from config.db_config import get_db_config
    config = get_db_config()
    if config.database in {"financial_report", "finsight_real_eval"}:
        raise ValueError("Never write original or frozen evaluation database")
    if args.apply and not args.extract_only and args.expected_database != config.database:
        raise ValueError(
            "Publication requires --expected-database matching the env database exactly "
            f"(resolved: {config.database})"
        )

    engine = create_engine(config.connection_string, pool_pre_ping=True)
    output = (ROOT / args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / args.manifest).read_text(encoding="utf-8"))
    selected, excluded = select_documents(manifest)
    raw_stock_codes = args.stock_codes
    if args.stock_codes_file:
        raw_stock_codes = load_stock_codes_file((ROOT / args.stock_codes_file).resolve())
    selected, requested_stock_codes = filter_documents_by_stock_codes(selected, raw_stock_codes)
    rollout_audit_by_sha: dict[str, dict[str, Any]] = {}
    rollout_audit_path: Path | None = None
    if args.apply and not args.extract_only:
        rollout_audit_path = (ROOT / str(args.audit_file)).resolve()
        if not rollout_audit_path.is_file():
            raise ValueError(f"Rollout audit file does not exist: {rollout_audit_path}")
        rollout_payload = json.loads(rollout_audit_path.read_text(encoding="utf-8"))
        for audit_record in rollout_payload.get("records") or []:
            source_sha256 = audit_record.get("source_sha256")
            if source_sha256:
                rollout_audit_by_sha[str(source_sha256)] = audit_record
    existing = _read_existing(engine)
    plan = {"selected": len(selected), "excluded": excluded, "existing_snapshots": len(existing),
            "requested_stock_codes": requested_stock_codes,
            "target_database": config.database, "documents": selected}
    _atomic_write_json(output / "selection.json", plan)
    if not args.apply:
        print(json.dumps({key: value for key, value in plan.items() if key != "documents"}, ensure_ascii=False))
        return

    from loguru import logger
    from src.agent.llm_client import LLMClient
    from src.etl.etl_worker import ETLWorker
    from src.etl.pipeline import _update_company_master
    from src.utils.ocr_json_parser import find_json_cache_for_pdf
    from src.utils.company_registry import register_company_for_etl
    from urllib.parse import urlparse
    logger.remove()
    logger.add(output / "extraction.log", rotation="20 MB", level="INFO", encoding="utf-8")
    llm = LLMClient()
    if (llm.provider != "ollama" or llm.model != "qwen3.5:9b-q4_K_M" or llm.reasoning_effort != "low"
            or urlparse(llm.api_url).hostname not in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("Requires local Qwen3.5 with thinking enabled")

    journal = output / "extraction.jsonl"
    records = _read_jsonl(journal)
    last = {record["source_sha256"]: record for record in records if record.get("source_sha256")}
    selected.sort(key=lambda row: (_identity(row) not in existing, *map(str, _identity(row))))

    def checkpoint(active: str | None = None) -> None:
        latest = {record["source_sha256"]: record for record in records if record.get("source_sha256")}
        _atomic_write_json(output / "progress.json", {
            "planned": len(selected), "excluded": excluded, "processed": len(latest),
            "statuses": dict(Counter(record.get("status", "unknown") for record in latest.values())),
            "active": active, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": llm.model, "thinking": llm.reasoning_effort, "extract_only": args.extract_only,
            "optional_llm_supplement": not args.disable_optional_llm_supplement,
            "llm_extraction": not args.rules_only,
        })

    with _database_lock(engine, config.database) as heartbeat:
        if not args.extract_only:
            _snapshot_database(engine, config.database, output / "publish.before.json")
        done = 0
        for row in selected:
            key, sha = _identity(row), row["sha256"]
            previous, published = last.get(sha), existing.get(key)
            if published:
                if sha != (published.get("document") or {}).get("source_sha256"):
                    raise ValueError(f"Published source differs from selected version: {key}")
                if not previous or previous.get("status") not in {"published", "existing"}:
                    record = {**{name: row[name] for name in IDENTITY}, "source_sha256": sha,
                              "status": "existing", "file": row["source_path"]}
                    records.append(record); last[sha] = record; _append_jsonl(journal, record)
                continue
            if previous and previous.get("status") == "published":
                continue
            if (args.extract_only and not args.refresh_extracted and previous
                    and previous.get("status") in {"extracted", "review_required"}):
                continue
            if previous and previous.get("status") == "failed" and not args.retry_failed:
                continue
            if args.limit and done >= args.limit:
                break
            heartbeat(); checkpoint(row["source_path"]); started = time.monotonic()
            print(f"EXTRACT {done + 1} {row['company']} {row['report_year']} {row['report_period']}", flush=True)
            record = {**{name: row[name] for name in IDENTITY}, "source_sha256": sha, "file": row["source_path"]}
            worker = None
            try:
                # 作品说明：材料目录已经过身份核验；只在内存中登记本文件公司，使仅提取模式保持数据库只读，成功发布后再更新已导入公司主表。
                register_company_for_etl(row["stock_code"], row["company"])
                pdf = (ROOT / row["source_path"]).resolve()
                if not pdf.is_relative_to((ROOT / "data_root").resolve()):
                    raise ValueError("Selected PDF is outside data_root")
                with pdf.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != sha:
                    raise ValueError("PDF changed after catalogue import")
                detail = output / "extracted" / (sha + ".json")
                if (not args.refresh_extracted and previous
                        and previous.get("status") in {"extracted", "review_required"} and detail.exists()):
                    result = json.loads(detail.read_text(encoding="utf-8"))
                else:
                    cache = find_json_cache_for_pdf(str(pdf))
                    if not cache:
                        raise ValueError("No OCR cache for structured table extraction; PDF text remains readable")
                    worker = ETLWorker(
                        llm,
                        enable_llm_extraction=not args.rules_only,
                        enable_optional_llm_supplement=(
                            not args.disable_optional_llm_supplement and not args.rules_only
                        ),
                    )
                    result = worker.run(str(cache), save_to_db=False, source_pdf_path=str(pdf))
                audit = audit_extraction(result, row)
                if args.extract_only:
                    _atomic_write_json(detail, result)
                else:
                    if not detail.is_file():
                        raise ValueError("Publication requires a previously audited extraction detail")
                    if rollout_audit_path is None or rollout_audit_path.stat().st_mtime < detail.stat().st_mtime:
                        raise ValueError("Rollout audit is older than the extracted detail; run the audit again")
                record.update(audit)
                if args.extract_only:
                    record["status"] = "review_required" if audit["review"] == "warn" else "extracted"
                else:
                    allowed, reason = publication_decision(
                        audit,
                        rollout_audit_by_sha.get(sha),
                        publish_warnings=args.publish_warnings,
                    )
                    if not allowed:
                        record.update(status="review_required", error=reason)
                        records.append(record); last[sha] = record; _append_jsonl(journal, record); checkpoint(); done += 1
                        print(json.dumps(record, ensure_ascii=False), flush=True)
                        continue
                    if worker is None:
                        worker = ETLWorker(
                            llm,
                            enable_llm_extraction=not args.rules_only,
                            enable_optional_llm_supplement=(
                                not args.disable_optional_llm_supplement and not args.rules_only
                            ),
                        )
                    worker.save_records_to_db([result])
                    stored = _verify_published(engine, row, audit)
                    existing[key] = stored
                    try:
                        _update_company_master(result["data"]["stock_code"], result["data"]["stock_abbr"])
                    except Exception as company_error:
                        record["company_update_warning"] = f"{type(company_error).__name__}: {company_error}"
                    record["status"] = "published"
            except Exception as error:
                record.update(status="failed", error=f"{type(error).__name__}: {error}")
            finally:
                if worker is not None and worker._db_engine is not None:
                    worker._db_engine.dispose()
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            records.append(record); last[sha] = record; _append_jsonl(journal, record); checkpoint(); done += 1
            print(json.dumps(record, ensure_ascii=False), flush=True)
        checkpoint()
    engine.dispose()
    print("BATCH END", flush=True)


if __name__ == "__main__":
    main()
