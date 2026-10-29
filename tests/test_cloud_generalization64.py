"""Offline contract tests for the independent 48 seen + 16 new Java/SSE run."""
from __future__ import annotations

import copy
import importlib
import json
from collections import Counter
from pathlib import Path

import pytest


@pytest.fixture
def harness():
    if importlib.util.find_spec('eval.cloud_generalization64') is None:
        pytest.skip('Run the explicit runner-availability assertion first')
    return importlib.import_module('eval.cloud_generalization64')


def test_independent_runner_is_available():
    assert importlib.util.find_spec('eval.cloud_generalization64') is not None, 'independent 64-turn runner is missing'


def test_suite_has_exact_seen_objects_and_independent_question_hash(harness):
    suite = harness.load_suite()
    cases = suite['cases']
    old = harness.base.read(harness.OLD_QUESTIONS)
    old_index = {case['id']: case for case in old['cases']}
    assert len(cases) == 64
    assert Counter(suite['cohorts'].values()) == {'seen48': 48, 'new16': 16}
    assert {case['company_code'] for case in cases} == set(harness.base.COHORT)
    for case in cases:
        if suite['cohorts'][case['id']] == 'seen48':
            assert case == old_index[case['id']]
    manifest = harness.base.read(harness.TEST_MANIFEST)
    assert harness.base.digest(harness.CASE_FILE) == manifest['suite_file_sha256']
    assert harness.questions_digest(cases) == manifest['questions_sha256']
    assert harness.questions_digest(cases) != harness.base.QUESTIONS_SHA256
    assert harness.base.digest(harness.OLD_QUESTIONS) == manifest['old_questions_file_sha256']


def test_groups_are_complete_contiguous_and_never_cross_cohorts_or_company_splits(harness):
    suite = harness.load_suite()
    for cohort, singles, groups in [('seen48', 24, 6), ('new16', 8, 2)]:
        rows = [case for case in suite['cases'] if suite['cohorts'][case['id']] == cohort]
        assert sum(case['group'] is None for case in rows) == singles
        names = {case['group'] for case in rows if case['group']}
        assert len(names) == groups
        for name in names:
            members = [case for case in rows if case['group'] == name]
            assert [case['turn'] for case in members] == [1, 2, 3, 4]
            positions = [suite['cases'].index(case) for case in members]
            assert positions == list(range(positions[0], positions[0] + 4))
            assert len({case['partition'] for case in members}) == 1
    assert all(len(ids) == 3 for ids in suite['seen_single_families'].values())


@pytest.mark.parametrize('change', ['seen_expectation', 'incomplete_group', 'cohort_move', 'cross_company_split', 'missing_report_lookup', 'financial_as_no_data'])
def test_schema_rejects_changed_seen_cases_and_invalid_generalization_cases(harness, change):
    suite = copy.deepcopy(harness.load_suite())
    new = [case for case in suite['cases'] if suite['cohorts'][case['id']] == 'new16']
    if change == 'seen_expectation':
        suite['cases'][0]['expect']['no_chart'] = False
    elif change == 'incomplete_group':
        suite['cases'].pop()
    elif change == 'cohort_move':
        suite['cohorts'][new[0]['id']] = 'seen48'
    elif change == 'cross_company_split':
        new[0]['expect']['goal_expectations'][0]['codes'] = ['300750']
    elif change == 'missing_report_lookup':
        item = next(case for case in new if case['company_code'] == '600031')
        item['expect']['goal_expectations'][0].update(years=[2025], periods=['Q1'])
    else:
        new[0]['expect']['kind'] = 'no_data'
    with pytest.raises(harness.base.FreezeMismatch):
        harness.validate_suite(suite, harness.base.read(harness.OLD_QUESTIONS))


def test_old_200_loader_and_constants_are_never_rebound(harness):
    before = harness.base.SUITE_SHA256, harness.base.QUESTIONS_SHA256, harness.base.CASE_FILE
    harness.load_suite()
    assert before == (harness.base.SUITE_SHA256, harness.base.QUESTIONS_SHA256, harness.base.CASE_FILE)
    with pytest.raises(harness.base.FreezeMismatch, match='upload200 suite'):
        harness.base.load_cases(harness.CASE_FILE)


