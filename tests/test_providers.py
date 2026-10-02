# -*- coding: utf-8 -*-
"""作品说明：Provider 抽象层单测（离线，不触网、不加载模型权重）。

覆盖：
- 对话 provider 解析：auto 选路、显式指定、新旧环境变量优先级与兼容
- Ollama 兼容性差异：无需 API Key、请求体必须省略 tool_choice
- 向量 provider 选路与知识库指纹校验（构建/查询模型不一致必须报错）
"""

import sys
import types
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.agent.llm_client import LLMClient
from src.agent.providers import (
    BGE_DEFAULT_MODEL,
    EMBEDDING_FINGERPRINT_KEY,
    OLLAMA_DEFAULT_BASE_URL,
    OLLAMA_DEFAULT_MODEL,
    PROVIDER_OLLAMA,
    PROVIDER_OPENAI_COMPAT,
    RECOMMENDED_NUM_CTX,
    BgeLocalEmbeddingFunction,
    assert_collection_embedding,
    build_collection_metadata,
    embedding_fingerprint,
    resolve_chat_provider,
    resolve_embedding_function,
)

# 作品说明：解析逻辑读全局环境，测试必须先清空真实配置（本机 .env 可能已注入 Key）。
_MANAGED_ENV_VARS = [
    "LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY",
    "LLM_TIMEOUT_SECONDS", "LLM_MAX_RETRIES", "LLM_RETRY_BACKOFF_SECONDS", "LLM_NUM_CTX",
    "LLM_MAX_TOKENS", "OLLAMA_REASONING_EFFORT",
    "OLLAMA_BASE_URL", "OLLAMA_MODEL", "OLLAMA_CONTEXT_LENGTH",
    "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
    "DEEPSEEK_TIMEOUT_SECONDS", "DEEPSEEK_MAX_RETRIES", "DEEPSEEK_RETRY_BACKOFF_SECONDS",
    "EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "EMBEDDING_DEVICE", "DASHSCOPE_API_KEY",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in _MANAGED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class FakeCollection:
    """作品说明：只暴露指纹校验需要的 metadata / count 接口。"""

    def __init__(self, metadata: Optional[Dict[str, Any]] = None, count: int = 1):
        self.metadata = metadata
        self._count = count

    def count(self) -> int:
        return self._count


# 作品说明：对话 provider 选路

class TestChatProviderResolution:
    def test_auto_without_key_falls_back_to_local_ollama(self):
        """作品说明：无任何 Key 时必须走本地开源模型，这是零密钥演示的前提。"""
        config = resolve_chat_provider()
        assert config.provider == PROVIDER_OLLAMA
        assert config.base_url == OLLAMA_DEFAULT_BASE_URL
        assert config.model == OLLAMA_DEFAULT_MODEL
        assert config.is_local is True
        assert config.requires_api_key is False
        assert config.is_configured is True

    def test_auto_with_legacy_key_keeps_remote_behavior(self, clean_env):
        """作品说明：已有部署只配了 DEEPSEEK_API_KEY，行为不能被改造改变。"""
        clean_env.setenv("DEEPSEEK_API_KEY", "sk-legacy")
        config = resolve_chat_provider()
        assert config.provider == PROVIDER_OPENAI_COMPAT
        assert config.base_url == "https://api.deepseek.com"
        assert config.model == "deepseek-chat"
        assert config.api_key == "sk-legacy"
        assert config.completions_url == "https://api.deepseek.com/chat/completions"

    def test_explicit_ollama_ignores_present_remote_key(self, clean_env):
        clean_env.setenv("DEEPSEEK_API_KEY", "sk-legacy")
        clean_env.setenv("LLM_PROVIDER", "ollama")
        config = resolve_chat_provider()
        assert config.provider == PROVIDER_OLLAMA
        assert config.is_local is True

    def test_new_vars_take_precedence_over_legacy(self, clean_env):
        clean_env.setenv("DEEPSEEK_BASE_URL", "https://legacy.example.com")
        clean_env.setenv("DEEPSEEK_MODEL", "legacy-model")
        clean_env.setenv("LLM_API_KEY", "sk-new")
        clean_env.setenv("LLM_BASE_URL", "https://new.example.com/v1")
        clean_env.setenv("LLM_MODEL", "new-model")
        config = resolve_chat_provider()
        assert config.base_url == "https://new.example.com/v1"
        assert config.model == "new-model"
        assert config.api_key == "sk-new"

    def test_explicit_arguments_win_over_env(self, clean_env):
        clean_env.setenv("LLM_BASE_URL", "https://env.example.com")
        config = resolve_chat_provider(
            api_url="https://arg.example.com/v1", api_key="sk-arg", model="arg-model"
        )
        assert config.base_url == "https://arg.example.com/v1"
        assert config.api_key == "sk-arg"
        assert config.model == "arg-model"

    def test_remote_without_key_is_not_configured(self, clean_env):
        clean_env.setenv("LLM_PROVIDER", "openai_compat")
        config = resolve_chat_provider()
        assert config.is_configured is False
        assert "LLM_API_KEY" in config.missing_config_hint()

    def test_unknown_provider_degrades_to_openai_compat(self, clean_env):
        clean_env.setenv("LLM_PROVIDER", "not-a-provider")
        assert resolve_chat_provider().provider == PROVIDER_OPENAI_COMPAT

    def test_invalid_numeric_env_falls_back_to_default(self, clean_env):
        """作品说明：配置写错不应让服务启动即崩。"""
        clean_env.setenv("LLM_MAX_RETRIES", "abc")
        clean_env.setenv("LLM_TIMEOUT_SECONDS", "")
        config = resolve_chat_provider()
        assert config.max_retries == 3
        assert config.timeout_seconds > 0

    def test_num_ctx_defaults_and_accepts_ollama_env(self, clean_env):
        assert resolve_chat_provider().num_ctx == RECOMMENDED_NUM_CTX
        clean_env.setenv("OLLAMA_CONTEXT_LENGTH", "32768")
        assert resolve_chat_provider().num_ctx == 32768

    def test_local_config_warns_about_server_side_context(self):
        """作品说明：num_ctx 无法随请求下发，配置层必须给出服务端设置提示。"""
        hint = resolve_chat_provider().context_hint()
        assert "OLLAMA_CONTEXT_LENGTH" in hint

    def test_remote_config_has_no_context_hint(self, clean_env):
        clean_env.setenv("DEEPSEEK_API_KEY", "sk-legacy")
        assert resolve_chat_provider().context_hint() == ""


# 作品说明：LLMClient 与 provider 差异

class TestLLMClientProviderWiring:
    @staticmethod
    def _capture_payload(client: LLMClient) -> Dict[str, Any]:
        captured: Dict[str, Any] = {}

        def fake_request(payload):
            captured.update(payload)
            return {"choices": [{"message": {"content": "ok"}}]}

        client._request_completion = fake_request  # type: ignore[assignment]
        return captured

    def test_local_client_omits_unsupported_tool_choice(self):
        """作品说明：Ollama 的 OpenAI 兼容层不支持 tool_choice，带上只会造成误解。"""
        client = LLMClient()
        assert client.provider == PROVIDER_OLLAMA
        captured = self._capture_payload(client)
        client.chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=[])
        assert "tool_choice" not in captured
        assert captured["max_tokens"] == 4096

    def test_max_tokens_is_configurable(self, clean_env):
        clean_env.setenv("LLM_MAX_TOKENS", "512")
        client = LLMClient()
        captured = self._capture_payload(client)
        client.chat(messages=[{"role": "user", "content": "hi"}])
        assert captured["max_tokens"] == 512
        assert client.describe()["max_tokens"] == 512

    def test_remote_client_keeps_tool_choice(self, clean_env):
        clean_env.setenv("DEEPSEEK_API_KEY", "sk-legacy")
        client = LLMClient()
        captured = self._capture_payload(client)
        client.chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=[])
        assert captured.get("tool_choice") == "auto"

    def test_local_client_is_configured_without_key(self):
        """作品说明：健康检查不能再以 api_key 是否存在判断可用性。"""
        client = LLMClient()
        assert client.is_configured is True
        assert client.describe()["is_local"] is True

    def test_remote_client_without_key_is_not_configured(self, clean_env):
        clean_env.setenv("LLM_PROVIDER", "openai_compat")
        assert LLMClient().is_configured is False

    def test_endpoint_is_built_from_base_url(self, clean_env):
        clean_env.setenv("LLM_PROVIDER", "ollama")
        clean_env.setenv("LLM_BASE_URL", "http://127.0.0.1:11434/v1/")
        assert LLMClient().api_url == "http://127.0.0.1:11434/v1/chat/completions"


