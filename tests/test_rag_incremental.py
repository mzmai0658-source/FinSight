"""作品说明：通过内存向量库和确定性向量替身测试索引，不访问网络。"""
import hashlib
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.etl.rag_builder import (
    _research_extra_meta,
    _source_already_current,
    _source_fingerprint,
    _upsert_document_records,
)
from src.etl.research_metadata import ResearchMeta, normalize_title


class FakeEmbedder:
    """作品说明：确定性向量：hash 前 8 字节展开，无网络依赖。"""

    def name(self):
        return "fake"

    def __call__(self, input):
        vectors = []
        for text in input:
            digest = hashlib.md5(str(text).encode("utf-8")).digest()
            vectors.append([b / 255.0 for b in digest[:8]])
        return vectors


@pytest.fixture()
def collection():
    chromadb = pytest.importorskip("chromadb")
    import uuid

    # 作品说明：EphemeralClient 在同进程内共享存储，集合名需唯一避免跨用例冲突
    client = chromadb.EphemeralClient()
    return client.create_collection(f"test_kb_{uuid.uuid4().hex[:8]}", embedding_function=FakeEmbedder())


def _records():
    return [
        {"text": "营业收入同比增长百分之十五，主要受核心产品放量驱动，经营情况整体稳健向好。",
         "doc_type": "research_report_equity", "chunk_index": 0, "page_start": 1,
         "section_title": "业绩概述", "title_path": "业绩概述"},
        {"text": "公司研发投入持续加大，创新管线进入收获期，维持买入评级并上调目标价格区间。",
         "doc_type": "research_report_equity", "chunk_index": 1, "page_start": 2,
         "section_title": "投资建议", "title_path": "投资建议"},
    ]


class TestIncrementalBuild:
    def test_upsert_writes_fingerprint(self, collection, tmp_path):
        pdf = tmp_path / "某研报.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        inserted = _upsert_document_records(
            collection, _records(), source_pdf_path=pdf, source_json_path=None,
            stock_code="600000", report_year=2024, doc_category="research",
        )

        assert inserted == 2
        payload = collection.get(limit=1, include=["metadatas"])
        fingerprint = payload["metadatas"][0]["source_fingerprint"]
        assert fingerprint == _source_fingerprint(pdf, None) and fingerprint
        assert payload["metadatas"][0]["source_chunk_count"] == 2

    def test_unchanged_source_detected(self, collection, tmp_path):
        pdf = tmp_path / "某研报.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        source = str(pdf).replace("\\", "/")

        _upsert_document_records(
            collection, _records(), source_pdf_path=pdf, source_json_path=None,
            stock_code="600000", report_year=2024, doc_category="research",
        )

        assert _source_already_current(collection, source, None, _source_fingerprint(pdf, None))

    def test_changed_source_triggers_rebuild(self, collection, tmp_path):
        pdf = tmp_path / "某研报.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        source = str(pdf).replace("\\", "/")

        _upsert_document_records(
            collection, _records(), source_pdf_path=pdf, source_json_path=None,
            stock_code="600000", report_year=2024, doc_category="research",
        )
        old_fp = _source_fingerprint(pdf, None)
        pdf.write_bytes(b"%PDF-1.4 fake CHANGED CONTENT")
        new_fp = _source_fingerprint(pdf, None)

        assert new_fp != old_fp
        assert not _source_already_current(collection, source, None, new_fp)

    def test_reupsert_replaces_old_chunks(self, collection, tmp_path):
        pdf = tmp_path / "某研报.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        _upsert_document_records(
            collection, _records(), source_pdf_path=pdf, source_json_path=None,
            stock_code="600000", report_year=2024, doc_category="research",
        )
        _upsert_document_records(
            collection, _records()[:1], source_pdf_path=pdf, source_json_path=None,
            stock_code="600000", report_year=2024, doc_category="research",
        )

        assert collection.count() == 1

    def test_partial_source_is_not_treated_as_current(self, collection, tmp_path):
        pdf = tmp_path / "某研报.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        source = str(pdf).replace("\\", "/")

        _upsert_document_records(
            collection, _records(), source_pdf_path=pdf, source_json_path=None,
            stock_code="600000", report_year=2024, doc_category="research",
        )
        payload = collection.get(where={"source": source})
        collection.delete(ids=[payload["ids"][0]])

        assert not _source_already_current(
            collection, source, None, _source_fingerprint(pdf, None)
        )

    def test_legacy_source_without_chunk_count_remains_compatible(self, collection, tmp_path):
        pdf = tmp_path / "旧研报.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        source = str(pdf).replace("\\", "/")
        fingerprint = _source_fingerprint(pdf, None)
        collection.add(
            ids=["legacy-1"],
            documents=["旧索引分片仍可按来源指纹增量跳过。"],
            metadatas=[{"source": source, "source_fingerprint": fingerprint}],
        )

        assert _source_already_current(collection, source, None, fingerprint)

    def test_missing_fingerprint_never_skips(self, collection):
        assert not _source_already_current(collection, "x", None, "")