def test_new_questions_are_absent_from_prompt_examples_and_public_inventory(harness):
    suite = harness.load_suite()
    prompt_files = [path for path in (harness.ROOT/'src/agent').rglob('*.py') if 'prompt' in path.name]
    prompt_text = '\n'.join(path.read_text(encoding='utf-8') for path in prompt_files)
    public = json.dumps(harness.suite_inventory(suite), ensure_ascii=False)
    for case in suite['cases']:
        if suite['cohorts'][case['id']] == 'new16':
            assert case['question'] not in prompt_text
            assert case['question'] not in public
            assert not any(key in case for key in ('history', 'oracle', 'answer', 'expected_state'))


def valid_env():
    return dict(FINSIGHT_UPGRADE_PROFILE='cloud-upload120-v1', DB_NAME='finsight_upload120_20261021',
        MYSQL_DATABASE='finsight_upload120_20261021', SQL_DB_NAME='finsight_upload120_20261021',
        API_PORT='8120', SERVER_PORT='18280', REDIS_PORT='6391', RABBITMQ_PORT='5673',
        LLM_PROVIDER='deepseek', LLM_MODEL='deepseek-flash', LLM_API_KEY='test-private-value',
        LLM_BASE_URL='https://api.deepseek.com', LLM_TEMPERATURE='0', LLM_THINKING='false',
        LLM_MAX_TOKENS='3072', LLM_TIMEOUT_SECONDS='60', LLM_MAX_RETRIES='0',
        EMBEDDING_PROVIDER='dashscope', EMBEDDING_MODEL='text-embedding-v4', EMBEDDING_DIMENSION='1024',
        DASHSCOPE_API_KEY='test-private-embedding-value',
        UPLOAD200_USERNAME='test-user', UPLOAD200_PASSWORD='test-password')


@pytest.mark.parametrize('change', [dict(LLM_API_KEY=''), dict(LLM_PROVIDER='ollama'),
    dict(LLM_MODEL='local-weight'), dict(LLM_BASE_URL='http://127.0.0.1:11434'),
    dict(LLM_BASE_URL='https://untrusted.example/v1'), dict(LLM_BASE_URL='https://api.deepseek.com?key=secret'),
    dict(SERVER_PORT='8080'), dict(DB_NAME='financial_report'), dict(LLM_THINKING='true'),
    dict(DASHSCOPE_API_KEY=''), dict(EMBEDDING_PROVIDER='bge_local'),
    dict(EMBEDDING_MODEL='local-weight'), dict(EMBEDDING_DIMENSION='512')])
def test_environment_guards_stop_without_cloud_or_local_fallback(harness, change):
    env = {**valid_env(), **change}
    with pytest.raises(ValueError) as caught:
        harness.guard_environment(env)
    assert 'test-private-value' not in str(caught.value)
    assert 'secret' not in str(caught.value)


def test_frozen_model_policy_is_actual_deepseek_flash_and_never_contains_credentials(harness):
    policy = harness.guard_environment(valid_env())
    assert policy['model'] == 'deepseek-flash'
    assert policy['provider'] == 'openai_compat'
    assert policy['thinking'] == 'disabled' and policy['temperature'] == 0
    assert 'test-private-value' not in json.dumps(policy)
    assert not any('key' in key or 'password' in key for key in policy)


def greeting_case(identifier='C1', group=None, turn=1):
    return dict(id=identifier, company_code='000333', partition='development', group=group, turn=turn,
        question='你好 '+identifier, expect=dict(kind='greeting', goal_expectations=[], no_query=True, no_chart=True))


class MemoryJava:
    """The real runner protocol; requests never carry expectations or injected history."""
    def __init__(self):
        self.sessions = {}
        self.sent = []
        self.pending_once = False

    def call(self, method, path, **kwargs):
        if method == 'POST' and path == '/api/chat/sessions':
            uid = 's'+str(len(self.sessions)+1)
            self.sessions[uid] = dict(messages=[])
            return dict(sessionUid=uid)
        if method == 'GET' and path.startswith('/api/chat/sessions/'):
            return copy.deepcopy(self.sessions[path.rsplit('/', 1)[-1]])
        raise AssertionError((method, path))

    def turn(self, question, uid=None, client_id=None, **kwargs):
        assert kwargs == {}
        self.sent.append(dict(question=question, uid=uid, client_id=client_id))
        if self.pending_once:
            self.pending_once = False
            raise OSError('connection interrupted')
        state = dict(conditions={})
        result = dict(answer=dict(content='你好。'), request_contract=dict(conditions={}, goals=[]),
            dialogue_state=state, outcome=dict(status='answered'),
            diagnostics=dict(timings=[dict(model='deepseek-flash')]))
        self.sessions[uid]['messages'].extend([dict(role='user', content=question),
            dict(role='assistant', content='你好。', metadata=dict(dialogue_state=state))])
        return dict(question=question, session_uid=uid, client_request_id=client_id, task_id='t-'+client_id,
            task=dict(status='completed', saved=True, result=result))


