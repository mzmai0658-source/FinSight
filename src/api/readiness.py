"""使用有界协议探测判断就绪，不能只看配置是否存在。"""
from pathlib import Path
import os
import httpx
from src.agent.providers import assert_collection_embedding, resolve_embedding_function, resolve_chat_provider
from src.agent.cloud_protocol import tls_context,status_reason


def selected_repository(engine):
    """就绪检查与问答使用同一显式资料策略，不以严格小库代替试验库验收。"""
    if os.getenv('FINSIGHT_OCR_TRIAL')=='true':
        from src.etl.ocr_trial import configured_trial_repository
        return configured_trial_repository(engine)
    from src.agent.v3.repository import CanonicalRepository
    return CanonicalRepository(engine)


def _model_get(config):
    with httpx.Client(timeout=httpx.Timeout(15,connect=5),verify=tls_context()) as client:
        return client.get(config.base_url.rstrip('/')+'/models',headers={'Authorization':'Bearer '+config.api_key})


def probe_llm() -> tuple[bool, str]:
    try:
        config=resolve_chat_provider()
        if not config.is_configured:return False,'云端密钥缺失，请配置服务端 LLM_API_KEY'
        response=_model_get(config)
        if response.status_code!=200:return False,'云端模型未就绪：'+status_reason(response.status_code)
        models=response.json().get('data',[])
        if not isinstance(models,list) or not any(isinstance(m,dict) and m.get('id')==config.model for m in models):
            return False,'云端服务未提供配置的型号：'+config.model
        return True,'云端认证与型号列表可用：'+config.model+'；生成能力需单独验收'
    except ValueError:
        return False,'云端配置或响应格式无效，请核对 LLM_* 配置'
    except Exception as exc:
        return False, f"模型服务未就绪（{type(exc).__name__}）"


def probe_knowledge(path: Path,collection_name='financial_reports') -> tuple[bool, str]:
    if not path.is_dir() or not (path / 'chroma.sqlite3').is_file():
        return False, "知识库尚未构建"
    try:
        import chromadb
        ef = resolve_embedding_function(purpose='health')
        # 健康探测读取已有向量，不替换集合中持久化的编码函数配置。
        collection = chromadb.PersistentClient(path=str(path)).get_collection(collection_name, embedding_function=None)
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
        expected = getattr(ef,'dimensions',None) or (512 if 'bge-small-zh' in model_name else 768 if 'bge-base-zh' in model_name else 1024 if 'bge-large-zh' in model_name else None)
        if expected and dimension != expected:
            return False, f"向量维度不匹配：{dimension} / {expected}"
        return True, f"集合可读取：{count} 片段，{dimension} 维，模型指纹匹配"
    except Exception as exc:
        return False, f"知识库未就绪（{type(exc).__name__}）"
