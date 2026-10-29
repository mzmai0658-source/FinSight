"""单份真实OCR缓存的有界抽取检查，不调用入库、索引或发布入口。"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.etl.etl_worker import ETLWorker
from src.utils.ocr_json_parser import find_json_cache_for_pdf


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class BoundedClient:
    """Count every actual call, including failed calls; no hidden retry allowance."""
    def __init__(self, client, maximum):
        self.client, self.maximum = client, maximum
        self.calls, self.diagnostics = 0, []

    def chat(self, *args, **kwargs):
        if self.calls >= self.maximum:
            raise RuntimeError('extraction_generation_budget_exhausted')
        self.calls += 1
        try:
            return self.client.chat(*args, **kwargs)
        finally:
            diagnostics = dict(getattr(self.client, 'last_diagnostics', {}) or {})
            self.diagnostics.append({k: v for k, v in diagnostics.items() if k in {'model', 'usage', 'reason', 'attempts'}})


def load_sample(audit, assets_root):
    audit, assets_root = Path(audit), Path(assets_root).resolve()
    reports = json.loads((audit/'reports.json').read_text(encoding='utf-8'))
    report = sorted(reports, key=lambda r: (r['stock_code'], r['year'], r['period']))[0]
    pdf = (assets_root/report['source_path']).resolve()
    if not pdf.is_relative_to((assets_root/'data_root').resolve()) or not pdf.is_file() or sha(pdf) != report['document_version']:
        raise ValueError('Original real PDF identity/hash does not match the accepted report')
    cache = find_json_cache_for_pdf(str(pdf))
    if not cache or sha(cache) != report['cache_sha256']:
        raise ValueError('Original real OCR cache identity/hash does not match the accepted report')
    worker = ETLWorker(None, enable_llm_extraction=False)
    content, chunks = worker._load_from_single_ocr_json(cache)
    if not content.strip() or not chunks:
        raise ValueError('The accepted OCR sample has no readable text/table chunks')
    return {'report': report, 'pdf': pdf, 'cache': Path(cache), 'pdf_sha256': sha(pdf),
            'cache_sha256': sha(cache), 'content': content, 'chunks': chunks}


def run_probe(audit, assets_root=ROOT, *, generate=False, client=None):
    sample = load_sample(audit, assets_root)
    if generate and client is None:
        from src.agent.llm_client import LLMClient
        client = LLMClient()
        if not client.is_configured or client.model != 'deepseek-flash' or client.max_retries != 0:
            raise ValueError('Use the same configured cloud model and zero network retries as focused28')
    bounded = BoundedClient(client, 2 if generate else 0)
    worker = ETLWorker(bounded, enable_llm_extraction=generate, enable_optional_llm_supplement=generate)
    # Use the real source parser and normal rule-first extraction. A full run() is
    # intentionally excluded because it archives originals and may write wide tables.
    chunks = [worker._normalize_chunk_for_processing(c) for c in sample['chunks']]
    selected = next((c for c in chunks if worker._should_accept_chunk(c)), None)
    if selected is None:
        raise ValueError('No supported financial table in the verified OCR sample')
    report = sample['report']
    result = {'scope': 'one verified real OCR sample; metadata and one normal table extraction; not a full ETL/RAG rebuild',
              'report': {k: report[k] for k in ('stock_code', 'year', 'period')},
              'pdf': str(sample['pdf']), 'cache': str(sample['cache']),
              'pdf_sha256': sample['pdf_sha256'], 'cache_sha256': sample['cache_sha256'],
              'table_type': selected.get('table_type'), 'page': selected.get('page_index'),
              'database_writes': 0, 'index_writes': 0, 'generation_calls': 0, 'generated': generate}
    if not generate:
        result['status'] = 'local_cache_verified_cloud_not_run'
        return result
    try:
        metadata = worker._extract_metadata_with_llm(sample['content'][:6000])
        result['metadata'] = metadata
        result['metadata_matches_report'] = (
            str(metadata.get('stock_code', '')).zfill(6) == report['stock_code'] and
            str(metadata.get('report_year')) == str(report['year']) and metadata.get('report_period') == report['period'])
        data, route = worker._extract_chunk_data(selected)
        result.update(table_data=data, table_route=route,
                      status='completed_pending_numeric_review' if result['metadata_matches_report'] else 'metadata_identity_failed')
    except Exception as exc:
        # Preserve attempted calls and safe diagnostics, never provider exception text.
        result.update(status='failed', error_type=type(exc).__name__)
    result.update(generation_calls=bounded.calls, calls=bounded.diagnostics)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, default=ROOT/'.local_runtime/Real Rebuild Room/FinSight/data/runtime/real-rebuild-ocr')
    parser.add_argument('--assets-root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--generate', action='store_true', help='Explicitly allow at most two cloud calls; default is offline')
    args = parser.parse_args(argv)
    if args.generate and args.output is None:
        parser.error('--generate requires a new --output to retain its first result')
    if args.output and args.output.exists():
        parser.error('The first OCR result already exists; it will not be replaced')
    result = run_probe(args.audit, args.assets_root, generate=args.generate)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k not in {'table_data', 'metadata', 'calls'}}, ensure_ascii=False))
    return 0 if result['status'] not in {'failed', 'metadata_identity_failed'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