def test_resume_preserves_pending_request_identity_and_first_success(harness, tmp_path):
    cases = [greeting_case('C1'), greeting_case('C2')]
    ledger = harness.GeneralizationLedger(tmp_path, dict(version='frozen'), cases,
        cohorts={'C1': 'seen48', 'C2': 'new16'})
    client = MemoryJava()
    client.pending_once = True
    with pytest.raises(harness.base.PendingSubmission):
        harness.base.run_cases(cases, ledger, client, {}, {}, model_version='deepseek-flash')
    pending = harness.base.read(ledger.path('requests', cases[0]))
    ledger = harness.GeneralizationLedger(tmp_path, dict(version='frozen'), cases, resume=True,
        cohorts={'C1': 'seen48', 'C2': 'new16'})
    events = []
    summary = harness.base.run_cases(cases, ledger, client, {}, {}, model_version='deepseek-flash',
        verify=lambda: events.append('verify'))
    assert client.sent[0]['client_id'] == client.sent[1]['client_id'] == pending['client_request_id']
    assert summary['cohorts']['seen48']['saved'] == summary['cohorts']['new16']['saved'] == 1
    raw_path = ledger.path('answers', cases[0])
    raw_bytes = raw_path.read_bytes()
    before = len(client.sent)
    harness.base.run_cases(cases, ledger, client, {}, {}, model_version='deepseek-flash')
    assert len(client.sent) == before and raw_path.read_bytes() == raw_bytes
    assert len(events) == len(cases)+1
    with pytest.raises(FileExistsError):
        ledger.save_raw(cases[0], ledger.raw(cases[0]))


def test_dialogue_turns_use_only_actual_previous_server_history(harness, tmp_path):
    cases = [greeting_case('G1', 'G', 1), greeting_case('G2', 'G', 2)]
    ledger = harness.GeneralizationLedger(tmp_path, {}, cases, cohorts={case['id']: 'new16' for case in cases})
    client = MemoryJava()
    harness.base.run_cases(cases, ledger, client, {}, {}, model_version='deepseek-flash')
    assert client.sent[0]['uid'] == client.sent[1]['uid']
    before_second = harness.base.read(ledger.path('requests', cases[1]))['before']['messages']
    assert [row['content'] for row in before_second] == [cases[0]['question'], '你好。']
    assert len(client.sessions[client.sent[0]['uid']]['messages']) == 4


def test_wrong_actual_model_metadata_fails_even_when_configured_name_is_right(harness):
    case = greeting_case()
    client = MemoryJava()
    uid = client.call('POST', '/api/chat/sessions')['sessionUid']
    raw = client.turn(case['question'], uid=uid, client_id='fixed')
    score = harness.base.score_case(case, raw, {}, {}, model_version='deepseek-flash')
    assert 'model_identity_not_confirmed' not in score['errors']
    raw['task']['result']['diagnostics']['timings'][0]['model'] = 'some-other-model'
    score = harness.base.score_case(case, raw, {}, {}, model_version='deepseek-flash')
    assert 'model_identity_not_confirmed' in score['errors'] and not score['passed']


def test_same_run_progress_reports_frozen_data_gaps_before_chat(harness, tmp_path):
    coverage = dict(reports_available=119, target_reports=120, report_inventory_missing=[['600031', 2025, 'Q1']],
        cases_with_oracle_gaps=3, gap_counts={'metric_not_extracted': 3}, independent_review_required_cases=4)
    harness.base.write_new(tmp_path/'preparation.json', coverage)
    progress = []
    cases = [greeting_case()]
    ledger = harness.GeneralizationLedger(tmp_path, {}, cases, cohorts={'C1': 'new16'}, progress=progress.append)
    summary = ledger.export()
    assert summary['answers_saved'] == 0 and progress[0]['answers_saved'] == 0
    assert summary['data_coverage'] == coverage
    assert progress[0]['data_coverage']['report_inventory_missing'] == [['600031', 2025, 'Q1']]


