"""作品说明：验证 OCR 传输与缓存故障；构造样例不等于远程 OCR 验收。"""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from src.etl import ocr_client as ocr
from src.utils import ocr_async_json as async_ocr


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in ("OCR_API_URL", "OCR_API_PROTOCOL", "OCR_MODEL", "OCR_DISABLE_CACHE", "OCR_CACHE_ALLOW_LEGACY", "OCR_API_TOKEN", "PADDLEOCR_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    import src.utils.data_paths as paths
    monkeypatch.setattr(paths, "find_financial_reports_root", lambda: None)
    monkeypatch.setattr(paths, "find_research_reports_root", lambda: None)


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "600000_2024.pdf"
    path.write_bytes(b"%PDF-1.4 financial source one")
    return path


@pytest.fixture
def payload():
    return [{"markdown": {"text": "营业收入 1,234 万元"}}]


def test_valid_cache_is_offline_and_content_bound(pdf, payload, monkeypatch):
    cache = ocr.write_validated_ocr_cache(pdf, payload)
    remote = Mock(side_effect=AssertionError("network must not be called"))
    monkeypatch.setattr(ocr, "_call_remote_ocr", remote)
    assert ocr.ensure_ocr_json(pdf) == cache
    metadata = json.loads(Path(str(cache) + ".meta.json").read_text(encoding="utf-8"))
    assert metadata["pdf_sha256"] == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert metadata["content_review_status"] == "not_reviewed"
    remote.assert_not_called()


@pytest.mark.parametrize("changed", ["pdf", "cache"])
def test_hash_mismatch_never_reused_even_with_legacy_enabled(pdf, payload, monkeypatch, changed):
    cache = ocr.write_validated_ocr_cache(pdf, payload)
    monkeypatch.setenv("OCR_CACHE_ALLOW_LEGACY", "true")
    if changed == "pdf":
        pdf.write_bytes(b"%PDF-1.4 financial source two")
    else:
        cache.write_text(json.dumps([{"markdown": {"text": "wrong company"}}]), encoding="utf-8")
    with pytest.raises(ocr.OcrNotConfiguredError):
        ocr.ensure_ocr_json(pdf)


def test_legacy_import_is_explicit_and_never_claims_review(pdf, payload, monkeypatch):
    cache = Path(str(pdf) + "_by_PaddleOCR-VL-1.5.json")
    cache.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ocr.OcrNotConfiguredError):
        ocr.ensure_ocr_json(pdf)
    monkeypatch.setenv("OCR_CACHE_ALLOW_LEGACY", "true")
    assert ocr.ensure_ocr_json(pdf) == cache
    metadata = json.loads(Path(str(cache) + ".meta.json").read_text(encoding="utf-8"))
    assert metadata["origin"] == "legacy_import"
    assert metadata["content_review_status"] == "not_reviewed"


def test_workspace_cache_materialized_next_to_uploaded_pdf(pdf, payload, tmp_path, monkeypatch):
    import src.utils.data_paths as paths
    root = tmp_path / "reports"
    root.mkdir()
    original = root / pdf.name
    original.write_bytes(pdf.read_bytes())
    source_cache = ocr.write_validated_ocr_cache(original, payload, model="PaddleOCR-VL-1.5")
    monkeypatch.setattr(paths, "find_financial_reports_root", lambda: root)
    reused = ocr.ensure_ocr_json(pdf)
    assert reused.parent == pdf.parent and reused != source_cache
    assert json.loads(reused.read_text(encoding="utf-8")) == payload
    assert Path(str(reused) + ".meta.json").is_file()


@pytest.mark.parametrize("use_env", [False, True])
def test_forced_run_bypasses_all_cache_locations(pdf, payload, monkeypatch, use_env):
    ocr.write_validated_ocr_cache(pdf, payload)
    monkeypatch.setenv("OCR_API_URL", "http://localhost/ocr")
    if use_env:
        monkeypatch.setenv("OCR_DISABLE_CACHE", "true")
    remote = Mock(return_value=Path("result.json"))
    monkeypatch.setattr(ocr, "_call_remote_ocr", remote)
    assert ocr.ensure_ocr_json(pdf, force=not use_env) == Path("result.json")
    remote.assert_called_once()


@pytest.mark.parametrize("bad", [{}, [], {"error": "failure"}, {"data": {"jobId": "123"}}, {"markdown": {"text": ""}}, {"markdown": "bad"}, {"prunedResult": {"parsing_res_list": [1]}}])
def test_invalid_response_never_published(pdf, monkeypatch, bad):
    monkeypatch.setenv("OCR_API_URL", "http://localhost/ocr")
    response = Mock()
    response.json.return_value = bad
    monkeypatch.setattr(requests, "post", Mock(return_value=response))
    with pytest.raises(ocr.OcrInvalidResponseError):
        ocr.ensure_ocr_json(pdf)
    assert not list(pdf.parent.glob("*.json"))


