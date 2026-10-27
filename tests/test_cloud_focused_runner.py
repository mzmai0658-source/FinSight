"""Offline guarantees for the user-run focused cloud batch; never contact services."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def harness():
    assert importlib.util.find_spec('eval.cloud_focused') is not None, 'focused cloud runner is not implemented'
    return importlib.import_module('eval.cloud_focused')


def case(identifier, group=None):
    return {'id': identifier, 'group': group, 'question': 'A frozen question '+identifier,
            'expect': {'kind': 'concept', 'goal_expectations': []}}


def result(question, session, request_id):
    return {'question': question, 'session_uid': session, 'client_request_id': request_id,
            'task_id': 'task-'+request_id, 'seconds': 1.25, 'events': [],
            'task': {'status': 'completed', 'saved': True, 'result': {
                'answer': {'content': 'First answer for '+question}, 'facts': [],
                'request_contract': {'goals': []}}}}


class FakeNative:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def turn(self, question, uid=None, client_id=None, **kwargs):
        self.calls.append({'question': question, 'uid': uid, 'client_id': client_id, **kwargs})
        if len(self.calls) == self.fail_at:
            raise ConnectionError('ambiguous transport interruption')
        return result(question, uid or 'session-'+client_id, client_id)


def passing_score(*args, **kwargs):
    assert kwargs['entry'] == 'java'
    return {'passed': True, 'automated_pass': True, 'errors': [], 'critical': [], 'review_required': []}


def test_first_raw_result_cannot_be_replaced(harness, tmp_path):
    item = case('A')
    ledger = harness.RunLedger(tmp_path/'run', {'version': 'one'}, [item])
    raw = result(item['question'], 'saved-session', 'same-request')
    ledger.save_raw(item, raw)
    original = (tmp_path/'run'/'answers'/'A.json').read_bytes()
    with pytest.raises(FileExistsError):
        ledger.save_raw(item, {**raw, 'seconds': 99})
    assert (tmp_path/'run'/'answers'/'A.json').read_bytes() == original


def test_resume_rejects_changed_freeze_before_client_calls(harness, tmp_path):
    items = [case('A')]
    harness.RunLedger(tmp_path/'run', {'source': 'one', 'data': 'same'}, items)
    with pytest.raises(harness.FreezeMismatch):
        harness.RunLedger(tmp_path/'run', {'source': 'two', 'data': 'same'}, items, resume=True)


def test_new_run_never_overwrites_existing_output(harness, tmp_path):
    items = [case('A')]
    harness.RunLedger(tmp_path/'run', {'source': 'one'}, items)
    with pytest.raises(FileExistsError):
        harness.RunLedger(tmp_path/'run', {'source': 'one'}, items)


def test_resume_preserves_actual_group_session_and_pending_request(harness, tmp_path):
    items = [case('G5T1', 'G5'), case('G5T2', 'G5'), case('SINGLE')]
    fingerprint = {'source': 'one'}
    ledger = harness.RunLedger(tmp_path/'run', fingerprint, items)
    interrupted = FakeNative(fail_at=2)
    with pytest.raises(harness.PendingSubmission):
        harness.run_cases(items, ledger, interrupted, passing_score, {'facts': {}}, {'cards': {}})
    first = ledger.raw(items[0])
    first_bytes = (ledger.folder/'answers'/'G5T1.json').read_bytes()
    resumed = harness.RunLedger(ledger.folder, fingerprint, items, resume=True)
    client = FakeNative()
    harness.run_cases(items, resumed, client, passing_score, {'facts': {}}, {'cards': {}})
    assert len(client.calls) == 2
    assert client.calls[0]['question'] == items[1]['question']
    assert client.calls[0]['uid'] == first['session_uid']
    assert client.calls[0]['client_id'] == interrupted.calls[1]['client_id']
    assert client.calls[1]['uid'] is None
    assert (ledger.folder/'answers'/'G5T1.json').read_bytes() == first_bytes


def test_raw_answer_is_saved_when_frozen_scorer_raises(harness, tmp_path):
    item = case('A')
    ledger = harness.RunLedger(tmp_path/'run', {'source': 'one'}, [item])

    def broken(*args, **kwargs):
        raise RuntimeError('secret-bearing exception must not be serialized')

    harness.run_cases([item], ledger, FakeNative(), broken, {'facts': {}}, {'cards': {}})
    assert ledger.raw(item)['task']['saved'] is True
    verdict = ledger.score(item)
    assert verdict['passed'] is False
    assert 'scorer_error_RuntimeError' in verdict['errors']
    assert 'independent_final_answer_review' in verdict['review_required']
    assert 'secret-bearing' not in (ledger.folder/'scores'/'A.json').read_text('utf-8')


def test_resume_scores_preserved_raw_without_repeating_generation(harness, tmp_path):
    item = case('A')
    ledger = harness.RunLedger(tmp_path/'run', {'source': 'one'}, [item])
    ledger.save_raw(item, result(item['question'], 'saved', 'fixed'))
    resumed = harness.RunLedger(ledger.folder, {'source': 'one'}, [item], resume=True)
    client = FakeNative()
    harness.run_cases([item], resumed, client, passing_score, {'facts': {}}, {'cards': {}})
    assert client.calls == []
    assert resumed.score(item)['automated_pass'] is True
    assert resumed.score(item)['passed'] is False


def test_tampered_first_answer_is_rejected(harness, tmp_path):
    item = case('A')
    ledger = harness.RunLedger(tmp_path/'run', {'source': 'one'}, [item])
    ledger.save_raw(item, result(item['question'], 'saved', 'fixed'))
    path = ledger.folder/'answers'/'A.json'
    saved = json.loads(path.read_text('utf-8'))
    saved['run']['task']['result']['answer']['content'] = 'changed'
    path.write_text(json.dumps(saved), encoding='utf-8')
    with pytest.raises(harness.FreezeMismatch):
        harness.RunLedger(ledger.folder, {'source': 'one'}, [item], resume=True)


def test_source_and_data_and_scorer_hashes_are_frozen(harness, tmp_path):
    root = tmp_path/'source'
    (root/'src').mkdir(parents=True)
    (root/'src'/'model.py').write_text('version = 1', encoding='utf-8')
    prior = tmp_path/'prior'
    prior.mkdir()
    for name in ('scoring_review_v2.py', 'scoring.py', 'support.py', 'freeze.json'):
        (prior/name).write_text('{}', encoding='utf-8')
    suite = prior/'cases_focused28.json'
    suite.write_text('{}', encoding='utf-8')
    oracle = prior/'oracle.json'
    oracle.write_text('{}', encoding='utf-8')
    audit = tmp_path/'audit'
    audit.mkdir()
    for name in ('facts.json', 'acceptance.json', 'reports.json', 'manifest.json', 'index_coverage.json', 'narratives.jsonl'):
        (audit/name).write_text('{}', encoding='utf-8')
    before = harness.freeze_inputs(root, suite, prior, audit, oracle)
    (root/'src'/'model.py').write_text('version = 2', encoding='utf-8')
    assert harness.freeze_inputs(root, suite, prior, audit, oracle) != before
    (audit/'facts.json').write_text('{"changed": true}', encoding='utf-8')
    after_data = harness.freeze_inputs(root, suite, prior, audit, oracle)
    (prior/'scoring.py').write_text('# changed', encoding='utf-8')
    assert harness.freeze_inputs(root, suite, prior, audit, oracle) != after_data


def test_original_inputs_for_all_k05_variants_require_independent_review(harness, tmp_path):
    items = [case('K05V1'), case('K05V2'), case('K05V3')]
    ledger = harness.RunLedger(tmp_path/'run', {'source': 'one'}, items)
    harness.run_cases(items, ledger, FakeNative(), passing_score, {'facts': {}}, {'cards': {}})
    for item in items:
        assert 'original_input_values_and_units' in ledger.score(item)['review_required']
        assert ledger.score(item)['passed'] is False


def test_suite_shape_cannot_silently_add_remove_or_reorder_cases(harness):
    items = [case(identifier, group) for identifier, group in harness.FOCUSED_ORDER]
    harness.validate_suite(items)
    with pytest.raises(ValueError):
        harness.validate_suite(items[:-1])
    changed = copy.deepcopy(items)
    changed[0], changed[1] = changed[1], changed[0]
    with pytest.raises(ValueError):
        harness.validate_suite(changed)


def test_existing_score_is_not_replaced_during_resume(harness, tmp_path):
    item = case('A')
    ledger = harness.RunLedger(tmp_path/'run', {'source': 'one'}, [item])
    harness.run_cases([item], ledger, FakeNative(), passing_score, {'facts': {}}, {'cards': {}})
    before = (ledger.folder/'scores'/'A.json').read_bytes()

    def must_not_score(*args, **kwargs):
        pytest.fail('completed first score was rerun')

    resumed = harness.RunLedger(ledger.folder, {'source': 'one'}, [item], resume=True)
    harness.run_cases([item], resumed, FakeNative(), must_not_score, {'facts': {}}, {'cards': {}})
    assert (ledger.folder/'scores'/'A.json').read_bytes() == before


def test_viewer_escapes_answer_html_and_summary_never_claims_review_pass(harness, tmp_path):
    item = case('A')
    ledger = harness.RunLedger(tmp_path/'run', {'source': 'one'}, [item])
    run = result(item['question'], 'saved', 'fixed')
    run['task']['result']['answer']['content'] = '<script>unsafe()</script>'
    ledger.save_raw(item, run)
    harness.run_cases([item], ledger, FakeNative(), passing_score, {'facts': {}}, {'cards': {}})
    viewer = (ledger.folder/'answers.html').read_text('utf-8')
    assert '<script>unsafe()</script>' not in viewer
    assert '&lt;script&gt;unsafe()&lt;/script&gt;' in viewer
    summary = json.loads((ledger.folder/'summary.json').read_text('utf-8'))
    assert summary['confirmed_passed'] == 0
    assert summary['pending_review'] == 1


def isolated_env(folder):
    return {'DB_NAME': 'finsight_cloud_alignment_20261020', 'MYSQL_DATABASE': 'finsight_cloud_alignment_20261020',
            'SQL_DB_NAME': 'finsight_real_rebuild_20261002', 'SQL_DB_USER': 'finance_reader', 'DB_USER': 'business_writer',
            'REDIS_DATABASE': '13', 'RABBITMQ_VHOST': '/finsight-cloud-alignment-20261020',
            'FINSIGHT_TASK_DB': str(folder/'tasks.sqlite3'), 'EMBEDDING_PROVIDER': 'bge_local',
            'EMBEDDING_MODEL': 'BAAI/bge-small-zh-v1.5'}


def test_isolation_never_accepts_finance_as_chat_database(harness, tmp_path):
    env = isolated_env(tmp_path)
    harness.validate_isolation(env, tmp_path)
    env['DB_NAME'] = env['MYSQL_DATABASE'] = env['SQL_DB_NAME']
    with pytest.raises(ValueError):
        harness.validate_isolation(env, tmp_path)


@pytest.mark.parametrize('key,value', [('REDIS_DATABASE', '0'), ('RABBITMQ_VHOST', '/'),
                                     ('FINSIGHT_TASK_DB', 'D:/outside-run/tasks.sqlite3')])
def test_isolation_rejects_shared_namespaces(harness, tmp_path, key, value):
    env = isolated_env(tmp_path)
    env[key] = value
    with pytest.raises(ValueError):
        harness.validate_isolation(env, tmp_path)


def test_cancel_completion_race_is_inconclusive_not_passed(harness):
    class Race(FakeNative):
        def call(self, method, path):
            return {'status': 'completed', 'saved': True, 'result': {}}
    outcome = harness.cancel_probe(Race(), lambda: {'model': {'active': 0, 'waiting': 0}}, pause=lambda: None)
    assert outcome['status'] == 'inconclusive_completion_race'
    assert outcome['passed'] is False


def test_cancel_requires_saved_terminal_no_late_output_and_idle_scheduler(harness):
    class Cancelled(FakeNative):
        def turn(self, question, **kwargs):
            self.calls.append(kwargs)
            run = result(question, 'cancel-session', kwargs['client_id'])
            run['task'] = {'status': 'cancelled', 'saved': True, 'result': {}}
            return run

        def call(self, method, path):
            return {'status': 'cancelled', 'saved': True, 'result': {}}
    client = Cancelled()
    outcome = harness.cancel_probe(client, lambda: {'model': {'active': 0, 'waiting': 0}}, pause=lambda: None)
    assert outcome['passed'] is True
    assert client.calls[0]['cancel_after'] == .2
    busy = harness.cancel_probe(Cancelled(), lambda: {'model': {'active': 1, 'waiting': 0}}, pause=lambda: None)
    assert busy['passed'] is False


def test_cancel_allows_bounded_background_title_to_finish(harness):
    class Cancelled(FakeNative):
        def turn(self, question, **kwargs):
            run = result(question, 'cancel-session', kwargs['client_id'])
            run['task'] = {'status': 'cancelled', 'saved': True, 'result': {}}
            return run
        def call(self, method, path):
            return {'status': 'cancelled', 'saved': True, 'result': {}}
    states = iter([{'model': {'active': 1, 'waiting': 0}},
                   {'model': {'active': 1, 'waiting': 0}},
                   {'model': {'active': 0, 'waiting': 0}}])
    outcome = harness.cancel_probe(Cancelled(), lambda: next(states), pause=lambda: None)
    assert outcome['passed'] is True


def test_model_audit_export_only_keeps_identity_counts_and_usage(harness, tmp_path):
    path = tmp_path/'audit.jsonl'
    path.write_text(json.dumps({'event': 'start', 'id': 'a', 'kind': 'InternalTitleResponse',
                               'model': 'deepseek-flash', 'api_key': 'must-not-copy', 'input': 'private prompt'})+'\n'+
                    json.dumps({'event': 'end', 'id': 'a', 'model': 'deepseek-flash',
                                'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}})+'\n',
                    encoding='utf-8')
    audit = harness.audit_summary(path)
    assert audit['starts'] == 1
    assert audit['titles'] == 1
    assert audit['usage']['total_tokens'] == 12
    assert 'must-not-copy' not in json.dumps(audit)
    assert 'private prompt' not in json.dumps(audit)


def test_runtime_build_must_match_current_v3_sources(harness, tmp_path):
    folder = tmp_path/'src'/'agent'/'v3'
    folder.mkdir(parents=True)
    (folder/'a.py').write_bytes(b'first')
    (folder/'b.py').write_bytes(b'second')
    expected = hashlib.sha256(b'firstsecond').hexdigest()
    harness.verify_agent_version({'dialogue_version': 3, 'build': expected}, tmp_path)
    with pytest.raises(harness.FreezeMismatch):
        harness.verify_agent_version({'dialogue_version': 3, 'build': 'older-process'}, tmp_path)
    with pytest.raises(harness.FreezeMismatch):
        harness.verify_agent_version({'dialogue_version': 2, 'build': expected}, tmp_path)


def test_stack_marker_must_record_launch_source_before_tests(harness, tmp_path):
    source = {'src/a.py': 'one', 'requirements.txt': 'two'}
    marker = {'source_sha256': harness.value_digest(source)}
    harness.verify_launch_source(marker, source)
    with pytest.raises(harness.FreezeMismatch):
        harness.verify_launch_source(marker, {**source, 'src/a.py': 'modified-after-launch'})
    with pytest.raises(harness.FreezeMismatch):
        harness.verify_launch_source({}, source)
