"""作品说明：PDF 字段来源与财务快照在同一事务中版本化保存。"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from sqlalchemy import Column, Integer, JSON, MetaData, String, Table, and_, select

ROOT = Path(__file__).resolve().parents[2]
metadata = MetaData()
current = Table('financial_report_provenance', metadata,
    Column('stock_code', String(20), primary_key=True), Column('report_year', Integer, primary_key=True),
    Column('report_period', String(8), primary_key=True), Column('payload', JSON, nullable=False))
versions = Table('financial_report_versions', metadata,
    Column('version_id', String(64), primary_key=True), Column('payload', JSON, nullable=False))


def describe_pdf(path: str | Path) -> dict:
    from pypdf import PdfReader
    path = Path(path).resolve()
    if not path.is_relative_to((ROOT/'data_root').resolve()) or path.suffix.lower() != '.pdf':
        raise ValueError('Original financial PDF must be under data_root')
    with path.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
    return {'document_id':'pdf-'+digest,'source_sha256':digest,'document_version':digest,
            'source_path':path.relative_to(ROOT).as_posix(),'page_count':len(PdfReader(path).pages)}


def identity_clause(data: dict):
    return and_(*(current.c[k] == data[k] for k in ('stock_code','report_year','report_period')))


def persist_snapshot(conn, data: dict, tables: dict, fill_only: bool = False) -> None:
    """作品说明：不自行执行 DDL 或提交，与调用方财务行使用同一事务。"""
    identity={k:data[k] for k in ('stock_code','report_year','report_period')}
    old=conn.execute(select(current.c.payload).where(identity_clause(identity))).scalar_one_or_none()
    payload=old if fill_only and old else {**identity,'document':data.get('_source_document'),'facts':[]}
    if not (fill_only and old):
        payload['quality']={
            'review_status':str(data.get('_pre_save_review_status') or 'unknown'),
            'warnings':list(data.get('_pre_save_review_warnings') or []),
            'blockers':list(data.get('_pre_save_review_blockers') or []),
            'missing_required_fields':list(data.get('_missing_required_fields') or []),
            'optional_missing_fields':list(data.get('_optional_llm_missing_fields') or []),
            'anomaly_flags':list(data.get('anomaly_flags') or []),
            'requires_manual_review':bool(data.get('_requires_manual_review')),
        }
    by_key={(f['table'],f['field']):f for f in payload['facts']}
    document=data.get('_source_document') or {}
    sources=data.get('_field_sources') or {}
    for table,row in tables.items():
        for name,value in row.items():
            if name in {'stock_code','stock_abbr','report_year','report_period'} or value is None:
                continue
            if fill_only and (table,name) in by_key and math.isclose(float(by_key[table,name]['value']),float(value),rel_tol=1e-12,abs_tol=1e-8): continue
            source=sources.get(f'{table}.{name}') or sources.get(name) or {}
            fact={**identity,'stock_abbr':data.get('stock_abbr'),'table':table,'field':name,'value':value,
                  'status':'source_unlocated'}
            value_semantics=str(source.get('value_semantics') or 'direct')
            page=source.get('page_start')
            try: matches_new = math.isclose(float(data.get(f'{table}.{name}', data.get(name))),float(value),rel_tol=1e-12,abs_tol=1e-8)
            except (TypeError,ValueError): matches_new=False
            if 'normalized_value' in source:
                try:
                    precision=max(0,min(int(source.get('storage_precision',2)),8))
                    matches_new = matches_new and math.isclose(float(source['normalized_value']),float(value),rel_tol=0,abs_tol=0.5*10**(-precision)+1e-8)
                except (TypeError,ValueError): matches_new=False
            page_end=source.get('page_end') or page
            if matches_new and value_semantics in {'derived','defaulted'}:
                fact.update({k:source.get(k) for k in (
                    'value_semantics','derivation','derived_from','supporting_sources',
                    'extraction_mode','statement_scope','page_start','page_end','table_name',
                ) if source.get(k) is not None})
                fact.update(
                    status='derived' if value_semantics == 'derived' else 'defaulted',
                    location_precision='calculated_from_sources' if value_semantics == 'derived' else 'no_literal_source',
                    human_verified=False,
                )
            elif matches_new and document and isinstance(page,int) and isinstance(page_end,int) and 1 <= page <= page_end <= document['page_count']:
                fact.update(document)
                fact.update({k:source.get(k) for k in ('page_start','page_end','table_name','raw_value','unit_multiplier','extraction_mode','statement_scope','row_name','column_name','value_semantics','mapped_from')})
                fact.update(status='source_located',location_precision='table_page',human_verified=False)
            by_key[table,name]=fact
    payload['facts']=list(by_key.values())
    # 作品说明：完整来源快照生成稳定版本，便于审计及重试。
    payload=json.loads(json.dumps(payload,ensure_ascii=False,default=str))
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if conn.execute(select(versions.c.version_id).where(versions.c.version_id==digest)).first() is None:
        conn.execute(versions.insert().values(version_id=digest,payload=payload))
    conn.execute(current.delete().where(identity_clause(identity)))
    conn.execute(current.insert().values(**identity,payload=payload))


def read_facts(conn, identities: list[dict]) -> list[dict]:
    clauses=[];seen=set()
    for data in identities:
        if any(data.get(k) is None for k in ('stock_code','report_year','report_period')): continue
        key=tuple(str(data[k]) for k in ('stock_code','report_year','report_period'))
        if key not in seen: clauses.append(identity_clause(data));seen.add(key)
    if not clauses:return []
    from sqlalchemy import or_
    return [fact for payload in conn.execute(select(current.c.payload).where(or_(*clauses))).scalars() for fact in payload.get('facts',[])]
