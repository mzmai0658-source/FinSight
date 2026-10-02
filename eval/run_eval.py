"""作品说明：提供可复现公开虚构基准与独立证据重放。"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from eval.oracle import METRICS, score_record, contains_refusal, chart_values_supported as _chart_values_supported, expected_values_supported as _expected_values_supported
from eval.experiment import MODES, RealRuntime, FakeLLMRuntime, fingerprint, build_manifest

DEFAULT_DATASET = ROOT_DIR / "eval/dataset.jsonl"
DEFAULT_REPORT = ROOT_DIR / "eval/runs/latest/report.md"
CATEGORIES = ("numeric", "chart", "citation", "security", "out_of_scope")

def validate_dataset(cases: Sequence[Mapping[str, Any]]) -> None:
    ids = set()
    for case in cases:
        if not case.get("id") or case["id"] in ids or not str(case.get("question", "")).strip():
            raise ValueError("Missing/duplicate case id or empty question")
        ids.add(case["id"])
        if case.get("category") not in CATEGORIES:
            raise ValueError("Unknown category")
        expected = case.get("expected") or {}
        if case["category"] in {"numeric", "chart"}:
            if not expected.get("values"):
                raise ValueError("Independent labelled facts required")
            for fact in expected["values"]:
                if not {"stock_code", "stock_abbr", "report_year", "report_period", "field", "unit", "value"}.issubset(fact):
                    raise ValueError("Incomplete financial fact identity")
        if case["category"] == "citation" and not expected.get("sources"):
            raise ValueError("Independent source annotations required")

def load_dataset(path: Path = DEFAULT_DATASET) -> list:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    validate_dataset(cases)
    return cases

def load_detail_records(path: Path, expected_manifest: Mapping[str, Any] | None = None) -> list:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        if not row.get("case_id") or row.get("mode") not in MODES:
            raise ValueError(f"Invalid detail record: {path}")
        if expected_manifest and row.get("manifest_fingerprint") != expected_manifest.get("fingerprint"):
            raise ValueError("Refusing reuse: code, dataset, source, model, hardware or runtime configuration differs, or manifest is missing")
    return rows

def rescore_records(cases: Sequence[Mapping[str, Any]], records: Sequence[Mapping[str, Any]]) -> list:
    oracle, output, seen = {c["id"]: c for c in cases}, [], set()
    for record in records:
        case = oracle.get(record["case_id"])
        key = record["case_id"], record["mode"]
        if case is None or key in seen or record.get("case_fingerprint") != fingerprint(case):
            raise ValueError("Replay contains unknown/duplicate cases or changed oracle")
        seen.add(key)
        output.append({**record, "scores": score_record(case, record), "scorer_sha256": hashlib.sha256((ROOT_DIR / "eval/oracle.py").read_bytes()).hexdigest()})
    return output

def run_evaluation(cases: Sequence[Mapping[str, Any]], *, mode: str = "both", fake: bool = False, manifest=None, runtime=None) -> list:
    runtime = runtime or (FakeLLMRuntime() if fake else RealRuntime())
    manifest = manifest or build_manifest(cases, runtime.runtime_info(), fake)
    modes = list(MODES) if mode == "all" else ["bare", "agent"] if mode == "both" else [mode]
    records = []
    for index, case in enumerate(cases, 1):
        for selected in modes:
            print(f"[{index}/{len(cases)}] {selected} {case['id']}", flush=True)
            started_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            started = time.monotonic()
            try:
                output = runtime.run(case, selected)
                error = "" if output.get("terminal_status") == "completed" else "Missing successful terminal event"
            except Exception as exc:
                output = {"answer": "", "result": {}, "tools": [], "tool_results": [], "events": [], "terminal_status": "error", "latency_seconds": round(time.monotonic()-started, 3)}
                error = f"{type(exc).__name__}: {exc}"
            record = {"case_id": case["id"], "category": case["category"], "split": case.get("split"), "mode": selected,
                      "case_fingerprint": fingerprint(case), "manifest_fingerprint": manifest["fingerprint"], "runtime": manifest["runtime"], "fake": fake, **output, "error": error,
                      "started_at_utc": started_at_utc, "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            record["scores"] = score_record(case, record)
            record["scorer_sha256"] = hashlib.sha256((ROOT_DIR / "eval/oracle.py").read_bytes()).hexdigest()
            records.append(record)
    return records

def aggregate(records: Sequence[Mapping[str, Any]], mode: str) -> dict:
    selected = [r for r in records if r.get("mode") == mode]
    def rate(metric):
        values = [r.get("scores", {}).get(metric) for r in selected if r.get("scores", {}).get(metric) is not None]
        return sum(bool(v) for v in values)/len(values) if values else None
    latencies = sorted(float(r.get("latency_seconds") or 0) for r in selected)
    return {**{metric: rate(metric) for metric in METRICS}, "cases": len(selected),
            "average_latency": statistics.mean(latencies) if latencies else None,
            "p50_latency": statistics.median(latencies) if latencies else None,
            "p95_latency": latencies[max(0, math.ceil(len(latencies)*.95)-1)] if latencies else None,
            "errors": sum(bool(r.get("error")) for r in selected),
            "refusals": sum(contains_refusal(str(r.get("answer") or ""), {}) for r in selected),
            "incomplete": sum(r.get("terminal_status") != "completed" for r in selected)}

def _format_metric(value, percent=True) -> str:
    return "N/A" if value is None else f"{float(value)*100:.1f}%" if percent else f"{float(value):.3f}s"

def render_markdown(records: Sequence[Mapping[str, Any]], dataset_size: int) -> str:
    modes = [m for m in MODES if any(r.get("mode") == m for r in records)]
    titles = {"bare": "裸模型", "tool_baseline": "同数据工具基线", "agent": "FinSight Agent", "no_verifier": "关闭核验", "no_rerank": "关闭重排"}
    fake = any(r.get("fake") for r in records)
    lines = ["# FinSight 公开合成评测", "", "**离线 fixture 冒烟：以下数字不是模型实测，不可作为参赛性能指标。**" if fake else "真实运行记录；该小型合成集不代表真实财报或泛化能力。", "", f"覆盖 {dataset_size} 题。代码、数据、模型、依赖和硬件见同名 manifest。", "", "| 指标 | " + " | ".join(titles[m] for m in modes) + " |", "| --- | " + " | ".join("---:" for _ in modes) + " |"]
    summaries = {m: aggregate(records, m) for m in modes}
    labels = (("数值准确率（含图表点位）", "numeric_accuracy"), ("工具路由完成率", "tool_routing"), ("安全拦截或明确拒绝率", "dangerous_sql_blocked"), ("相关引用身份与原文一致率", "citation_supported"), ("解释支持率（人工）", "explanation_supported"), ("证据不足拒答率", "honest_refusal"), ("应用徽标通过率（非真值）", "verification_pass"), ("错误放行率（越低越好）", "false_acceptance"))
    for label, metric in labels:
        lines.append("| " + label + " | " + " | ".join(_format_metric(summaries[m][metric]) for m in modes) + " |")
    for metric in ("average_latency", "p50_latency", "p95_latency"):
        lines.append("| " + metric + " | " + " | ".join(_format_metric(summaries[m][metric], False) for m in modes) + " |")
    lines += ["", "安全区分明确拒绝、Guard 拦截、异常和未完成；裸模型无工具/图表/引用指标为 N/A。解释支持只接受与答案哈希绑定的人工标注；原文存在不证明自由文本解释。", "", "| 模式 | 运行异常 | 未完成 | 拒答 |", "| --- | ---: | ---: | ---: |"]
    for m in modes:
        s = summaries[m]
        lines.append(f"| {titles[m]} | {s['errors']} | {s['incomplete']} | {s['refusals']} |")
    lines += ["", "## 逐题异常与未通过项", "", "| 用例 | 模式 | 未通过项 | 错误 |", "| --- | --- | --- | --- |"]
    for record in records:
        failed = [k for k,v in record.get("scores", {}).items() if (v is False and k != "false_acceptance") or (k == "false_acceptance" and v is True)]
        if failed or record.get("error"):
            err = str(record.get("error") or "-").replace("|", "/")
            lines.append(f"| {record['case_id']} | {record['mode']} | {', '.join(failed)} | {err} |")
    return "\n".join(lines) + "\n"

def write_outputs(records: Sequence[Mapping[str, Any]], report_path: Path, dataset_size: int, manifest=None) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown(records, dataset_size), encoding="utf-8")
    # 作品说明：保留完整嵌套结果，包括引用、数据行、事实、图表及事件，避免丢失关键证据。
    report_path.with_suffix(".jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in records), encoding="utf-8")
    if manifest is not None:
        report_path.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--mode", choices=[*MODES, "both", "all"], default="both")
    parser.add_argument("--category", choices=CATEGORIES, action="append")
    parser.add_argument("--split", choices=["all", "development", "holdout"], default="all")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--fake", action="store_true")
    parser.add_argument("--reuse-details", type=Path, action="append", default=[])
    parser.add_argument("--rescore", type=Path)
    parser.add_argument("--manual-reviews", type=Path, help="JSONL case_id/mode/reviewer/answer_sha256/supported annotations")
    args = parser.parse_args()
    cases = load_dataset(args.dataset)
    if args.rescore:
        manifest = json.loads(args.rescore.with_suffix(".manifest.json").read_text(encoding="utf-8"))
        if manifest.get("dataset_sha256") != fingerprint(cases):
            raise ValueError("Replay dataset differs from recorded dataset")
        records = load_detail_records(args.rescore, manifest)
        manifest["replay"] = {"source_details_sha256": hashlib.sha256(args.rescore.read_bytes()).hexdigest(),
                              "scorer_sha256": hashlib.sha256((ROOT_DIR / "eval/oracle.py").read_bytes()).hexdigest(),
                              "rescored_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    else:
        os.environ.setdefault("LLM_PROVIDER", "ollama")
        os.environ.setdefault("EMBEDDING_PROVIDER", "bge_local")
        runtime = FakeLLMRuntime() if args.fake else RealRuntime()
        manifest = build_manifest(cases, runtime.runtime_info(), args.fake)
        records = [r for path in args.reuse_details for r in load_detail_records(path, manifest)]
        selected = [c for c in cases if (not args.category or c["category"] in args.category) and (args.split == "all" or c.get("split") == args.split)]
        if args.limit:
            selected = selected[:args.limit]
        requested = set(MODES if args.mode == "all" else ("bare", "agent") if args.mode == "both" else [args.mode])
        reused = {(r["case_id"], r["mode"]) for r in records}
        for case in selected:
            for mode in MODES:
                if mode in requested and (case["id"], mode) not in reused:
                    records.extend(run_evaluation([case], mode=mode, fake=args.fake, runtime=runtime, manifest=manifest))
                    # 作品说明：后续模型调用或主机失败时，保留所有已完成调用。
                    write_outputs(records, args.output, len({r["case_id"] for r in records}), manifest)
    if args.manual_reviews:
        reviews = {(r["case_id"], r["mode"]):r for r in (json.loads(line) for line in args.manual_reviews.read_text(encoding="utf-8").splitlines() if line.strip())}
        records = [{**r, "manual_review": reviews.get((r["case_id"], r["mode"]), r.get("manual_review", {}))} for r in records]
    records = rescore_records(cases, records)
    write_outputs(records, args.output, len({r["case_id"] for r in records}), manifest)
    print(f"Saved {args.output} and full JSONL + manifest")
    return 2 if any(r.get("error") for r in records) else 0

if __name__ == "__main__":
    raise SystemExit(main())