# 作品说明：向量 provider 与知识库指纹

@pytest.mark.parametrize("method", ["chat", "chat_json", "chat_with_tools", "chat_stream"])
@pytest.mark.parametrize("provider,effort,expected", [
    ("ollama", "none", "none"), ("ollama", "", "none"), ("ollama", "low", "low"), ("openai_compat", "none", None),
])
def test_thinking_control_reaches_all_http_paths(clean_env, method, provider, effort, expected):
    clean_env.setenv("LLM_PROVIDER", provider)
    clean_env.setenv("OLLAMA_REASONING_EFFORT", effort)
    captured = []
    response = types.SimpleNamespace(status_code=200, raise_for_status=lambda: None,
        json=lambda: {"choices": [{"message": {"role": "assistant", "content": "{}"}}]},
        iter_lines=lambda **kwargs: iter(['data: {"choices":[{"delta":{"content":"OK"}}]}', 'data: [DONE]']))
    def post(url, **kwargs):
        captured.append(kwargs["json"])
        return response
    client = LLMClient()
    client._get_session = lambda: types.SimpleNamespace(post=post)
    kwargs = {"messages": [{"role": "user", "content": "hi"}]}
    if method == "chat_with_tools":
        kwargs["tools"] = []
    result = getattr(client, method)(**kwargs)
    if method == "chat_stream":
        assert list(result) == ["OK"]
    assert captured and all(p.get("reasoning_effort") == expected for p in captured)
    if expected is None:
        assert all("reasoning_effort" not in p for p in captured)


