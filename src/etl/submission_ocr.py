"""新文档首次识别只调远端；保存作业号后断点续查，绝不扫描旧 OCR 样本。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlparse

from src.etl.ocr_client import OcrInvalidResponseError, validate_ocr_payload, write_validated_ocr_cache
from src.utils.ocr_json_parser import iter_layout_pages


def validate_pdf(path):
    import fitz
    path = Path(path)
    if path.suffix.lower() != '.pdf' or not path.is_file() or path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError('财报须为不超过100MB的PDF原件')
    with path.open('rb') as stream:
        if b'%PDF-' not in stream.read(1024):
            raise ValueError('文件不是可解析的PDF')
        stream.seek(0)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    with fitz.open(path) as document:
        if document.needs_pass or document.is_encrypted or not 1 <= len(document) <= 1500:
            raise ValueError('不支持加密、空白或超过1500页的PDF')
        return digest, len(document)


class PaddleJobs:
    def __init__(self):
        self.url = os.getenv('OCR_API_URL', '').strip().rstrip('/')
        self.token = os.getenv('PADDLEOCR_API_TOKEN') or os.getenv('OCR_API_TOKEN', '')
        parsed = urlparse(self.url)
        if not self.token or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query:
            raise ValueError('请配置有效的HTTPS异步OCR_API_URL与服务端PADDLEOCR_API_TOKEN')
        if os.getenv('OCR_API_PROTOCOL', 'paddle_async') != 'paddle_async':
            raise ValueError('新财报处理采用可恢复的paddle_async协议')
        self.model = os.getenv('OCR_MODEL', 'PaddleOCR-VL-1.6')
        if self.model != 'PaddleOCR-VL-1.6':
            raise ValueError('新语料统一采用PaddleOCR-VL-1.6')

    def submit(self, path):
        from src.utils.ocr_async_json import submit_async_ocr_job
        return submit_async_ocr_job(str(path), model=self.model, timeout=60, job_url=self.url, token=self.token)

    def status(self, id):
        import requests
        try:
            response = requests.get(self.url + '/' + str(id), headers={'Authorization': 'Bearer ' + self.token}, timeout=30)
        except requests.RequestException:
            raise ConnectionError('OCR连接未完成') from None
        if response.status_code in {401, 402, 403, 429}:
            raise RuntimeError('OCR认证、余额或额度检查未通过；请检查服务端账号')
        if response.status_code >= 500 or response.status_code == 408:
            raise ConnectionError('OCR服务暂不可达')
        if response.status_code != 200:
            raise RuntimeError('OCR作业查询失败，HTTP ' + str(response.status_code))
        try:
            result = response.json()['data']
        except (ValueError, KeyError, TypeError):
            raise ValueError('OCR作业返回格式无效') from None
        if not isinstance(result, dict):
            raise ValueError('OCR作业返回格式无效')
        return result

    def download(self, result):
        import requests
        url = (result.get('resultUrl') or {}).get('jsonUrl')
        parsed = urlparse(url or '')
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('OCR完成结果缺少有效HTTPS JSON地址')
        try:
            with requests.get(url, timeout=60, stream=True) as response:
                if response.status_code != 200:
                    raise RuntimeError('OCR结果下载失败，HTTP ' + str(response.status_code))
                chunks, size = [], 0
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > 256 * 1024 * 1024:
                        raise ValueError('OCR结果超过256MB限制')
                    chunks.append(chunk)
                text = b''.join(chunks).decode('utf-8-sig').strip()
        except requests.RequestException:
            raise ConnectionError('OCR结果下载连接未完成；继续使用同一作业') from None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            try:
                return [json.loads(line) for line in text.splitlines() if line.strip()]
            except json.JSONDecodeError:
                raise ValueError('OCR结果不是可解析的JSON或JSONL') from None


def _checked_pages(payload, expected):
    try:
        validate_ocr_payload(payload)
    except OcrInvalidResponseError:
        raise ValueError('OCR结果没有可用的page页版面') from None
    pages = list(iter_layout_pages(payload))
    if len(pages) != expected:
        raise ValueError('OCR/PDF page页数不一致，不能登记为已完成')
    return pages


def fresh_ocr(path, id, store, folder, *, client=None, poll_interval=5, timeout=3600,
              network_attempts=2, heartbeat=None):
    digest, page_count = validate_pdf(path)
    if digest != id:
        raise ValueError('OCR原件摘要与候选登记不一致')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'ocr.json'
    markdown = folder / 'ocr.md'
    submitted = store.ocr_submission(id)
    if target.exists():
        sidecar = Path(str(target) + '.meta.json')
        if not submitted or not submitted.get('job_id') or not sidecar.is_file() or Path(str(target) + '.publishing').exists():
            raise ValueError('OCR文件缺少本次作业记录或完整摘要，不能视为新识别结果')
        meta = json.loads(sidecar.read_text(encoding='utf-8'))
        raw = target.read_bytes()
        if meta.get('pdf_sha256') != id or meta.get('cache_sha256') != hashlib.sha256(raw).hexdigest() or meta.get('origin') != 'remote_ocr':
            raise ValueError('OCR文件与本次原件或远端识别身份不一致')
        payload = json.loads(raw)
    else:
        client = client or PaddleJobs()
        reservation = store.reserve_ocr(id, page_count, limit=int(os.getenv('OCR_DAILY_PAGE_LIMIT', '20000')))
        job_id = reservation.get('job_id')
        if not job_id:
            store.record_ocr_submission(id, state='submitting')
            try:
                job_id = client.submit(Path(path))
            except BaseException as exc:
                store.record_ocr_submission(id, state='uncertain')
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                raise RuntimeError('OCR submission uncertain：提交结果未确认，请在控制台核对并关联原job_id；不能重复提交') from None
            store.record_ocr_submission(id, state='submitted', job_id=job_id)
        started, failures = time.monotonic(), 0
        while True:
            if heartbeat:
                heartbeat()
            if time.monotonic() - started >= timeout:
                raise TimeoutError('OCR等待超时；作业号已保存，可继续查询同一作业')
            try:
                result = client.status(job_id)
                failures = 0
            except (ConnectionError, TimeoutError):
                failures += 1
                if failures >= max(1, network_attempts):
                    raise RuntimeError('OCR network连接连续失败；作业号已保存，稍后续跑') from None
                time.sleep(poll_interval)
                continue
            state = result.get('state')
            if state == 'done':
                payload = client.download(result)
                break
            if state == 'failed':
                raise RuntimeError('OCR远端作业失败；保留作业号，不能重复自动提交')
            if state not in {'pending', 'running'}:
                raise ValueError('OCR远端作业状态无效')
            time.sleep(poll_interval)
        _checked_pages(payload, page_count)
        write_validated_ocr_cache(path, payload, model='PaddleOCR-VL-1.6',
                                 expected_pdf_sha256=id, cache_path=target)
    pages = _checked_pages(payload, page_count)
    parts = []
    for number, page in enumerate(pages, 1):
        text = (page.get('markdown') or {}).get('text') or '\n'.join(
            block.get('block_content', '') for block in (page.get('prunedResult') or {}).get('parsing_res_list', []))
        parts.append(f'<!-- 原件物理页 {number}；PDF SHA-256 {id} -->\n\n{text.strip()}')
    from src.etl.ocr_client import _atomic_write
    _atomic_write(markdown, ('\n\n'.join(parts) + '\n').encode('utf-8'))
    store.record_ocr_submission(id, state='completed', job_id=submitted.get('job_id') if submitted else None)
    return {'json': target, 'markdown': markdown, 'pages': page_count,
            'job_id': store.ocr_submission(id)['job_id'], 'payload': payload}
