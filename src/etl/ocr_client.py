"""作品说明：复用已校验缓存，支持同步与异步 OCR。PDF 与 JSON 摘要检查及旧缓存显式导入只能证明身份，不能证明识别准确。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from src.utils.ocr_json_parser import OCR_JSON_SUFFIXES, iter_layout_pages

OCR_MODEL_VERSION = "PaddleOCR-VL-1.6"


class OcrNotConfiguredError(RuntimeError):
    """作品说明：OCR 未配置且无缓存时返回明确错误。"""


class OcrInvalidResponseError(RuntimeError):
    """作品说明：版面不可用或 PDF 身份不匹配时阻断。"""


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_ocr_payload(payload: Any) -> None:
    """作品说明：拒绝把作业回执、错误响应或空版面当作 OCR 结果。"""
    if not isinstance(payload, (dict, list)):
        raise OcrInvalidResponseError("OCR response must contain layout pages")
    if isinstance(payload, dict) and (payload.get("error") or payload.get("errorCode") not in (None, 0, "0")):
        raise OcrInvalidResponseError("OCR service returned an error response")
    try:
        pages = list(iter_layout_pages(payload))
        if not pages or not all(isinstance(page, dict) for page in pages):
            raise ValueError("no layout pages")
        usable = False
        for page in pages:
            markdown = page.get("markdown") or {}
            pruned = page.get("prunedResult") or {}
            if not isinstance(markdown, dict) or not isinstance(pruned, dict):
                raise ValueError("invalid layout page")
            text = markdown.get("text") or ""
            blocks = pruned.get("parsing_res_list") or []
            if not isinstance(text, str) or not isinstance(blocks, list):
                raise ValueError("invalid page text/blocks")
            for block in blocks:
                if not isinstance(block, dict) or not isinstance(block.get("block_content", ""), str):
                    raise ValueError("invalid text block")
            usable = usable or bool(text.strip()) or any(block.get("block_content", "").strip() for block in blocks)
        if not usable:
            raise ValueError("empty page content")
    except (TypeError, AttributeError, ValueError) as exc:
        raise OcrInvalidResponseError(f"OCR response has no usable layout content: {exc}") from exc


def _atomic_write(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_validated_ocr_cache(
    pdf_path: str | Path, payload: Any, *, model: str = OCR_MODEL_VERSION,
    origin: str = "remote_ocr", expected_pdf_sha256: str | None = None,
    cache_path: str | Path | None = None,
) -> Path:
    """作品说明：先保存 JSON 再保存摘要；缓存对不完整时阻断使用。"""
    pdf_path = Path(pdf_path)
    validate_ocr_payload(payload)
    pdf_hash = _sha256(pdf_path)
    if expected_pdf_sha256 and pdf_hash != expected_pdf_sha256:
        raise OcrInvalidResponseError("PDF changed while OCR was running")
    if model not in {"PaddleOCR-VL-1.5", "PaddleOCR-VL-1.6"}:
        raise ValueError("OCR_MODEL must be PaddleOCR-VL-1.5 or PaddleOCR-VL-1.6")
    target = Path(cache_path) if cache_path else Path(f"{pdf_path}_by_{model}.json")
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    metadata = {
        "schema_version": 1, "pdf_sha256": pdf_hash,
        "cache_sha256": hashlib.sha256(encoded).hexdigest(), "model": model,
        "origin": origin, "created_at": datetime.now(timezone.utc).isoformat(),
        "content_review_status": "not_reviewed",
    }
    pending = Path(str(target) + ".publishing")
    _atomic_write(pending, b"OCR cache publication in progress")
    _atomic_write(target, encoded)
    _atomic_write(Path(str(target) + ".meta.json"), json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
    pending.unlink()
    return target


def _read_valid_cache(pdf_path: Path, cache_path: Path, pdf_hash: str) -> tuple[Any, dict] | None:
    if not cache_path.is_file():
        return None
    try:
        if Path(str(cache_path) + ".publishing").exists():
            raise OcrInvalidResponseError("OCR cache publication was interrupted")
        encoded = cache_path.read_bytes()
        payload = json.loads(encoded)
        validate_ocr_payload(payload)
        metadata_path = Path(str(cache_path) + ".meta.json")
        if metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if (metadata.get("schema_version") != 1 or metadata.get("pdf_sha256") != pdf_hash
                    or metadata.get("cache_sha256") != hashlib.sha256(encoded).hexdigest()):
                raise OcrInvalidResponseError("OCR cache/PDF content hash mismatch")
        elif _enabled("OCR_CACHE_ALLOW_LEGACY"):
            model = "PaddleOCR-VL-1.5" if "VL-1.5" in cache_path.name else OCR_MODEL_VERSION
            metadata = {"model": model, "origin": "legacy_import"}
            logger.warning("[ocr] explicit legacy_import (generation-time PDF binding unknown): {}", cache_path.name)
        else:
            raise OcrInvalidResponseError("OCR cache lacks PDF hash metadata; explicit legacy import required")
        return payload, metadata
    except (OSError, ValueError, TypeError, AttributeError, OcrInvalidResponseError) as exc:
        logger.warning("[ocr] cache rejected {}: {}", cache_path.name, exc)
        return None


def ensure_ocr_json(pdf_path: str | Path, *, force: bool = False) -> Path:
    """作品说明：强制无缓存模式跳过工作区缓存。"""
    pdf_path = Path(pdf_path)
    pdf_hash = _sha256(pdf_path)
    if not (force or _enabled("OCR_DISABLE_CACHE")):
        for suffix in OCR_JSON_SUFFIXES:
            cached = Path(str(pdf_path) + suffix)
            match = _read_valid_cache(pdf_path, cached, pdf_hash)
            if match:
                payload, metadata = match
                if not Path(str(cached) + ".meta.json").exists():
                    return write_validated_ocr_cache(pdf_path, payload, model=metadata["model"],
                                                     origin="legacy_import", expected_pdf_sha256=pdf_hash,
                                                     cache_path=cached)
                logger.info("[ocr] valid local cache hit: {}", cached.name)
                return cached
        workspace_hit = _find_workspace_cache(pdf_path)
        if workspace_hit:
            match = _read_valid_cache(pdf_path, workspace_hit, pdf_hash)
            if match:
                payload, metadata = match
                target = write_validated_ocr_cache(pdf_path, payload, model=metadata["model"],
                                                   origin=metadata.get("origin", "workspace_reuse"),
                                                   expected_pdf_sha256=pdf_hash)
                logger.info("[ocr] workspace cache materialized beside upload: {}", target.name)
                return target
    api_url = (os.getenv("OCR_API_URL") or "").strip()
    if not api_url:
        raise OcrNotConfiguredError(f"PDF has no valid OCR cache and OCR_API_URL is unset: {pdf_path.name}")
    return _call_remote_ocr(pdf_path, api_url)


def _find_workspace_cache(pdf_path: Path) -> Path | None:
    from src.utils.data_paths import find_financial_reports_root, find_research_reports_root
    pdf_hash = _sha256(pdf_path)
    for root in [find_financial_reports_root(), find_research_reports_root()]:
        if not root or not Path(root).is_dir():
            continue
        for suffix in OCR_JSON_SUFFIXES:
            for hit in Path(root).rglob(pdf_path.name + suffix):
                original = Path(str(hit)[:-len(suffix)])
                if original.is_file() and _sha256(original) == pdf_hash and _read_valid_cache(original, hit, pdf_hash):
                    return hit
    return None


def _call_remote_ocr(pdf_path: Path, api_url: str) -> Path:
    import requests
    model = os.getenv("OCR_MODEL", OCR_MODEL_VERSION).strip()
    if model not in {"PaddleOCR-VL-1.5", "PaddleOCR-VL-1.6"}:
        raise ValueError("OCR_MODEL must be PaddleOCR-VL-1.5 or PaddleOCR-VL-1.6")
    timeout = int(os.getenv("OCR_TIMEOUT_SECONDS", "600"))
    protocol = os.getenv("OCR_API_PROTOCOL", "multipart").strip().lower()
    token = os.getenv("PADDLEOCR_API_TOKEN") or os.getenv("OCR_API_TOKEN", "")
    pdf_hash = _sha256(pdf_path)
    logger.info("[ocr] remote OCR request model={} protocol={} file={}", model, protocol, pdf_path.name)
    if protocol == "paddle_async":
        from src.utils.ocr_async_json import submit_async_ocr_job, poll_async_ocr_job, download_async_ocr_json
        if not token:
            raise OcrNotConfiguredError("paddle_async OCR requires PADDLEOCR_API_TOKEN or OCR_API_TOKEN")
        job_id = submit_async_ocr_job(str(pdf_path), model=model, job_url=api_url, token=token,
                                      timeout=min(timeout, 120))
        job_result = poll_async_ocr_job(job_id, job_url=api_url, token=token, timeout=timeout,
                                        poll_interval=float(os.getenv("OCR_POLL_INTERVAL_SECONDS", "5")))
        payload = download_async_ocr_json(job_result)
    elif protocol == "multipart":
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with pdf_path.open("rb") as stream:
            response = requests.post(api_url, files={"file": (pdf_path.name, stream, "application/pdf")},
                                     data={"model": model}, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    else:
        raise ValueError("OCR_API_PROTOCOL must be multipart or paddle_async")
    return write_validated_ocr_cache(pdf_path, payload, model=model, expected_pdf_sha256=pdf_hash)