def test_invalid_local_thinking_control_fails_before_request(clean_env):
    clean_env.setenv("OLLAMA_REASONING_EFFORT", "invalid")
    with pytest.raises(ValueError, match="OLLAMA_REASONING_EFFORT"):
        LLMClient()

class TestEmbeddingProvider:
    def test_auto_without_key_uses_local_open_source_model(self):
        ef = resolve_embedding_function()
        assert isinstance(ef, BgeLocalEmbeddingFunction)
        assert ef.model_name == BGE_DEFAULT_MODEL

    def test_explicit_bge_ignores_dashscope_key(self, clean_env):
        clean_env.setenv("DASHSCOPE_API_KEY", "sk-dashscope")
        clean_env.setenv("EMBEDDING_PROVIDER", "bge_local")
        assert isinstance(resolve_embedding_function(), BgeLocalEmbeddingFunction)

    def test_stale_dashscope_model_name_is_not_used_locally(self, clean_env):
        """作品说明：旧配置里的 text-embedding-v4 对本地 provider 无意义，不能当成权重名去下载。"""
        clean_env.setenv("EMBEDDING_PROVIDER", "bge_local")
        clean_env.setenv("EMBEDDING_MODEL", "text-embedding-v4")
        assert resolve_embedding_function().model_name == BGE_DEFAULT_MODEL

    def test_dashscope_without_key_fails_loudly(self, clean_env):
        clean_env.setenv("EMBEDDING_PROVIDER", "dashscope")
        with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
            resolve_embedding_function()

    def test_local_model_is_loaded_lazily(self):
        """作品说明：构造时不得加载权重，否则离线测试与启动都会被拖慢。"""
        ef = BgeLocalEmbeddingFunction()
        assert ef._model is None
        assert ef.fingerprint == f"bge_local:{BGE_DEFAULT_MODEL}"

    def test_fingerprint_prefers_explicit_attribute(self):
        assert embedding_fingerprint(BgeLocalEmbeddingFunction()) == f"bge_local:{BGE_DEFAULT_MODEL}"

    def test_fingerprint_falls_back_to_name_method(self):
        class NamedEF:
            def name(self):
                return "dashscope:text-embedding-v4"

        assert embedding_fingerprint(NamedEF()) == "dashscope:text-embedding-v4"

    def test_fingerprint_never_raises_on_odd_objects(self):
        class Broken:
            def name(self):
                raise ValueError("boom")

        assert embedding_fingerprint(Broken()) == "Broken"
        assert embedding_fingerprint(None) == "unknown"


