"""作品说明：使用有界协议探测判断就绪，不能只看配置是否存在。"""
from pathlib import Path
import requests
from src.agent.providers import assert_collection_embedding, resolve_embedding_function, resolve_chat_provider


def probe_llm() -> tuple[bool, str]:
    config = resolve_chat_provider()
    if not config.is_configured:
        return False, "模型配置缺失"
    try:
        if config.is_local:
            base = config.base_url.rstrip('/').removesuffix('/v1')
            response = requests.get(base + '/api/tags', timeout=3)
            response.raise_for_status()
            installed = {m.get('name') for m in response.json().get('models', [])}
            if config.model not in installed:
                return False, f"Ollama 存活，但目标模型 {config.model} 未安装"
            running = requests.get(base + '/api/ps', timeout=3)
            running.raise_for_status()
            for model in running.json().get('models', []):
                context_length = int(model.get('context_length') or 0)
                if model.get('name') == config.model and context_length and context_length < config.num_ctx:
                    return True, (f"Ollama 可访问且目标模型 {config.model} 已安装；"
                                  f"当前运行上下文 {context_length} 低于建议 {config.num_ctx}，长输入可能截断")
            return True, f"Ollama 可访问且目标模型 {config.model} 已安装；不代表推理质量已验证"
        response = requests.get(config.base_url.rstrip('/') + '/models', headers={'Authorization': f'Bearer {config.api_key}'}, timeout=3)
        response.raise_for_status()
        return True, "远端模型服务可访问；推理能力需单独验收"
    except Exception as exc:
        return False, f"模型服务未就绪（{type(exc).__name__}）"


def probe_knowledge(path: Path) -> tuple[bool, str]:
    if not path.is_dir() or not (path / 'chroma.sqlite3').is_file():
        return False, "知识库尚未构建"
    try:
        import chromadb
        ef = resolve_embedding_function(purpose='health')
        collection = chromadb.PersistentClient(path=str(path)).get_collection('financial_reports', embedding_function=ef)
        assert_collection_embedding(collection, ef)
        count = collection.count()
        if not count:
            return False, "知识库集合为空"
        sample = collection.get(limit=1, include=['embeddings'])
        vectors = sample.get('embeddings')
        if vectors is None or len(vectors) == 0 or len(vectors[0]) == 0:
            return False, "知识库缺少可用向量"
        dimension = len(vectors[0])
        model_name = getattr(ef, 'model_name', '')
        expected = 512 if 'bge-small-zh' in model_name else 768 if 'bge-base-zh' in model_name else 1024 if 'bge-large-zh' in model_name else None
        if expected and dimension != expected:
            return False, f"向量维度不匹配：{dimension} / {expected}"
        return True, f"集合可读取：{count} 片段，{dimension} 维，模型指纹匹配"
    except Exception as exc:
        return False, f"知识库未就绪（{type(exc).__name__}）"