def test_output_cannot_touch_immutable_old_200(harness):
    with pytest.raises(ValueError, match='old200'):
        harness.guard_output(harness.OLD_QUESTIONS.parent)
    with pytest.raises(ValueError, match='old200'):
        harness.guard_output(harness.OLD_QUESTIONS.parent/'new64')


def fake_reference(tmp_path):
    return dict(version='trial119-v1', facts={}, reports=[{} for _ in range(119)], manifest={},
        artifacts={}, documents={}, folder=str(tmp_path), inventory_gaps=[['600031', 2025, 'Q1']])


def fake_snapshot(harness, version='trial119-v1'):
    return dict(profile='ocr-trial-v1', pointer='ocr_trial', version=version, collection='same-collection',
        index_sha256='a'*64, index_content_sha256='b'*64, index_dimensions=1024,
        model_policy=harness.guard_environment(valid_env()))


def install_offline_release_boundary(harness, monkeypatch, tmp_path):
    calls = []
    reference = fake_reference(tmp_path)
    def release(folder, *, root, require_cohort):
        calls.append(('release', require_cohort))
        assert require_cohort is False
        return copy.deepcopy(reference)
    def oracle(cases, ref, *, root, record_data_gaps):
        calls.append(('oracle', len(cases), record_data_gaps))
        assert record_data_gaps is True
        return dict(schema=2, data_version=ref['version'], questions_sha256=harness.base.QUESTIONS_SHA256,
            reference_sha256=harness.base._oracle_reference_digest(ref), cards={}, documents={},
            calculations={case['id']: [] for case in cases},
            data_gaps=[dict(case_id=cases[0]['id'], reason='metric_not_extracted')])
    monkeypatch.setattr(harness.base, 'load_release', release)
    monkeypatch.setattr(harness.base, 'build_oracle', oracle)
    return calls


def test_prepare_keeps_financial_expectations_and_records_gaps_without_cloud(harness, tmp_path, monkeypatch):
    calls = install_offline_release_boundary(harness, monkeypatch, tmp_path)
    def no_java(*args, **kwargs):
        pytest.fail('Offline preparation must not construct a Java client')
    monkeypatch.setattr(harness.base, 'JavaSSEClient', no_java)
    output = tmp_path/'prepared'
    harness.prepare(tmp_path, output, root=tmp_path, runtime_snapshot=fake_snapshot(harness),
        model_policy=harness.guard_environment(valid_env()))
    assert ('oracle', 64, True) in calls and ('release', False) in calls
    oracle = harness.base.read(output/'oracle.json')
    assert oracle['questions_sha256'] == harness.QUESTIONS_SHA256
    assert harness.base.QUESTIONS_SHA256 != harness.QUESTIONS_SHA256
    assert harness.base.read(output/'questions.freeze.json')['cases'] == harness.load_cases()
    preparation = harness.base.read(output/'preparation.json')
    assert preparation['reports_available'] == 119
    assert preparation['report_inventory_missing'] == [['600031', 2025, 'Q1']]
    assert preparation['cases_with_oracle_gaps'] == 1
    assert preparation['no_cloud_request_made'] is True
    assert not list(output.glob('answers/*.json'))


def test_prepared_source_model_index_and_question_freeze_cannot_change(harness, tmp_path, monkeypatch):
    install_offline_release_boundary(harness, monkeypatch, tmp_path)
    output = tmp_path/'prepared'
    options = dict(root=tmp_path, runtime_snapshot=fake_snapshot(harness), model_policy=harness.guard_environment(valid_env()))
    harness.prepare(tmp_path, output, **options)
    harness.prepare(tmp_path, output, **options)
    changed = copy.deepcopy(options)
    changed['runtime_snapshot']['index_content_sha256'] = 'c'*64
    with pytest.raises(harness.base.FreezeMismatch):
        harness.prepare(tmp_path, output, **changed)
    source = tmp_path/'src/module.py'
    source.parent.mkdir()
    source.write_text('changed = True\n', encoding='utf-8')
    with pytest.raises(harness.base.FreezeMismatch):
        harness.prepare(tmp_path, output, **options)