class TestBgePooling:
    """作品说明：bge 要求 CLS 池化。上游在配置缺失时会退化为 mean pooling 且仅打一行提示，
    维度与指纹均不变，只有检索质量下降，因此需要显式断言。
    """

    class Pooling:
        """作品说明：池化模块按类名识别，故此处必须叫 Pooling。"""

        def __init__(self, mode: str):
            self._mode = mode

        def get_pooling_mode_str(self) -> str:
            return self._mode

    class FakeModel:
        def __init__(self, pooling: Any):
            self._modules = {"0": object(), "1": pooling} if pooling is not None else {}

    def test_bge_model_expects_cls_pooling(self):
        assert BgeLocalEmbeddingFunction(BGE_DEFAULT_MODEL)._expects_cls_pooling is True

    def test_non_bge_model_has_no_pooling_expectation(self):
        assert BgeLocalEmbeddingFunction("sentence-transformers/all-MiniLM-L6-v2")._expects_cls_pooling is False

    def test_cls_pooling_accepted(self):
        ef = BgeLocalEmbeddingFunction()
        ef._assert_expected_pooling(self.FakeModel(self.Pooling("cls")))

    def test_mean_pooling_rejected(self):
        ef = BgeLocalEmbeddingFunction()
        with pytest.raises(RuntimeError, match="CLS"):
            ef._assert_expected_pooling(self.FakeModel(self.Pooling("mean")))

    def test_missing_pooling_module_rejected(self):
        ef = BgeLocalEmbeddingFunction()
        with pytest.raises(RuntimeError, match="CLS"):
            ef._assert_expected_pooling(self.FakeModel(None))

    def test_non_bge_model_skips_the_check(self):
        ef = BgeLocalEmbeddingFunction("sentence-transformers/all-MiniLM-L6-v2")
        ef._assert_expected_pooling(self.FakeModel(self.Pooling("mean")))

    def test_explicit_cls_pooling_is_built_for_bge(self, monkeypatch):
        """作品说明：bge 不能走 ST 自动配置，必须显式搭 Transformer + CLS Pooling。"""
        built = {}
        monkeypatch.setattr(BgeLocalEmbeddingFunction, "_local_model_source", lambda self: self.model_name)

        class FakeTransformer:
            def __init__(self, name, max_seq_length=None):
                built["model_name"] = name
                built["max_seq_length"] = max_seq_length

            def get_word_embedding_dimension(self):
                return 512

        class FakePoolingModule:
            def __init__(self, dim, pooling_mode=None):
                built["dim"] = dim
                built["pooling_mode"] = pooling_mode

        # 作品说明：通过导入接缝注入，避免替换 sys.modules 破坏真实包，也不加载任何权重。
        monkeypatch.setattr(
            BgeLocalEmbeddingFunction,
            "_import_st_models",
            staticmethod(lambda: types.SimpleNamespace(
                Transformer=FakeTransformer, Pooling=FakePoolingModule
            )),
        )

        def fake_cls(*args, **kwargs):
            built["modules"] = kwargs.get("modules")
            built["positional"] = args
            return "model"

        assert BgeLocalEmbeddingFunction()._build_model(fake_cls) == "model"
        assert built["model_name"] == BGE_DEFAULT_MODEL
        assert built["pooling_mode"] == "cls"
        assert built["dim"] == 512
        assert len(built["modules"]) == 2
        assert built["positional"] == ()

    def test_non_bge_model_uses_standard_loader(self, monkeypatch):
        seen = {}

        def fake_cls(name=None, device=None, **kwargs):
            seen["name"] = name
            return "model"

        ef = BgeLocalEmbeddingFunction("sentence-transformers/all-MiniLM-L6-v2")
        assert ef._build_model(fake_cls) == "model"
        assert seen["name"] == "sentence-transformers/all-MiniLM-L6-v2"


class TestCollectionFingerprintGuard:
    def test_metadata_carries_fingerprint_and_extras(self):
        metadata = build_collection_metadata(
            BgeLocalEmbeddingFunction(), {"hnsw:space": "cosine"}
        )
        assert metadata[EMBEDDING_FINGERPRINT_KEY] == f"bge_local:{BGE_DEFAULT_MODEL}"
        assert metadata["hnsw:space"] == "cosine"

    def test_matching_fingerprint_passes(self):
        ef = BgeLocalEmbeddingFunction()
        collection = FakeCollection(metadata=build_collection_metadata(ef))
        assert_collection_embedding(collection, ef)

    def test_mismatched_fingerprint_raises_with_rebuild_hint(self):
        """作品说明：构建/查询模型不一致时向量空间不可比，必须快速失败而非静默错乱。"""
        collection = FakeCollection(
            metadata={EMBEDDING_FINGERPRINT_KEY: "dashscope:text-embedding-v4"}
        )
        with pytest.raises(RuntimeError) as excinfo:
            assert_collection_embedding(collection, BgeLocalEmbeddingFunction())
        message = str(excinfo.value)
        assert "dashscope:text-embedding-v4" in message
        assert "rebuild_kb" in message

    def test_legacy_nonempty_collection_without_fingerprint_must_rebuild(self):
        """作品说明：旧库无法证明向量空间一致，必须重建而不是带风险继续查询。"""
        with pytest.raises(RuntimeError, match="未记录向量指纹"):
            assert_collection_embedding(FakeCollection(metadata={}), BgeLocalEmbeddingFunction())
        with pytest.raises(RuntimeError, match="未记录向量指纹"):
            assert_collection_embedding(FakeCollection(metadata=None), BgeLocalEmbeddingFunction())

    def test_empty_collection_without_fingerprint_is_fine(self):
        assert_collection_embedding(
            FakeCollection(metadata={}, count=0), BgeLocalEmbeddingFunction()
        )


def test_cached_bge_uses_local_files_without_remote_validation(tmp_path, monkeypatch):
    from src.agent.providers import BgeLocalEmbeddingFunction
    import huggingface_hub
    for name in ('config.json','model.safetensors','vocab.txt'):
        (tmp_path/name).write_text('fixture')
    monkeypatch.setattr(huggingface_hub,'try_to_load_from_cache',lambda *args,**kw:str(tmp_path/'config.json'))
    assert BgeLocalEmbeddingFunction()._local_model_source() == str(tmp_path)
    (tmp_path/'model.safetensors').unlink()
    assert BgeLocalEmbeddingFunction()._local_model_source() == BGE_DEFAULT_MODEL
