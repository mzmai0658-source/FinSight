"""作品说明：按内容盘点原始 PDF，从封面识别报告身份，不采用披露日期替代期间。"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]


def inspect_pdf(path: Path) -> dict:
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    record = {'document_id': 'pdf-' + digest, 'source_path': path.relative_to(ROOT).as_posix(),
              'source_sha256': digest, 'size_bytes': path.stat().st_size,
              'source_url': None, 'identity_status': 'pending', 'error': None}
    try:
        reader = PdfReader(path)
        cover = '\n'.join(p.extract_text() or '' for p in reader.pages[:3])
        compact = re.sub(r'\s+', '', cover)
        title = re.search(r'(20\d{2})年?(半年度|年度|第一季度|第三季度|一季度|三季度)报告(摘要)?', compact)
        code = re.search(r'(?<!\d)([036]\d{5})(?!\d)', path.stem)
        record.update(page_count=len(reader.pages), stock_code=code[1] if code else None,
                      ocr_caches=[p.relative_to(ROOT).as_posix() for p in sorted(path.parent.glob(path.name+'*OCR*.json'))])
        if title:
            record.update(report_year=int(title[1]), report_period={'半年度':'HY','年度':'FY','第一季度':'Q1','一季度':'Q1','第三季度':'Q3','三季度':'Q3'}[title[2]],
                          is_summary=bool(title[3]), title_match=title[0])
            record['identity_status'] = 'cover_candidate' if code and code[1] in compact else 'needs_code_confirmation'
        else:
            record.update(report_year=None, report_period=None, is_summary=None)
    except Exception as exc:
        record['error'] = f'{type(exc).__name__}: {exc}'
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT/'data_root/financial_reports')
    parser.add_argument('--output',type=Path,default=ROOT/'data/runtime/real_validation/catalog.json')
    args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    checkpoint=args.output.with_suffix('.jsonl')
    existing={r['source_path']:r for r in (json.loads(s) for s in checkpoint.read_text(encoding='utf-8').splitlines())} if checkpoint.exists() else {}
    rows=[]
    with checkpoint.open('a',encoding='utf-8') as stream:
        for index,path in enumerate(sorted(args.root.rglob('*.pdf')),1):
            relative=path.relative_to(ROOT).as_posix()
            # 作品说明：恢复盘点时仍重新计算文件摘要，以发现内容变化。
            old=existing.get(relative)
            with path.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
            row=old if old and old.get('source_sha256')==digest else inspect_pdf(path)
            rows.append(row)
            if row is not old:
                stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
            if index%50==0: print(f'Catalogued {index} PDFs',flush=True)
    groups={}
    for row in rows: groups.setdefault(row['source_sha256'],[]).append(row['source_path'])
    payload={'created_at_utc':datetime.now(timezone.utc).isoformat(),'file_count':len(rows),'unique_contents':len(groups),
             'identity_note':'Cover candidates need confirmation; publication dates are not report years. No human review is implied.',
             'duplicate_groups':[v for v in groups.values() if len(v)>1], 'documents':rows}
    temp=args.output.with_suffix('.tmp');temp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(args.output)
    print(f'Inventory complete: {len(rows)} files, {len(groups)} content hashes; {args.output}',flush=True)


if __name__=='__main__': main()
