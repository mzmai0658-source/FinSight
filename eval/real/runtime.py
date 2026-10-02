"""作品说明：为真实财务 PDF 提供隔离本机运行环境，传输异常即时保留记录并停止。"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from eval.experiment import DeterministicLLM, RealRuntime, fingerprint

ROOT = Path(__file__).resolve().parents[2]
MODEL = "qwen3.5:9b-q4_K_M"


class ModelDisconnected(BaseException):
    """作品说明：绕过应用的兜底重试捕获，使异常在留证后终止本次运行。"""


def assert_local_isolation(config, chroma_path: str, endpoint: str) -> None:
    if config.database != "finsight_real_eval" or config.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Real PDF evaluation requires local finsight_real_eval")
    path = Path(chroma_path)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if path != (ROOT / "data/real_eval_chroma_db").resolve():
        raise RuntimeError("Real PDF evaluation requires data/real_eval_chroma_db")
    parsed = urlparse(endpoint)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.scheme != "http" or parsed.username or parsed.password:
        raise RuntimeError("Evaluation model endpoint must be local HTTP without embedded credentials")


def configure_environment(env_file: Path) -> None:
    from dotenv import dotenv_values
    if not env_file.is_file():
        raise FileNotFoundError(f"Real-evaluation environment file missing: {env_file}")
    # 作品说明：普通应用导入被 PYTHON_DOTENV_DISABLED 保护时，dotenv_values 仍读取显式指定的验证配置文件。
    os.environ.update({key: value for key, value in dotenv_values(env_file).items() if value is not None})
    os.environ.update(LLM_PROVIDER="ollama", OLLAMA_MODEL=MODEL, LLM_MODEL=MODEL,
                      OLLAMA_REASONING_EFFORT="low", EMBEDDING_PROVIDER="bge_local",
                      EMBEDDING_DEVICE="cpu", HF_HUB_OFFLINE="1")
    os.environ["LLM_BASE_URL"] = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    # 作品说明：后续配置模块不得再读取无关的应用 .env。
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"


class HTTPAudit:
    def __init__(self):
        self.calls: list[dict] = []
        self.original = None

    def __enter__(self):
        import requests
        from src.agent.llm_client import LLMClient
        self.original = LLMClient._build_session
        audit = self

        def build_session():
            session = audit.original()
            post = session.post

            def measured_post(url, **kwargs):
                started = time.monotonic()
                entry = {"stream": bool(kwargs.get("stream")), "status": None,
                         "request": kwargs.get("json", {})}
                audit.calls.append(entry)
                try:
                    response = post(url, **kwargs)
                    entry.update(status=response.status_code, response_headers_seconds=round(time.monotonic()-started, 3))
                    if not kwargs.get("stream"):
                        try:
                            body = response.json()
                            entry.update(response=body, usage=body.get("usage"),
                                         finish_reasons=[c.get("finish_reason") for c in body.get("choices", [])])
                            if response.status_code >= 400:
                                entry["error"] = body.get("error") or f"HTTP {response.status_code}"
                        except ValueError:
                            entry["error"] = f"Non-JSON HTTP {response.status_code}"
                    else:
                        original_iter = response.iter_lines

                        def measured_lines(*args, **line_kwargs):
                            try:
                                for line in original_iter(*args, **line_kwargs):
                                    yield line
                            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as exc:
                                entry["error"] = type(exc).__name__
                                entry["infrastructure_failure"] = True
                                raise ModelDisconnected(f"Model stream disconnected: {type(exc).__name__}") from exc
                        response.iter_lines = measured_lines
                    return response
                except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as exc:
                    entry["error"] = type(exc).__name__
                    entry["infrastructure_failure"] = True
                    raise ModelDisconnected(f"Local model transport failed: {type(exc).__name__}") from exc
            session.post = measured_post
            return session

        LLMClient._build_session = staticmethod(build_session)
        return self

    def __exit__(self, *args):
        from src.agent.llm_client import LLMClient
        LLMClient._build_session = staticmethod(self.original)


class PDFRuntime(RealRuntime):
    """作品说明：复用生产执行方法，但不使用虚构样例构造器。"""
    def __init__(self, documents: Sequence[Mapping[str, Any]], selected_documents: Sequence[Mapping[str, Any]]):
        from config.db_config import get_readonly_db_config
        from src.agent.domain import get_table_fields
        from src.agent.rag_tool import RAGTool
        from src.agent.sql_tool import SQLTool
        config = get_readonly_db_config()
        assert_local_isolation(config, os.environ.get("CHROMA_DB_PATH", ""), os.environ["LLM_BASE_URL"])
        self.llm, self.agents = DeterministicLLM(), {}
        if self.llm.model != MODEL or self.llm.reasoning_effort != "low" or self.llm.provider != "ollama":
            raise RuntimeError("Real-PDF runtime requires local Qwen3.5 9B with thinking enabled")
        identities = {(str(d["stock_code"]), int(d["report_year"]), d["report_period"]) for d in documents}
        selected_ids = {(str(d["stock_code"]), int(d["report_year"]), d["report_period"]) for d in selected_documents}
        snapshots, sql_ids, unexpected = {}, set(), []
        for table, fields in get_table_fields().items():
            query = f"SELECT {','.join(fields)} FROM {table} ORDER BY stock_code,report_year,report_period"
            output = SQLTool().run(query)
            if output.get("status") != "success":
                raise RuntimeError(f"Real SQL table unavailable: {table}: {output.get('message')}")
            snapshots[table] = output.get("rows") or []
            for row in snapshots[table]:
                identity = str(row["stock_code"]), int(row["report_year"]), row["report_period"]
                sql_ids.add(identity)
                if identity not in identities:
                    unexpected.append({"table": table, "identity": identity})
        self.database_snapshot_sha256 = fingerprint(snapshots)
        self.database_snapshot = snapshots
        collection = RAGTool()._get_collection()
        index_rows, index_ids, indexed_sources = [], set(), set()
        for offset in range(0, collection.count(), 500):
            batch = collection.get(offset=offset, limit=500, include=["documents", "metadatas", "embeddings"])
            embeddings = batch.get("embeddings")
            for i, key in enumerate(batch["ids"]):
                meta = (batch.get("metadatas") or [])[i] or {}
                identity = str(meta.get("stock_code")), int(meta.get("report_year") or 0), meta.get("report_period")
                index_ids.add(identity)
                indexed_sources.add(str(meta.get("source_sha256") or ""))
                if identity not in identities:
                    unexpected.append({"chunk_id": key, "identity": identity})
                vector = embeddings[i].tolist() if embeddings is not None and hasattr(embeddings[i], "tolist") else list(embeddings[i]) if embeddings is not None else None
                index_rows.append({"id": key, "document": batch["documents"][i], "metadata": meta, "embedding": vector})
        self.index_snapshot_sha256 = fingerprint(sorted(index_rows, key=lambda r: r["id"]))
        self.index_count = len(index_rows)
        required_hashes = {d["source_sha256"] for d in selected_documents}
        self.preflight = {"selected_report_identities": sorted(selected_ids),
                          "missing_sql_reports": sorted(selected_ids - sql_ids),
                          "missing_index_reports": sorted(selected_ids - index_ids),
                          "missing_index_pdf_hashes": sorted(required_hashes - indexed_sources),
                          "unexpected_reports": unexpected}
        if any(self.preflight[key] for key in ("missing_sql_reports", "missing_index_reports", "missing_index_pdf_hashes", "unexpected_reports")):
            raise RuntimeError("Real PDF preflight failed; no selected samples may be silently omitted: " + json.dumps(self.preflight, ensure_ascii=False))

    def runtime_info(self) -> dict:
        return {**super().runtime_info(), "embedding_device": "cpu", "max_retries": self.llm.max_retries,
                "timeout_seconds": self.llm.timeout, "index_snapshot_sha256": self.index_snapshot_sha256,
                "index_count": self.index_count, "preflight": self.preflight,
                "local_inference_only": True, "dataset_kind": "original_pdf_machine_candidate_labels"}

    def warmup(self) -> None:
        result = self.llm.chat([{"role": "user", "content": "Reply with exactly OK."}])
        if not result:
            raise RuntimeError("Model warmup returned no answer")
        info = self.runtime_info()
        context = (info.get("loaded_model") or {}).get("context_length")
        if context != info["num_ctx"]:
            raise RuntimeError(f"Loaded context {context} differs from configured context {info['num_ctx']}")

    def run(self, case: Mapping[str, Any], mode: str) -> dict:
        # 作品说明：流式回答途中断连时，保留已经发出的证据。
        events = []
        if mode not in {"bare", "tool_baseline"}:
            from src.agent.orchestrator import FinancialReportAgent
            if mode not in self.agents:
                self.agents[mode] = FinancialReportAgent(structured_planning=False, llm=self.llm, verification_enabled=mode != "no_verifier", rerank_enabled=mode != "no_rerank")
            agent = self.agents[mode]

            class EventRecorder:
                def run(self, *args, **kwargs):
                    for event in agent.run(*args, **kwargs):
                        events.append({"type": event.type, "data": event.data})
                        yield event

            self.agents[mode] = EventRecorder()
        try:
            return super().run(case, mode)
        except ModelDisconnected as exc:
            exc.partial_output = {"answer": "", "result": {}, "events": events,
                                  "tools": [e["data"].get("tool") for e in events if e["type"] == "tool_call"],
                                  "tool_results": [e["data"] for e in events if e["type"] == "tool_result"]}
            raise
        finally:
            if mode not in {"bare", "tool_baseline"}:
                self.agents[mode] = agent
