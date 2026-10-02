"""作品说明：导入本机财务 PDF 目录与现有文本，不提取财务事实。要求显式环境文件，先预览再用 --apply 发布；逐文档文件控制内存并支持恢复。"""
import argparse
from collections import Counter
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pypdf import PdfReader
from src.utils.ocr_json_parser import find_json_cache_for_pdf, iter_layout_pages
from scripts.import_local_research_materials import digest


REPORT_TITLE_PATTERN = re.compile(
    r'(20\d{2})年?(半年度报告|中期报告|第一季度报告|第三季度报告|年度报告|年报)'
    r'(?P<summary>[（(]?摘要(?:版)?[)）]?)?'
)
IDENTITY_COLUMNS = ('stock_code', 'company', 'report_year', 'report_period', 'report_kind', 'identity_status')


def title_is_summary(text, title):
    if not title:
        return False
    if title.group('summary'):
        return True
    # 作品说明：部分交易所摘要 PDF 的标题省略摘要字样，可通过标题紧邻的标准说明确认摘要身份。
    title_region = text[title.start():title.end() + 400]
    return bool(re.search(r'本(?:半年度|年度|季度)报告摘要来自(?:半年度|年度|季度)?报告全文', title_region))


def company_index():
    import pandas as pd
    codes, names = {}, {}
    for path in (ROOT/'data_root/registry').glob('*.xlsx'):
        for _, row in pd.read_excel(path, dtype=str).iterrows():
            digits = re.sub(r'\D', '', str(row.get('股票代码', '')))
            if not digits or len(digits) > 6:
                continue
            code = digits.zfill(6)
            name = str(row.get('A股简称', '')).strip()
            if name and name.lower() != 'nan':
                codes[code] = name
                names[re.sub(r'\s+', '', name)] = code
    return codes, names


def identify(filename, cover, codes, names):
    """作品说明：报告年度来自报告标题，不能由披露日期推断。"""
    compact = lambda s: re.sub(r'\s+', '', unicodedata.normalize('NFKC', s))
    fname, head = compact(filename), compact(re.sub(r'<[^>]+>', ' ', cover[:64000]))
    prefix = compact(re.split(r'[:：]', filename)[0])
    match = re.match(r'(\d{6})_', fname)
    code = match.group(1) if match else ''
    if not code:
        code = names.get(prefix, '')
    if not code:
        match = re.search(r'(?:公司代码|证券代码|股票代码|A股代码)[:：]?(\d{6})', head)
        code = match.group(1) if match else ''
    filename_title = REPORT_TITLE_PATTERN.search(fname)
    cover_title = REPORT_TITLE_PATTERN.search(head)
    match = filename_title or cover_title
    year, period = None, ''
    if match:
        year = int(match.group(1))
        period = {'半年度报告':'HY','中期报告':'HY','第一季度报告':'Q1','第三季度报告':'Q3','年度报告':'FY','年报':'FY'}[match.group(2)]
    # 作品说明：只有与实际报告标题绑定的摘要标记有效；正文目录或备查文件中的摘要及全文表述不能使完整报告误判为摘要。
    summary = title_is_summary(fname, filename_title) or title_is_summary(head, cover_title)
    source_name = prefix if re.fullmatch(r'[\u4e00-\u9fffA-Za-z*]{2,30}', prefix) else ''
    return {'stock_code':code, 'company':codes.get(code, source_name or code or '公司待确认'),
            'report_year':year, 'report_period':period, 'report_kind':'summary' if summary else 'full' if match else 'unclassified',
            'identity_status':'identified' if code and year and period else 'unresolved'}


def prepare_one(pdf, codes, names):
    cache = find_json_cache_for_pdf(str(pdf))
    pages, content_source, notes = [], 'none', []
    ocr_sha = ''
    if cache:
        cache = Path(cache)
        ocr_sha = digest(cache)
        try:
            raw = json.loads(cache.read_text(encoding='utf-8'))
            pages = [str((p.get('markdown') or {}).get('text') or '') for p in iter_layout_pages(raw)]
            if any(p.strip() for p in pages):
                content_source = 'ocr'
            else:
                pages = []
        except (ValueError, OSError, TypeError) as error:
            notes.append('OCR 缓存不可读：'+type(error).__name__)
    reader = PdfReader(pdf)
    pdf_pages = len(reader.pages)
    if not pages:
        pages = [p.extract_text() or '' for p in reader.pages]
        if any(p.strip() for p in pages):
            content_source = 'pdf_text'
            notes.append('缺少可用 OCR，使用 PDF 内嵌文字')
        else:
            pages = []
            notes.append('缺少可阅读的文字')
    cover = '\n'.join(pages[:20])
    identity = identify(pdf.name, cover, codes, names)
    if identity['identity_status'] != 'identified':
        pdf_cover = '\n'.join(p.extract_text() or '' for p in reader.pages[:20])
        alternate = identify(pdf.name, pdf_cover, codes, names)
        if alternate['identity_status'] == 'identified':
            identity = alternate
    state = 'ready' if pages and len(pages)==pdf_pages else 'partial' if pages else 'missing'
    if state=='partial': notes.append('正文页数与 PDF 页数不一致')
    source_path = pdf.relative_to(ROOT).as_posix()
    return {**identity,'source_key':hashlib.sha256(source_path.encode()).hexdigest(), 'source_path':source_path,
            'file_name':pdf.name, 'sha256':digest(pdf), 'ocr_sha256':ocr_sha,
            'page_count':pdf_pages,'text_page_count':len(pages), 'pages':pages,
            'content_source':content_source, 'content_state':state, 'notes':'；'.join(notes)}


