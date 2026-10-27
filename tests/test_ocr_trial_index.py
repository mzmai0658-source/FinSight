"""试验知识库复用成功向量，网络失败后只补缺失片段。"""
import importlib.util
import json
from pathlib import Path

import pytest

from src.agent.v3.repository import identity_digest
from src.etl.submission_index import narrative_chunks,build_index,CHUNKER_VERSION
from tests.test_submission_index import Embedding,narrative


def trial(tmp_path,monkeypatch,embedding):
    import chromadb
    path=Path(__file__).resolve().parents[1]/'scripts/ocr_trial.py'
    spec=importlib.util.spec_from_file_location('ocr_trial_script',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr('src.agent.providers.resolve_embedding_function',lambda *_:embedding)
    monkeypatch.setattr('src.agent.tool_shared.resolve_chroma_db_path',lambda:tmp_path/'chroma')
    client=chromadb.PersistentClient(path=str(tmp_path/'chroma'))
    rows=[narrative('已有原文向量。'),{**narrative('新原文向量。'),'page':2}]
    old=build_index(rows[:1],'a'*64,embedding=embedding,client=client)
    chunks=narrative_chunks(rows);version='trial'
    name='financial_cloud_'+identity_digest(dict(version=version,fingerprint=embedding.fingerprint,
        chunks=identity_digest([row['id'] for row in chunks]),chunker=CHUNKER_VERSION))[:32]
    work=tmp_path/'trial';work.mkdir()
    (work/'narratives.json').write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
    (work/'reports.json').write_text(json.dumps([{'document_version':'a'*64}]),encoding='utf-8')
    monkeypatch.setattr(module,'current',lambda:dict(version=version,work='trial',collection=name,chunks=2))
    return module,client,name


def test_bulk_reuse_and_resume_do_not_reembed_completed_chunks(tmp_path,monkeypatch):
    embedding=Embedding();module,client,name=trial(tmp_path,monkeypatch,embedding)
    before=len(embedding.batches)
    result=module.index()
    assert result['accepted'] and result['reused_vectors_this_run']==1 and result['new_vectors_this_run']==1
    assert len(embedding.batches)==before+1 and client.get_collection(name).count()==2
    resumed=module.index()
    assert resumed['accepted'] and resumed['embedding_calls_this_run']==0
    assert len(embedding.batches)==before+1


def test_failed_embedding_keeps_reused_vectors_for_resume(tmp_path,monkeypatch):
    embedding=Embedding();module,client,name=trial(tmp_path,monkeypatch,embedding)
    original=embedding.embed_documents
    def fail(*args):raise RuntimeError('simulated provider interruption')
    embedding.embed_documents=fail
    with pytest.raises(RuntimeError,match='interruption'):module.index()
    assert client.get_collection(name).count()==1
    embedding.embed_documents=original
    result=module.index()
    assert result['accepted'] and result['existing_before_run']==1 and result['new_vectors_this_run']==1
