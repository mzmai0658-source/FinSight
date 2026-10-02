"""作品说明：登记本机研报 PDF 与已有 OCR 文本，不调用 OCR、模型或网络。指定 --env-file 预览，加 --apply 导入；重复运行更新同一登记，避免重复文件。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.etl.research_metadata import load_research_metadata, normalize_title
from src.utils.ocr_json_parser import find_json_cache_for_pdf, iter_layout_pages
from pypdf import PdfReader
from sqlalchemy import create_engine, text


def filename_key(title):
    return re.sub(r'[<>:"/\\|?*]', '_', normalize_title(title)).casefold()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare(root, metadata):
    candidates = {}
    for meta in metadata.values():
        candidates.setdefault(filename_key(meta.title), []).append(meta)
    records = []
    for pdf in sorted(root.rglob('*.pdf')):
        meta = metadata.get(normalize_title(pdf.stem))
        match = 'exact'
        if meta is None:
            matches = candidates.get(filename_key(pdf.stem), [])
            if len(matches) == 1:
                meta, match = matches[0], 'filename_normalized'
            else:
                raise ValueError(f'Metadata missing or ambiguous: {pdf.name}')
        cache = find_json_cache_for_pdf(str(pdf))
        pages, ocr_sha = [], ''
        if cache:
            cache_path = Path(cache)
            raw = json.loads(cache_path.read_text(encoding='utf-8'))
            pages = [str((page.get('markdown') or {}).get('text') or '') for page in iter_layout_pages(raw)]
            ocr_sha = digest(cache_path)
        if not pages or not any(page.strip() for page in pages):
            raise ValueError(f'No usable OCR text: {pdf.name}')
        extra = meta.extra or {}
        record = {
            'title': meta.title[:300], 'report_type': 'stock' if pdf.parent.name == '个股研报' else 'industry',
            'stock_code': meta.stock_code, 'stock_name': meta.stock_name[:50],
            'org_name': meta.org_name[:120], 'org_sname': meta.org_sname[:60],
            'publish_date': meta.publish_date or None, 'industry_name': meta.industry_name[:80],
            'rating': meta.rating[:30], 'last_rating': extra.get('last_rating', '')[:30],
            'researcher': meta.researcher[:120], 'predict_this_year_eps': extra.get('predict_this_year_eps', '')[:20],
            'predict_this_year_pe': extra.get('predict_this_year_pe', '')[:20],
            'aim_price': extra.get('aim_price', '')[:20], 'dataset_tag': meta.dataset_tag[:20], 'pdf_present': 1,
        }
        source_path = pdf.resolve().relative_to(ROOT).as_posix() if pdf.resolve().is_relative_to(ROOT) else pdf.resolve().as_posix()
        records.append({'metadata': record, 'source_path': source_path,
                        'source_key': hashlib.sha256(source_path.encode()).hexdigest(), 'file_name': pdf.name,
                        'sha256': digest(pdf), 'ocr_sha256': ocr_sha, 'pages': pages,
                        'page_count': len(PdfReader(pdf).pages), 'metadata_match': match})
    return records


def apply_records(engine, records):
    """作品说明：通过单个事务发布批次，调用方应在调用前完成数据库备份。"""
    with engine.begin() as conn:
        for record in records:
            key = {'key': record['source_key']}
            report_id = conn.execute(text('SELECT report_id FROM research_report_material WHERE source_key=:key'), key).scalar()
            meta = record['metadata']
            if report_id is None:
                report_id = conn.execute(text('SELECT id FROM research_report WHERE title=:title AND org_name=:org_name '
                                              'AND publish_date <=> :publish_date'), meta).scalar()
            if report_id is None:
                columns = ','.join(meta)
                params = ','.join(':'+key for key in meta)
                report_id = conn.execute(text(f'INSERT INTO research_report ({columns}) VALUES ({params})'), meta).lastrowid
            else:
                assignments = ','.join(f'{key}=:{key}' for key in meta)
                conn.execute(text(f'UPDATE research_report SET {assignments} WHERE id=:id'), {**meta, 'id': report_id})
            values = {key: record[key] for key in ['source_key','source_path','file_name','sha256','ocr_sha256','page_count']}
            values.update(report_id=report_id, pages=json.dumps(record['pages'], ensure_ascii=False))
            conn.execute(text('INSERT INTO research_report_material (report_id,source_key,source_path,file_name,sha256,ocr_sha256,pages,page_count) '
                              'VALUES (:report_id,:source_key,:source_path,:file_name,:sha256,:ocr_sha256,:pages,:page_count) '
                              'ON DUPLICATE KEY UPDATE source_path=VALUES(source_path),file_name=VALUES(file_name),'
                              'sha256=VALUES(sha256),ocr_sha256=VALUES(ocr_sha256),pages=VALUES(pages),page_count=VALUES(page_count)'), values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--output', default='data/runtime/real_validation/research_library_637')
    args = parser.parse_args()
    from dotenv import dotenv_values
    os.environ.update({k:v for k,v in dotenv_values(args.env_file).items() if v is not None})
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    from config.db_config import get_db_config
    records = prepare(ROOT/'data_root/research_reports', load_research_metadata())
    output = ROOT/args.output
    output.mkdir(parents=True, exist_ok=True)
    manifest = [{k:v for k,v in record.items() if k != 'pages'} | {'ocr_pages':len(record['pages'])} for record in records]
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = {'pdfs':len(records), 'stock':sum(r['metadata']['report_type']=='stock' for r in records),
               'industry':sum(r['metadata']['report_type']=='industry' for r in records),
               'ocr_pages':sum(len(r['pages']) for r in records),
               'normalized_matches':sum(r['metadata_match']=='filename_normalized' for r in records),
               'page_count_mismatches':sum(len(r['pages'])!=r['page_count'] for r in records), 'applied':False}
    if args.apply:
        engine = create_engine(get_db_config().connection_string)
        # 作品说明：只备份此功能相关表，不记录连接凭据。
        backup = output/'before_import.json'
        if not backup.exists():
            with engine.connect() as conn:
                snapshot = {table:[dict(r) for r in conn.execute(text(f'SELECT * FROM {table}')).mappings()]
                            for table in ['research_report','research_report_material']}
            backup.write_text(json.dumps(snapshot, ensure_ascii=False, default=str), encoding='utf-8')
        apply_records(engine, records)
        with engine.connect() as conn:
            summary['registered_total'] = conn.execute(text('SELECT COUNT(*) FROM research_report_material')).scalar_one()
        summary['applied'] = True
        engine.dispose()
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
