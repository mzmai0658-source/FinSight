"""作品说明：提供当前发布财务快照，不允许访问任意文件。"""
from pathlib import Path
import hashlib
from sqlalchemy import inspect, select, func, or_, text
import json
from fastapi import HTTPException
from src.etl.provenance_store import current
from src.api.assets import _approved_path
from src.utils.original_archive import retained_original


def _published_catalogue(engine, page=1, size=12, keyword=''):
    if not inspect(engine).has_table(current.name):
        return {'records': [], 'total': 0, 'page': page}
    name = current.c.payload['facts'][0]['stock_abbr'].as_string()
    source = current.c.payload['document']['source_path'].as_string()
    quality = current.c.payload['quality']
    statement = select(current.c.stock_code, current.c.report_year, current.c.report_period,
                       name.label('company'), current.c.payload['document'].label('document'),
                       quality.label('quality'))
    conditions = [source.is_not(None)]
    if keyword.strip():
        pattern = '%' + keyword.strip().replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
        conditions.append(or_(current.c.stock_code.like(pattern, escape='!'), name.like(pattern, escape='!'), source.like(pattern, escape='!')))
    with engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(current).where(*conditions)).scalar_one()
        rows = conn.execute(statement.where(*conditions).order_by(current.c.report_year.desc(), current.c.stock_code, current.c.report_period).offset((page-1)*size).limit(size)).mappings().all()
    records=[]
    for row in rows:
        doc=row['document'] or {}
        quality_data=row['quality'] or {}
        available=False
        try:
            path=_approved_path(doc.get('source_path', ''))
            available=path.suffix.lower()=='.pdf' and bool(doc.get('source_sha256'))
        except (OSError, ValueError):
            pass
        records.append({'stockCode':row['stock_code'],'company':row['company'] or row['stock_code'],
                        'reportYear':row['report_year'],'reportPeriod':row['report_period'],
                        'fileName':str(doc.get('source_path') or '').replace('\\','/').split('/')[-1],
                        'pageCount':doc.get('page_count'),'sha256':doc.get('source_sha256'),
                        'status':'imported','pdfAvailable':available,
                        'qualityStatus':quality_data.get('review_status','unknown'),
                        'qualityWarnings':quality_data.get('warnings') or [],
                        'missingFields':quality_data.get('missing_required_fields') or []})
    return {'records':records,'total':total,'page':page}


def catalogue(engine, page=1, size=12, keyword=''):
    if not inspect(engine).has_table('financial_report_material'):
        return _published_catalogue(engine, page, size, keyword)
    # 作品说明：列表只返回轻量元数据，不加载大规模页内容。
    with engine.connect() as conn:
        rows=conn.execute(text('SELECT id,stock_code,company,report_year,report_period,report_kind,identity_status,'
                               'file_name,sha256,page_count,text_page_count,content_source,content_state,notes '
                               'FROM financial_report_material')).mappings().all()
    published=_published_catalogue(engine,1,100000)['records']
    by_hash={r['sha256']:r for r in published}
    records=[]
    seen=set()
    for row in rows:
        matching=by_hash.get(row['sha256'])
        structured=bool(matching and row['stock_code']==matching['stockCode'] and row['report_year']==matching['reportYear']
                        and row['report_period']==matching['reportPeriod'])
        seen.add(row['sha256'])
        records.append({'materialId':row['id'], 'stockCode':row['stock_code'],'company':row['company'],
                        'reportYear':row['report_year'],'reportPeriod':row['report_period'],'reportKind':row['report_kind'],
                        'identityStatus':row['identity_status'],'fileName':row['file_name'],'sha256':row['sha256'],
                        'pageCount':row['page_count'],'textPageCount':row['text_page_count'],
                        'contentSource':row['content_source'],'contentState':row['content_state'],'notes':row['notes'],
                        'status':'imported' if structured else 'text_only', 'structured':structured,
                        'pdfAvailable':bool(matching and structured and matching['pdfAvailable']),
                        'qualityStatus':matching.get('qualityStatus','unknown') if matching else 'not_structured',
                        'qualityWarnings':matching.get('qualityWarnings',[]) if matching else [],
                        'missingFields':matching.get('missingFields',[]) if matching else []})
    # 作品说明：新发布上传在批处理前即可出现在列表中。
    records.extend({**row,'structured':True} for row in published if row['sha256'] not in seen)
    stats={'total':len(records),'structured':sum(r.get('structured',False) for r in records),
           'readable':sum(r.get('textPageCount',0)>0 for r in records)}
    needle=keyword.strip().casefold()
    if needle:
        records=[r for r in records if needle in ' '.join(str(r.get(k) or '') for k in ['stockCode','company','fileName','reportYear']).casefold()]
    records.sort(key=lambda r:(-(r['reportYear'] or 0),r['stockCode'],r['reportPeriod'],r['fileName']))
    return {'records':records[(page-1)*size:page*size], 'total':len(records),'page':page,'stats':stats}


def read_pages(engine, material_id: int, page: int=1):
    with engine.connect() as conn:
        row=conn.execute(text('SELECT file_name,sha256,page_count,text_page_count,pages,content_source,content_state,notes '
                              'FROM financial_report_material WHERE id=:id'),{'id':material_id}).mappings().first()
    if row is None: raise HTTPException(404,'财报正文未登记')
    pages=json.loads(row['pages']) if isinstance(row['pages'],str) else row['pages']
    if page<1 or page>max(1,len(pages)): raise HTTPException(404,'页码超出范围')
    return {'fileName':row['file_name'],'sha256':row['sha256'],'pageCount':row['page_count'],
            'ocrPageCount':len(pages),'page':page,'markdown':pages[page-1] if pages else '',
            'ocrAvailable':bool(pages),'contentSource':row['content_source'],'contentState':row['content_state'],'notes':row['notes']}


def original_pdf(engine, code: str, year: int, period: str) -> Path:
    if not inspect(engine).has_table(current.name):
        raise HTTPException(404, '财报尚未入库')
    with engine.connect() as conn:
        doc=conn.execute(select(current.c.payload['document']).where(current.c.stock_code==code,
                            current.c.report_year==year,current.c.report_period==period)).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, '财报原件未登记')
    try:
        try:return retained_original(doc.get('source_sha256',''))
        except FileNotFoundError:pass
        path=_approved_path(doc.get('source_path',''))
        if path.suffix.lower()!='.pdf': raise ValueError('Not PDF')
        with path.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=doc.get('source_sha256'): raise ValueError('Version changed')
        return path
    except (OSError, ValueError):
        raise HTTPException(404, '原件缺失或版本已变化，请重新导入')
