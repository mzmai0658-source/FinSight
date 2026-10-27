"""管理员选中的已核验候选组成新版本，事实、兼容表和发布指针一起提交。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import MetaData,Table,select,text

from src.agent.v3.contracts import Fact
from src.agent.v3.repository import (
    identity_digest,stage_release,publish_release,pointer,releases,metadata,json_payload)
from src.etl.submission_artifacts import ROOT,file_digest,pipeline_digest,verify_artifacts,validate_facts
from src.etl.submission_index import build_index
from src.etl.submission_store import valid_id


def _report_key(value):return value['stock_code'],value['year'],value['period']


def _load_json(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def _read_lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def _write(path,value,lines=False):
    from src.etl.ocr_client import _atomic_write
    content=''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in value) if lines else json.dumps(value,ensure_ascii=False,indent=2)+'\n'
    _atomic_write(path,content.encode('utf-8'))


def _current(engine):
    with engine.connect() as conn:
        version=conn.execute(select(pointer.c.version).where(pointer.c.name=='serving')).scalar_one_or_none()
        if not version:return None
        release=conn.execute(select(releases).where(releases.c.version==version)).mappings().one()
        if release['status']!='accepted':raise ValueError('Serving financial release is not accepted')
        # 发布连接池保持可写；不能在这里调用会设置SESSION READ ONLY的查询仓库。
        return SimpleNamespace(version=version,collection=release['index_collection'],manifest=json_payload(release['manifest']))


def _previous_artifacts(repository):
    manifest=repository.manifest
    folder=(ROOT/manifest.get('export_path','')).resolve()
    if not folder.is_relative_to(ROOT/'data/runtime/submissions/releases'):
        raise ValueError('Previous cloud release has no reusable, frozen original artifacts; explicit migration is required')
    for filename in ('reports.json','facts.json','narratives.jsonl'):
        if file_digest(folder/filename)!=(manifest.get('artifact_hashes') or {}).get(filename):
            raise ValueError('Previous release export changed')
    return _load_json(folder/'reports.json'),_load_json(folder/'facts.json'),_read_lines(folder/'narratives.jsonl')


def _project_release(conn,facts,reports):
    from src.etl.canonical_projection import apply_projections,projections,MODELS
    apply_projections(conn,facts,reports)
    for report in reports:
        scoped=[f for f in facts if (f.stock_code,f.year,f.period)==_report_key(report)]
        expected,_=projections(scoped,report)
        for name,values in expected.items():
            table=MODELS[name]
            where=[table.c[key]==values[key] for key in ('stock_code','report_year','report_period')]
            actual=conn.execute(select(table).where(*where)).mappings().one()
            if any(actual[key]!=value for key,value in values.items()):
                raise ValueError('Compatibility projection differs from the accepted financial facts')
    company=Table('company',MetaData(),autoload_with=conn)
    names={report['stock_code']:report['company'] for report in reports}
    for code,name in names.items():
        years=[r['year'] for r in reports if r['stock_code']==code]
        values=dict(abbr=name,dataset_tag='真实财报',data_status='imported',first_year=min(years),last_year=max(years))
        if conn.execute(select(company.c.stock_code).where(company.c.stock_code==code)).first():
            conn.execute(company.update().where(company.c.stock_code==code).values(**values))
        else:conn.execute(company.insert().values(stock_code=code,**values))


def publish_candidates(candidate_ids,operator_id,*,store=None,engine=None,embedding=None,client=None,refresh_callback=None):
    if type(operator_id) is not int or operator_id<1:
        raise ValueError('A trusted administrator is required')
    if not isinstance(candidate_ids,list) or not 1<=len(candidate_ids)<=200 or len(set(candidate_ids))!=len(candidate_ids):
        raise ValueError('Choose one to 200 distinct approved financial candidates')
    ids=[valid_id(id) for id in candidate_ids]
    from src.etl.submission_service import get_store
    store=store or get_store()
    owned_engine=engine is None
    if engine is None:
        from sqlalchemy import create_engine
        from config.db_config import get_db_config
        engine=create_engine(get_db_config().connection_string,pool_pre_ping=True)
    try:
        metadata.create_all(engine)
        with store.publishing(ids) as heartbeat:
            rows=[store.get(id) for id in ids]
            if any(row.get('review_status') not in {'APPROVED','PUBLISHED'} for row in rows):
                raise ValueError('Only explicitly approved financial candidates may be published')
            published=[row for row in rows if row.get('review_status')=='PUBLISHED']
            if published:
                versions={row.get('published_version') for row in published}
                if len(published)!=len(rows) or len(versions)!=1 or not all(row.get('publication') for row in published):
                    raise ValueError('Choose one unpublished approved batch or replay one original published batch')
                return {**published[0]['publication'],'idempotent':True}
            request_digest=identity_digest(dict(candidates=[dict(id=row['candidate_id'],pipeline=row.get('pipeline_sha256'),
                artifacts=row.get('artifact_hashes'),review=row.get('review')) for row in sorted(rows,key=lambda r:r['candidate_id'])]))
            with engine.connect() as conn:
                accepted=list(conn.execute(select(releases).where(releases.c.status=='accepted')).mappings())
            recovered=[row for row in accepted if json_payload(row['manifest']).get('publication_request_sha256')==request_digest]
            if recovered:
                if len(recovered)!=1:raise ValueError('Publication receipt recovery is ambiguous')
                accepted_release=recovered[0];manifest=json_payload(accepted_release['manifest'])
                receipt=dict(version=accepted_release['version'],reports=manifest['reports'],facts=manifest['facts'],
                    collection=accepted_release['index_collection'],idempotent=True)
                store.mark_published(ids,receipt,operator_id)
                return receipt
            pipeline=pipeline_digest()
            for row in rows:
                if row.get('pipeline_sha256')!=pipeline:
                    raise ValueError('Candidate extraction code changed; reprocess and review before publication')
                verify_artifacts(row.get('artifact_hashes'),root=ROOT,asset_id=row['candidate_id'])
                if file_digest(ROOT/row['source_path'])!=row['candidate_id']:
                    raise ValueError('Original financial report changed after review')
                validate_facts(row['facts'],[row['report']],data_version=row['candidate_id'])
                if not row.get('index',{}).get('accepted') or not all(row.get('quality',{}).get(k) is True for k in
                    ('identity_verified','reports_verified','facts_verified','index_verified')):
                    raise ValueError('Candidate verification is incomplete')
            selected=[row['report'] for row in rows]
            if len({_report_key(report) for report in selected})!=len(selected):
                raise ValueError('Two selected originals compete for the same report period')
            from src.agent.providers import resolve_embedding_function,embedding_fingerprint
            embedding=embedding or resolve_embedding_function('publish')
            fingerprint=embedding_fingerprint(embedding)
            if any(row['index'].get('embedding_fingerprint')!=fingerprint for row in rows):
                raise ValueError('Candidate embedding space differs from the configured query space')
            previous=_current(engine)
            old_version=previous.version if previous else None
            reports,facts,narratives=[],[],[]
            reuse=[row['index']['collection'] for row in rows]
            if previous:
                if previous.manifest.get('embedding_fingerprint')!=fingerprint:
                    raise ValueError('Current library uses another embedding space; use a new isolated library or explicit migration')
                reports,facts,narratives=_previous_artifacts(previous)
                reuse.append(previous.collection)
                previous_inventory={_report_key(report):report['document_version'] for report in reports}
                if all(previous_inventory.get(_report_key(row['report']))==row['candidate_id'] for row in rows) and previous.manifest.get('pipeline_sha256')==pipeline:
                    receipt=dict(version=previous.version,reports=len(reports),facts=len(facts),collection=previous.collection,idempotent=True)
                    store.mark_published(ids,receipt,operator_id)
                    if refresh_callback:refresh_callback(previous.version,reports)
                    return receipt
            replace={_report_key(report) for report in selected}
            reports=[report for report in reports if _report_key(report) not in replace]
            facts=[fact for fact in facts if _report_key(fact) not in replace]
            narratives=[n for n in narratives if _report_key(n) not in replace]
            for row in rows:
                identity=row['identity']
                reports.append({**row['report'],'aliases':identity.get('aliases',[])})
                facts.extend(row['facts'])
                path=(ROOT/row['narratives_file']).resolve()
                if not path.is_relative_to(ROOT/'data/runtime/submissions/assets'/row['candidate_id']):
                    raise ValueError('Candidate narratives escape their original asset directory')
                narratives.extend(_read_lines(path))
            reports.sort(key=_report_key)
            facts.sort(key=lambda value:(*_report_key(value),value['id']))
            narratives.sort(key=lambda n:(*_report_key(n),n['page']))
            names={}
            ordered=sorted(reports,key=lambda r:(r['year'],{'Q1':1,'HY':2,'Q3':3,'FY':4}[r['period']],r['document_version']))
            for report in ordered:names[report['stock_code']]=report['company']
            aliases={code:{r.get('original_company',r['company']) for r in reports if r['stock_code']==code} for code in names}
            for report in reports:
                report['original_company']=report.get('original_company',report['company'])
                report['company']=names[report['stock_code']]
                report['aliases']=sorted(set(report.get('aliases',[]))|aliases[report['stock_code']]-{report['company']})
            facts=[{**fact,'company':names[fact['stock_code']]} for fact in facts]
            narratives=[{**row,'original_company':row.get('original_company',row['company']),
                         'company':names[row['stock_code']]} for row in narratives]
            version=identity_digest(dict(pipeline=pipeline,embedding=fingerprint,reports=reports,
                facts=[{k:v for k,v in f.items() if k!='data_version'} for f in facts],narratives=identity_digest(dict(rows=narratives))))
            facts=[Fact.model_validate({**fact,'data_version':version}) for fact in facts]
            validate_facts(facts,reports,data_version=version)
            index=build_index(narratives,version,embedding=embedding,client=client,
                reuse_collections=reuse,heartbeat=heartbeat,index_scope='release')
            folder=ROOT/'data/runtime/submissions/releases'/version
            folder.mkdir(parents=True,exist_ok=True)
            _write(folder/'reports.json',reports)
            _write(folder/'facts.json',[f.model_dump(mode='json') for f in facts])
            _write(folder/'narratives.jsonl',narratives,lines=True)
            _write(folder/'index_coverage.json',index)
            inventory=[{key:report[key] for key in ('stock_code','year','period','document_version')} for report in reports]
            manifest=dict(version=version,dataset='real-uploaded-financial-reports',reports=len(reports),facts=len(facts),
                pipeline_sha256=pipeline,embedding_fingerprint=fingerprint,embedding_dimensions=embedding.dimensions,
                publication_request_sha256=request_digest,publication_candidate_ids=sorted(ids),
                export_path=folder.relative_to(ROOT).as_posix(),previous_version=old_version,
                dataset_profile=dict(id='user-financial-v1',kind='real',reports=len(reports),companies=len(names),inventory=inventory),
                artifact_hashes={name:file_digest(folder/name) for name in ('reports.json','facts.json','narratives.jsonl','index_coverage.json')})
            from src.etl.release_profiles import check_report_coverage
            check_report_coverage(manifest,reports)
            acceptance=dict(facts_verified=True,reports_verified=True,index_verified=index.get('accepted') is True,projections_verified=True,
                verified_facts=len(facts),excluded_items=sum(len(row.get('excluded',[])) for row in rows),
                extraction_complete=False,review_kind='administrator_confirmation_of_verified_subset')
            _write(folder/'manifest.json',manifest)
            _write(folder/'acceptance.json',acceptance)
            heartbeat()
            stage_release(engine,version,facts,reports,index['collection'],manifest)
            publish_release(engine,version,acceptance,project=lambda conn:_project_release(conn,facts,reports),expected_previous=old_version)
            receipt=dict(version=version,reports=len(reports),facts=len(facts),collection=index['collection'],idempotent=False)
            try:
                store.mark_published(ids,receipt,operator_id)
            except Exception:
                raise RuntimeError('Financial release committed but review receipt was not saved; retry publication to reconcile the same version') from None
            if refresh_callback:refresh_callback(version,reports)
            return receipt
    finally:
        if owned_engine:engine.dispose()
