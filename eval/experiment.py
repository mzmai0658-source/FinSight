"""作品说明：记录公开基准的运行环境及可复现元数据。"""
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
    paths = [p for folder in ("src", "config", "eval", "demo", "scripts") for p in (ROOT / folder).rglob("*") if p.is_file() and p.suffix in {".py", ".json", ".jsonl", ".sql", ".md"} and "__pycache__" not in p.parts and "runs" not in p.parts and p.name not in {"report.md", "report.jsonl"}]
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
    """作品说明：基础模型、工具调用、综合回答与各项消融采用相同温度参数。"""
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
    def __init__(self):
        self.llm, self.agents = DeterministicLLM(), {}
        from config.db_config import get_readonly_db_config
        from src.agent.sql_tool import SQLTool
        config = get_readonly_db_config()
        if config.database != "finsight_demo" or Path(os.getenv("CHROMA_DB_PATH", "")).as_posix() not in {"data/demo_chroma_db", (ROOT / "data/demo_chroma_db").as_posix()}:
            raise RuntimeError("Public evaluation requires .env.demo with isolated finsight_demo and data/demo_chroma_db")
        facts = json.loads((ROOT / "demo/financial_facts.json").read_text(encoding="utf-8"))["facts"]
        snapshots = {}
        for table in sorted({f["table"] for f in facts}):
            fields = sorted({f["field"] for f in facts if f["table"] == table})
            query = "SELECT stock_code,stock_abbr,report_year,report_period," + ",".join(fields) + f" FROM {table} ORDER BY stock_code,report_year,report_period"
            output = SQLTool().run(query)
            if output.get("status") != "success":
                raise RuntimeError(f"Public SQL fixture unavailable: {table}")
            rows = [{k:row[k] for k in ("stock_code", "stock_abbr", "report_year", "report_period", *fields)} for row in output["rows"]]
            if len(rows) != 15:
                raise RuntimeError(f"Expected 15 synthetic company-period rows in {table}")
            for fact in (f for f in facts if f["table"] == table):
                row = next((r for r in rows if all(str(r[k]) == str(fact[k]) for k in ("stock_code", "report_year", "report_period"))), None)
                if not row or row["stock_abbr"] != fact["stock_abbr"] or abs(float(row[fact["field"]])-float(fact["value"])) > .00001:
                    raise RuntimeError(f"Public fixture content changed: {fact['fact_id']}")
            snapshots[table] = rows
        self.database_snapshot_sha256 = fingerprint(snapshots)

    def runtime_info(self) -> dict:
        config = self.llm.config
        info = {"provider": config.provider, "model": self.llm.model, "num_ctx": config.num_ctx, "max_tokens": config.max_tokens,
                "seed": None, "seed_note": "provider seed not configured", "temperature": 0.0,
                "reasoning_effort": self.llm.reasoning_effort or "server_default",
                "embedding_model": os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"),
                "db_name": os.getenv("SQL_DB_NAME") or os.getenv("DB_NAME"), "chroma_path": os.getenv("CHROMA_DB_PATH"), "model_digest": "unavailable"}
        info["database_snapshot_sha256"] = self.database_snapshot_sha256
        if config.provider == "ollama":
            import requests
            try:
                base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/").removesuffix("/v1")
                models = requests.get(base + "/api/tags", timeout=5).json().get("models", [])
                info["model_digest"] = next((m["digest"] for m in models if m.get("name") == self.llm.model), "unavailable")
                info["ollama_version"] = requests.get(base + "/api/version", timeout=5).json().get("version")
                details = requests.post(base + "/api/show", json={"model": self.llm.model}, timeout=10).json()
                info["model_details"] = details.get("details", {})
                info["model_default_parameters"] = details.get("parameters", "")
                loaded = requests.get(base + "/api/ps", timeout=5).json().get("models", [])
                info["loaded_model"] = next((m for m in loaded if m.get("name") == self.llm.model), None)
            except (requests.RequestException, ValueError):
                pass
        return info

    def run(self, case: Mapping[str, Any], mode: str) -> dict:
        if mode in {"bare", "tool_baseline"}:
            return self.run_baseline(case, mode)
        from src.agent.orchestrator import FinancialReportAgent
        if mode not in self.agents:
            self.agents[mode] = FinancialReportAgent(structured_planning=False, llm=self.llm, verification_enabled=mode != "no_verifier", rerank_enabled=mode != "no_rerank")
        started, events, result, terminal = time.monotonic(), [], {}, "incomplete"
        for event in self.agents[mode].run(str(case["question"]), chart_prefix=f"eval_{mode}_{case['id']}"):
            events.append({"type": event.type, "data": event.data})
            if event.type == "done":
                result, terminal = dict(event.data.get("result") or {}), "completed"
            elif event.type == "error":
                terminal = "error"
        return {"answer": str((result.get("answer") or {}).get("content") or ""), "result": result, "events": events,
                "tools": [e["data"].get("tool") for e in events if e["type"] == "tool_call"],
                "tool_results": [e["data"] for e in events if e["type"] == "tool_result"],
                "terminal_status": terminal, "latency_seconds": round(time.monotonic()-started, 3)}

    def run_baseline(self, case: Mapping[str, Any], mode: str) -> dict:
        """作品说明：数据、工具和模型保持一致，采用有界循环，不引入规则兜底或核验器。"""
        from src.agent.prompts import TOOL_SCHEMAS, build_system_prompt, SYNTHESIS_INSTRUCTION
        from src.agent.sql_tool import SQLTool
        from src.agent.rag_tool import RAGTool
        from src.agent.chart_tool import ChartTool
        started = time.monotonic()
        messages = [{"role": "system", "content": build_system_prompt() if mode == "tool_baseline" else "用中文回答财报问题。不知道时明确说明，不编造数字或来源。"}, {"role": "user", "content": str(case["question"])}]
        results, events, refs, charts, answer = [], [], [], [], ""
        if mode == "bare":
            answer = self.llm.chat(messages) or ""
        else:
            rag = RAGTool(rerank_enabled=False)
            for _ in range(6):
                message = self.llm.chat_with_tools(messages, tools=TOOL_SCHEMAS)
                if not message:
                    raise RuntimeError("No model response")
                messages.append({"role": "assistant", **message})
                calls = message.get("tool_calls") or []
                if not calls:
                    answer = self.llm.chat(messages + [{"role": "user", "content": SYNTHESIS_INSTRUCTION}]) or ""
                    break
                for call in calls:
                    function = call.get("function") or {}
                    name, args = function.get("name"), json.loads(function.get("arguments") or "{}")
                    events.append({"type": "tool_call", "data": {"tool": name, "arguments": args}})
                    if name == "query_database":
                        output = SQLTool().run(args.get("sql", ""))
                    elif name == "search_documents":
                        output = rag.run(args.get("query") or args.get("question") or case["question"], top_k=int(args.get("top_k", 5)))
                        refs.extend(output.get("results") or [])
                    elif name == "render_chart":
                        allowed = {k:v for k,v in args.items() if k in {"chart_type", "title", "x_data", "y_data", "x_label", "y_label", "series_name"}}
                        output = ChartTool().run(**allowed)
                        if output.get("chart_data"):
                            charts.append(output["chart_data"])
                    else:
                        output = {"status": "rejected", "message": "Unsupported baseline tool"}
                    event = {"tool": name, **output}
                    results.append(event)
                    events.append({"type": "tool_result", "data": event})
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(output, ensure_ascii=False, default=str)})
        return {"answer": answer, "result": {"answer": {"content": answer, "references": refs}, "chart_data_list": charts}, "events": events,
                "tool_results": results, "tools": [r["tool"] for r in results], "terminal_status": "completed" if answer else "incomplete", "latency_seconds": round(time.monotonic()-started, 3)}

class FakeLLMRuntime:
    """作品说明：标准答案驱动的样例仅验证调用链连通，不能作为模型能力证据。"""
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
