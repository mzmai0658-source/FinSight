"""在候选集合生成云端向量，按完整片段清单验收，断点续建不重收成功批次。"""
from __future__ import annotations

from collections import Counter
import math

from src.agent.v3.repository import identity_digest

CHUNKER_VERSION = 'paragraph-lines-v1'


def narrative_chunks(narratives, max_chars=1100):
    if not 100 <= max_chars <= 2000:
        raise ValueError('Narrative chunk size must stay within the cloud input bound')
    chunks = {}
    for narrative in narratives:
        text = narrative['text']
        start, ordinal = 0, 0
        while start < len(text):
            end = min(len(text), start + max_chars)
            if end < len(text):
                boundary = text.rfind('\n\n', start + max_chars // 3, end)
                if boundary < 0:
                    boundary = text.rfind('\n', start + max_chars // 3, end)
                if boundary >= 0:
                    end = boundary + 1
            raw = text[start:end]
            content = raw.strip()
            if content:
                offset = start + len(raw) - len(raw.lstrip())
                id = identity_digest(dict(document_version=narrative['document_version'], page=narrative['page'],
                                          offset=offset, content=content, chunker=CHUNKER_VERSION))
                keys = ('stock_code','company','year','period','document_version','source_path','page')
                metadata = {key: narrative[key] for key in keys}
                metadata.update(offset=offset, chunk_index=ordinal, chunker_version=CHUNKER_VERSION,
                                text_origin=narrative.get('text_origin','ocr_markdown_cleaned'),
                                ocr_sha256=narrative.get('ocr_sha256',''))
                chunks[id] = dict(id=id, text=content, metadata=metadata)
                ordinal += 1
            start = end
    return list(chunks.values())


def _vectors_ok(vectors, count, dimensions):
    return len(vectors)==count and all(len(vector)==dimensions and all(
        isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
        for value in vector) for vector in vectors)


def build_index(narratives, version, *, embedding=None, client=None, reuse_collections=(), heartbeat=None,index_scope='document'):
    from src.agent.providers import resolve_embedding_function, build_collection_metadata, embedding_fingerprint, assert_collection_embedding
    chunks = narrative_chunks(narratives)
    expected_reports = {(row['stock_code'],row['year'],row['period']) for row in narratives}
    covered_reports = {(row['metadata']['stock_code'],row['metadata']['year'],row['metadata']['period']) for row in chunks}
    if not chunks or expected_reports != covered_reports:
        raise ValueError('Each report must have nonempty OCR narrative content before indexing')
    embedding = embedding or resolve_embedding_function('ingest')
    if client is None:
        import chromadb
        from src.agent.tool_shared import resolve_chroma_db_path
        client = chromadb.PersistentClient(path=str(resolve_chroma_db_path()))
    fingerprint = embedding_fingerprint(embedding)
    inventory = identity_digest([row['id'] for row in chunks])
    name = 'financial_cloud_' + identity_digest(dict(version=version, fingerprint=fingerprint,
                               chunks=inventory,chunker=CHUNKER_VERSION))[:32]
    if index_scope not in {'document','release'}:raise ValueError('Invalid financial index scope')
    meta = build_collection_metadata(embedding,dict(data_version=version,chunker_version=CHUNKER_VERSION,index_scope=index_scope,
                                     source_inventory_sha256=inventory,dimensions=embedding.dimensions))
    collection = client.get_or_create_collection(name, metadata=meta, embedding_function=None)
    assert_collection_embedding(collection, embedding)
    if collection.metadata != meta:
        raise ValueError('Existing candidate collection metadata differs from its immutable inventory')
    reusable=[]
    for old_name in dict.fromkeys(reuse_collections):
        if old_name==name:
            continue
        old=client.get_collection(old_name,embedding_function=None)
        assert_collection_embedding(old,embedding)
        reusable.append(old)
    batch_size = min(10, getattr(embedding,'max_batch_size',10))
    new_count, reused_count = 0, 0
    for start in range(0,len(chunks),batch_size):
        if heartbeat:
            heartbeat()
        batch=chunks[start:start+batch_size]
        old=collection.get(ids=[row['id'] for row in batch],include=['documents','metadatas'])
        existing={id:(text,metadata) for id,text,metadata in zip(old['ids'],old['documents'],old['metadatas'])}
        for row in batch:
            if row['id'] in existing and existing[row['id']]!=(row['text'],row['metadata']):
                raise ValueError('Stored chunk text or provenance changed')
        missing=[row for row in batch if row['id'] not in existing]
        for old_collection in reusable:
            if not missing:
                break
            eligible=[row for row in missing if old_collection.metadata.get('index_scope')!='document' or
                      old_collection.metadata.get('data_version')==row['metadata']['document_version']]
            if not eligible:continue
            reused={}
            old=old_collection.get(ids=[row['id'] for row in eligible],include=['documents','metadatas','embeddings'])
            source={id:(text,metadata,vector) for id,text,metadata,vector in zip(old['ids'],old['documents'],old['metadatas'],old['embeddings'])}
            for row in missing:
                if row['id'] in source and source[row['id']][:2]==(row['text'],row['metadata']):
                    vector=list(map(float,source[row['id']][2]))
                    if not _vectors_ok([vector],1,embedding.dimensions):
                        raise ValueError('Reused vector shape or values differ from the declared space')
                    reused[row['id']]=vector
            if reused:
                rows=[row for row in missing if row['id'] in reused]
                collection.upsert(ids=[row['id'] for row in rows],documents=[row['text'] for row in rows],
                    metadatas=[row['metadata'] for row in rows],embeddings=[reused[row['id']] for row in rows])
                reused_count+=len(rows)
                missing=[row for row in missing if row['id'] not in reused]
        if missing:
            vectors=embedding.embed_documents([row['text'] for row in missing])
            if not _vectors_ok(vectors,len(missing),embedding.dimensions):
                raise ValueError('Cloud embedding batch has invalid vector count, dimensions, or values')
            collection.upsert(ids=[row['id'] for row in missing],documents=[row['text'] for row in missing],
                metadatas=[row['metadata'] for row in missing],embeddings=vectors)
            new_count+=len(missing)
    lookup={row['id']:row for row in chunks}
    if collection.count()!=len(chunks):
        raise ValueError('Candidate index contains missing or extra chunks')
    seen=set()
    for start in range(0,len(chunks),250):
        if heartbeat:heartbeat()
        actual=collection.get(limit=250,offset=start,include=['documents','metadatas','embeddings'])
        for id,text,metadata,vector in zip(actual['ids'],actual['documents'],actual['metadatas'],actual['embeddings']):
            if id in seen or id not in lookup or text!=lookup[id]['text'] or metadata!=lookup[id]['metadata'] or not _vectors_ok([list(map(float,vector))],1,embedding.dimensions):
                raise ValueError('Stored vector or source binding failed final inspection')
            seen.add(id)
    if seen!=set(lookup):raise ValueError('Candidate index contains missing or extra chunks')
    coverage=Counter((row['metadata']['stock_code'],row['metadata']['year'],row['metadata']['period']) for row in chunks)
    return dict(version=version,collection=name,chunks=len(chunks),reports=len(coverage),accepted=True,
        embedding_fingerprint=fingerprint,dimensions=embedding.dimensions,chunker_version=CHUNKER_VERSION,
        inventory_sha256=inventory,new_vectors=new_count,reused_vectors=reused_count,
        coverage=[dict(stock_code=key[0],year=key[1],period=key[2],chunks=value) for key,value in sorted(coverage.items())])
