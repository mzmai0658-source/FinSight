"""Export or restore a local financial release without users, secrets or model calls.

The transport directory contains real copyrighted reports and OCR-derived content.
It is a private installation input, not part of the public source ZIP. Restoring
preserves experimental/verified statuses; it is not a new extraction acceptance.
"""
from __future__ import annotations
import argparse, datetime, decimal, gzip, hashlib, json, os, re, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TABLES = ('company', 'income_sheet', 'balance_sheet', 'cash_flow_sheet',
          'core_performance_indicators_sheet', 'financial_report_material',
          'financial_report_versions', 'financial_report_provenance',
          'financial_data_releases', 'financial_canonical_reports',
          'financial_canonical_facts', 'financial_data_pointer')
# Material rows refer to private users and submissions. They are never exported.
TABLES = tuple(x for x in TABLES if x != 'financial_report_material')

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f, 'sha256').hexdigest()

def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=encode)+'\n', encoding='utf-8')

def encode(value):
    if isinstance(value, (datetime.datetime, datetime.date, decimal.Decimal)):return str(value)
    raise TypeError(type(value).__name__)

def paths(value):
    if isinstance(value, str):yield value
    elif isinstance(value, dict):
        for v in value.values():yield from paths(v)
    elif isinstance(value, list):
        for v in value:yield from paths(v)

def configure(env):
    from dotenv import dotenv_values
    values={k:str(v or '') for k,v in dotenv_values(env).items()}
    if not values:raise ValueError('Private environment file is missing or empty')
    os.environ.update(values)
    return values

def export(folder, env, asset_root=ROOT):
    from sqlalchemy import create_engine, MetaData, select
    from config.db_config import get_db_config
    import chromadb
    if folder.exists():raise ValueError('Export destination must be new; existing evidence is immutable')
    folder.mkdir(parents=True);engine=create_engine(get_db_config().connection_string)
    metadata=MetaData();metadata.reflect(bind=engine, only=[x for x in TABLES if x])
    records={}
    with engine.connect() as c:
        pointers=[dict(r) for r in c.execute(select(metadata.tables['financial_data_pointer'])).mappings()]
        versions={r['version'] for r in pointers}
        for name in TABLES:
            t=metadata.tables[name];query=select(t)
            if name=='financial_data_releases':query=query.where(t.c.version.in_(versions))
            elif name in {'financial_canonical_reports','financial_canonical_facts'}:query=query.where(t.c.data_version.in_(versions))
            records[name]=[dict(r) for r in c.execute(query).mappings()]
    engine.dispose()
    dump(folder/'records.json',records)
    files=set()
    for value in paths(records):
        if not value.startswith(('data/', 'data_root/')):continue
        p=(asset_root/value).resolve()
        if not p.is_relative_to(asset_root):raise ValueError('Source asset escapes project root')
        if p.is_file():files.add(p)
        elif p.is_dir() and value.startswith('data/runtime/submissions/'):
            files.update(x for x in p.rglob('*') if x.is_file())
    # The export_path contains the canonical facts, reports, narrative and index receipts.
    for p in files:
        target=folder/'assets'/p.relative_to(asset_root);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,target)
    chroma_path=Path(env['CHROMA_DB_PATH']);chroma_path=chroma_path if chroma_path.is_absolute() else asset_root/chroma_path
    client=chromadb.PersistentClient(path=str(chroma_path));collections=[]
    for release in records['financial_data_releases']:
        name=release['index_collection'];collection=client.get_collection(name,embedding_function=None)
        file='vectors/'+name+'.jsonl.gz';target=folder/file;target.parent.mkdir(exist_ok=True)
        with gzip.open(target,'wt',encoding='utf-8') as f:
            for offset in range(0,collection.count(),200):
                batch=collection.get(limit=200,offset=offset,include=['documents','metadatas','embeddings'])
                for i,ident in enumerate(batch['ids']):
                    f.write(json.dumps(dict(id=ident,document=batch['documents'][i],metadata=batch['metadatas'][i],
                        embedding=batch['embeddings'][i].tolist()),ensure_ascii=False,separators=(',',':'))+'\n')
        collections.append(dict(name=name,metadata=collection.metadata,count=collection.count(),file=file))
    inventory={p.relative_to(folder).as_posix():sha(p) for p in folder.rglob('*') if p.is_file()}
    dump(folder/'transport.json',dict(schema=1,scope='private financial-only snapshot; no OCR regeneration or new acceptance',
         tables={k:len(v) for k,v in records.items()},collections=collections,files=inventory,
         statuses_preserved=True,users_exported=False,credentials_exported=False))
    print(json.dumps(dict(status='exported',files=len(inventory),collections=len(collections))))

