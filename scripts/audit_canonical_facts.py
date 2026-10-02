"""作品说明：从当前 117 份原件准备候选事实，提取完成本身不构成发布资格。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.etl.canonical_audit import audit_report,extractor_digest
from src.agent.v3.repository import identity_digest, json_payload, stage_release


def accepted_original_cache(engine,folder):
    import hashlib
    from sqlalchemy import text
    version=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))['version']
    with engine.connect() as conn:
        value=conn.execute(text("SELECT manifest FROM financial_data_releases WHERE version=:version AND status='accepted'"),{'version':version}).scalar_one()
    artifacts=json_payload(value)['acceptance']['artifacts']
    receipt=artifacts['narratives.jsonl']
    path=folder/'narratives.jsonl'
    if hashlib.sha256(path.read_bytes()).hexdigest()!=receipt:raise ValueError('Accepted original-text artifact changed')
    report_path=folder/'reports.json'
    if hashlib.sha256(report_path.read_bytes()).hexdigest()!=artifacts['reports.json']:raise ValueError('Accepted original page-count artifact changed')
    reports=json.loads(report_path.read_text(encoding='utf-8'))
    page_counts={row['document_version']:row['page_count'] for row in reports}
    documents={}
    for line in path.read_text(encoding='utf-8').splitlines():
        row=json.loads(line);pages=documents.setdefault(row['document_version'],{})
        if row['page'] in pages:raise ValueError('Duplicate original cache page')
        pages[row['page']]=row['text']
    result={}
    for digest,page_count in page_counts.items():
        pages=documents.get(digest,{})
        if any(page<1 or page>page_count for page in pages):raise ValueError('Original cache page exceeds its accepted physical document')
        # 作品说明：已验收提取器不将空白文本页纳入叙述；封存的报告清单保留缺页的物理位置。
        result[digest]=[pages.get(page,'') for page in range(1,page_count+1)]
    return result,dict(version=version,narratives_sha256=receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', default='.env.integration')
    parser.add_argument('--output', default='data/runtime/v3/audit')
    parser.add_argument('--codes', default='')
    parser.add_argument('--stage', action='store_true')
    parser.add_argument('--reuse-originals',default='',help='Reuse only a DB-accepted, hash-anchored original-text artifact; PDF bytes are still checked')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / args.env, override=True)
    from sqlalchemy import create_engine, text
    from config.db_config import get_db_config
    engine = create_engine(get_db_config().connection_string)
    original_cache,cache_receipt=accepted_original_cache(engine,ROOT/args.reuse_originals) if args.reuse_originals else (None,None)
    with engine.connect() as conn:
        payloads = [json_payload(p) for p in conn.execute(text('SELECT payload FROM financial_report_provenance')).scalars()]
    if args.codes:
        payloads = [p for p in payloads if p['stock_code'] in args.codes.split(',')]
    import hashlib
    extractor=extractor_digest()
    version = identity_digest({'extractor': extractor,
        'documents': sorted(p['document']['source_sha256'] for p in payloads)})
    output = (ROOT / args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    all_facts, ledgers, errors = [], [], []
    with (output / 'narratives.jsonl').open('w', encoding='utf-8') as narrative_file:
        for index, p in enumerate(payloads, 1):
            d = p['document']
            report = {**d, 'stock_code': p['stock_code'], 'company': next((f.get('stock_abbr') for f in p['facts'] if f.get('stock_abbr')), p['stock_code']),
                'year': p['report_year'], 'period': p['report_period']}
            try:
                fact_list, ledger, narratives = audit_report(report, version,original_cache)
                all_facts.extend(fact_list); ledgers.append(ledger)
                for row in narratives:
                    narrative_file.write(json.dumps(row, ensure_ascii=False) + '\n')
                print(f'{index}/{len(payloads)} {p["stock_code"]} {p["report_year"]}{p["report_period"]}: {len(fact_list)} facts, {len(ledger["conflicts"])} conflicts', flush=True)
            except Exception as exc:
                errors.append(dict(report=report, error=type(exc).__name__, detail=str(exc)))
                print(f'{index}/{len(payloads)} ERROR {p["stock_code"]}: {type(exc).__name__}', flush=True)
    (output / 'facts.json').write_text(json.dumps([f.model_dump(mode='json') for f in all_facts], ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'reports.json').write_text(json.dumps(ledgers, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest = dict(version=version, extractor_sha256=extractor, reports=len(ledgers), facts=len(all_facts), errors=errors,
        conflicts=sum(len(r['conflicts']) for r in ledgers), accepted=False, original_cache_receipt=cache_receipt)
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.stage:
        if errors or len(ledgers) != 117:
            raise ValueError('Cannot stage an incomplete report cohort')
        stage_release(engine, version, all_facts, ledgers, 'financial_v3_' + version[:16], manifest)
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == '__main__':
    main()
