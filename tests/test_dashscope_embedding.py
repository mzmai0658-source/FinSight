"""Cloud embedding contracts use an HTTP transport and never contact a paid API."""

import inspect
import json
import os
import sys
import threading
import types
from concurrent.futures import CancelledError

import httpx
import pytest

from src.agent.dashscope_embedding import DashScopeEmbeddingFunction


ENDPOINT = "https://account.cn-beijing.maas.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"


@pytest.fixture(autouse=True)
def cloud_env(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("DASHSCOPE_") or name.startswith("EMBEDDING_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "env-test-key")
    monkeypatch.setenv("DASHSCOPE_EMBEDDING_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("DASHSCOPE_MAX_RETRIES", "0")
    # Guard the old SDK path while reproducing the zero-request bug.
    def forbidden_sdk_call(**kwargs):
        pytest.fail("The legacy SDK must not make embedding requests")

    monkeypatch.setitem(sys.modules, "dashscope", types.SimpleNamespace(
        TextEmbedding=types.SimpleNamespace(call=forbidden_sdk_call)))
    monkeypatch.setitem(sys.modules, "config.local_keys_private", types.SimpleNamespace(
        DASHSCOPE_API_KEY="code-secret-must-not-be-used"))


def success(texts):
    return {
        "status_code": 200,
        "request_id": "offline-test",
        "code": "",
        "message": "",
        "output": {"embeddings": [
            {"text_index": index, "embedding": [float(text.split("-")[-1])] * 1024}
            for index, text in reversed(list(enumerate(texts)))
        ]},
        "usage": {"total_tokens": len(texts)},
    }


@pytest.fixture
def capture_http(monkeypatch):
    requests = []
    clients = []
    client_class = httpx.Client

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=success(json.loads(request.content)["input"]["texts"]))

    def client_factory(**kwargs):
        client = client_class(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", client_factory)
    yield requests
    for client in clients:
        client.close()


def test_zero_retries_sends_initial_http_request_and_sorts_vectors(capture_http):
    ef = DashScopeEmbeddingFunction(api_key="explicit-test-key")
    vectors = ef(["document-0", "document-1"])

    assert [vector[0] for vector in vectors] == [0.0, 1.0]
    assert len(capture_http) == 1
    request = capture_http[0]
    assert str(request.url) == ENDPOINT
    assert request.headers["Authorization"] == "Bearer explicit-test-key"
    assert json.loads(request.content) == {
        "model": "text-embedding-v4",
        "input": {"texts": ["document-0", "document-1"]},
        "parameters": {"dimension": 1024, "text_type": "document", "output_type": "dense"},
    }


def test_document_batches_never_exceed_ten_and_keep_global_order(capture_http):
    vectors = DashScopeEmbeddingFunction(api_key="test-key").embed_documents(
        [f"document-{index}" for index in range(23)])

    assert [len(json.loads(request.content)["input"]["texts"]) for request in capture_http] == [10, 10, 3]
    assert [vector[0] for vector in vectors] == list(range(23))


def test_query_uses_query_convention_and_preserves_scalar_api(capture_http):
    vector = DashScopeEmbeddingFunction(api_key="test-key").embed_query("query-7")

    assert vector == [7.0] * 1024
    assert json.loads(capture_http[0].content)["parameters"]["text_type"] == "query"


def test_credentials_use_private_environment_instead_of_importing_code():
    ef = DashScopeEmbeddingFunction()

    assert ef.api_key == "env-test-key"


def test_default_fingerprint_records_space_and_excludes_account_config():
    ef = DashScopeEmbeddingFunction(api_key="secret-test-key")

    assert ef.fingerprint == (
        "dashscope:text-embedding-v4:dim=1024:algorithm=dashscope-dense-v1:document=document:query=query")
    assert ef.dimensions == ef.dimension == 1024
    assert ef.max_batch_size == 10
    assert ef.max_input_tokens == ef.max_input_bytes == 8192
    assert "secret" not in ef.fingerprint
    assert "account" not in ef.fingerprint
    assert list(inspect.signature(type(ef).__call__).parameters) == ["self", "input"]


def test_chroma_config_roundtrip_never_serializes_credentials_or_endpoint(monkeypatch):
    ef = DashScopeEmbeddingFunction(api_key="secret-test-key")
    config = ef.get_config()
    assert "secret" not in json.dumps(config)
    assert "account" not in json.dumps(config)
    assert ef.is_legacy() is False
    assert ef.default_space() == "cosine"
    assert ef.supported_spaces() == ["cosine", "l2", "ip"]
    assert DashScopeEmbeddingFunction.name() == "dashscope"
    monkeypatch.setenv("DASHSCOPE_API_KEY", "new-env-key")
    rebuilt = DashScopeEmbeddingFunction.build_from_config(config)
    assert rebuilt.fingerprint == ef.fingerprint
    assert rebuilt.api_key == "new-env-key"


def test_chroma_config_cannot_restore_an_incompatible_algorithm():
    with pytest.raises(ValueError, match="algorithm|configuration"):
        DashScopeEmbeddingFunction.build_from_config({"model_name": "text-embedding-v4", "algorithm": "old"})


def test_chroma_sequence_queries_preserve_every_query(capture_http):
    vectors = DashScopeEmbeddingFunction().embed_query(input=["query-3", "query-8"])

    assert [vector[0] for vector in vectors] == [3.0, 8.0]
    assert json.loads(capture_http[0].content)["parameters"]["text_type"] == "query"


@pytest.mark.filterwarnings("error:legacy embedding function config")
def test_real_chroma_document_and_query_paths_use_same_validated_cloud_space(capture_http, tmp_path):
    import chromadb
    from chromadb.api.types import validate_embedding_function
    from chromadb.config import Settings

    ef = DashScopeEmbeddingFunction()
    validate_embedding_function(ef)
    client = chromadb.PersistentClient(path=str(tmp_path / "cloud-chroma"),
                                       settings=Settings(anonymized_telemetry=False))
    collection = client.create_collection("cloud_embedding_contract", embedding_function=ef,
                                          configuration={"hnsw": {"space": "l2"}})
    try:
        collection.add(ids=["a", "b"], documents=["document-1", "document-2"])
        result = collection.query(query_texts=["query-1"], n_results=1)

        assert result["ids"] == [["a"]]
        assert [json.loads(request.content)["parameters"]["text_type"] for request in capture_http] == [
            "document", "query"]
    finally:
        client.delete_collection(collection.name)


@pytest.mark.parametrize("method,value", [("__call__", []), ("embed_documents", []), ("embed_query", "")])
def test_empty_inputs_do_not_make_paid_requests(capture_http, method, value):
    assert getattr(DashScopeEmbeddingFunction(), method)(value) == []
    assert capture_http == []


@pytest.mark.parametrize("value", [["valid-1", ""], ["valid-1", None], [3], [["nested-1"]]])
def test_invalid_document_rows_fail_before_request_instead_of_shifting_alignment(capture_http, value):
    with pytest.raises(ValueError, match="text|document"):
        DashScopeEmbeddingFunction().embed_documents(value)
    assert capture_http == []


def test_all_texts_are_length_checked_before_first_batch_is_sent(capture_http):
    texts = [f"document-{index}" for index in range(10)] + ["界" * 2731]
    with pytest.raises(ValueError, match="8192"):
        DashScopeEmbeddingFunction().embed_documents(texts)
    assert capture_http == []


def test_utf8_length_bound_accepts_boundary_text_without_estimating_words(monkeypatch):
    seen = []

    def handler(request):
        seen.extend(json.loads(request.content)["input"]["texts"])
        return httpx.Response(200, json=success(["document-1"]))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        vectors = DashScopeEmbeddingFunction(client=client).embed_documents(["a" * 8192])
    assert seen == ["a" * 8192]
    assert len(vectors[0]) == 1024


def test_missing_environment_key_never_uses_code_file_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        DashScopeEmbeddingFunction()


def test_explicit_empty_key_does_not_fall_back_to_environment():
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        DashScopeEmbeddingFunction(api_key="")


def test_explicit_account_endpoint_is_required(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_EMBEDDING_ENDPOINT")
    with pytest.raises(RuntimeError, match="DASHSCOPE_HTTP_BASE_URL|DASHSCOPE_EMBEDDING_ENDPOINT"):
        DashScopeEmbeddingFunction()


@pytest.mark.parametrize("endpoint", [
    "http://account.example/api/v1", "https://user:password@account.example/api/v1",
    "https://account.example/api/v1?api_key=secret", "https://account.example/api/v1#secret",
    "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1", "https://%7BWorkspaceId%7D.example/api/v1",
    "not-an-endpoint",
])
def test_unsafe_or_placeholder_endpoints_are_rejected_without_echoing_them(endpoint, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_EMBEDDING_ENDPOINT", endpoint)
    with pytest.raises(ValueError, match="HTTPS|endpoint|端点") as excinfo:
        DashScopeEmbeddingFunction()
    assert endpoint not in str(excinfo.value)
    assert "secret" not in str(excinfo.value)


@pytest.mark.parametrize("base_url", ["https://account.example", "https://account.example/api/v1/"])
def test_account_host_or_api_base_builds_official_endpoint(base_url, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_EMBEDDING_ENDPOINT")
    monkeypatch.setenv("DASHSCOPE_HTTP_BASE_URL", base_url)
    assert DashScopeEmbeddingFunction().endpoint == (
        "https://account.example/api/v1/services/embeddings/text-embedding/text-embedding")


@pytest.mark.parametrize("name,value", [
    ("DASHSCOPE_TIMEOUT_SECONDS", "0"), ("DASHSCOPE_TIMEOUT_SECONDS", "61"),
    ("DASHSCOPE_TIMEOUT_SECONDS", "nan"), ("DASHSCOPE_MAX_RETRIES", "-1"),
    ("DASHSCOPE_MAX_RETRIES", "3"), ("DASHSCOPE_MAX_RETRIES", "1.1"),
    ("DASHSCOPE_RETRY_BACKOFF_SECONDS", "nan"), ("DASHSCOPE_RETRY_BACKOFF_SECONDS", "6"),
    ("EMBEDDING_DIMENSION", "768"),
])
def test_request_budget_and_approved_dimension_are_validated(name, value, monkeypatch):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        DashScopeEmbeddingFunction()


@pytest.mark.parametrize("embeddings,reason", [
    ([], "cardinality"),
    ([{"text_index": 0, "embedding": [0.0] * 1024}] * 2, "indices"),
    ([{"text_index": 1, "embedding": [0.0] * 1024}, {"text_index": 2, "embedding": [0.0] * 1024}], "indices"),
    ([{"embedding": [0.0] * 1024}, {"text_index": 1, "embedding": [0.0] * 1024}], "indices"),
    ([{"text_index": False, "embedding": [0.0] * 1024}, {"text_index": 1, "embedding": [0.0] * 1024}], "indices"),
    ([{"text_index": 0, "embedding": [0.0] * 512}, {"text_index": 1, "embedding": [0.0] * 1024}], "dimension"),
    ([{"text_index": 0, "embedding": ["secret"] * 1024}, {"text_index": 1, "embedding": [0.0] * 1024}], "numeric"),
    ([{"text_index": 0, "embedding": [True] * 1024}, {"text_index": 1, "embedding": [0.0] * 1024}], "numeric"),
])
def test_malformed_vector_responses_are_rejected(embeddings, reason):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"output": {"embeddings": embeddings}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match=reason) as excinfo:
            DashScopeEmbeddingFunction(client=client).embed_documents(["document-1", "document-2"])
    assert len(calls) == 1
    assert "secret" not in str(excinfo.value)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_embedding_values_are_rejected(number):
    payload = json.dumps(success(["document-1"]))
    payload = payload.replace("[1.0", "[" + number, 1)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))) as client:
        with pytest.raises(RuntimeError, match="finite"):
            DashScopeEmbeddingFunction(client=client)(["document-1"])


@pytest.mark.parametrize("payload", ["not-json secret", "[]", '{"output": null}', '{"output": {"embeddings": null}}'])
def test_invalid_response_structure_is_a_sanitized_error(payload):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))) as client:
        with pytest.raises(RuntimeError, match="DashScope") as excinfo:
            DashScopeEmbeddingFunction(client=client)(["document-1"])
    assert "secret" not in str(excinfo.value)


