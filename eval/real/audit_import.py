"""作品说明：将导入的开发事实与独立原始 PDF 候选比对；结果属于导入审计，不是模型得分或人工核验准确率。"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a new audit output')
    from eval.real.runtime import configure_environment
    configure_environment(ROOT / '.env.real-eval')
    from config.db_config import get_readonly_db_config
    from src.agent.sql_tool import SQLTool
    from src.agent.domain import get_table_fields
    from pypdf import PdfReader
    if get_readonly_db_config().database != 'finsight_real_eval':
        raise RuntimeError('Dedicated evaluation database required')
    labels = [json.loads(line) for line in (ROOT / 'data/runtime/real_validation/gold.development.jsonl').read_text(encoding='utf-8').splitlines() if line]
    schema = get_table_fields()
    documents = {}
    records = []
    for label in labels:
        if not label['id'].startswith('real-num-'):
            continue
        for expected in label['expected'].get('values', []):
            table, field = expected['table'], expected['field']
            code, year, period = expected['stock_code'], int(expected['report_year']), expected['report_period']
            if table not in schema or field not in schema[table] or not re.fullmatch(r'\d{6}', code) or period not in {'FY', 'HY', 'Q1', 'Q3'}:
                raise ValueError('Invalid candidate identity or field')
            output = SQLTool().run(f"SELECT stock_code,report_year,report_period,{field} FROM {table} WHERE stock_code='{code}' AND report_year={year} AND report_period='{period}'")
            rows = output.get('rows') or []
            actual = rows[0].get(field) if len(rows) == 1 else None
            source = (rows[0].get('_provenance') or {}).get(field, {}) if len(rows) == 1 else {}
            same_value = actual is not None and math.isclose(float(actual), float(expected['value']), rel_tol=0, abs_tol=expected['tolerance'])
            page_match = False
            source_error = None
            if source.get('status') == 'source_located':
                try:
                    path = (ROOT / source['source_path']).resolve()
                    if not path.is_relative_to((ROOT / 'data_root').resolve()):
                        raise ValueError('Source outside original document directory')
                    if path not in documents:
                        documents[path] = (hashlib.sha256(path.read_bytes()).hexdigest(), PdfReader(path))
                    digest, reader = documents[path]
                    start, end = int(source['page_start']), int(source['page_end'])
                    if digest != expected['source_sha256'] or digest != source['source_sha256'] or not 1 <= start <= end <= len(reader.pages):
                        raise ValueError('Source version or physical page mismatch')
                    raw = source.get('raw_value')
                    numbers = []
                    for page in reader.pages[start-1:end]:
                        text = page.extract_text() or ''
                        numbers.extend(float(n.replace(',', '')) for n in re.findall(r'(?<![\d.])-?\d[\d,]*(?:\.\d+)?', text))
                    page_match = raw is not None and any(math.isclose(float(raw), n, rel_tol=0, abs_tol=1e-7) for n in numbers)
                except Exception as exc:
                    source_error = f'{type(exc).__name__}: {exc}'
            records.append({'id': label['id'], 'actual': actual, 'candidate': expected['value'],
                            'candidate_agreement': same_value, 'source': source,
                            'raw_number_on_claimed_pdf_pages': page_match, 'source_error': source_error,
                            'human_verified': False})
    report = {'kind': 'development_ingestion_audit', 'human_verified': False,
              'planned': len(records), 'present': sum(r['actual'] is not None for r in records),
              'candidate_agreement': sum(r['candidate_agreement'] for r in records),
              'raw_number_on_claimed_pdf_pages': sum(r['raw_number_on_claimed_pdf_pages'] for r in records),
              'note': 'Numeric occurrence is a page-location check; it does not replace row/column/scope review.',
              'records': records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'records'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
