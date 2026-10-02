"""作品说明：用开发原件的隔离副本开展不复用缓存的 OCR 验收。"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=3)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Preserve previous OCR evidence; use a new output directory')
    from dotenv import dotenv_values
    values = dotenv_values(ROOT / '.env')
    token = values.get('PADDLEOCR_API_TOKEN') or values.get('OCR_API_TOKEN')
    if not token:
        raise RuntimeError('No configured OCR token')
    os.environ.update(PADDLEOCR_API_TOKEN=token, OCR_API_PROTOCOL='paddle_async',
                      OCR_API_URL='https://paddleocr.aistudio-app.com/api/v2/ocr/jobs',
                      OCR_MODEL=values.get('OCR_MODEL') or 'PaddleOCR-VL-1.6', OCR_TIMEOUT_SECONDS='3600',
                      PYTHON_DOTENV_DISABLED='1')
    from src.etl.ocr_client import ensure_ocr_json
    from pypdf import PdfReader
    manifest = json.loads((ROOT / 'data/runtime/real_validation/report_manifest.json').read_text(encoding='utf-8'))
    # 作品说明：每家开发公司选页数最少的一份原件，不根据 OCR 结果挑选样本。
    selected = {}
    for doc in manifest['documents']:
        if doc['split'] != 'development':
            continue
        pages = len(PdfReader(ROOT / doc['source_path']).pages)
        key = doc['stock_code']
        if key not in selected or pages < selected[key]['page_count']:
            selected[key] = {**doc, 'page_count': pages}
    documents = list(selected.values())[:args.limit]
    args.output.mkdir(parents=True)
    status = {'planned': len(documents), 'selection': documents, 'records': [], 'status': 'running'}

    def checkpoint():
        target = args.output / 'ocr_check.json'
        pending = target.with_suffix('.tmp')
        pending.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
        pending.replace(target)

    checkpoint()
    for doc in documents:
        original = ROOT / doc['source_path']
        if hashlib.sha256(original.read_bytes()).hexdigest() != doc['source_sha256']:
            raise RuntimeError('Frozen original changed')
        copied = args.output / original.name
        shutil.copyfile(original, copied)
        started = time.monotonic()
        record = {'source_sha256': doc['source_sha256'], 'file': copied.name, 'force': True}
        try:
            cache = ensure_ocr_json(copied, force=True)
            metadata = json.loads(Path(str(cache) + '.meta.json').read_text(encoding='utf-8'))
            from src.utils.ocr_json_parser import iter_layout_pages
            payload = json.loads(cache.read_text(encoding='utf-8'))
            returned_pages = len(list(iter_layout_pages(payload)))
            record.update(cache=cache.name, metadata=metadata, returned_page_count=returned_pages,
                          expected_page_count=doc['page_count'])
            if returned_pages != doc['page_count']:
                raise RuntimeError(f'OCR page coverage mismatch: {returned_pages}/{doc["page_count"]}')
            record['status'] = 'success'
        except Exception as exc:
            record.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        record['elapsed_seconds'] = round(time.monotonic() - started, 3)
        status['records'].append(record)
        checkpoint()
        print(record['file'], record['status'], flush=True)
        if record['status'] == 'failed':
            break
    status['status'] = 'completed' if len(status['records']) == len(documents) and all(r['status'] == 'success' for r in status['records']) else 'incomplete'
    checkpoint()
    return 0 if status['status'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
