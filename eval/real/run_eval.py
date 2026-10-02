"""作品说明：本机评估真实财务 PDF，保留独立标注与完整重放记录。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.experiment import MODES, build_manifest, fingerprint
from eval.real.oracle import METRICS, VERSION, score_record, validate_source
from eval.real.runtime import HTTPAudit, ModelDisconnected, PDFRuntime, configure_environment

DATA_ROOT = ROOT / "data/runtime/real_validation"
CATEGORIES = ("numeric", "chart", "citation", "security", "out_of_scope")
SPLITS = ("development", "prospective_holdout")
PUBLIC_FIELDS = {"id", "question", "category", "split"}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def jsonl(rows: Sequence[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows)


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def write_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def public_case(case: Mapping[str, Any]) -> dict:
    """作品说明：即使使用自定义运行环境，也只将指定字段送入推理边界。"""
    return {key: case[key] for key in PUBLIC_FIELDS}


def load_cases(questions: Path, gold: Path, allowed_root: Path, *, verify_sources: bool = True) -> list[dict]:
    questions_rows, gold_rows = read_jsonl(questions), read_jsonl(gold)
    question_ids, gold_ids = [r.get("id") for r in questions_rows], [r.get("id") for r in gold_rows]
    if not question_ids or len(set(question_ids)) != len(question_ids) or len(set(gold_ids)) != len(gold_ids):
        raise ValueError("Question/gold files must have nonempty unique IDs")
    if set(question_ids) != set(gold_ids):
        raise ValueError("Question and gold IDs differ; no unlabelled or extra samples may be silently dropped")
    gold_by_id, cases, errors = {r["id"]: r for r in gold_rows}, [], []
    for question in questions_rows:
        identifier = question.get("id")
        label = gold_by_id[identifier]
        try:
            if not identifier or not str(question.get("question", "")).strip() or question.get("category") not in CATEGORIES or question.get("split") not in SPLITS:
                raise ValueError("Question id, text, category or split is invalid")
            if set(question) - PUBLIC_FIELDS:
                raise ValueError("Question input permits only id/question/category/split; keep labels and evidence in gold.jsonl")
            if label.get("label_status") not in {"machine_candidate", "human_reviewed", "annotation_missing"}:
                raise ValueError("Explicit label_status is required")
            if label["label_status"] == "human_reviewed" and not (label.get("reviewer") and label.get("reviewed_at")):
                raise ValueError("Human-reviewed labels require reviewer and reviewed_at provenance")
            expected = label.get("expected") or {}
            missing = label["label_status"] == "annotation_missing"
            if question["category"] in {"numeric", "chart"} and not expected.get("values") and not missing:
                raise ValueError("Numeric/chart cases require independently extracted labelled values")
            if question["category"] == "citation" and not expected.get("sources") and not missing:
                raise ValueError("Citation cases require original PDF source/page/excerpt labels")
            sources = [] if missing else list(expected.get("sources") or []) + list(expected.get("values") or [])
            for source in sources:
                required = {"stock_code", "report_year", "report_period", "source_path", "source_sha256", "page", "excerpt"}
                if not required.issubset(source) or source.get("report_period") not in {"FY", "HY", "Q1", "Q3"}:
                    raise ValueError("Every fact/source requires complete company-period-PDF-page evidence")
                if source in expected.get("values", []) and not {"stock_abbr", "field", "value", "unit"}.issubset(source):
                    raise ValueError("Incomplete financial value identity")
                if verify_sources:
                    validate_source(source, allowed_root)
            cases.append({**question, "label_status": label["label_status"], "label_review": {k: label.get(k) for k in ("reviewer", "reviewed_at")},
                          "label_metadata": {k: v for k, v in label.items() if k not in {"id", "expected", "label_status"}}, "expected": expected})
        except (ValueError, TypeError, KeyError, OSError) as exc:
            errors.append({"case_id": identifier, "error": f"{type(exc).__name__}: {exc}"})
    if errors:
        raise ValueError("Dataset preflight failed; all affected cases retained in this error: " + json.dumps(errors, ensure_ascii=False))
    return cases


def rescore_records(cases: Sequence[Mapping[str, Any]], records: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any], source_root: Path) -> list[dict]:
    known, seen, rescored = {case["id"]: case for case in cases}, set(), []
    selected = set(manifest["selected_case_ids"])
    for record in records:
        key = record.get("case_id"), record.get("mode")
        case = known.get(key[0])
        if key in seen or case is None or key[0] not in selected or key[1] not in manifest["modes"]:
            raise ValueError("Replay has duplicate, unknown or unplanned records")
        if record.get("case_fingerprint") != fingerprint(case) or record.get("manifest_fingerprint") != manifest["fingerprint"]:
            raise ValueError("Replay labels or manifest changed")
        seen.add(key)
        rescored.append({**record, "scores": score_record(case, record, source_root), "scorer_version": VERSION,
                         "scorer_sha256": hashlib.sha256((ROOT / "eval/real/oracle.py").read_bytes()).hexdigest()})
    return rescored


def validate_run_coverage(records: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any], status: Mapping[str, Any]) -> None:
    planned = {(case_id, mode) for case_id in manifest["selected_case_ids"] for mode in manifest["modes"]}
    actual = {(record["case_id"], record["mode"]) for record in records}
    if status.get("status") in {"completed", "completed_with_errors"} and actual != planned:
        raise ValueError("Completed run JSONL is missing planned records; cannot silently report a complete replay")


def render_report(records: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any], status: Mapping[str, Any]) -> str:
    selected = manifest.get("selected_case_ids") or []
    modes = manifest.get("modes") or []
    planned = len(selected) * len(modes)
    lines = ["# 真实财报评测记录", "", f"运行状态：**{status.get('status', 'unknown')}**；计划 {len(selected)} 题 × {len(modes)} 模式 = {planned} 条，已记录 {len(records)} 条。",
             "", "原件是真实公司财报；机器抽取的参考答案仍需人工核对。当前自动分数衡量与候选标注的一致性，不能直接当成人工确认的准确率。",
             "prospective_holdout 为此次冻结后的保留组；历史调试曝光不确定，不声称历史从未见过。解释和用户研究没有人工结果时保持未审核。",
             "annotation_missing 样本继续运行并保留在计划分母中，依赖真值的分数为 N/A；缺标不能当通过，也不会静默删除题目。", "",
             "| 模式 | 已记录/计划 | 正常完成 | 异常 | 平均耗时（秒） |", "| --- | ---: | ---: | ---: | ---: |"]
    for mode in modes:
        group = [r for r in records if r["mode"] == mode]
        completed = sum(r.get("terminal_status") == "completed" and not r.get("error") and not r.get("infrastructure_failure") for r in group)
        latency = statistics.mean(float(r.get("latency_seconds") or 0) for r in group) if group else None
        lines.append(f"| {mode} | {len(group)}/{len(selected)} | {completed} | {sum(bool(r.get('error')) for r in group)} | {latency:.3f} |" if latency is not None else f"| {mode} | 0/{len(selected)} | 0 | 0 | N/A |")
    lines += ["", "下表每项显示通过数/已评分数；未执行题仍保留在上述计划分母中，未完成整组不可作为完整对比。", "", "| 自动指标 | " + " | ".join(modes) + " |", "| --- | " + " | ".join("---:" for _ in modes) + " |"]
    for metric in METRICS:
        cells = []
        for mode in modes:
            values = [r["scores"][metric] for r in records if r["mode"] == mode and r.get("scores", {}).get(metric) is not None]
            cells.append(f"{sum(bool(v) for v in values)}/{len(values)}" if values else "N/A")
        lines.append("| " + metric + " | " + " | ".join(cells) + " |")
    counts = Counter(r.get("label_status") for r in records)
    lines += ["", f"已运行记录标注来源：{dict(counts)}。false_acceptance 越低越好；verification_pass 是应用自身状态，不是独立真值。",
              "", "## 运行问题与保留材料", "", "完整 answer/result/events/tool_results 及模型HTTP诊断见 report.jsonl；输入、候选标签、SQL快照、模型摘要、源码哈希见本目录快照和 manifest。",
              "人工解释审核队列见 manual_review.jsonl；用户研究任务与空白登记见 user_study_tasks.jsonl、user_study_results.template.jsonl。没有自动填入参与者、评分、耗时或结论。"]
    if status.get("error"):
        lines += ["", "```text", str(status["error"]), "```"]
    failures = [r for r in records if r.get("error") or any(value is False for key, value in r.get("scores", {}).items() if key not in {"verification_pass", "false_acceptance"})]
    if failures:
        lines += ["", "| 题目 | 模式 | 未通过项 | 异常 |", "| --- | --- | --- | --- |"]
        for record in failures:
            failed = ", ".join(k for k, v in record.get("scores", {}).items() if v is False and k not in {"verification_pass", "false_acceptance"})
            error = str(record.get("error") or "-").replace("|", "/").replace("\n", " ")
            lines.append(f"| {record['case_id']} | {record['mode']} | {failed} | {error} |")
    return "\n".join(lines) + "\n"


def export_review_materials(folder: Path, cases: Sequence[Mapping[str, Any]], records: Sequence[Mapping[str, Any]]) -> None:
    lookup = {case["id"]: case for case in cases}
    reviews = []
    for record in records:
        case = lookup[record["case_id"]]
        if case["category"] != "citation":
            continue
        answer = str(record.get("answer") or "")
        reviews.append({"case_id": case["id"], "mode": record["mode"], "reviewer": None,
                        "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
                        "supported": None, "review_status": "pending", "question": case["question"],
                        "answer": answer, "expected_source_candidates": case["expected"].get("sources") or [],
                        "returned_references": (record.get("result", {}).get("answer") or {}).get("references") or [],
                        "rubric": {"company_period_correct": None, "numbers_supported": None,
                                   "causal_claims_supported": None, "uncertainty_honest": None}, "notes": None})
    atomic_text(folder / "manual_review.jsonl", jsonl(reviews))
    tasks = [{"task_id": case["id"], "question": case["question"], "split": case["split"],
              "category": case["category"], "status": "not_conducted"} for case in cases]
    results = [{"participant_id": None, "task_id": task["task_id"], "condition": None,
                "task_order": None, "started_at": None, "finished_at": None, "elapsed_seconds": None,
                "answer_correct": None, "evidence_located": None, "confidence_1_to_5": None,
                "notes": None, "status": "not_conducted"} for task in tasks]
    atomic_text(folder / "user_study_tasks.jsonl", jsonl(tasks))
    atomic_text(folder / "user_study_results.template.jsonl", jsonl(results))
    atomic_text(folder / "REVIEW_INSTRUCTIONS.md", "# 人工审核与用户研究待办\n\n"
                "先逐条核对 gold.snapshot.jsonl 的公司、报告期、PDF 哈希、物理页码、表格口径、单位和数值。机器候选不得直接改称人工金标准。\n\n"
                "解释审核需阅读原页和完整答案；逐条判断归因是否受原文支持。填写 reviewer、answer_sha256、supported 布尔值和 notes，再通过 --rescore 与 --manual-reviews 导入。引用存在本身不等于解释正确。\n\n"
                "用户研究尚未开展。对比原 PDF 检索与系统辅助两种条件，交错分配条件和题目顺序；匿名登记参与者和任务耗时、正确性、证据定位、信心评分。任务表不包含参考答案，评审者另持候选标签。正式使用前先人工审定题目和答案。不要用模型生成参与者回答或填充结果。\n")


def write_checkpoint(folder: Path, records: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any], status: Mapping[str, Any]) -> None:
    atomic_text(folder / "report.jsonl", jsonl(records))
    write_json(folder / "report.manifest.json", manifest)
    write_json(folder / "run_status.json", {**status, "updated_at_utc": utc_now(), "records": len(records)})
    atomic_text(folder / "report.md", render_report(records, manifest, status))


def run_records(cases: Sequence[Mapping[str, Any]], runtime, manifest: Mapping[str, Any], source_root: Path,
                folder: Path, audit, *, checkpoint=write_checkpoint) -> tuple[list[dict], dict]:
    records, status = [], {"status": "running"}
    for index, case in enumerate(cases, 1):
        modes = manifest["modes"] if index % 2 else list(reversed(manifest["modes"]))
        for mode in modes:
            audit.calls.clear()
            started, started_utc, infrastructure = time.monotonic(), utc_now(), False
            print(f"REAL {index}/{len(cases)} {mode} {case['id']} {case['split']}", flush=True)
            try:
                output = runtime.run(public_case(case), mode)
                error = "" if output.get("terminal_status") == "completed" else "Missing successful terminal event"
            except (ModelDisconnected, Exception) as exc:
                infrastructure = isinstance(exc, ModelDisconnected)
                error = f"{type(exc).__name__}: {exc}"
                output = {"answer": "", "result": {}, "events": [], "tools": [], "tool_results": [], **getattr(exc, "partial_output", {}),
                          "terminal_status": "infrastructure_error" if infrastructure else "error",
                          "latency_seconds": round(time.monotonic()-started, 3)}
            record = {"case_id": case["id"], "case_fingerprint": fingerprint(case), "manifest_fingerprint": manifest["fingerprint"],
                      "category": case["category"], "split": case["split"], "label_status": case["label_status"], "mode": mode,
                      "scoring_status": "annotation_missing" if case["label_status"] == "annotation_missing" else "human_reviewed" if case["label_status"] == "human_reviewed" else "candidate_agreement_only",
                      **output, "error": error, "infrastructure_failure": infrastructure,
                      "started_at_utc": started_utc, "finished_at_utc": utc_now(), "llm_diagnostics": list(audit.calls),
                      "model_http_errors": sum(bool(c.get("error")) or (c.get("status") or 0) >= 400 for c in audit.calls),
                      "model_truncated_calls": sum("length" in c.get("finish_reasons", []) for c in audit.calls)}
            record["scores"] = score_record(case, record, source_root)
            record["scorer_version"] = VERSION
            record["scorer_sha256"] = hashlib.sha256((ROOT / "eval/real/oracle.py").read_bytes()).hexdigest()
            records.append(record)
            if infrastructure:
                status = {"status": "aborted_infrastructure", "error": error, "failed_case_id": case["id"], "failed_mode": mode}
            checkpoint(folder, records, manifest, status)
            print("RESULT", case["id"], mode, record["latency_seconds"], record["scores"], flush=True)
            if infrastructure:
                return records, status
    status = {"status": "completed_with_errors" if any(r["error"] for r in records) else "completed"}
    checkpoint(folder, records, manifest, status)
    return records, status


def replay(args) -> int:
    original = args.rescore.parent
    manifest = json.loads((original / "report.manifest.json").read_text(encoding="utf-8"))
    cases = load_cases(original / "questions.snapshot.jsonl", original / "gold.snapshot.jsonl", args.source_root)
    if fingerprint(cases) != manifest["dataset_sha256"]:
        raise ValueError("Recorded question/gold snapshot changed")
    records = read_jsonl(args.rescore)
    if args.manual_reviews:
        reviews = read_jsonl(args.manual_reviews)
        keys = [(r.get("case_id"), r.get("mode")) for r in reviews]
        known = {(r["case_id"], r["mode"]): r for r in records}
        if len(set(keys)) != len(keys) or set(keys) - set(known):
            raise ValueError("Manual review contains duplicate or unknown records")
        valid = {}
        for review in reviews:
            if not review.get("reviewer") or not isinstance(review.get("supported"), bool):
                continue  # 作品说明：待填写的标注模板保持未审查状态。
            key = review["case_id"], review["mode"]
            if review.get("answer_sha256") != hashlib.sha256(str(known[key].get("answer") or "").encode("utf-8")).hexdigest():
                raise ValueError("Manual review answer hash differs from recorded answer")
            valid[key] = review
        records = [{**r, "manual_review": valid.get((r["case_id"], r["mode"]), r.get("manual_review", {}))} for r in records]
    rescored = rescore_records(cases, records, manifest, args.source_root)
    if args.output.resolve() == original.resolve():
        raise ValueError("Replay output must use a new directory; preserve original evidence")
    if (args.output / "report.jsonl").exists():
        raise FileExistsError("Replay output already exists")
    status = json.loads((original / "run_status.json").read_text(encoding="utf-8"))
    validate_run_coverage(rescored, manifest, status)
    status.update(replay=True, source_jsonl_sha256=hashlib.sha256(args.rescore.read_bytes()).hexdigest(), rescored_at_utc=utc_now())
    write_checkpoint(args.output, rescored, manifest, status)
    for name in ("questions.snapshot.jsonl", "gold.snapshot.jsonl", "source_manifest.snapshot.json"):
        if (original / name).exists():
            atomic_text(args.output / name, (original / name).read_text(encoding="utf-8"))
    export_review_materials(args.output, [c for c in cases if c["id"] in manifest["selected_case_ids"]], rescored)
    print(f"Replayed {len(rescored)} complete records without model/database calls: {args.output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=DATA_ROOT / "questions.jsonl")
    parser.add_argument("--gold", type=Path, default=DATA_ROOT / "gold.jsonl")
    parser.add_argument("--source-manifest", type=Path, default=DATA_ROOT / "report_manifest.json")
    parser.add_argument("--source-root", type=Path, default=ROOT / "data_root/financial_reports")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.real-eval")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--split", choices=["all", *SPLITS], default="development")
    parser.add_argument("--rescore", type=Path)
    parser.add_argument("--manual-reviews", type=Path)
    parser.add_argument("--require-human-labels", action="store_true", help="Fail before inference unless every selected label has recorded human review")
    args = parser.parse_args()
    if args.rescore:
        return replay(args)
    if args.manual_reviews:
        parser.error("--manual-reviews requires --rescore")
    if len(set(args.modes)) != len(args.modes):
        parser.error("Duplicate modes are not permitted")
    if (args.output / "report.jsonl").exists() or (args.output / "run_status.json").exists():
        raise FileExistsError("Output already contains a run; choose a new directory")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {"modes": args.modes, "selected_case_ids": [], "fingerprint": "preflight_pending"}
    cases = []
    try:
        question_rows = read_jsonl(args.questions)
        manifest["selected_case_ids"] = [q.get("id") for q in question_rows if args.split == "all" or q.get("split") == args.split]
        if not manifest["selected_case_ids"]:
            raise ValueError("Selected split contains no questions")
        cases = load_cases(args.questions, args.gold, args.source_root)
        selected = [c for c in cases if c["id"] in manifest["selected_case_ids"]]
        if args.require_human_labels and any(c["label_status"] != "human_reviewed" for c in selected):
            raise ValueError("Formal reviewed run requires all selected labels to be human_reviewed; pending IDs: " + ", ".join(c["id"] for c in selected if c["label_status"] != "human_reviewed"))
        source_manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
        documents = source_manifest["documents"]
        selected_documents = [d for d in documents if args.split == "all" or d.get("split") == args.split]
        if not selected_documents:
            raise ValueError("No source documents registered for selected split")
        registered = {(d["source_path"], d["source_sha256"], str(d["stock_code"]), int(d["report_year"]), d["report_period"]) for d in documents}
        for case in selected:
            if case["label_status"] == "annotation_missing":
                continue
            for source in list(case["expected"].get("sources") or []) + list(case["expected"].get("values") or []):
                if (source["source_path"], source["source_sha256"], str(source["stock_code"]), int(source["report_year"]), source["report_period"]) not in registered:
                    raise ValueError(f"Label references an unregistered original PDF: {case['id']}")
        for name, rows in (("questions.snapshot.jsonl", question_rows), ("gold.snapshot.jsonl", read_jsonl(args.gold))):
            atomic_text(args.output / name, jsonl(rows))
        write_json(args.output / "source_manifest.snapshot.json", source_manifest)
        export_review_materials(args.output, selected, [])
        configure_environment(args.env_file)
        with HTTPAudit() as audit:
            runtime = PDFRuntime(documents, selected_documents)
            runtime.warmup()
            info = runtime.runtime_info()
            base = build_manifest(cases, info, False)
            base.pop("fingerprint", None)
            manifest = {**base, "schema_version": 1, "scorer_version": VERSION,
                        "selected_case_ids": [c["id"] for c in selected], "modes": args.modes,
                        "split": args.split, "source_root": str(args.source_root.resolve()),
                        "source_manifest_sha256": fingerprint(source_manifest),
                        "label_status_counts": dict(Counter(c["label_status"] for c in selected)),
                        "order": "modes alternate direction within each case; warmup excluded",
                        "holdout_note": "Prospective only; historical debugging exposure is unknown"}
            manifest["fingerprint"] = fingerprint(manifest)
            write_json(args.output / "database.snapshot.json", runtime.database_snapshot)
            write_checkpoint(args.output, [], manifest, {"status": "running"})
            records, status = run_records(selected, runtime, manifest, args.source_root, args.output, audit)
        rescored = rescore_records(cases, read_jsonl(args.output / "report.jsonl"), manifest, args.source_root)
        validate_run_coverage(rescored, manifest, status)
        if [r["scores"] for r in rescored] != [r["scores"] for r in records]:
            raise AssertionError("Disk replay changed independently scored results")
        status["disk_replay_verified"] = True
        write_checkpoint(args.output, records, manifest, status)
        export_review_materials(args.output, selected, records)
        print(f"Saved original-PDF evaluation and review queue: {args.output}")
        return 2 if status["status"] != "completed" else 0
    except (ModelDisconnected, Exception) as exc:
        # 作品说明：后续重放或报告生成失败时，不得抹去已检查点保存的调用记录。
        existing = read_jsonl(args.output / "report.jsonl") if (args.output / "report.jsonl").exists() else []
        write_checkpoint(args.output, existing, manifest, {"status": "preflight_failed" if not existing else "aborted_error", "error": f"{type(exc).__name__}: {exc}"})
        print(f"Run stopped; affected case IDs and error preserved in {args.output / 'run_status.json'}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
