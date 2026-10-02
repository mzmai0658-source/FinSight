"""作品说明：在现用集合之外构建按内容寻址的不可变索引。"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent.v3.repository import identity_digest
from src.etl.release_profiles import check_report_coverage


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env', default='.env.integration')
    p.add_argument('--audit', default='data/runtime/v3/audit')
    p.add_argument('--reuse-collection', default='', help='Reuse matching vectors from an accepted index; check metadata and text first')
    args = p.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / args.env, override=True)
    from src.agent.providers import resolve_embedding_function, build_collection_metadata
    from src.agent.tool_shared import resolve_chroma_db_path
    import chromadb
    folder = ROOT / args.audit
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    reports=json.loads((folder / 'reports.json').read_text(encoding='utf-8'))
    profile=check_report_coverage(manifest,reports)
    if manifest['errors'] or manifest['conflicts']:
        raise ValueError('Index requires audited reports without unresolved identity conflicts')
    version = manifest['version']
    embedding = resolve_embedding_function('ingest')
    client = chromadb.PersistentClient(path=str(resolve_chroma_db_path()))
    collection = client.get_or_create_collection('financial_v3_' + version[:16], metadata=build_collection_metadata(embedding, {'data_version': version}))
    reusable=None
    if args.reuse_collection:
        from sqlalchemy import create_engine,text
        from config.db_config import get_readonly_db_config
        engine=create_engine(get_readonly_db_config().connection_string)
        with engine.connect() as conn:
            accepted=conn.execute(text("SELECT COUNT(*) FROM financial_data_releases WHERE index_collection=:collection AND status='accepted'"),{'collection':args.reuse_collection}).scalar_one()
        if not accepted:raise ValueError('Vector reuse requires an accepted source collection')
        reusable=client.get_collection(args.reuse_collection)
        metadata_without_version=lambda c:{key:value for key,value in c.metadata.items() if key!='data_version'}
        if metadata_without_version(reusable)!=metadata_without_version(collection):
            raise ValueError('Embedding fingerprint changed; cannot reuse vectors')
    reused_count,embedded_count=0,0
    chunks = {}
    for line in (folder / 'narratives.jsonl').read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        # 作品说明：路径只作为元数据；内容版本、物理页与页内位置决定身份，同一文件换路径上传不会产生重复片段。
        for start in range(0, len(row['text']), 700):
            text = row['text'][start:start + 850].strip()
            if len(text) < 40:
                continue
            id = identity_digest({'document_version': row['document_version'], 'page': row['page'], 'offset': start, 'content': text})
            chunks[id] = dict(text=text, metadata={k: row[k] for k in ('stock_code', 'company', 'year', 'period', 'document_version', 'source_path', 'page')})
    items = list(chunks.items())
    for start in range(0, len(items), 64):
        batch = items[start:start + 64]
        ids = [i for i, _ in batch]
        existing = set(collection.get(ids=ids)['ids'])
        batch = [(i, row) for i, row in batch if i not in existing]
        if batch:
            reuse_rows={}
            if reusable:
                old=reusable.get(ids=[i for i,_ in batch],include=['embeddings','documents','metadatas'])
                reuse_rows={id:(doc,meta,vector) for id,doc,meta,vector in zip(old['ids'],old['documents'],old['metadatas'],old['embeddings'])}
            verified_reuse=[(i,row,reuse_rows[i][2]) for i,row in batch if i in reuse_rows and
                reuse_rows[i][0]==row['text'] and reuse_rows[i][1]==row['metadata']]
            if verified_reuse:
                collection.upsert(ids=[i for i,_,_ in verified_reuse],documents=[row['text'] for _,row,_ in verified_reuse],
                    metadatas=[row['metadata'] for _,row,_ in verified_reuse],embeddings=[vector.tolist() for _,_,vector in verified_reuse])
                reused_count+=len(verified_reuse)
            reused_ids={i for i,_,_ in verified_reuse}
            batch=[(i,row) for i,row in batch if i not in reused_ids]
        if batch:
            texts = [r['text'] for _, r in batch]
            collection.upsert(ids=[i for i, _ in batch], documents=texts,
                embeddings=embedding.embed_documents(texts), metadatas=[r['metadata'] for _, r in batch])
            embedded_count+=len(batch)
        print(f'{min(start + 64, len(items))}/{len(items)} indexed', flush=True)
    stored = collection.get(include=['metadatas'])
    coverage = Counter((r['stock_code'], r['year'], r['period']) for r in stored['metadatas'])
    if set(coverage) != {(r['stock_code'],r['year'],r['period']) for r in reports} or collection.count() != len(items) or set(stored['ids']) != set(chunks):
        raise ValueError('Index coverage or deduplication failed')
    ledger = dict(version=version, collection=collection.name, chunks=collection.count(), reports=len(coverage),
        coverage=[dict(stock_code=k[0], year=k[1], period=k[2], chunks=v) for k, v in sorted(coverage.items())],
        reused_vectors=reused_count,new_vectors=embedded_count,reuse_source=args.reuse_collection or None,accepted=True)
    (folder / 'index_coverage.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in ledger.items() if k != 'coverage'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