@pytest.mark.parametrize('change', ['profile', 'pointer', 'version', 'model', 'index'])
def test_runtime_selection_guard_rejects_strict_fallback_and_missing_snapshot(harness, change):
    snapshot = fake_snapshot(harness)
    if change == 'model':
        snapshot['model_policy']['model'] = 'other-model'
    elif change == 'index':
        snapshot.pop('index_content_sha256')
    else:
        snapshot[change] = {'profile': 'strict', 'pointer': 'serving', 'version': ''}[change]
    with pytest.raises((ValueError, harness.base.FreezeMismatch)):
        harness.guard_snapshot(snapshot, harness.guard_environment(valid_env()))


def test_evaluation_target_is_selected_before_preparation_without_cloud_or_service_control(harness, tmp_path, monkeypatch):
    from src.etl import submission_upgrade
    order = []
    folder = tmp_path/'selected'
    def target(engine, env):
        order.append('selected_repository')
        return folder, fake_snapshot(harness)
    def prepared(actual, output, **kwargs):
        order.append('prepare')
        assert actual == folder and kwargs['runtime_snapshot'] == fake_snapshot(harness)
        harness.base.write_new(Path(output)/'preparation.json', dict(reports_available=119,
            report_inventory_missing=[['600031', 2025, 'Q1']]))
        return Path(output)
    monkeypatch.setattr(submission_upgrade, 'evaluation_target', target)
    monkeypatch.setattr(submission_upgrade, '_configure_process', lambda env: None)
    monkeypatch.setattr(harness, 'prepare', prepared)
    monkeypatch.setattr(submission_upgrade, 'start', lambda *a: pytest.fail('Service start is forbidden'))
    monkeypatch.setattr(submission_upgrade, 'restart', lambda *a: pytest.fail('Service restart is forbidden'))
    result = harness.evaluate_selected(valid_env(), tmp_path/'output', prepare_only=True, engine=object())
    assert order == ['selected_repository', 'prepare']
    assert result['status'] == 'prepared_without_cloud_calls'
    assert result['preparation']['reports_available'] == 119
    assert 'test-private-value' not in json.dumps(result)


def test_per_turn_runtime_mismatch_stops_before_any_java_request(harness, tmp_path, monkeypatch):
    install_offline_release_boundary(harness, monkeypatch, tmp_path)
    original = fake_snapshot(harness)
    changed = copy.deepcopy(original)
    changed['index_content_sha256'] = 'c'*64
    client = MemoryJava()
    with pytest.raises(harness.base.FreezeMismatch, match='runtime|profile|index'):
        harness.run(tmp_path, tmp_path/'run', root=tmp_path, client=client, interval=0,
            model_policy=harness.guard_environment(valid_env()), runtime_snapshot=original, verify=lambda: changed)
    assert client.sent == [] and client.sessions == {}


def test_cli_prepare_uses_existing_private_environment_without_secret_arguments(harness, tmp_path, monkeypatch, capsys):
    from src.etl import submission_upgrade
    observed = {}
    def load(root, path):
        observed['env_path'] = str(path)
        return valid_env()
    def evaluate(env, output, **kwargs):
        observed.update(output=str(output), options=kwargs)
        return dict(status='prepared_without_cloud_calls', no_cloud_request_made=True)
    monkeypatch.setattr(submission_upgrade, 'load_environment', load)
    monkeypatch.setattr(harness, 'evaluate_selected', evaluate)
    assert harness.main(['--env', str(tmp_path/'private.env'), '--output', str(tmp_path/'run'), '--prepare-only']) == 0
    output = capsys.readouterr()
    assert observed['options']['prepare_only'] is True
    assert observed['env_path'].endswith('private.env')
    assert 'test-private-value' not in output.out+output.err
    assert 'test-password' not in output.out+output.err


