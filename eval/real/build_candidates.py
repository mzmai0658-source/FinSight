"""作品说明：先固定真实报告问题，再仅从原始 PDF 文本生成标注候选。不读取数据库、提取结果、OCR、模型或索引；机器候选不等于人工标准答案或正式质量得分。"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re
import sys

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKSPACE = ROOT / "data/runtime/real_validation"
METRICS = {
    "total_operating_revenue": ("营业收入", "万元", "income_sheet", r"营业(?:总)?收入"),
    "eps": ("基本每股收益", "元/股", "core_performance_indicators_sheet", r"基本每股收益"),
    "operating_cf_net_amount": ("经营活动产生的现金流量净额", "万元", "cash_flow_sheet", r"经营活动产生的现金流量净额"),
    "roe": ("加权平均净资产收益率", "%", "core_performance_indicators_sheet", r"加权平均净资产收益率"),
}
NUMBER = re.compile(r"(?<![\d.])[-−－]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?(?:%|％)?")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dump_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dump_lines(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def build_plan(documents: list[dict]) -> list[dict]:
    plan = []
    companies = {}
    for document in documents:
        code, year, period = document["stock_code"], document["report_year"], document["report_period"]
        name, split = document["stock_abbr"], document["split"]
        companies.setdefault(code, {"name": name, "split": split, "documents": []})["documents"].append(document)
        for field, (label, unit, _, _) in METRICS.items():
            plan.append({
                "question_record": {"id": f"real-num-{code}-{year}-{period}-{field}", "category": "numeric", "split": split,
                    "question": f"{name}（{code}）{year}年{'全年' if period == 'FY' else '上半年'}的{label}是多少？请注明公司、报告期和单位（{unit}），并给出原始财报证据。"},
                "documents": [document], "field": field,
            })
    for code, company in companies.items():
        name, split = company["name"], company["split"]
        annuals = [d for d in company["documents"] if d["report_period"] == "FY" and d["report_year"] in (2022, 2023)]
        recent = [d for d in annuals if d["report_year"] == 2023]
        plan.extend([
            {"question_record": {"id": f"real-chart-{code}-2022-2023-FY-revenue", "category": "chart", "split": split,
                "question": f"请绘制{name}（{code}）2022年至2023年全年营业收入趋势图，按年份升序，单位万元，并提供对应财报证据。"},
             "documents": annuals, "field": "total_operating_revenue"},
            {"question_record": {"id": f"real-cite-{code}-2023-FY-business", "category": "citation", "split": split,
                "question": f"请根据{name}（{code}）2023年年度报告，摘引一段“经营情况讨论与分析”的原文并简要说明经营情况，标明财报页码；证据不足时请说明。"},
             "documents": recent},
            {"question_record": {"id": f"real-scope-{code}-2030-FY", "category": "out_of_scope", "split": split,
                "question": f"{name}（{code}）2030年全年营业收入是多少？请给出财报证据，不能根据其他年份推测。"},
             "documents": company["documents"]},
        ])
    return plan


def freeze_questions(workspace: Path, plan: list[dict], manifest: Path) -> None:
    questions = [entry["question_record"] for entry in plan]
    path = workspace / "questions.jsonl"
    if path.exists():
        existing = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if existing != questions:
            raise RuntimeError("Frozen questions differ; use a new evaluation workspace instead of changing the denominator")
    else:
        dump_lines(path, questions)
    receipt = workspace / "question_freeze.json"
    if receipt.exists():
        frozen = json.loads(receipt.read_text(encoding="utf-8"))
        if frozen["question_sha256"] != digest(path) or frozen["report_manifest_sha256"] != digest(manifest):
            raise RuntimeError("Frozen questions or report manifest changed; create a separate evaluation workspace")
    else:
        dump_json(receipt, {"frozen_at_utc": datetime.now(timezone.utc).isoformat(), "question_count": len(questions),
            "question_sha256": digest(path), "report_manifest_sha256": digest(manifest),
            "method": "All report/metric/category questions fixed before opening PDF content; failures retained"})


def extract_pages(document: dict, workspace: Path, max_pages: int) -> tuple[list[dict], list[str]]:
    source = (ROOT / document["source_path"]).resolve()
    if not source.is_relative_to((ROOT / "data_root").resolve()) or source.suffix.lower() != ".pdf":
        return [], ["Original PDF path is outside data_root"]
    try:
        if digest(source) != document["source_sha256"]:
            return [], ["Original PDF hash does not match frozen report manifest"]
        reader = PdfReader(source)
    except Exception as exc:
        return [], [f"Original PDF unavailable: {type(exc).__name__}: {exc}"]
    pages, errors = [], []
    folder = workspace / "page_text" / document["source_sha256"]
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(min(max_pages, len(reader.pages))):
        try:
            page_text = reader.pages[index].extract_text(extraction_mode="layout") or ""
            path = folder / f"page-{index + 1:03d}.txt"
            path.write_text(page_text, encoding="utf-8")
            pages.append({"page": index + 1, "text": page_text, "text_path": path.relative_to(ROOT).as_posix(),
                          "text_sha256": digest(path)})
        except Exception as exc:
            errors.append(f"PDF page {index + 1}: {type(exc).__name__}: {exc}")
    dump_json(folder / "index.json", {"source_path": document["source_path"], "source_sha256": document["source_sha256"],
        "page_count": len(reader.pages), "extraction": "pypdf extract_text(extraction_mode='layout')",
        "pages": [{key: value for key, value in page.items() if key != "text"} for page in pages], "errors": errors})
    return pages, errors


def source_fields(document: dict, page: dict, excerpt: str) -> dict:
    return {key: document[key] for key in ("stock_code", "stock_abbr", "report_year", "report_period", "source_path", "source_sha256", "document_id")} | {
        "document_version": document["source_sha256"], "page": page["page"], "page_start": page["page"], "page_end": page["page"],
        "excerpt": excerpt, "page_text_path": page["text_path"], "page_text_sha256": page["text_sha256"],
        "location_precision": "table_page", "human_verified": False,
    }


def numeric_candidate(document: dict, pages: list[dict], field: str) -> tuple[dict | None, str]:
    label, unit, table, pattern = METRICS[field]
    candidates = []
    for page_index, page in enumerate(pages[:12]):
        lines = page["text"].splitlines()
        for position, line in enumerate(lines):
            numbers = list(NUMBER.finditer(line))
            if len(numbers) < 2:
                continue
            first_number = numbers[0]
            row_label = compact(line[:first_number.start()])
            if not row_label.startswith({"total_operating_revenue": "营业", "eps": "基本每股",
                                         "operating_cf_net_amount": "经营活动产生", "roe": "加权平均"}[field]):
                continue
            # 作品说明：只拼接最左侧行名列的文本；数值列保留空白边界，避免把本期与上期金额拼为同一数字。
            row_lines = [line]
            for following in lines[position + 1:position + 3]:
                if not following.strip() or NUMBER.search(following):
                    break
                continued = compact(following)
                is_label_suffix = label.startswith(row_label + continued)
                is_unit_suffix = bool(re.fullmatch(r"[（(](?:[%％]|元[/／]股)[）)]", continued))
                if not (is_label_suffix or is_unit_suffix):
                    break
                row_label += continued
                row_lines.append(following)
            if not re.fullmatch(pattern + r"(?:[（(](?:元[/／]股|[%％])[）)])?", row_label):
                continue
            previous_page = pages[page_index - 1]["text"].splitlines()[-35:] if page_index else []
            before = "\n".join(previous_page + lines[:position])
            scope = compact(before)
            major_position = max(scope.rfind("主要会计数据"), scope.rfind("主要财务指标"))
            excluded_position = max(scope.rfind(marker) for marker in ("分季度", "第一季度", "母公司", "非经常性损益项目", "境内外会计准则"))
            if major_position < 0 or excluded_position > major_position:
                continue
            major_lines = [entry for entry in before.splitlines() if "主要会计数据" in compact(entry) or "主要财务指标" in compact(entry)]
            if major_lines and any(marker in compact(major_lines[-1]) for marker in ("季度", "母公司")):
                continue
            raw = first_number.group(0).rstrip("%％").replace("，", ",").replace("−", "-").replace("－", "-")
            # 作品说明：该行之前必须存在明确的本期标题。
            current_heading = str(document["report_year"]) + "年" in scope or "本报告期" in scope or "本期" in scope
            if not current_heading:
                continue
            if unit == "万元":
                units = re.findall(r"单位[:：]?(亿元|百万元|万元|千元|元)", compact(before))
                if not units:
                    continue
                raw_unit = units[-1]
                factor = {"亿元": Decimal("10000"), "百万元": Decimal("100"), "万元": Decimal("1"), "千元": Decimal("0.1"), "元": Decimal("0.0001")}[raw_unit]
            else:
                raw_unit, factor = unit, Decimal("1")
                if field == "eps" and not re.search(r"基本每股收益[（(]元[/／]股[）)]", row_label):
                    continue
                if field == "roe" and not re.search(r"加权平均净资产收益率[（(][%％][）)]", row_label):
                    continue
            try:
                value = Decimal(raw.replace(",", "")) * factor
            except InvalidOperation:
                continue
            candidate = {**source_fields(document, page, "\n".join(row_lines)), "field": field, "table": table, "metric_label": label,
                "value": float(value), "unit": unit, "tolerance": 0.011 if unit == "万元" else 0.00011,
                "raw_value": raw, "raw_unit": raw_unit, "unit_multiplier": float(factor),
                "column_semantics": "First current-period numeric column in major accounting/financial indicators table",
                "column_header_excerpt": "\n".join(before.splitlines()[-12:]), "parsed_row_label": row_label,
                "table_name": "主要会计数据/主要财务指标", "statement_scope": "consolidated_candidate",
                "annotation_method": "original_pdf_layout_regex", "review_status": "pending"}
            candidates.append(candidate)
    unique = {(candidate["page"], candidate["excerpt"]): candidate for candidate in candidates}
    if len(unique) != 1:
        return None, "No unambiguous major-indicator row with explicit unit/current-period column" if not unique else "Multiple candidate rows require human selection"
    return next(iter(unique.values())), "Current column, amount/unit conversion and PDF identity require human review"


def citation_candidate(document: dict, pages: list[dict]) -> tuple[dict | None, str]:
    for page in pages:
        lines = page["text"].splitlines()
        for index, line in enumerate(lines):
            if "经营情况讨论与分析" not in compact(line) or re.search(r"\.{3}|…", line):
                continue
            body = []
            for candidate in lines[index + 1:index + 16]:
                if re.match(r"^\s*(?:二|三|四)[、.．]", candidate):
                    break
                if candidate.strip() and not re.match(r"^\s*\d+\s*/\s*\d+\s*$", candidate):
                    body.append(candidate)
                if len(compact("\n".join(body))) >= 180 and candidate.rstrip().endswith(("。", "！", "？")):
                    break
            excerpt = "\n".join(body).strip()
            ending = max(excerpt.rfind(marker) for marker in ("。", "！", "？"))
            if ending >= 0:
                excerpt = excerpt[:ending + 1]
            if len(compact(excerpt)) >= 60 and not re.search(r"\.{4}|……", excerpt):
                return {**source_fields(document, page, excerpt), "location_precision": "page", "section": "经营情况讨论与分析",
                        "annotation_method": "original_pdf_section_heading", "review_status": "pending"}, "Section relevance and explanation support await human review"
    return None, "No unambiguous operating-discussion passage in scanned original PDF pages"


def build_labels(plan: list[dict], pages_by_id: dict, errors_by_id: dict) -> list[dict]:
    labels = []
    for entry in plan:
        question, documents = entry["question_record"], entry["documents"]
        expected, reasons = {"values": [], "sources": []}, []
        if question["category"] in {"numeric", "chart"}:
            expected["tools"] = ["query_database"] + (["render_chart"] if question["category"] == "chart" else [])
            for document in sorted(documents, key=lambda value: value["report_year"]):
                candidate, reason = numeric_candidate(document, pages_by_id.get(document["document_id"], []), entry["field"])
                reasons.append(reason)
                if candidate:
                    expected["values"].append(candidate)
            complete = len(expected["values"]) == len(documents) and bool(documents)
        elif question["category"] == "citation":
            expected["tools"] = ["search_documents"]
            for document in documents:
                candidate, reason = citation_candidate(document, pages_by_id.get(document["document_id"], []))
                reasons.append(reason)
                if candidate:
                    expected["sources"].append(candidate)
            complete = len(expected["sources"]) == 1
        else:
            expected.update(refusal=True, report_year=2030, refusal_keywords=["证据不足", "没有", "未找到", "无法", "不能", "尚未"])
            reasons.append("Frozen corpus has no 2030 report; human review of refusal rubric remains pending")
            complete = not any(document["report_year"] == 2030 for document in documents)
        errors = [error for document in documents for error in errors_by_id.get(document["document_id"], [])]
        labels.append({"id": question["id"], "label_status": "machine_candidate" if complete else "annotation_missing",
            "human_verified": False, "review_status": "pending", "split": question["split"], "expected": expected,
            "document_ids": [document["document_id"] for document in documents],
            "annotation_notes": list(dict.fromkeys(reasons)), "input_errors": errors})
    return labels


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--freeze-only", action="store_true")
    parser.add_argument("--development-only", action="store_true")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    manifest = workspace / "report_manifest.json"
    documents = json.loads(manifest.read_text(encoding="utf-8"))["documents"]
    plan = build_plan(documents)
    freeze_questions(workspace, plan, manifest)
    if args.freeze_only:
        print(json.dumps({"frozen_questions": len(plan), "pdf_content_opened": False}))
        return 0
    if args.development_only:
        documents = [document for document in documents if document["split"] == "development"]
        plan = [entry for entry in plan if entry["question_record"]["split"] == "development"]
    if (workspace / "gold.jsonl").exists() and not args.development_only:
        raise RuntimeError("Existing complete candidate run is preserved; use a separate workspace to regenerate")
    if not args.development_only:
        snapshot = workspace / "candidate_parser.snapshot.py"
        if snapshot.exists() and snapshot.read_bytes() != Path(__file__).read_bytes():
            raise RuntimeError("Parser changed after complete-run freeze; preserve this run and use a new workspace")
        snapshot.write_bytes(Path(__file__).read_bytes())
    pages_by_id, errors_by_id = {}, {}
    for document in documents:
        pages, errors = extract_pages(document, workspace, 40 if document["report_year"] == 2023 and document["report_period"] == "FY" else 12)
        pages_by_id[document["document_id"]], errors_by_id[document["document_id"]] = pages, errors
    labels = build_labels(plan, pages_by_id, errors_by_id)
    suffix = ".development" if args.development_only else ""
    dump_lines(workspace / f"gold{suffix}.jsonl", labels)
    summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "question_count": len(plan),
        "label_counts": dict(Counter(label["label_status"] for label in labels)), "human_verified_count": 0,
        "pending_human_review_count": len(labels), "source_document_count": len(documents),
        "document_input_error_count": sum(bool(value) for value in errors_by_id.values()),
        "input_error_count": sum(len(value) for value in errors_by_id.values()),
        "parser_sha256": digest(Path(__file__)), "questions_sha256": digest(workspace / "questions.jsonl"),
        "pypdf_version": version("pypdf"), "numeric_page_scan_limit": 12, "citation_page_scan_limit": 40,
        "gold_sha256": digest(workspace / f"gold{suffix}.jsonl"), "report_manifest_sha256": digest(manifest),
        "method": "Original PDF text layer only; candidate labels remain separate from runtime and RAG",
        "holdout_policy": "No holdout answer values printed or used to tune parser; historical exposure unknown", "split": "development" if args.development_only else "all"}
    dump_json(workspace / f"candidate_summary{suffix}.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    if args.development_only:
        for label in labels:
            print(json.dumps({"id": label["id"], "label_status": label["label_status"],
                "values": [{key: value[key] for key in ("field", "raw_value", "raw_unit", "value", "unit", "page")} for value in label["expected"]["values"]]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
