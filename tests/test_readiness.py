from types import SimpleNamespace
from pathlib import Path
import pytest
from src.api import readiness


def test_empty_folder_is_not_ready(tmp_path):
    assert not readiness.probe_knowledge(tmp_path)[0]


def test_missing_target_model_and_short_context_warning(monkeypatch):
    config=SimpleNamespace(is_configured=True,is_local=True,base_url='http://local/v1',model='qwen:test',num_ctx=16384)
    monkeypatch.setattr(readiness,'resolve_chat_provider',lambda:config)
    payloads=[]
    def response(*args,**kwargs):
        assert kwargs['timeout'] == 3
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:payloads.pop(0))
    monkeypatch.setattr(readiness.requests,'get',response)
    payloads.append({'models':[{'name':'other'}]})
    assert not readiness.probe_llm()[0]
    payloads.extend([{'models':[{'name':'qwen:test'}]}, {'models':[{'name':'qwen:test','context_length':4096}]}])
    short_ok, short_detail = readiness.probe_llm()
    assert short_ok and '4096' in short_detail and '低于建议' in short_detail
    payloads.extend([{'models':[{'name':'qwen:test'}]}, {'models':[{'name':'qwen:test','context_length':16384}]}])
    assert readiness.probe_llm()[0]


def test_empty_collection_and_wrong_dimensions_not_ready(tmp_path,monkeypatch):
    import chromadb
    (tmp_path/'chroma.sqlite3').touch()
    monkeypatch.setattr(readiness,'resolve_embedding_function',lambda **kw:SimpleNamespace(model_name='BAAI/bge-small-zh-v1.5'))
    monkeypatch.setattr(readiness,'assert_collection_embedding',lambda *args:None)
    collection=SimpleNamespace(count=lambda:0,get=lambda **kw:{'embeddings':[[0.]*768]})
    monkeypatch.setattr(chromadb,'PersistentClient',lambda **kw:SimpleNamespace(get_collection=lambda *args,**kw:collection))
    assert not readiness.probe_knowledge(tmp_path)[0]
    collection.count=lambda:1
    assert not readiness.probe_knowledge(tmp_path)[0]
    collection.get=lambda **kw:{'embeddings':[[0.]*512]}
    assert readiness.probe_knowledge(tmp_path)[0]