def verify(folder):
    manifest=json.loads((folder/'transport.json').read_text('utf-8'))
    if manifest.get('schema')!=1 or manifest.get('users_exported') is not False:raise ValueError('Unsupported transport manifest')
    expected=set(manifest['files'])|{'transport.json'}
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if expected!=actual:raise ValueError('Transport file inventory differs')
    for name,digest in manifest['files'].items():
        p=(folder/name).resolve()
        if not p.is_relative_to(folder) or sha(p)!=digest:raise ValueError('Transport asset identity mismatch')
    records=json.loads((folder/'records.json').read_text('utf-8'))
    if set(records)!=set(TABLES) or any(len(records[k])!=manifest['tables'][k] for k in TABLES):raise ValueError('Financial table inventory differs')
    for spec in manifest['collections']:
        name=spec.get('name','');file=spec.get('file','');target=(folder/file).resolve()
        if (not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{1,510}[a-zA-Z0-9]',name)
                or file not in manifest['files'] or not target.is_relative_to(folder)
                or not file.startswith('vectors/') or not file.endswith('.jsonl.gz')):
            raise ValueError('Unsafe vector artifact')
    if {s['name'] for s in manifest['collections']}!={r['index_collection'] for r in records['financial_data_releases']}:
        raise ValueError('Release and vector collection inventories differ')
    from src.agent.v3.contracts import Fact
    from src.etl.release_profiles import check_report_coverage
    releases={r['version']:r for r in records['financial_data_releases']}
    reports=records['financial_canonical_reports']
    for r in releases.values():
        check_report_coverage(r['manifest'],[x['payload'] for x in reports if x['data_version']==r['version']])
        if r['status'] not in {'accepted','experimental'}:raise ValueError('Only existing published snapshots may be restored')
    original_hashes={}
    for row in records['financial_canonical_facts']:
        f=Fact.model_validate(row['payload'])
        if (f.data_version,f.id,f.status)!=(row['data_version'],row['id'],row['status']):raise ValueError('Fact row/payload mismatch')
        if f.source:
            p=(folder/'assets'/f.source.source_path).resolve()
            if not p.is_relative_to(folder/'assets') or not p.is_file():
                raise ValueError('Fact original PDF identity mismatch')
            if p not in original_hashes:original_hashes[p]=sha(p)
            if original_hashes[p]!=f.source.source_sha256:
                raise ValueError('Fact original PDF identity mismatch')
    return manifest,records