@pytest.mark.parametrize("status,code", [
    (401, "InvalidApiKey"), (403, "AccessDenied"), (402, "Arrearage"),
    (429, "Throttling.AllocationQuota"), (503, "InsufficientBalance"), (200, "QuotaExceeded"),
])
def test_auth_balance_and_quota_errors_are_never_retried(status, code, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_MAX_RETRIES", "2")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={
            "code": code, "message": "secret-test-key private document-1", "request_id": "private-id"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="DashScope") as excinfo:
            DashScopeEmbeddingFunction(client=client)(["document-1"])
    assert len(calls) == 1
    assert "secret-test-key" not in str(excinfo.value)
    assert "document-1" not in str(excinfo.value)
    assert "private-id" not in str(excinfo.value)


def test_transient_failure_is_retried_with_bounded_request_timeout(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_MAX_RETRIES", "1")
    monkeypatch.setenv("DASHSCOPE_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("DASHSCOPE_TIMEOUT_SECONDS", "12")
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"code": "ServiceUnavailable"})
        return httpx.Response(200, json=success(["document-1"]))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert DashScopeEmbeddingFunction(client=client)(["document-1"]) == [[1.0] * 1024]
        assert not client.is_closed
    assert len(calls) == 2
    assert 0 < calls[0].extensions["timeout"]["read"] <= 12