def test_remote_disconnect_does_not_create_empty_cache(pdf, monkeypatch):
    monkeypatch.setenv("OCR_API_URL", "http://localhost/ocr")
    monkeypatch.setattr(requests, "post", Mock(side_effect=requests.ConnectionError("offline")))
    with pytest.raises(requests.ConnectionError):
        ocr.ensure_ocr_json(pdf)
    assert not list(pdf.parent.glob("*.json"))


def test_pdf_changed_during_request_rejected(pdf, payload, monkeypatch):
    monkeypatch.setenv("OCR_API_URL", "http://localhost/ocr")
    def post(*args, **kwargs):
        pdf.write_bytes(b"different version")
        response = Mock()
        response.json.return_value = payload
        return response
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(ocr.OcrInvalidResponseError, match="changed"):
        ocr.ensure_ocr_json(pdf)
    assert not list(pdf.parent.glob("*.json"))


def test_interrupted_metadata_publication_is_not_legacy_imported(pdf, payload, monkeypatch):
    original = ocr._atomic_write
    def write(path, data):
        if str(path).endswith(".meta.json"):
            raise OSError("disk full")
        original(path, data)
    monkeypatch.setattr(ocr, "_atomic_write", write)
    with pytest.raises(OSError):
        ocr.write_validated_ocr_cache(pdf, payload)
    monkeypatch.setenv("OCR_CACHE_ALLOW_LEGACY", "true")
    with pytest.raises(ocr.OcrNotConfiguredError):
        ocr.ensure_ocr_json(pdf)


@pytest.mark.parametrize('model', ['PaddleOCR-VL-1.5', 'PaddleOCR-VL-1.6'])
def test_async_submit_poll_download_contract_uses_runtime_settings(pdf, payload, monkeypatch, model):
    monkeypatch.setenv("OCR_API_URL", "https://ocr.example/jobs")
    monkeypatch.setenv("OCR_API_PROTOCOL", "paddle_async")
    monkeypatch.setenv("OCR_MODEL", model)
    monkeypatch.setenv("PADDLEOCR_API_TOKEN", "fixture-only-token")
    submit = Mock(status_code=202)
    submit.json.return_value = {"data": {"jobId": "job-1"}}
    pending = Mock(status_code=200)
    pending.json.return_value = {"data": {"state": "pending"}}
    done = Mock(status_code=200)
    done.json.return_value = {"data": {"state": "done", "resultUrl": {"jsonUrl": "https://ocr.example/result"}}}
    download = Mock(status_code=200, text=json.dumps(payload))
    post = Mock(return_value=submit)
    get = Mock(side_effect=[pending, done, download])
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(async_ocr.time, "sleep", lambda delay: None)
    cache = ocr.ensure_ocr_json(pdf)
    assert cache.name.endswith(f"_by_{model}.json")
    assert post.call_args.args[0] == "https://ocr.example/jobs"
    assert post.call_args.kwargs["headers"]["Authorization"] == "bearer fixture-only-token"
    assert post.call_args.kwargs["data"]["model"] == model
    assert get.call_args_list[0].args[0] == "https://ocr.example/jobs/job-1"
    assert "headers" not in get.call_args_list[-1].kwargs  # 作品说明：OCR 认证令牌不得发送给结果下载主机。


@pytest.mark.parametrize("state", ["failed", "unknown", None])
def test_async_failed_or_unknown_state_fails_immediately(monkeypatch, state):
    response = Mock(status_code=200)
    response.json.return_value = {"data": {"state": state}}
    monkeypatch.setattr(requests, "get", Mock(return_value=response))
    with pytest.raises(RuntimeError):
        async_ocr.poll_async_ocr_job("job-1", job_url="https://ocr.example/jobs", token="fixture")


def test_async_download_accepts_pretty_json_object(monkeypatch, payload):
    monkeypatch.setattr(requests, "get", Mock(return_value=Mock(status_code=200, text=json.dumps(payload[0], indent=2))))
    assert async_ocr.download_async_ocr_json({"resultUrl": {"jsonUrl": "https://ocr.example/result"}}) == payload[0]


def test_poll_recovers_connection_reset_without_resubmitting(monkeypatch):
    done = Mock(status_code=200)
    done.json.return_value = {'data': {'state': 'done'}}
    get = Mock(side_effect=[requests.ConnectionError('reset'), Mock(status_code=503), done])
    post = Mock()
    monkeypatch.setattr(requests, 'get', get)
    monkeypatch.setattr(requests, 'post', post)
    monkeypatch.setattr(async_ocr.time, 'sleep', lambda delay: None)
    assert async_ocr.poll_async_ocr_job('same-job', job_url='https://ocr.example/jobs', token='fixture')['state'] == 'done'
    assert all(call.args[0].endswith('/same-job') for call in get.call_args_list)
    post.assert_not_called()
