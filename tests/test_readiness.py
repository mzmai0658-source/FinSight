from types import SimpleNamespace
from pathlib import Path
import pytest
from src.api import readiness


def test_readiness_selects_the_same_explicit_trial_pointer_as_chat(monkeypatch):
    from tests.test_ocr_trial import prepared
    from src.etl.ocr_trial import publish_trial
    # The model transport is outside this repository-policy test's scope.
    monkeypatch.setenv('LLM_API_KEY', '')
    monkeypatch.setattr('src.agent.v3.published_agent.AsyncModel', object)
    engine, _, _ = prepared()
    publish_trial(engine, 'v', dict(inventory_checked=True, index_verified=True))
    monkeypatch.setenv('FINSIGHT_OCR_TRIAL', 'true')
    monkeypatch.setenv('FINSIGHT_DATA_PROFILE', 'ocr-trial-v1')
    repo = readiness.selected_repository(engine)
    assert repo.version == 'v'
    assert repo.numeric_source_policy == 'ocr_trial'
    monkeypatch.setenv('FINSIGHT_DATA_PROFILE', 'strict')
    with pytest.raises(ValueError):
        readiness.selected_repository(engine)


def test_readiness_keeps_strict_repository_when_trial_is_disabled(monkeypatch):
    from src.agent.v3 import repository
    selected = []
    sentinel = object()
    monkeypatch.setenv('FINSIGHT_OCR_TRIAL', 'false')
    monkeypatch.setenv('FINSIGHT_DATA_PROFILE', 'ocr-trial-v1')
    monkeypatch.setattr(repository, 'CanonicalRepository', lambda engine:selected.append(engine) or sentinel)
    assert readiness.selected_repository('connection') is sentinel
    assert selected == ['connection']


def test_empty_folder_is_not_ready(tmp_path):
    assert not readiness.probe_knowledge(tmp_path)[0]


def test_cloud_model_list_checks_target_without_generating(monkeypatch):
    config=SimpleNamespace(is_configured=True,base_url='https://api.example.test',model='deepseek-flash')
    monkeypatch.setattr(readiness,'resolve_chat_provider',lambda:config)
    payloads=[]
    def response(actual):
        assert actual is config
        return SimpleNamespace(status_code=200,json=lambda:payloads.pop(0))
    monkeypatch.setattr(readiness,'_model_get',response)
    payloads.append({'data':[{'id':'other'}]})
    assert not readiness.probe_llm()[0]
    payloads.append({'data':[{'id':'deepseek-flash'}]})
    assert readiness.probe_llm()[0]
    assert payloads==[]


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


def test_existing_manually_encoded_index_is_checked_without_replacing_its_encoder(tmp_path,monkeypatch):
    import chromadb
    (tmp_path/'chroma.sqlite3').touch()
    monkeypatch.setattr(readiness,'resolve_embedding_function',lambda **kw:SimpleNamespace(model_name='BAAI/bge-small-zh-v1.5'))
    monkeypatch.setattr(readiness,'assert_collection_embedding',lambda *args:None)
    collection=SimpleNamespace(count=lambda:1,get=lambda **kwargs:{'embeddings':[[0.]*512]})
    def get_collection(name,embedding_function=None):
        assert embedding_function is None,'Health probe must not replace a persisted encoder'
        return collection
    monkeypatch.setattr(chromadb,'PersistentClient',lambda **kwargs:SimpleNamespace(get_collection=get_collection))
    assert readiness.probe_knowledge(tmp_path)[0]
