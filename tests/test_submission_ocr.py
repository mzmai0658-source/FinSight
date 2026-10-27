"""新上传只能使用本次受登记的 OCR 作业；失败续跑不重复提交。"""
import json
from hashlib import sha256

import fitz
import pytest

from src.etl.submission_store import SubmissionStore


class Jobs:
    def __init__(self):
        self.submits = 0
        self.fail_poll = False
        self.fail_submit = False

    def submit(self, path):
        self.submits += 1
        if self.fail_submit:
            raise ConnectionError('network')
        return 'same-remote-job'

    def status(self, id):
        assert id == 'same-remote-job'
        if self.fail_poll:
            raise ConnectionError('network')
        return {'state': 'done'}

    def download(self, result):
        return [{'markdown': {'text': '# 来自本次远端识别\n真实页数验证'}, 'prunedResult': {'parsing_res_list': []}}]


def setup_asset(tmp_path):
    path = tmp_path / 'report.pdf'
    doc = fitz.open()
    doc.new_page().insert_text((40, 40), 'original')
    doc.save(path)
    doc.close()
    id = sha256(path.read_bytes()).hexdigest()
    store = SubmissionStore(tmp_path / 'state.sqlite3')
    store.register(id, {'source_sha256': id, 'page_count': 1})
    return path, id, store


def test_old_neighbor_cache_is_ignored_and_new_json_md_bind_original(tmp_path):
    from src.etl.submission_ocr import fresh_ocr
    path, id, store = setup_asset(tmp_path)
    Path = type(path)
    Path(str(path) + '_by_PaddleOCR-VL-1.6.json').write_text('{}')
    jobs = Jobs()
    result = fresh_ocr(path, id, store, tmp_path / 'asset', client=jobs, poll_interval=0)
    assert jobs.submits == 1
    assert store.ocr_submission(id)['job_id'] == 'same-remote-job'
    assert json.loads(result['json'].read_text(encoding='utf-8'))[0]['markdown']['text'].startswith('# 来自本次')
    assert '原件物理页 1' in result['markdown'].read_text(encoding='utf-8')
    assert json.loads((Path(str(result['json']) + '.meta.json')).read_text())['pdf_sha256'] == id


def test_poll_network_interruption_resumes_bound_job_without_resubmission(tmp_path):
    from src.etl.submission_ocr import fresh_ocr
    path, id, store = setup_asset(tmp_path)
    jobs = Jobs()
    jobs.fail_poll = True
    with pytest.raises(RuntimeError, match='network|连接'):
        fresh_ocr(path, id, store, tmp_path / 'asset', client=jobs, poll_interval=0, network_attempts=2)
    jobs.fail_poll = False
    fresh_ocr(path, id, store, tmp_path / 'asset', client=jobs, poll_interval=0)
    assert jobs.submits == 1


def test_uncertain_submission_does_not_repeat_billable_post(tmp_path):
    from src.etl.submission_ocr import fresh_ocr
    path, id, store = setup_asset(tmp_path)
    jobs = Jobs()
    jobs.fail_submit = True
    with pytest.raises(RuntimeError, match='uncertain|确认'):
        fresh_ocr(path, id, store, tmp_path / 'asset', client=jobs, poll_interval=0)
    with pytest.raises((RuntimeError, ValueError), match='uncertain|确认'):
        fresh_ocr(path, id, store, tmp_path / 'asset', client=jobs, poll_interval=0)
    assert jobs.submits == 1


def test_missing_ocr_page_is_not_saved_as_completed(tmp_path):
    from src.etl.submission_ocr import fresh_ocr
    path, id, store = setup_asset(tmp_path)
    jobs = Jobs()
    jobs.download = lambda result: []
    with pytest.raises(ValueError, match='page|页|版面'):
        fresh_ocr(path, id, store, tmp_path / 'asset', client=jobs, poll_interval=0)
    assert store.ocr_submission(id)['state'] != 'completed'
