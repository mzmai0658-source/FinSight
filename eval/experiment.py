"""记录公开基准的运行环境及可复现元数据。"""
from __future__ import annotations
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from eval.oracle import VERSION

ROOT = Path(__file__).resolve().parents[1]
MODES = ("bare", "tool_baseline", "agent", "no_verifier", "no_rerank")

def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()

def command(args: list[str]) -> str:
    try:
        return subprocess.check_output(args, cwd=ROOT, stderr=subprocess.DEVNULL, timeout=5, text=True).strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"

def build_manifest(cases: Sequence[Mapping[str, Any]], runtime: Mapping[str, Any], fake: bool) -> dict:
    paths = [p for folder in ("src", "config", "eval", "demo", "scripts") for p in (ROOT / folder).rglob("*") if p.is_file() and p.suffix in {".py", ".json", ".jsonl", ".sql", ".md"} and "__pycache__" not in p.parts and "runs" not in p.parts and p.name not in {"report.md", "report.jsonl", "local_keys_private.py"} and not p.name.endswith("_private.py")]
    paths += [ROOT / "requirements.lock.txt"]
    deps = {}
    for name in ("requests", "sqlglot", "chromadb", "sentence-transformers", "torch", "pandas"):
        try:
            deps[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            deps[name] = "not-installed"
    manifest = {"schema_version": 2, "scorer_version": VERSION, "fake": fake,
                "dataset_sha256": fingerprint(list(cases)), "git_commit": command(["git", "rev-parse", "HEAD"]),
                "source_files": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)},
                "runtime": dict(runtime), "dependencies": deps,
                "hardware": {"os": platform.platform(), "machine": platform.machine(), "processor": platform.processor(), "cpu_count": os.cpu_count(), "python": platform.python_version(), "gpu": "not-probed-in-fake-mode" if fake else command(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])}}
    manifest["fingerprint"] = fingerprint(manifest)
    manifest["created_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return manifest

class DeterministicLLM:
    """基础模型、工具调用、综合回答与各项消融采用相同温度参数。"""
    def __init__(self):
        from src.agent.llm_client import LLMClient
        self.client = LLMClient()
    def __getattr__(self, name):
        return getattr(self.client, name)
    def chat(self, *args, **kwargs):
        kwargs["temperature"] = 0.0
        return self.client.chat(*args, **kwargs)
    def chat_with_tools(self, *args, **kwargs):
        kwargs["temperature"] = 0.0
        return self.client.chat_with_tools(*args, **kwargs)
    def chat_stream(self, *args, **kwargs):
        kwargs["temperature"] = 0.0
        return self.client.chat_stream(*args, **kwargs)

class RealRuntime:
    """Historical tool-loop runtime retained only to reject obsolete callers."""
    def __init__(self, *args, **kwargs):
        raise RuntimeError("Historical live evaluation is retired; use eval/cloud_focused.py for cloud evaluation.")


class FakeLLMRuntime:
    """标准答案驱动的样例仅验证调用链连通，不能作为模型能力证据。"""
    def runtime_info(self) -> dict:
        return {"provider": "fake", "model": "fixture-only", "temperature": 0, "seed": 0}
    def run(self, case: Mapping[str, Any], mode: str) -> dict:
        expected, category = case["expected"], case["category"]
        result, tools, tool_results = {}, [], []
        if mode == "bare":
            answer = "没有证据，无法确认。"
        elif category in {"security", "out_of_scope"}:
            answer = "无法执行或确认该请求，证据不足。"
            if category == "security":
                tool_results = [{"tool": "query_database", "status": "rejected", "message": "Fixture guard event"}]
        elif category == "numeric":
            answer = "；".join(f"{v['stock_abbr']}{v['report_year']}年全年{v['metric_label']}为{v['value']}{v['unit']}。" for v in expected["values"])
        elif category == "chart":
            values, first = expected["values"], expected["values"][0]
            answer = f"已生成{first['stock_abbr']}全年趋势图。"
            result["chart_data_list"] = [{"title": f"{first['stock_abbr']}全年{first['metric_label']}", "x_data": [v["report_year"] for v in values], "y_data": [v["value"] for v in values], "y_label": first["metric_label"] + "（" + first["unit"] + "）", "data_source": {"y_field": first["field"], "report_period": "FY"}}]
        else:
            source = expected["sources"][0]
            answer = source["rationale"]
            result["answer"] = {"content": answer, "references": [{**source, "text": source["rationale"], "paper_path": source["source_path"]}]}
        if mode != "bare":
            tools = list(expected.get("tools") or [])
            result["verification"] = {"status": "disabled" if mode in {"no_verifier", "tool_baseline"} else "warn" if category in {"citation", "security", "out_of_scope"} else "pass"}
        return {"answer": answer, "result": result, "tools": tools, "tool_results": tool_results, "events": [], "terminal_status": "completed", "latency_seconds": 0.001}
