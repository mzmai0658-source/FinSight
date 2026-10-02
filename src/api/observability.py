"""作品说明：内部服务记录追踪信息与 Prometheus 指标。"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

_TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="-")


def _normalize_trace_id(trace_id: Optional[str]) -> str:
    value = str(trace_id or "").strip()
    return value[:64] if value else "-"


def current_trace_id() -> str:
    return _TRACE_ID.get()


@contextmanager
def trace_context(trace_id: Optional[str]) -> Iterator[None]:
    token = _TRACE_ID.set(_normalize_trace_id(trace_id))
    try:
        yield
    finally:
        _TRACE_ID.reset(token)


class ApiMetrics:
    """作品说明：用统一包装器封装监控指标。"""

    def __init__(self, registry: Optional[CollectorRegistry] = None):
        self.registry = registry or CollectorRegistry()
        self.internal_requests = Counter(
            "finsight_python_internal_requests_total",
            "Internal Python API requests.",
            ["endpoint", "status"],
            registry=self.registry,
        )
        self.internal_request_seconds = Histogram(
            "finsight_python_internal_request_seconds",
            "Internal Python API request duration.",
            ["endpoint", "status"],
            registry=self.registry,
        )
        self.agent_turn_seconds = Histogram(
            "finsight_python_agent_turn_seconds",
            "Agent turn duration.",
            ["status"],
            registry=self.registry,
        )
        self.etl_task_seconds = Histogram(
            "finsight_python_etl_task_seconds",
            "ETL task duration.",
            ["file_type", "status"],
            registry=self.registry,
        )
        self.llm_failures = Counter(
            "finsight_python_llm_failures_total",
            "LLM failures observed by the Python service.",
            ["operation"],
            registry=self.registry,
        )
        self.agent_tool_calls = Counter(
            "finsight_python_agent_tool_call_total",
            "Agent tool calls.",
            ["tool", "status"],
            registry=self.registry,
        )
        self.agent_tool_errors = Counter(
            "finsight_python_agent_tool_error_total",
            "Agent tool errors.",
            ["tool"],
            registry=self.registry,
        )
        self.agent_sql_rejected = Counter(
            "finsight_python_agent_sql_rejected_total",
            "SQL tool rejected unsafe or invalid SQL.",
            registry=self.registry,
        )
        self.agent_rag_empty = Counter(
            "finsight_python_agent_rag_empty_total",
            "RAG tool returned empty results.",
            registry=self.registry,
        )

    def record_request(self, endpoint: str, status: str, duration_seconds: float) -> None:
        labels = {"endpoint": endpoint, "status": self._status(status)}
        self.internal_requests.labels(**labels).inc()
        self.internal_request_seconds.labels(**labels).observe(max(float(duration_seconds), 0.0))

    def record_agent_turn(self, status: str, duration_seconds: float) -> None:
        self.agent_turn_seconds.labels(status=self._status(status)).observe(max(float(duration_seconds), 0.0))

    def record_etl_task(self, file_type: str, status: str, duration_seconds: float) -> None:
        self.etl_task_seconds.labels(file_type=file_type or "unknown", status=self._status(status)).observe(
            max(float(duration_seconds), 0.0)
        )

    def record_llm_failure(self, operation: str) -> None:
        self.llm_failures.labels(operation=operation or "unknown").inc()

    def record_agent_tool(self, tool: str, status: str) -> None:
        self.agent_tool_calls.labels(tool=tool or "unknown", status=self._status(status)).inc()

    def record_agent_tool_error(self, tool: str) -> None:
        self.agent_tool_errors.labels(tool=tool or "unknown").inc()

    def record_sql_rejected(self) -> None:
        self.agent_sql_rejected.inc()

    def record_rag_empty(self) -> None:
        self.agent_rag_empty.inc()

    def render(self) -> str:
        return generate_latest(self.registry).decode("utf-8")

    @staticmethod
    def _status(status: str) -> str:
        value = str(status or "").strip().lower()
        return value or "unknown"


metrics = ApiMetrics(registry=CollectorRegistry())


@contextmanager
def observe_request(endpoint: str) -> Iterator[None]:
    started_at = time.perf_counter()
    status = "success"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        metrics.record_request(endpoint, status, time.perf_counter() - started_at)
