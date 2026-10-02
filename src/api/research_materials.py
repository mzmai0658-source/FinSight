"""作品说明：通过报告标识读取 OCR 快照，不接收浏览器文件路径。"""
import json
from fastapi import HTTPException
from sqlalchemy import text


def read_pages(engine, report_id: int, page: int = 1):
    with engine.connect() as conn:
        row = conn.execute(text('SELECT file_name, sha256, ocr_sha256, pages, page_count '
                                'FROM research_report_material WHERE report_id=:id'), {'id': report_id}).mappings().first()
    if row is None:
        raise HTTPException(404, '该研报尚未登记可阅读的正文')
    pages = row['pages']
    if isinstance(pages, str):
        pages = json.loads(pages)
    if page < 1 or page > max(1, len(pages)):
        raise HTTPException(404, '页码超出范围')
    return {'fileName': row['file_name'], 'sha256': row['sha256'], 'page': page,
            'pageCount': row['page_count'], 'ocrPageCount': len(pages),
            'markdown': pages[page-1] if pages else '',
            'ocrAvailable': bool(pages), 'source': 'imported_ocr'}
