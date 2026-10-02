"""作品说明：统一适配对话与向量服务，本地主路径使用 Ollama 和 BGE；远端服务仅在显式配置时启用。兼容接口的上下文与工具选择差异由适配层处理，原生调用使用各自支持的参数。"""

import os
from pathlib import Path
from dataclasses import dataclass
from threading import RLock
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

# 作品说明：对话模型服务适配。

PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENAI_COMPAT = "openai_compat"

OLLAMA_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
OLLAMA_DEFAULT_MODEL = "qwen3.5:9b-q4_K_M"
OLLAMA_PLACEHOLDER_KEY = "ollama"

OPENAI_COMPAT_DEFAULT_BASE_URL = "https://api.deepseek.com"
OPENAI_COMPAT_DEFAULT_MODEL = "deepseek-chat"

# 作品说明：长工具和检索请求的建议上下文；实际是否截断取决于请求长度。
RECOMMENDED_NUM_CTX = 16384

_PROVIDER_ALIASES = {
    "ollama": PROVIDER_OLLAMA,
    "local": PROVIDER_OLLAMA,
    "openai": PROVIDER_OPENAI_COMPAT,
    "openai_compat": PROVIDER_OPENAI_COMPAT,
    "openai-compat": PROVIDER_OPENAI_COMPAT,
    "deepseek": PROVIDER_OPENAI_COMPAT,
    "vllm": PROVIDER_OPENAI_COMPAT,
}


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _env_int(*names: str, default: int) -> int:
    raw = _first_env(*names)
    if not raw:
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        logger.warning(f"环境变量 {names[0]} 取值非法（{raw}），改用默认值 {default}")
        return default


def _env_float(*names: str, default: float) -> float:
    raw = _first_env(*names)
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(f"环境变量 {names[0]} 取值非法（{raw}），改用默认值 {default}")
        return default


@dataclass(frozen=True)
class ChatProviderConfig:
    """作品说明：一次会话使用的对话模型配置，解析完即不可变，便于日志与健康检查回显。"""

    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: int
    max_retries: int
    retry_backoff_seconds: float
    num_ctx: int
    max_tokens: int

    @property
    def completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def is_local(self) -> bool:
        return self.provider == PROVIDER_OLLAMA

    @property
    def requires_api_key(self) -> bool:
        return not self.is_local

    @property
    def is_configured(self) -> bool:
        """作品说明：本地模型无需 Key；远端必须有 Key 才算可用。"""
        return self.is_local or bool(self.api_key)

    @property
    def supports_tool_choice(self) -> bool:
        return not self.is_local

    @property
    def supports_response_format(self) -> bool:
        # 作品说明：Ollama 支持 response_format=json_object。
        return True

    def missing_config_hint(self) -> str:
        if self.is_configured:
            return ""
        return (
            "未检测到对话模型 API Key。设置 LLM_API_KEY（或旧变量 DEEPSEEK_API_KEY）"
            f"，或改用本地开源模型：LLM_PROVIDER=ollama 并在 {OLLAMA_DEFAULT_BASE_URL} 启动 Ollama"
        )

    def context_hint(self) -> str:
        """作品说明：兼容接口与原生接口的单次调用选项分别适配。"""
        if not self.is_local:
            return ""
        return (
            f"本地上下文目标={self.num_ctx}；结构化 Agent 使用原生 num_ctx 参数。"
            f"其他 OpenAI 兼容接口调用需服务端配置 OLLAMA_CONTEXT_LENGTH={self.num_ctx}。"
            "此提示不是上下文截断的实测结论。"
        )

    def describe(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "is_local": self.is_local,
            "is_configured": self.is_configured,
            "requires_api_key": self.requires_api_key,
            "expected_num_ctx": self.num_ctx,
            "max_tokens": self.max_tokens,
        }


