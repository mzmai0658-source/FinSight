"""检索空间与来源指纹检查不因云端问答迁移而删减。"""
import sys
import types
from pathlib import Path
from typing import Any, Dict, Optional
import pytest
from src.agent.providers import (BGE_DEFAULT_MODEL, EMBEDDING_FINGERPRINT_KEY,
    BgeLocalEmbeddingFunction, assert_collection_embedding, build_collection_metadata,
    embedding_fingerprint, resolve_embedding_function)

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    import os
    for name in tuple(os.environ):
        if not (name.startswith('EMBEDDING_') or name.startswith('DASHSCOPE_')):
            continue
        monkeypatch.delenv(name,raising=False)
    return monkeypatch

class FakeCollection:
    def __init__(self,metadata=None,count=1):self.metadata=metadata;self._count=count
    def count(self):return self._count

class TestEmbeddingProvider:
    def test_default_cloud_without_key_fails_instead_of_loading_local_weights(self):
        with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
            resolve_embedding_function()

    @pytest.mark.parametrize("provider", ["dashscope", "auto", ""])
    def test_cloud_default_with_key_uses_1024_dimension_space(self, clean_env, provider):
        from src.agent.dashscope_embedding import DashScopeEmbeddingFunction
        clean_env.setenv("EMBEDDING_PROVIDER", provider)
        clean_env.setenv("DASHSCOPE_API_KEY", "test-key")
        clean_env.setenv("DASHSCOPE_HTTP_BASE_URL", "https://account.cn-beijing.maas.aliyuncs.com/api/v1")

        ef = resolve_embedding_function()

        assert isinstance(ef, DashScopeEmbeddingFunction)
        assert ef.model_name == "text-embedding-v4"
        assert ef.dimensions == 1024
        assert ef.max_batch_size == 10

    def test_unknown_embedding_provider_is_rejected(self, clean_env):
        clean_env.setenv("EMBEDDING_PROVIDER", "dashscpoe")
        with pytest.raises(ValueError, match="EMBEDDING_PROVIDER"):
            resolve_embedding_function()

    def test_explicit_bge_ignores_dashscope_key(self, clean_env):
        clean_env.setenv("DASHSCOPE_API_KEY", "sk-dashscope")
        clean_env.setenv("EMBEDDING_PROVIDER", "bge_local")
        assert isinstance(resolve_embedding_function(), BgeLocalEmbeddingFunction)

    def test_stale_dashscope_model_name_is_not_used_locally(self, clean_env):
        """旧配置里的 text-embedding-v4 对本地 provider 无意义，不能当成权重名去下载。"""
        clean_env.setenv("EMBEDDING_PROVIDER", "bge_local")
        clean_env.setenv("EMBEDDING_MODEL", "text-embedding-v4")
        assert resolve_embedding_function().model_name == BGE_DEFAULT_MODEL

    def test_dashscope_without_key_fails_loudly(self, clean_env):
        clean_env.setenv("EMBEDDING_PROVIDER", "dashscope")
        with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
            resolve_embedding_function()

    def test_local_model_is_loaded_lazily(self):
        """构造时不得加载权重，否则离线测试与启动都会被拖慢。"""
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
    """bge 要求 CLS 池化。上游在配置缺失时会退化为 mean pooling 且仅打一行提示，
    维度与指纹均不变，只有检索质量下降，因此需要显式断言。
    """

    class Pooling:
        """池化模块按类名识别，故此处必须叫 Pooling。"""

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
        """bge 不能走 ST 自动配置，必须显式搭 Transformer + CLS Pooling。"""
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

        # 通过导入接缝注入，避免替换 sys.modules 破坏真实包，也不加载任何权重。
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
        """构建/查询模型不一致时向量空间不可比，必须快速失败而非静默错乱。"""
        collection = FakeCollection(
            metadata={EMBEDDING_FINGERPRINT_KEY: "dashscope:text-embedding-v4"}
        )
        with pytest.raises(RuntimeError) as excinfo:
            assert_collection_embedding(collection, BgeLocalEmbeddingFunction())
        message = str(excinfo.value)
        assert "dashscope:text-embedding-v4" in message
        assert "rebuild_kb" in message

    def test_legacy_nonempty_collection_without_fingerprint_must_rebuild(self):
        """旧库无法证明向量空间一致，必须重建而不是带风险继续查询。"""
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