class TestResearchMetaAttach:
    def test_extra_meta_attached_to_chunks(self, collection, tmp_path):
        pdf = tmp_path / "重组蛋白专家，科研试剂新星.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        meta = ResearchMeta(
            title="重组蛋白专家，科研试剂新星", report_type="stock",
            stock_code="301080", stock_name="百普赛斯", org_sname="太平洋",
            rating="买入", publish_date="2025-12-31",
        )
        index = {normalize_title(meta.title): meta}

        extra = _research_extra_meta(pdf, index)
        inserted = _upsert_document_records(
            collection, _records(), source_pdf_path=pdf, source_json_path=None,
            stock_code=extra.get("research_stock_code") or "", report_year=2025,
            doc_category="research", extra_meta=extra,
        )

        assert inserted == 2
        payload = collection.get(limit=1, include=["metadatas"])
        chunk_meta = payload["metadatas"][0]
        assert chunk_meta["research_stock_code"] == "301080"
        assert chunk_meta["research_org"] == "太平洋"
        assert chunk_meta["research_rating"] == "买入"
        assert chunk_meta["stock_code"] == "301080"

    def test_no_index_returns_empty(self, tmp_path):
        assert _research_extra_meta(tmp_path / "x.pdf", None) == {}
        assert _research_extra_meta(tmp_path / "x.pdf", {}) == {}


def test_embedding_failure_keeps_previous_published_version(collection, tmp_path, monkeypatch):
    import src.etl.rag_builder as builder
    source=tmp_path/'report.pdf'
    source.write_bytes(b'original')
    options=dict(source_pdf_path=source,source_json_path=None,stock_code='600000',report_year=2024,doc_category='financial')
    builder._upsert_document_records(collection,_records(),**options)
    original=set(collection.get()['ids'])
    def partial_write_then_fail(target, documents, metadatas, ids):
        target.upsert(ids=ids[:1],documents=documents[:1],metadatas=metadatas[:1])
        raise RuntimeError('embedding failed')
    monkeypatch.setattr(builder,'_batched_upsert',partial_write_then_fail)
    source.write_bytes(b'changed!')
    with pytest.raises(RuntimeError, match='embedding failed'):
        builder._upsert_document_records(collection,_records(),**options)
    assert set(collection.get()['ids']) == original
    assert all(m['publication_status']=='active' for m in collection.get()['metadatas'])
    assert builder._upsert_document_records(collection,[],**options) == 0
    assert set(collection.get()['ids']) == original


def test_fingerprint_detects_same_length_same_timestamp_change(tmp_path):
    import os
    source=tmp_path/'report.pdf'
    source.write_bytes(b'aaaa')
    stat=source.stat()
    original=_source_fingerprint(source,None)
    source.write_bytes(b'bbbb')
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert _source_fingerprint(source,None) != original
