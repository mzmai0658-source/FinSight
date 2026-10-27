"""按原件版本和向量空间建立候选索引，重试不生成重复片段。"""
import pytest


class Embedding:
    dimensions = 3
    max_batch_size = 2
    fingerprint = 'test-space-3-v1'
    def __init__(self):
        self.batches = []
    def embed_documents(self, texts):
        self.batches.append(list(texts))
        return [[float(len(text)), 1., 0.] for text in texts]


def narrative(text='第一段说明。\n\n第二段列示经营情况。'):
    return dict(stock_code='000789',company='陌生企业',year=2025,period='FY',document_version='a'*64,
                source_path='data_root/a.pdf',page=1,text=text,text_origin='ocr_markdown_cleaned',ocr_sha256='b'*64)


def test_chunks_keep_all_nonwhitespace_content_and_source_identity():
    from src.etl.submission_index import narrative_chunks
    text='经营原因。'*650+'\n\n'+'表格行 | -123.45 | 678.90\n'*90
    rows=narrative_chunks([narrative(text)],max_chars=800)
    assert ''.join(''.join(r['text'].split()) for r in rows)==''.join(text.split())
    assert all(len(r['text'])<=800 and r['metadata']['document_version']=='a'*64 for r in rows)
    assert [r['metadata']['chunk_index'] for r in rows]==list(range(len(rows)))
    assert len({r['id'] for r in rows})==len(rows)


def test_candidate_index_resumes_only_missing_vectors_and_matches_exact_inventory(tmp_path):
    import chromadb
    from src.etl.submission_index import build_index
    embedding=Embedding()
    client=chromadb.PersistentClient(path=str(tmp_path/'chroma'))
    rows=[narrative('经营说明。'*600)]
    first=build_index(rows,'version',embedding=embedding,client=client)
    calls=len(embedding.batches)
    second=build_index(rows,'version',embedding=embedding,client=client)
    assert first['collection']==second['collection']
    assert second['new_vectors']==0 and len(embedding.batches)==calls
    assert all(len(batch)<=2 for batch in embedding.batches)
    assert second['accepted'] and second['reports']==1


def test_same_document_cannot_mix_different_vector_spaces(tmp_path):
    import chromadb
    from src.etl.submission_index import build_index
    client=chromadb.PersistentClient(path=str(tmp_path/'chroma'))
    first=build_index([narrative()],'v',embedding=Embedding(),client=client)
    changed=Embedding()
    changed.fingerprint='different-space'
    second=build_index([narrative()],'v',embedding=changed,client=client)
    assert first['collection']!=second['collection']


def test_empty_original_text_is_not_a_successful_report_index(tmp_path):
    from src.etl.submission_index import build_index
    with pytest.raises(ValueError):
        build_index([narrative('')],'v',embedding=Embedding())


def test_partial_reuse_across_multiple_collections_only_upserts_current_matches(tmp_path):
    import chromadb
    from src.etl.submission_index import build_index
    embedding=Embedding();client=chromadb.PersistentClient(path=str(tmp_path/'chroma'))
    first=narrative('首个已完成片段。')
    missing={**narrative('尚未完成的片段。'),'page':2}
    unrelated={**narrative('另一个版本的片段。'),'page':3}
    old=build_index([first],'old',embedding=embedding,client=client,index_scope='release')
    other=build_index([unrelated],'other',embedding=embedding,client=client,index_scope='release')
    fresh=build_index([first,missing],'new',embedding=embedding,client=client,index_scope='release',
                      reuse_collections=[old['collection'],other['collection']])
    assert fresh['accepted'] and fresh['reused_vectors']==1 and fresh['new_vectors']==1