@pytest.mark.parametrize("error_class", [httpx.ReadTimeout, httpx.ConnectError])
def test_transport_failures_have_sanitized_errors_and_finite_attempts(error_class, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_MAX_RETRIES", "1")
    monkeypatch.setenv("DASHSCOPE_RETRY_BACKOFF_SECONDS", "0")
    calls = []

    def handler(request):
        calls.append(request)
        raise error_class("secret-test-key private document", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="DashScope") as excinfo:
            DashScopeEmbeddingFunction(client=client)(["document-1"])
    assert len(calls) == 2
    assert "secret" not in str(excinfo.value)
    assert "document" not in str(excinfo.value)


def test_redirect_is_not_followed_with_authorization_header():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(307, headers={"Location": "https://unrelated.example/collect"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(RuntimeError, match="DashScope"):
            DashScopeEmbeddingFunction(client=client)(["document-1"])
    assert len(calls) == 1


def test_cancellation_before_request_prevents_cloud_work():
    event = threading.Event()
    event.set()
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request))) as client:
        with pytest.raises(CancelledError):
            DashScopeEmbeddingFunction(client=client, cancel_event=event)(["document-1"])
    assert calls == []


def test_cancellation_after_first_batch_stops_subsequent_paid_requests():
    event = threading.Event()
    calls = []

    def handler(request):
        calls.append(request)
        event.set()
        return httpx.Response(200, json=success(json.loads(request.content)["input"]["texts"]))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CancelledError):
            DashScopeEmbeddingFunction(client=client, cancel_event=event)([f"document-{i}" for i in range(11)])
    assert len(calls) == 1