def resolve_chat_provider(
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> ChatProviderConfig:
    """作品说明：解析对话模型配置。显式入参优先，其次新变量 LLM_*，最后兼容旧变量 DEEPSEEK_*。
    
    LLM_PROVIDER 未设置时按 auto 处理：有 API Key 走远端（保持既有部署行为），
    没有 Key 则走本地 Ollama，从而保证「零密钥可演示」。
    """
    requested = _first_env("LLM_PROVIDER", default="auto").lower()
    has_remote_key = bool(_first_env("LLM_API_KEY", "DEEPSEEK_API_KEY"))

    if requested in ("auto", ""):
        provider = PROVIDER_OPENAI_COMPAT if has_remote_key else PROVIDER_OLLAMA
    else:
        provider = _PROVIDER_ALIASES.get(requested, PROVIDER_OPENAI_COMPAT)
        if requested not in _PROVIDER_ALIASES:
            logger.warning(f"未知 LLM_PROVIDER={requested}，按 openai_compat 处理")

    if provider == PROVIDER_OLLAMA:
        default_base_url = _first_env("OLLAMA_BASE_URL", default=OLLAMA_DEFAULT_BASE_URL)
        default_model = _first_env("OLLAMA_MODEL", default=OLLAMA_DEFAULT_MODEL)
        default_timeout = 300  # 作品说明：为本地模型的首段响应预留等待余量。
        # 作品说明：Ollama 忽略鉴权头，但空 Bearer 在部分反代下会被拒，填占位值。
        resolved_key = api_key or _first_env("LLM_API_KEY", default=OLLAMA_PLACEHOLDER_KEY)
    else:
        default_base_url = _first_env(
            "DEEPSEEK_BASE_URL", default=OPENAI_COMPAT_DEFAULT_BASE_URL
        )
        default_model = _first_env("DEEPSEEK_MODEL", default=OPENAI_COMPAT_DEFAULT_MODEL)
        default_timeout = 120
        resolved_key = api_key or _first_env("LLM_API_KEY", "DEEPSEEK_API_KEY")

    return ChatProviderConfig(
        provider=provider,
        base_url=api_url or _first_env("LLM_BASE_URL", default=default_base_url),
        model=model or _first_env("LLM_MODEL", default=default_model),
        api_key=resolved_key,
        timeout_seconds=_env_int(
            "LLM_TIMEOUT_SECONDS", "DEEPSEEK_TIMEOUT_SECONDS", default=default_timeout
        ),
        max_retries=_env_int("LLM_MAX_RETRIES", "DEEPSEEK_MAX_RETRIES", default=3),
        retry_backoff_seconds=_env_float(
            "LLM_RETRY_BACKOFF_SECONDS", "DEEPSEEK_RETRY_BACKOFF_SECONDS", default=2.0
        ),
        num_ctx=_env_int("LLM_NUM_CTX", "OLLAMA_CONTEXT_LENGTH", default=RECOMMENDED_NUM_CTX),
        # 作品说明：防止小模型在工具参数或合成阶段重复生成直到 HTTP 超时。
        max_tokens=_env_int("LLM_MAX_TOKENS", default=4096 if provider == PROVIDER_OLLAMA else 1024),
    )


# 作品说明：向量模型服务适配。

EMBEDDING_PROVIDER_BGE = "bge_local"
EMBEDDING_PROVIDER_DASHSCOPE = "dashscope"

BGE_DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
# 作品说明：bge-*-zh-v1.5 官方建议：检索场景下对 query 加指令前缀，文档侧不加。
BGE_DEFAULT_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
DASHSCOPE_DEFAULT_MODEL = "text-embedding-v4"

_EMBEDDING_ALIASES = {
    "bge": EMBEDDING_PROVIDER_BGE,
    "bge_local": EMBEDDING_PROVIDER_BGE,
    "local": EMBEDDING_PROVIDER_BGE,
    "sentence_transformers": EMBEDDING_PROVIDER_BGE,
    "dashscope": EMBEDDING_PROVIDER_DASHSCOPE,
    "qwen": EMBEDDING_PROVIDER_DASHSCOPE,
}


class BgeLocalEmbeddingFunction:
    """作品说明：本地开源中文向量模型（默认 BAAI/bge-small-zh-v1.5，MIT，约 100MB，CPU 可跑）。
    
    权重不入仓库，首次调用时由 sentence-transformers 下载并缓存。
    """

    def __init__(
        self,
        model_name: str = BGE_DEFAULT_MODEL,
        query_instruction: Optional[str] = None,
        device: Optional[str] = None,
    ):
        self.model_name = model_name
        self.query_instruction = (
            BGE_DEFAULT_QUERY_INSTRUCTION if query_instruction is None else query_instruction
        )
        self.device = device or os.getenv("EMBEDDING_DEVICE") or None
        self._model = None
        self._load_lock = RLock()

    def name(self) -> str:
        return f"{EMBEDDING_PROVIDER_BGE}:{self.model_name}"

    @property
    def fingerprint(self) -> str:
        return self.name()

    def _get_model(self):
        with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "缺少 sentence-transformers，无法加载本地向量模型。"
                    "请执行 pip install -r requirements.txt"
                ) from exc
            logger.info(f"加载本地向量模型 {self.model_name}（首次运行需下载权重）")
            model = self._build_model(SentenceTransformer)
            self._assert_expected_pooling(model)
            self._model = model
            return self._model

    def _build_model(self, sentence_transformer_cls: Any) -> Any:
        """作品说明：构造向量模型。
        
        bge 系列显式指定 CLS 池化，而不是依赖仓库里的 sentence-transformers 配置
        （modules.json）：该文件缺失或下载失败时，上游会静默退化为 mean pooling，
        维度与指纹都不变，问题只体现为检索质量下降。池化方式是确定的领域知识，
        不应交给一次网络请求决定。
        """
        if not self._expects_cls_pooling:
            return sentence_transformer_cls(self.model_name, device=self.device)

        st_models = self._import_st_models()
        transformer = st_models.Transformer(self._local_model_source(), max_seq_length=512)
        pooling = st_models.Pooling(
            transformer.get_word_embedding_dimension(), pooling_mode="cls"
        )
        return sentence_transformer_cls(modules=[transformer, pooling], device=self.device)

    def _local_model_source(self) -> str:
        """作品说明：权重缓存完整时直接加载，避免离线环境发起网络探测。"""
        if Path(self.model_name).is_dir():
            return self.model_name
        try:
            from huggingface_hub import try_to_load_from_cache
            config = try_to_load_from_cache(self.model_name, "config.json")
            if isinstance(config, str):
                folder = Path(config).parent
                weights = any((folder / name).is_file() for name in ("model.safetensors", "pytorch_model.bin"))
                tokenizer = any((folder / name).is_file() for name in ("tokenizer.json", "vocab.txt"))
                if weights and tokenizer:
                    return str(folder)
        except (ImportError, OSError, ValueError):
            pass
        return self.model_name

    @staticmethod
    def _import_st_models() -> Any:
        """作品说明：独立出来的导入接缝，便于在不加载真实权重的前提下单测结构装配。"""
        from sentence_transformers import models as st_models

        return st_models

    @property
    def _expects_cls_pooling(self) -> bool:
        return "bge" in self.model_name.lower()

    def _assert_expected_pooling(self, model: Any) -> None:
        """作品说明：bge 系列要求 CLS 池化。
        
        当 sentence-transformers 拉不到仓库里的 ST 配置（modules.json 等）时，它会
        退化为「Transformer + mean pooling」并只打一行提示继续运行。此时向量维度
        照旧、指纹照旧，但语义质量已经降级，建库之后极难排查，因此这里直接失败。
        """
        if not self._expects_cls_pooling:
            return
        pooling = next(
            (m for m in getattr(model, "_modules", {}).values() if type(m).__name__ == "Pooling"),
            None,
        )
        mode = ""
        if pooling is not None:
            getter = getattr(pooling, "get_pooling_mode_str", None)
            mode = str(getter() if callable(getter) else "")
        if "cls" in mode:
            return
        raise RuntimeError(
            f"{self.model_name} 应使用 CLS 池化，实际为 '{mode or '未知'}'。"
            "模型结构已显式构造，出现此错误说明 sentence-transformers 版本行为与预期不符，"
            "请检查依赖版本。"
        )

    @staticmethod
    def _normalize_texts(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, (list, tuple)):
            normalized: List[str] = []
            for item in value:
                normalized.extend(BgeLocalEmbeddingFunction._normalize_texts(item))
            return normalized
        text = str(value).strip()
        return [text] if text else []

    def _encode(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        vectors = self._get_model().encode(
            texts,
            normalize_embeddings=True,  # 作品说明：余弦相似度要求单位向量
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(v) for v in vector] for vector in vectors]

    def __call__(self, input: Sequence[str]) -> List[List[float]]:
        return self._encode(self._normalize_texts(input))

    def embed_documents(self, texts: Sequence[str] = None, **kwargs) -> List[List[float]]:
        if texts is None:
            texts = kwargs.get("texts") or kwargs.get("input") or []
        return self._encode(self._normalize_texts(texts))

    def embed_query(self, text: str = None, **kwargs) -> List[float]:
        if text is None:
            text = kwargs.get("text") or kwargs.get("input") or ""
        normalized = self._normalize_texts(text)
        if not normalized:
            return []
        vectors = self._encode([self.query_instruction + normalized[0]])
        return vectors[0] if vectors else []


