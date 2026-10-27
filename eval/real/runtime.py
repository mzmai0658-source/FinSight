"""为真实财务 PDF 提供隔离数据环境与云端模型配置，传输异常即时保留记录并停止。"""
from __future__ import annotations

import os
import time
from pathlib import Path

from src.agent.providers import resolve_chat_provider

ROOT = Path(__file__).resolve().parents[2]


class ModelDisconnected(BaseException):
    """绕过应用的兜底重试捕获，使异常在留证后终止本次运行。"""


def assert_local_isolation(config, chroma_path: str, endpoint: str) -> None:
    if config.database != "finsight_real_eval" or config.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Real PDF evaluation requires local finsight_real_eval")
    path = Path(chroma_path)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if path != (ROOT / "data/real_eval_chroma_db").resolve():
        raise RuntimeError("Real PDF evaluation requires data/real_eval_chroma_db")
    try:
        resolve_chat_provider(api_url=endpoint)
    except ValueError as exc:
        raise RuntimeError("Evaluation model requires a valid HTTPS cloud configuration") from exc


def configure_environment(env_file: Path) -> None:
    from dotenv import dotenv_values
    if not env_file.is_file():
        raise FileNotFoundError(f"Real-evaluation environment file missing: {env_file}")
    # 普通应用导入被 PYTHON_DOTENV_DISABLED 保护时，dotenv_values 仍读取显式指定的验证配置文件。
    os.environ.update({key: value for key, value in dotenv_values(env_file).items() if value is not None})
    os.environ.update(EMBEDDING_PROVIDER="bge_local", EMBEDDING_DEVICE="cpu", HF_HUB_OFFLINE="1")
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    resolve_chat_provider()
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
                    raise ModelDisconnected(f"Cloud model transport failed: {type(exc).__name__}") from exc
            session.post = measured_post
            return session

        LLMClient._build_session = staticmethod(build_session)
        return self

    def __exit__(self, *args):
        from src.agent.llm_client import LLMClient
        LLMClient._build_session = staticmethod(self.original)


class PDFRuntime:
    """The old SQL/PDF live adapter does not establish canonical v3 readiness."""
    def __init__(self, *args, **kwargs):
        raise RuntimeError("Historical PDF live evaluation is retired; use eval/cloud_focused.py. Saved evidence remains available through --rescore.")
