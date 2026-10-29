"""新财报候选的持久化、审核边界和重复处理保护。"""
from copy import deepcopy

import pytest


SHA = 'a' * 64


def asset():
    return {'source_sha256': SHA, 'source_path': 'data_root/financial_reports/submissions/a.pdf',
            'identity': {'stock_code': '000789', 'company': '测试企业', 'year': 2025, 'period': 'FY'},
            'page_count': 2}


def test_asset_registration_is_content_addressed_and_resume_keeps_remote_job(tmp_path):
    from src.etl.submission_store import SubmissionStore
    store = SubmissionStore(tmp_path / 'state.sqlite3')
    store.register(SHA, asset())
    token = store.claim(SHA)
    value = store.get(SHA)
    value['ocr'] = {'job_id': 'remote-one', 'state': 'running'}
    store.save(SHA, value, token=token)
    store.release(SHA, token)
    store.register(SHA, {**asset(), 'page_count': 999})
    reopened = SubmissionStore(tmp_path / 'state.sqlite3')
    assert reopened.get(SHA)['ocr']['job_id'] == 'remote-one'
    assert reopened.get(SHA)['page_count'] == 2


def test_live_lease_blocks_duplicate_workers_and_stale_owner_cannot_write(tmp_path):
    from src.etl.submission_store import SubmissionStore, SubmissionBusy
    store = SubmissionStore(tmp_path / 'state.sqlite3')
    store.register(SHA, asset())
    token = store.claim(SHA)
    with pytest.raises(SubmissionBusy):
        store.claim(SHA)
    with pytest.raises(SubmissionBusy):
        store.save(SHA, asset(), token='not-the-owner')
    store.release(SHA, token)
    assert store.claim(SHA) != token


def test_revision_check_prevents_lost_updates(tmp_path):
    from src.etl.submission_store import SubmissionStore, SubmissionConflict
    store = SubmissionStore(tmp_path / 'state.sqlite3')
    store.register(SHA, asset())
    stale = deepcopy(store.get(SHA))
    store.save(SHA, {**stale, 'message': 'new'})
    with pytest.raises(SubmissionConflict):
        store.save(SHA, {**stale, 'message': 'late'})
    assert store.get(SHA)['message'] == 'new'


def test_approval_requires_verified_identity_facts_index_and_confirmed_identity(tmp_path):
    from src.etl.submission_store import SubmissionStore
    store = SubmissionStore(tmp_path / 'state.sqlite3')
    store.register(SHA, asset())
    with pytest.raises(ValueError, match='verified|核验'):
        store.review(SHA, 'approve', operator_id=2, identity=asset()['identity'])
    value = store.get(SHA)
    value.update(status='ready', review_status='WAITING', quality={
        'identity_verified': True, 'facts_verified': True, 'reports_verified': True,
        'index_verified': True, 'verified_count': 1, 'conflicts': []})
    store.save(SHA, value)
    with pytest.raises(ValueError, match='identity|身份'):
        store.review(SHA, 'approve', operator_id=2, identity={**asset()['identity'], 'stock_code': '000001'})
    result = store.review(SHA, 'approve', operator_id=2, identity=asset()['identity'])
    assert result['review_status'] == 'APPROVED'
    assert result['review']['operator_id'] == 2
    assert result['review']['verification_kind'] == 'administrator_confirmation'


def test_rejection_records_reason_and_cannot_be_promoted_without_reprocessing(tmp_path):
    from src.etl.submission_store import SubmissionStore
    store = SubmissionStore(tmp_path / 'state.sqlite3')
    store.register(SHA, asset())
    with pytest.raises(ValueError):
        store.review(SHA, 'reject', operator_id=2, reason='')
    result = store.review(SHA, 'reject', operator_id=2, reason='公司身份冲突')
    assert result['review_status'] == 'REJECTED'
    with pytest.raises(ValueError):
        store.review(SHA, 'approve', operator_id=2, identity=asset()['identity'])


def test_ocr_daily_limit_is_durable_and_duplicate_reservation_does_not_charge_twice(tmp_path):
    from src.etl.submission_store import SubmissionStore
    store = SubmissionStore(tmp_path / 'state.sqlite3')
    store.reserve_ocr(SHA, 12, day='2026-10-21', limit=20)
    store.reserve_ocr(SHA, 12, day='2026-10-21', limit=20)
    assert store.ocr_usage('2026-10-21') == 12
    with pytest.raises(ValueError, match='quota|额度'):
        store.reserve_ocr('b' * 64, 9, day='2026-10-21', limit=20)
    assert SubmissionStore(tmp_path / 'state.sqlite3').ocr_usage('2026-10-21') == 12
    store.reserve_ocr('b' * 64, 9, day='2026-10-22', limit=20)
    assert store.ocr_usage('2026-10-22') == 9


def test_unknown_remote_submission_is_not_automatically_submitted_again(tmp_path):
    from src.etl.submission_store import SubmissionStore
    store = SubmissionStore(tmp_path / 'state.sqlite3')
    store.reserve_ocr(SHA, 12, day='2026-10-21', limit=20)
    store.record_ocr_submission(SHA, state='uncertain')
    with pytest.raises(ValueError, match='uncertain|确认'):
        store.reserve_ocr(SHA, 12, day='2026-10-21', limit=20)
    store.record_ocr_submission(SHA, state='submitted', job_id='reconciled-job')
    assert store.ocr_submission(SHA)['job_id'] == 'reconciled-job'


def test_review_revision_fence_rejects_stale_validation_and_boolean_operator(tmp_path):
    from src.etl.submission_store import SubmissionStore, SubmissionConflict
    store = SubmissionStore(tmp_path / 'state.sqlite3')
    store.register(SHA, asset())
    old=store.get(SHA)
    store.save(SHA,{**old,'message':'reprocessed'})
    with pytest.raises(SubmissionConflict):
        store.review(SHA,'reject',operator_id=2,reason='stale',expected_revision=old['revision'])
    with pytest.raises(ValueError):
        store.review(SHA,'reject',operator_id=True,reason='not a real administrator')
