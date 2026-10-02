import time
from types import SimpleNamespace
from fastapi.testclient import TestClient

import src.api.main as api
from src.agent.v3.model import ModelFailure


def test_identity_title_uses_the_question_without_calling_the_model(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "offline-title-test-credential-1234567890")
    calls = []

    class MustNotCall:
        async def structured(self, *args):
            calls.append(args)
            raise AssertionError("功能介绍不应再概括标题")

    monkeypatch.setattr(api, "_get_agent", lambda: SimpleNamespace(model=MustNotCall()))
    response = TestClient(api.app).post(
        "/internal/title",
        headers={"X-Internal-Token": "offline-title-test-credential-1234567890"},
        json={"question": "你是谁", "answer": "我是财报学习助手，可查询财报数字。"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "你是谁"
    assert calls == []


def test_title_timeout_returns_fallback_without_retrying_or_changing_chat(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MAX_TOKENS", "4096")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("OLLAMA_REASONING_EFFORT", "low")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "offline-title-test-credential-1234567890")
    calls = []

    class UnavailableModel:
        async def structured(self, schema, system, payload, budget):
            calls.append(budget)
            raise ModelFailure("model_timeout")

    monkeypatch.setattr(api, "_get_agent", lambda: SimpleNamespace(model=UnavailableModel()))
    response = TestClient(api.app).post(
        "/internal/title",
        headers={"X-Internal-Token": "offline-title-test-credential-1234567890"},
        json={"question": "金花股份2023年营业收入是多少？", "answer": "回答摘要"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "金花股份2023年营业收入是多少？"
    assert len(calls) == 1
    assert 0 < calls[0].deadline - time.time() <= 12