def publish(engine, prepared):
    from sqlalchemy import text
    for path in prepared:
        with gzip.open(path, 'rt', encoding='utf-8') as stream: record=json.load(stream)
        values={**record,'pages':json.dumps(record['pages'],ensure_ascii=False)}
        columns=','.join(values)
        params=','.join(':'+key for key in values)
        updates=','.join(f'{key}=VALUES({key})' for key in values if key!='source_key')
        # 作品说明：每份完整文档原子发布，失败批次支持安全恢复。
        with engine.begin() as conn:
            conn.execute(text(f'INSERT INTO financial_report_material ({columns}) VALUES ({params}) ON DUPLICATE KEY UPDATE {updates}'),values)


def publish_metadata(engine, records):
    """作品说明：只更新目录身份元数据，不插入或修改事实及页内容。"""
    from sqlalchemy import bindparam, text
    source_keys = [record['source_key'] for record in records]
    with engine.connect() as conn:
        existing = set(conn.execute(
            text('SELECT source_key FROM financial_report_material WHERE source_key IN :source_keys')
            .bindparams(bindparam('source_keys', expanding=True)),
            {'source_keys': source_keys},
        ).scalars())
    missing = sorted(set(source_keys) - existing)
    if missing:
        raise RuntimeError(f'Metadata-only apply refused: {len(missing)} catalogue rows are missing')
    assignments = ','.join(f'{key}=:{key}' for key in IDENTITY_COLUMNS)
    statement = text(f'UPDATE financial_report_material SET {assignments} WHERE source_key=:source_key')
    with engine.begin() as conn:
        for record in records:
            conn.execute(statement, {key: record[key] for key in ('source_key', *IDENTITY_COLUMNS)})
    return len(records)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--reuse-prepared',action='store_true')
    parser.add_argument('--metadata-only',action='store_true',
                        help='Require existing prepared files and refresh/apply only catalogue identity metadata')
    parser.add_argument('--output',default='data/runtime/real_validation/financial_library_1417')
    args=parser.parse_args()
    from dotenv import dotenv_values
    os.environ.update({k:v for k,v in dotenv_values(args.env_file).items() if v is not None})
    os.environ['PYTHON_DOTENV_DISABLED']='1'
    output=ROOT/args.output;prepared_dir=output/'prepared';prepared_dir.mkdir(parents=True,exist_ok=True)
    pdfs=sorted((ROOT/'data_root/financial_reports').rglob('*.pdf'))
    codes,names=company_index()
    manifest=[];prepared=[]
    logging.getLogger('pypdf').setLevel(logging.ERROR)
    for index,pdf in enumerate(pdfs,1):
        key=hashlib.sha256(pdf.relative_to(ROOT).as_posix().encode()).hexdigest()
        target=prepared_dir/(key+'.json.gz')
        if args.metadata_only and not target.exists():
            raise FileNotFoundError(f'Metadata-only refresh requires prepared data: {target}')
        if (args.reuse_prepared or args.metadata_only) and target.exists():
            with gzip.open(target,'rt',encoding='utf-8') as stream:record=json.load(stream)
            cache=find_json_cache_for_pdf(str(pdf))
            if record['sha256']!=digest(pdf) or record['ocr_sha256']!=(digest(Path(cache)) if cache else ''):
                raise ValueError(f'Source changed after preview: {pdf.name}')
            record.update(identify(pdf.name,'\n'.join(record['pages'][:20]),codes,names))
            if record['identity_status']=='unresolved':
                cover='\n'.join(p.extract_text() or '' for p in PdfReader(pdf).pages[:20])
                alternate=identify(pdf.name,cover,codes,names)
                if alternate['identity_status']=='identified':record.update(alternate)
            with gzip.open(target,'wt',encoding='utf-8') as stream:json.dump(record,stream,ensure_ascii=False)
        else:
            record=prepare_one(pdf,codes,names)
            with gzip.open(target,'wt',encoding='utf-8') as stream:json.dump(record,stream,ensure_ascii=False)
        manifest.append({k:v for k,v in record.items() if k!='pages'});prepared.append(target)
        if index%100==0:print(f'Prepared {index}/{len(pdfs)}',flush=True)
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    summary={'files':len(manifest),'mode':'metadata_only' if args.metadata_only else 'full',
             'companies':len({r['stock_code'] for r in manifest if r['stock_code']}),
             'content_sources':dict(Counter(r['content_source'] for r in manifest)),
             'content_states':dict(Counter(r['content_state'] for r in manifest)),
             'report_kinds':dict(Counter(r['report_kind'] for r in manifest)),
             'unresolved':sum(r['identity_status']=='unresolved' for r in manifest),
             'pdf_pages':sum(r['page_count'] for r in manifest),'text_pages':sum(r['text_page_count'] for r in manifest), 'applied':False}
    if args.apply:
        from config.db_config import get_db_config
        from sqlalchemy import create_engine,text
        engine=create_engine(get_db_config().connection_string)
        with engine.connect() as conn:
            before=conn.execute(text('SELECT COUNT(*) FROM financial_report_material')).scalar_one()
        summary['before_count']=before
        if args.metadata_only:
            summary['metadata_rows_updated']=publish_metadata(engine,manifest)
        else:
            publish(engine,prepared)
        with engine.connect() as conn:summary['registered_total']=conn.execute(text('SELECT COUNT(*) FROM financial_report_material')).scalar_one()
        summary['applied']=True;engine.dispose()
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