def calibrated_fixture(harness, tmp_path):
    suite = harness.load_suite()
    seen = [case for case in suite['cases'] if suite['cohorts'][case['id']] == 'seen48']
    old, calibrated = tmp_path/'old', tmp_path/'calibrated'
    for name in ('answers', 'scores'):
        (old/name).mkdir(parents=True)
    (calibrated/'comparisons').mkdir(parents=True)
    files = {}
    def write(path, value):
        harness.base.write_new(path, value)
        return harness.base.digest(path)
    for case in seen:
        raw = dict(question=case['question'], task=dict(status='completed', saved=True))
        before = dict(status='failed', passed=False, automated_pass=False, errors=['old_scoring_error'])
        after = dict(status='passed', passed=True, automated_pass=True, errors=[], review_required=[])
        files['answers/'+case['id']+'.json'] = write(old/'answers'/(case['id']+'.json'),
            dict(case=case, run=raw, sha256=harness.base.value_digest(dict(case=case, run=raw))))
        files['scores/'+case['id']+'.json'] = write(old/'scores'/(case['id']+'.json'),
            dict(answer_sha256=harness.base.value_digest(raw), score=before, sha256=harness.base.value_digest(before)))
        write(calibrated/'comparisons'/(case['id']+'.json'), dict(case_id=case['id'], partition=case['partition'],
            group=case['group'], question_sha256=harness.base.value_digest(case['question']),
            expected_sha256=harness.base.value_digest(case['expect']), raw_answer_sha256=harness.base.value_digest(raw),
            before=before, after=after, calibrated_with_original_oracle=after,
            rationale=dict(score_only_change=True, no_answer_regenerated=True)))
    (old/'questions.freeze.json').write_bytes(harness.OLD_QUESTIONS.read_bytes())
    files['questions.freeze.json'] = harness.base.digest(old/'questions.freeze.json')
    write(calibrated/'input-manifest.json', dict(input_run=str(old.resolve()), files=files,
        scorer_sha256=harness.base.digest(Path(harness.base.__file__))))
    write(calibrated/'input-integrity.json', dict(before=files, after=files, unchanged=True))
    write(calibrated/'oracle.json', dict(questions_sha256=harness.base.QUESTIONS_SHA256))
    write(calibrated/'summary.json', dict(no_model_call_made=True, no_network_generation=True,
        score_only_change=True, original_run_unchanged=True, data_version='trial119-v1'))
    return old, calibrated, seen


def test_matched48_baseline_uses_identical_old_first_answers_and_keeps_score_corrections_separate(harness, tmp_path):
    old, calibrated, seen = calibrated_fixture(harness, tmp_path)
    old_bytes = {path: path.read_bytes() for path in old.rglob('*.json')}
    result = harness.extract_matched_baseline(calibrated, tmp_path/'matched48', input_run=old)
    assert result['total'] == 48 and result['cohort'] == 'seen48'
    assert result['before']['passed'] == 0 and result['calibrated_baseline']['passed'] == 48
    assert result['model_gain_claimed'] is False and result['no_model_call_made'] is True
    assert {path: path.read_bytes() for path in old_bytes} == old_bytes
    assert len(list((tmp_path/'matched48/comparisons').glob('*.json'))) == 48
    with pytest.raises(FileExistsError):
        harness.extract_matched_baseline(calibrated, tmp_path/'matched48', input_run=old)


@pytest.mark.parametrize('change', ['scorer', 'first_answer', 'comparison_binding', 'input_integrity'])
def test_matched48_baseline_rejects_changed_answers_scorer_or_rescore_provenance(harness, tmp_path, change):
    old, calibrated, seen = calibrated_fixture(harness, tmp_path)
    if change == 'scorer':
        path = calibrated/'input-manifest.json'
        row = harness.base.read(path)
        row['scorer_sha256'] = '0'*64
    elif change == 'first_answer':
        path = old/'answers'/(seen[0]['id']+'.json')
        row = harness.base.read(path)
        row['run']['question'] = 'modified'
    elif change == 'comparison_binding':
        path = calibrated/'comparisons'/(seen[0]['id']+'.json')
        row = harness.base.read(path)
        row['raw_answer_sha256'] = '0'*64
    else:
        path = calibrated/'input-integrity.json'
        row = harness.base.read(path)
        row['unchanged'] = False
    path.write_text(json.dumps(row), encoding='utf-8')
    with pytest.raises(harness.base.FreezeMismatch):
        harness.extract_matched_baseline(calibrated, tmp_path/'matched48', input_run=old)


def test_cli_matched_baseline_needs_no_private_environment_or_runtime_access(harness, tmp_path, monkeypatch):
    from src.etl import submission_upgrade
    monkeypatch.setattr(submission_upgrade, 'load_environment', lambda *args: pytest.fail('Offline extraction does not need credentials'))
    monkeypatch.setattr(harness, 'extract_matched_baseline', lambda folder, output: dict(total=48, no_model_call_made=True), raising=False)
    assert harness.main(['--matched-baseline-from', str(tmp_path/'calibrated'), '--output', str(tmp_path/'baseline48')]) == 0