def restore(folder, env):
    from sqlalchemy import create_engine, MetaData, text, inspect
    from sqlalchemy.sql.sqltypes import DateTime, Date, Numeric
    from config.db_config import get_db_config
    manifest,records=verify(folder)
    config=get_db_config();database=config.database
    if not re.fullmatch(r'finsight_[A-Za-z0-9_]{1,48}',database):raise ValueError('Use a new finsight_* database for installation')
    server=create_engine(config.connection_string_no_db)
    with server.begin() as c:
        existing=c.execute(text('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=:name'),dict(name=database)).scalar_one_or_none()
        if existing:raise ValueError('Target database already exists; choose a new empty database, never overwrite')
        c.execute(text('CREATE DATABASE `'+database+'` CHARACTER SET utf8mb4'))
    server.dispose();engine=create_engine(config.connection_string)
    from src.init_db import Base
    from src.agent.v3.repository import metadata as canonical
    from src.etl.provenance_store import metadata as provenance
    Base.metadata.create_all(engine);canonical.create_all(engine);provenance.create_all(engine)
    sql=(ROOT/'backend-java/src/main/resources/db/migration/V2__company_table.sql').read_text('utf-8')
    sql=sql[sql.index('CREATE TABLE'):];sql=sql[:sql.index(';')+1]
    with engine.begin() as c:c.execute(text(sql))
    md=MetaData();md.reflect(bind=engine,only=list(TABLES))
    for p in (folder/'assets').rglob('*'):
        if not p.is_file():continue
        target=(ROOT/p.relative_to(folder/'assets')).resolve()
        if not target.is_relative_to(ROOT):raise ValueError('Target asset escapes root')
        if target.exists() and sha(target)!=sha(p):raise ValueError('Existing local asset differs; refusing overwrite')
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    import chromadb
    chroma_path=Path(env['CHROMA_DB_PATH']);chroma_path=chroma_path if chroma_path.is_absolute() else ROOT/chroma_path
    if chroma_path.exists() and any(chroma_path.iterdir()):raise ValueError('Target index directory must be empty')
    client=chromadb.PersistentClient(path=str(chroma_path))
    for spec in manifest['collections']:
        coll=client.create_collection(spec['name'],metadata=spec['metadata'],embedding_function=None)
        batch=[]
        def save():
            if batch:
                coll.add(ids=[r['id'] for r in batch],documents=[r['document'] for r in batch],
                         metadatas=[r['metadata'] for r in batch],embeddings=[r['embedding'] for r in batch]);batch.clear()
        with gzip.open(folder/spec['file'],'rt',encoding='utf-8') as f:
            for line in f:
                row=json.loads(line)
                if len(row['embedding'])!=spec['metadata']['dimensions']:raise ValueError('Embedding dimension mismatch')
                batch.append(row)
                if len(batch)>=200:save()
        save()
        if coll.count()!=spec['count']:raise ValueError('Restored index count differs')
    # Publish the pointers last, in the same SQL transaction as the fact records.
    with engine.begin() as c:
        for name in TABLES:
            t=md.tables[name];rows=records[name]
            for row in rows:
                for col in t.columns:
                    v=row.get(col.name)
                    if v is None:continue
                    if isinstance(col.type,DateTime):row[col.name]=datetime.datetime.fromisoformat(v) if isinstance(v,str) else v
                    elif isinstance(col.type,Date):row[col.name]=datetime.date.fromisoformat(v) if isinstance(v,str) else v
                    elif isinstance(col.type,Numeric):row[col.name]=decimal.Decimal(v)
            for start in range(0,len(rows),200):c.execute(t.insert(),rows[start:start+200])
    engine.dispose()
    from src.etl.real_delivery import create_reader
    create_reader()
    dump(ROOT/'data/runtime/release-restore-receipt.json',dict(status='restored',database=database,
         transport_sha256=sha(folder/'transport.json'),counts=manifest['tables'],
         collections=[dict(name=x['name'],count=x['count']) for x in manifest['collections']],
         original_statuses_preserved=True,ocr_regenerated=False,embeddings_regenerated=False))
    print(json.dumps(dict(status='restored',database=database,collections=len(manifest['collections']))))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['export','verify','restore'])
    p.add_argument('--folder',type=Path,required=True);p.add_argument('--env',type=Path)
    p.add_argument('--asset-root',type=Path,default=ROOT,help='Source assets for export only; defaults to this project')
    args=p.parse_args();folder=args.folder.resolve()
    if args.command=='verify':verify(folder);print('Transport integrity verified');return
    if not args.env:p.error('--env is required for export/restore')
    env=configure(args.env)
    try:
        if args.command=='export':export(folder,env,args.asset_root.resolve())
        else:restore(folder,env)
    except Exception as exc:
        # Connection URLs and private configuration are deliberately excluded.
        print(json.dumps(dict(status='failed',error_type=type(exc).__name__)),file=sys.stderr)
        raise SystemExit(1) from None

if __name__=='__main__':main()