def embedding_fingerprint(ef: Any) -> str:
    """作品说明：取向量函数的稳定标识，用于写入/校验知识库元数据。
    
    构建与查询使用不同模型时向量空间不可比，检索会返回貌似正常但实际错乱的
    结果，因此必须留下指纹而不是仅打印告警。
    """
    if ef is None:
        return "unknown"
    fingerprint = getattr(ef, "fingerprint", None)
    if isinstance(fingerprint, str) and fingerprint:
        return fingerprint
    name = getattr(ef, "name", None)
    if callable(name):
        try:
            value = name()
            if isinstance(value, str) and value:
                return value
        except Exception:
            pass
    elif isinstance(name, str) and name:
        return name
    return type(ef).__name__


EMBEDDING_FINGERPRINT_KEY = "embedding_fingerprint"


def build_collection_metadata(ef: Any, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """作品说明：知识库集合元数据：带上向量指纹，供查询侧校验构建/查询模型是否一致。"""
    metadata: Dict[str, Any] = {EMBEDDING_FINGERPRINT_KEY: embedding_fingerprint(ef)}
    if extra:
        metadata.update(extra)
    return metadata


def assert_collection_embedding(collection: Any, ef: Any, rebuild_hint: str = "") -> None:
    """作品说明：校验集合的构建向量模型与当前查询向量模型一致，不一致直接抛错。
    
    向量空间不同时检索仍会返回结果，只是排序毫无意义，这类静默错乱比报错更难
    排查，因此此处选择快速失败。
    """
    current = embedding_fingerprint(ef)
    metadata = getattr(collection, "metadata", None) or {}
    stored = metadata.get(EMBEDDING_FINGERPRINT_KEY)
    hint = rebuild_hint or "请用当前配置重建知识库（python scripts/rebuild_kb.py）"

    if not stored:
        try:
            is_empty = collection.count() == 0
        except Exception:
            is_empty = False
        if is_empty:
            return
        raise RuntimeError(
            f"知识库未记录向量指纹，无法确认与当前模型（{current}）一致。"
            f"为避免不同向量空间静默混用，已拒绝查询。{hint}"
        )

    if stored != current:
        raise RuntimeError(
            f"知识库向量模型不匹配：构建时为 {stored}，当前为 {current}。"
            f"两者向量空间不可比，检索结果无效。{hint}"
        )


def resolve_embedding_function(purpose: str = "query") -> Any:
    """作品说明：解析向量函数。
    
    EMBEDDING_PROVIDER 未设置时按 auto 处理：已配置 DashScope Key 则沿用
    DashScope（避免既有知识库失效），否则使用本地 BGE，从而保证无密钥环境下
    检索链路全部由开源模型承担。
    """
    requested = _first_env("EMBEDDING_PROVIDER", default="auto").lower()

    if requested in ("auto", ""):
        from src.agent.dashscope_embedding import DashScopeEmbeddingFunction

        candidate = DashScopeEmbeddingFunction(
            model_name=_first_env("EMBEDDING_MODEL", default=DASHSCOPE_DEFAULT_MODEL)
        )
        if candidate.dashscope is not None and candidate.api_key:
            logger.info(f"[{purpose}] 使用 DashScope embedding {candidate.model_name}")
            return candidate
        provider = EMBEDDING_PROVIDER_BGE
    else:
        provider = _EMBEDDING_ALIASES.get(requested)
        if provider is None:
            logger.warning(f"未知 EMBEDDING_PROVIDER={requested}，按 {EMBEDDING_PROVIDER_BGE} 处理")
            provider = EMBEDDING_PROVIDER_BGE

    if provider == EMBEDDING_PROVIDER_DASHSCOPE:
        from src.agent.dashscope_embedding import DashScopeEmbeddingFunction

        ef = DashScopeEmbeddingFunction(
            model_name=_first_env("EMBEDDING_MODEL", default=DASHSCOPE_DEFAULT_MODEL)
        )
        if ef.dashscope is None or not ef.api_key:
            raise RuntimeError(
                "EMBEDDING_PROVIDER=dashscope 但缺少可用的 DASHSCOPE_API_KEY 或 dashscope 包。"
                "改用本地开源模型请设置 EMBEDDING_PROVIDER=bge_local"
            )
        logger.info(f"[{purpose}] 使用 DashScope embedding {ef.model_name}")
        return ef

    model_name = _first_env("EMBEDDING_MODEL", default=BGE_DEFAULT_MODEL)
    if model_name == DASHSCOPE_DEFAULT_MODEL:
        # 作品说明：旧配置残留：DashScope 模型名对本地 provider 无意义。
        model_name = BGE_DEFAULT_MODEL
    logger.info(f"[{purpose}] 使用本地开源 embedding {model_name}")
    return BgeLocalEmbeddingFunction(model_name=model_name)
