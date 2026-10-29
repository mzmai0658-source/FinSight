"""Independent frozen 64-turn Java/SSE evaluation: 48 seen turns plus 16 new ones.

This module reuses the original-based oracle, Decimal scorer and immutable request
ledger from upload200. It never changes that suite's constants or load contract.
The command only uses the existing isolated application and private environment.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from eval import cloud_upload200 as base

ROOT = Path(__file__).resolve().parents[1]
CASE_FILE = ROOT/'eval/cloud_generalization64_cases.json'
ROUND = ROOT.parents[1]/'generalization_fix_20261024/evaluation64'
TEST_MANIFEST = ROUND/'test_manifest.json'
OLD_QUESTIONS = ROOT.parents[1]/'rollout_235_20261022/evaluation/final200/questions.freeze.json'
# Public question-only fixtures make the offline contract tests portable. Paid
# responses and original financial text remain in the separate local evidence.
if not OLD_QUESTIONS.is_file():
    OLD_QUESTIONS = ROOT/'eval/fixtures/generalization64/old_questions.freeze.json'
if not TEST_MANIFEST.is_file():
    TEST_MANIFEST = ROOT/'eval/fixtures/generalization64/test_manifest.json'
SUITE_SHA256 = 'a33ded0c62f57247194e40352815d27f380c342f585022ba9d7902386cd389e6'
QUESTIONS_SHA256 = '55eee60371dc59c6bd8f0f871bde8f6a19e20bd4f133d0ec061a8a9cf7b840f4'
OLD_FREEZE_SHA256 = 'df5d895933b3160bf555b6584995679622463838fc607f4d90caf19de1a2ae0d'
MODEL_VERSION = 'deepseek-flash'
QUESTION_FIELDS = ('id', 'partition', 'company_code', 'group', 'turn', 'question')
COHORT_COUNTS = Counter(seen48=48, new16=16)


def questions_digest(cases):
    return base.value_digest([{key: case[key] for key in QUESTION_FIELDS} for case in cases])


def validate_suite(suite, old):
    """Validate the independent shape without weakening the exact-200 validator."""
    def require(condition, reason):
        if not condition:
            raise base.FreezeMismatch(reason)
    try:
        cases, cohorts = suite['cases'], suite['cohorts']
        require(suite['schema'] == 1, 'Unsupported generalization64 schema')
        require(len(cases) == 64 and len({case['id'] for case in cases}) == 64,
                'Expected exactly 64 distinct frozen turns')
        require(set(cohorts) == {case['id'] for case in cases} and Counter(cohorts.values()) == COHORT_COUNTS,
                'The evaluation cohorts must remain seen48/new16')
        old_index = {case['id']: case for case in old['cases']}
        old_text = {case['question'] for case in old['cases']}
        seen = [case for case in cases if cohorts[case['id']] == 'seen48']
        new = [case for case in cases if cohorts[case['id']] == 'new16']
        require(all(case == old_index.get(case['id']) for case in seen),
                'Seen cases must remain complete unchanged old200 objects')
        require(all(case['id'] not in old_index and case['question'] not in old_text for case in new)
                and len({case['question'] for case in new}) == 16,
                'New expressions must remain distinct from old200')
        require({case['company_code'] for case in cases} == set(base.COHORT),
                'All ten new companies must remain represented')
        families = suite['seen_single_families']
        require(set(families) == {'basic', 'scope', 'negative_q3', 'yoy', 'mixed_eps_units', 'rank', 'chart', 'cause_quote'}
                and all(len(ids) == 3 for ids in families.values()),
                'Seen singles must retain the balanced eight-family selection')
        family_ids = [identifier for ids in families.values() for identifier in ids]
        require(len(set(family_ids)) == 24 and set(family_ids) == {case['id'] for case in seen if not case['group']},
                'Seen family inventory differs from the 24 singles')
        for cohort, rows, singles, group_count in [('seen48', seen, 24, 6), ('new16', new, 8, 2)]:
            groups = {case['group'] for case in rows if case['group']}
            require(sum(case['group'] is None for case in rows) == singles and len(groups) == group_count,
                    'Each cohort must retain its singles and complete four-turn groups')
            require(set(suite['seen_groups' if cohort == 'seen48' else 'new_groups']) == groups,
                    'Frozen dialogue inventory changed')
            for group in groups:
                members = [case for case in cases if case['group'] == group]
                positions = [cases.index(case) for case in members]
                require([case['turn'] for case in members] == [1, 2, 3, 4]
                        and positions == list(range(positions[0], positions[0]+4))
                        and {cohorts[case['id']] for case in members} == {cohort}
                        and len({case['partition'] for case in members}) == 1,
                        'Dialogue groups must remain complete, contiguous and on one side')
        for case in cases:
            require(bool(re.fullmatch(r'[A-Za-z0-9_-]+', case['id'])) and bool(case['question'].strip()),
                    'Invalid case identity or empty question')
            require(case['partition'] == base.COHORT[case['company_code']][1],
                    'A company crossed the held-out split')
            require(case['expect']['kind'] in {'financial', 'reference', 'clarify', 'unsupported', 'greeting', 'concept'},
                    'Supported financial expectations cannot become expected no_data')
            for spec in case['expect']['goal_expectations']:
                require(all(code in base.COHORT and base.COHORT[code][1] == case['partition'] for code in spec.get('codes', [])),
                        'A goal crossed the held-out company split')
                require(not any(key[:3] == ('600031', 2025, 'Q1') for key in base.spec_identities(spec)),
                        'Absent Sany 2025 Q1 is not a supported lookup')
            if case['group'] is None:
                require(case['turn'] == 1, 'A single turn cannot depend on another session')
            if cohorts[case['id']] == 'new16':
                require(not any(key in case for key in ('history', 'oracle', 'answer', 'expected_state')),
                        'New cases cannot inject expected history or oracle state')
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise base.FreezeMismatch('Malformed generalization64 suite: '+type(exc).__name__) from None


def load_suite(path=CASE_FILE, *, old_questions=OLD_QUESTIONS):
    if base.digest(path) != SUITE_SHA256 or base.digest(old_questions) != OLD_FREEZE_SHA256:
        raise base.FreezeMismatch('Frozen generalization64 or immutable old200 question bytes changed')
    suite, old = base.read(path), base.read(old_questions)
    if (suite.get('old_freeze_sha256') != OLD_FREEZE_SHA256
            or suite.get('old_questions_sha256') != base.QUESTIONS_SHA256
            or old.get('questions_sha256') != base.QUESTIONS_SHA256
            or questions_digest(suite['cases']) != QUESTIONS_SHA256):
        raise base.FreezeMismatch('Independent question projection or old200 provenance changed')
    validate_suite(suite, old)
    return suite


def load_cases(path=CASE_FILE, *, old_questions=OLD_QUESTIONS):
    return load_suite(path, old_questions=old_questions)['cases']


def suite_inventory(suite):
    """A topic/count handoff deliberately omits reserved question text."""
    return dict(suite=suite['name'], formal_total=len(suite['cases']), suite_sha256=SUITE_SHA256,
        questions_sha256=QUESTIONS_SHA256, cohorts=dict(Counter(suite['cohorts'].values())),
        company_partitions=dict(Counter(case['partition'] for case in suite['cases'])),
        seen_single_families={name: len(ids) for name, ids in suite['seen_single_families'].items()},
        complete_four_turn_groups=len(suite['seen_groups'])+len(suite['new_groups']),
        new_topics=['ordinary wording/order', 'company replacement', 'sort and limit', 'mixed units',
                    'source follow-up', 'incomplete history', 'greeting/capability', 'prediction refusal'])


def guard_environment(env):
    """Validate public settings without returning or logging private configuration."""
    from src.etl.submission_upgrade import DATABASE, PROFILE
    if env.get('FINSIGHT_UPGRADE_PROFILE') != PROFILE or any(env.get(key) != DATABASE for key in ('DB_NAME', 'MYSQL_DATABASE', 'SQL_DB_NAME')):
        raise ValueError('Refusing non-isolated evaluation profile/database')
    if any(env.get(key) != expected for key, expected in (('API_PORT', '8120'), ('SERVER_PORT', '18280'), ('REDIS_PORT', '6391'), ('RABBITMQ_PORT', '5673'))):
        raise ValueError('Refusing non-isolated evaluation service ports')
    if not (env.get('LLM_API_KEY') or env.get('DEEPSEEK_API_KEY') or '').strip():
        raise ValueError('LLM_API_KEY is missing; no evaluation request was sent')
    if not (env.get('DASHSCOPE_API_KEY') or '').strip():
        raise ValueError('DASHSCOPE_API_KEY is missing; no evaluation request or local fallback was attempted')
    if (env.get('EMBEDDING_PROVIDER') != 'dashscope' or env.get('EMBEDDING_MODEL') != 'text-embedding-v4'
            or env.get('EMBEDDING_DIMENSION') != '1024'):
        raise ValueError('Evaluation requires the existing DashScope text-embedding-v4 1024-dimensional space')
    if env.get('LLM_PROVIDER', '').lower() not in {'deepseek', 'openai_compat', 'openai-compat', 'openai'}:
        raise ValueError('Evaluation requires the configured DeepSeek cloud provider')
    if env.get('LLM_MODEL') != MODEL_VERSION:
        raise ValueError('Evaluation requires actual deepseek-flash metadata')
    endpoint = env.get('LLM_BASE_URL', 'https://api.deepseek.com').rstrip('/')
    try:
        parsed = urlsplit(endpoint)
        trusted = (parsed.scheme == 'https' and parsed.hostname == 'api.deepseek.com'
            and parsed.port in {None, 443} and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment and parsed.path in {'', '/v1'})
    except ValueError:
        trusted = False
    if not trusted:
        raise ValueError('Evaluation requires the trusted credential-free DeepSeek HTTPS endpoint')
    try:
        limit = int(env.get('LLM_MAX_TOKENS', '3072'))
        timeout = int(env.get('LLM_TIMEOUT_SECONDS', '60'))
        retries = int(env.get('LLM_MAX_RETRIES', '0'))
        temperature = float(env.get('LLM_TEMPERATURE', '0'))
    except (TypeError, ValueError):
        raise ValueError('Invalid public model limit/timeout/retry/temperature settings') from None
    if (not 1 <= limit <= 3072 or not 1 <= timeout <= 60 or retries != 0 or temperature != 0
            or env.get('LLM_THINKING', 'false').lower() not in {'false', '0', 'off', 'disabled'}):
        raise ValueError('Evaluation requires bounded model settings, zero temperature and disabled thinking')
    return dict(provider='openai_compat', model=MODEL_VERSION, thinking='disabled', temperature=0,
        output_limit=limit, timeout_seconds=timeout, network_retries=retries,
        endpoint_sha256=hashlib.sha256(endpoint.encode('utf-8')).hexdigest())


def guard_output(output):
    output, old = Path(output).resolve(), OLD_QUESTIONS.parent.resolve()
    if output.is_relative_to(old) or old.is_relative_to(output):
        raise ValueError('The independent run cannot write into or above immutable old200 output')
    return output


class GeneralizationLedger(base.RunLedger):
    """Keep base first-answer/request guarantees and report seen/new denominators."""
    def __init__(self, folder, fingerprint, cases, *, cohorts, resume=False, progress=None):
        self.cohorts, self.progress, self._last_progress = cohorts, progress, None
        path = Path(folder)/'preparation.json'
        preparation = base.read(path) if path.exists() else {}
        self.coverage = {name: preparation[name] for name in ('reports_available', 'target_reports',
            'report_inventory_missing', 'cases_with_oracle_gaps', 'gap_counts', 'independent_review_required_cases')
            if name in preparation}
        super().__init__(folder, fingerprint, cases, resume=resume)

    def export(self):
        summary = super().export()
        summary['suite'] = 'cloud-generalization64-20261024'
        summary['questions_sha256'] = QUESTIONS_SHA256
        summary['data_coverage'] = self.coverage
        summary['cohorts'] = {}
        for name in ('seen48', 'new16'):
            cases = [case for case in self.cases if self.cohorts[case['id']] == name]
            saved = [case for case in cases if self.raw(case) is not None]
            scores = [self.score(case) for case in saved if self.score(case) is not None]
            passed = sum(score.get('passed') is True for score in scores)
            summary['cohorts'][name] = dict(total=len(cases), saved=len(saved), scored=len(scores), passed=passed,
                automated_passed=sum(score.get('automated_pass') is True for score in scores),
                pending_review=sum(score.get('review_status') == 'pending_review' for score in scores),
                oracle_missing=sum(score.get('status') == 'oracle_missing' for score in scores),
                failed=sum(score.get('status') == 'failed' for score in scores),
                accuracy=passed/len(cases) if cases else 0)
        summary['comparison_policy'] = 'Compare seen48 only with the same 48 old first responses and the same calibrated scorer; report new16 separately. Scorer corrections are not model gains.'
        base.replace_derived(self.folder/'summary.json', json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
        page = (self.folder/'answers.html').read_text(encoding='utf-8')
        page = page.replace('NEW公司200轮首发回答', '64轮泛化首发回答').replace('NEW公司财报首发回答', '64轮泛化首发回答')
        base.replace_derived(self.folder/'answers.html', page)
        marker = (summary['answers_saved'], summary['scored'])
        if self.progress is not None and marker != self._last_progress:
            self.progress(dict(status='same_run_progress', **summary))
            self._last_progress = marker
        return summary


def guard_snapshot(snapshot, model_policy):
    snapshot = base._public_settings(snapshot)
    if snapshot.get('profile') != 'ocr-trial-v1' or snapshot.get('pointer') != 'ocr_trial':
        raise base.FreezeMismatch('Evaluation must select the existing trial119 profile and pointer')
    if not snapshot.get('version') or not snapshot.get('collection'):
        raise base.FreezeMismatch('Selected runtime release or collection identity is missing')
    if (snapshot.get('index_dimensions') != 1024
            or any(not re.fullmatch(r'[a-f0-9]{64}', str(snapshot.get(name, '')))
                   for name in ('index_sha256', 'index_content_sha256'))):
        raise base.FreezeMismatch('Selected runtime index content identity is missing or invalid')
    if snapshot.get('model_policy') != model_policy or model_policy.get('model') != MODEL_VERSION:
        raise base.FreezeMismatch('Actual runtime model policy differs from bounded deepseek-flash settings')
    return snapshot


def _freeze_new(path, value, reason):
    if Path(path).exists():
        if base.read(path) != value:
            raise base.FreezeMismatch(reason)
    else:
        base.write_new(path, value)


def _fingerprint(reference, oracle, *, root, cases_path, model_policy, runtime_snapshot):
    policy = base._public_settings(model_policy)
    runtime = guard_snapshot(runtime_snapshot, policy)
    if runtime['version'] != reference['version']:
        raise base.FreezeMismatch('Selected runtime and original-based oracle name different releases')
    return dict(source=base.source_fingerprint(root), artifacts=reference['artifacts'], originals=reference['documents'],
        data_version=reference['version'], suite_sha256=base.digest(cases_path), questions_sha256=QUESTIONS_SHA256,
        old_questions_file_sha256=OLD_FREEZE_SHA256, oracle_sha256=base.value_digest(oracle),
        model=dict(version=MODEL_VERSION, settings=policy), partition='all', runtime_snapshot=runtime)


def _trial_reference(candidate_artifacts, root):
    reference = base.load_release(candidate_artifacts, root=root, require_cohort=False)
    if len(reference['reports']) != 119 or reference.get('inventory_gaps') != [['600031', 2025, 'Q1']]:
        raise base.FreezeMismatch('Evaluation requires the published 119-report trial inventory with only Sany 2025 Q1 absent')
    return reference


def prepare(candidate_artifacts, output, *, model_policy, runtime_snapshot, root=ROOT, cases_path=CASE_FILE):
    """Freeze this independent run offline after explicit invocation on final code.

    Building the shared oracle returns upload200's historical metadata constant.
    Only the returned oracle's suite label is adapted; the shared module and its
    exact-200 loader are never rebound, patched, or invoked with another suite.
    """
    output = guard_output(output)
    suite = load_suite(cases_path)
    guard_snapshot(runtime_snapshot, model_policy)
    output.mkdir(parents=True, exist_ok=True)
    _freeze_new(output/'questions.freeze.json', dict(schema=1, suite_sha256=SUITE_SHA256,
        questions_sha256=QUESTIONS_SHA256, old_questions_file_sha256=OLD_FREEZE_SHA256,
        cohorts=suite['cohorts'], cases=suite['cases']), 'Independent questions changed after upfront freeze')
    reference = _trial_reference(candidate_artifacts, root)
    oracle_path = output/'oracle.json'
    if oracle_path.exists():
        oracle = base.read(oracle_path)
        if (oracle.get('schema') != 2 or oracle.get('questions_sha256') != QUESTIONS_SHA256
                or oracle.get('suite_sha256') != SUITE_SHA256
                or oracle.get('reference_sha256') != base._oracle_reference_digest(reference)
                or oracle.get('data_version') != reference['version']
                or any(base.digest(base.local_path(path, root)) != sha for path, sha in oracle.get('documents', {}).items())):
            raise base.FreezeMismatch('Independent original oracle, release or original PDF changed')
    else:
        checked = base.build_oracle(suite['cases'], reference, root=root, record_data_gaps=True)
        oracle = dict(checked, questions_sha256=QUESTIONS_SHA256, suite_sha256=SUITE_SHA256)
        base.write_new(oracle_path, oracle)
    fingerprint = _fingerprint(reference, oracle, root=root, cases_path=cases_path,
        model_policy=model_policy, runtime_snapshot=runtime_snapshot)
    preparation = base.preparation_summary(suite['cases'], reference, oracle)
    preparation.update(suite=suite['name'], questions_sha256=QUESTIONS_SHA256,
        suite_sha256=SUITE_SHA256, cohorts=dict(COHORT_COUNTS),
        target_reports=120, no_financial_expectations_relabelled_as_no_data=True,
        source_freeze_status='frozen_by_explicit_preparation',
        comparison_policy='Matched old48 baseline and new16 results are separate; calibrated old scores are not model gains.')
    _freeze_new(output/'preparation.json', preparation, 'Independent preparation coverage changed')
    ledger = GeneralizationLedger(output, fingerprint, suite['cases'], cohorts=suite['cohorts'],
        resume=(output/'freeze.json').exists())
    ledger.export()
    return output


def run(candidate_artifacts, output, *, model_policy, runtime_snapshot, verify, root=ROOT,
        cases_path=CASE_FILE, base_url='http://127.0.0.1:18280', username=None, password=None,
        client=None, resume=False, interval=11, progress=None):
    """Use actual saved Java history and immutable first answers; never reroll."""
    output = guard_output(output)
    if base_url.rstrip('/') != 'http://127.0.0.1:18280':
        raise ValueError('Evaluation requires the existing isolated Java endpoint on 18280')
    if not callable(verify):
        raise ValueError('A live selected-repository verification callback is required')
    if not 0 <= interval <= 60:
        raise ValueError('Evaluation interval must be in 0..60 seconds')
    if (output/'answers').exists() and not resume:
        raise FileExistsError('First results already exist; use --resume for unfinished turns')
    with base.runner_lock(output):
        prepare(candidate_artifacts, output, model_policy=model_policy, runtime_snapshot=runtime_snapshot,
            root=root, cases_path=cases_path)
        suite = load_suite(cases_path)
        reference, oracle = _trial_reference(candidate_artifacts, root), base.read(output/'oracle.json')
        fingerprint = _fingerprint(reference, oracle, root=root, cases_path=cases_path,
            model_policy=model_policy, runtime_snapshot=runtime_snapshot)
        ledger = GeneralizationLedger(output, fingerprint, suite['cases'], cohorts=suite['cohorts'],
            resume=True, progress=progress)
        ledger.export()
        def verify_frozen():
            current = verify()
            if current != fingerprint['runtime_snapshot']:
                raise base.FreezeMismatch('Active profile, pointer, release, model or index changed after freeze')
            if base.source_fingerprint(root) != fingerprint['source']:
                raise base.FreezeMismatch('Source changed after freeze')
            if base.digest(cases_path) != fingerprint['suite_sha256'] or base.digest(OLD_QUESTIONS) != OLD_FREEZE_SHA256:
                raise base.FreezeMismatch('Frozen question cases or old200 provenance changed')
            if any(base.digest(Path(reference['folder'])/name) != sha for name, sha in fingerprint['artifacts'].items()):
                raise base.FreezeMismatch('Candidate release changed after freeze')
        verify_frozen()
        if client is None:
            if not username or not password:
                raise ValueError('Existing private Java test account is missing; no chat request was sent')
            account = dict(username_sha256=hashlib.sha256(username.encode('utf-8')).hexdigest(), base_url=base_url.rstrip('/'))
            _freeze_new(output/'account.freeze.json', account, 'Resume must use the original Java account and endpoint')
            client = base.JavaSSEClient(base_url, username, password)
            password = None
        return base.run_cases(suite['cases'], ledger, client, reference, oracle, model_version=MODEL_VERSION,
            verify=verify_frozen, interval=interval)


def evaluate_selected(env, output, *, prepare_only=False, resume=False, interval=11, engine=None, progress=None):
    """Use exactly the runtime selector and index fingerprint used by upload120."""
    from src.etl import submission_upgrade
    policy = guard_environment(env)
    submission_upgrade._configure_process(env)
    owned_engine = engine is None
    if owned_engine:
        from sqlalchemy import create_engine
        from config.db_config import get_readonly_db_config
        engine = create_engine(get_readonly_db_config().connection_string)
    try:
        folder, snapshot = submission_upgrade.evaluation_target(engine, env)
        guard_snapshot(snapshot, policy)
        options = dict(root=ROOT, model_policy=policy, runtime_snapshot=snapshot)
        if prepare_only:
            prepared = prepare(folder, output, **options)
            return dict(status='prepared_without_cloud_calls', output=str(prepared), no_cloud_request_made=True,
                inventory=suite_inventory(load_suite()), preparation=base.read(prepared/'preparation.json'),
                runtime_snapshot=snapshot)
        return run(folder, output, username=env.get('UPLOAD200_USERNAME'), password=env.get('UPLOAD200_PASSWORD'),
            resume=resume, interval=interval, progress=progress,
            verify=lambda: submission_upgrade.evaluation_target(engine, env)[1], **options)
    finally:
        if owned_engine:
            engine.dispose()


def extract_matched_baseline(calibrated_folder, output, *, input_run=OLD_QUESTIONS.parent):
    """Extract the same 48 old first responses from the completed offline rescore.

    The rescorer already rechecked physical originals. This handoff validates its
    scorer and answer bindings without rerunning an audit or accessing a runtime.
    """
    calibrated, input_run, output = Path(calibrated_folder).resolve(), Path(input_run).resolve(), guard_output(output)
    if output.exists():
        raise FileExistsError('Matched baseline requires a new independent output directory')
    if (output.is_relative_to(calibrated) or calibrated.is_relative_to(output)
            or output.is_relative_to(input_run) or input_run.is_relative_to(output)):
        raise ValueError('Matched baseline output must not overlap its immutable inputs')
    suite = load_suite()
    manifest = base.read(calibrated/'input-manifest.json')
    integrity = base.read(calibrated/'input-integrity.json')
    rescore_summary = base.read(calibrated/'summary.json')
    if (Path(manifest.get('input_run', '')).resolve() != input_run
            or manifest.get('scorer_sha256') != base.digest(Path(base.__file__))
            or manifest.get('files', {}).get('questions.freeze.json') != OLD_FREEZE_SHA256
            or base.digest(input_run/'questions.freeze.json') != OLD_FREEZE_SHA256
            or integrity.get('unchanged') is not True
            or integrity.get('before') != manifest['files'] or integrity.get('after') != manifest['files']
            or any(rescore_summary.get(name) is not True for name in ('no_model_call_made', 'no_network_generation', 'score_only_change', 'original_run_unchanged'))):
        raise base.FreezeMismatch('Calibrated baseline scorer or immutable input provenance changed')
    comparisons, input_hashes = [], {}
    for case in suite['cases']:
        if suite['cohorts'][case['id']] != 'seen48':
            continue
        identifier = case['id']
        paths = {name: input_run/name/(identifier+'.json') for name in ('answers', 'scores')}
        for name, path in paths.items():
            relative = name+'/'+identifier+'.json'
            sha = base.digest(path)
            if manifest['files'].get(relative) != sha:
                raise base.FreezeMismatch('Original first-answer baseline file changed: '+identifier)
            input_hashes[relative] = sha
        answer, old_score = base.read(paths['answers']), base.read(paths['scores'])
        raw = answer.get('run')
        comparison = base.read(calibrated/'comparisons'/(identifier+'.json'))
        if (answer.get('case') != case or answer.get('sha256') != base.value_digest(dict(case=case, run=raw))
                or old_score.get('answer_sha256') != base.value_digest(raw)
                or old_score.get('sha256') != base.value_digest(old_score.get('score'))
                or comparison.get('case_id') != identifier
                or comparison.get('question_sha256') != base.value_digest(case['question'])
                or comparison.get('expected_sha256') != base.value_digest(case['expect'])
                or comparison.get('raw_answer_sha256') != base.value_digest(raw)
                or comparison.get('before') != old_score['score']
                or any((comparison.get('rationale') or {}).get(name) is not True for name in ('score_only_change', 'no_answer_regenerated'))):
            raise base.FreezeMismatch('Matched old first-answer or calibrated score binding changed: '+identifier)
        comparisons.append(comparison)
    def stats(scores):
        statuses = Counter(score['status'] for score in scores)
        return dict(total=len(scores), passed=sum(score.get('passed') is True for score in scores),
            automated_passed=sum(score.get('automated_pass') is True for score in scores),
            failed=statuses['failed'], oracle_missing=statuses['oracle_missing'], pending_review=statuses['pending_review'])
    summary = dict(schema=1, cohort='seen48', total=48, questions_sha256=QUESTIONS_SHA256,
        before=stats([row['before'] for row in comparisons]), calibrated_baseline=stats([row['after'] for row in comparisons]),
        calibrated_with_original_oracle=stats([row['calibrated_with_original_oracle'] for row in comparisons]),
        data_version=rescore_summary['data_version'], no_model_call_made=True, model_gain_claimed=False,
        new16_excluded=True, input_run=str(input_run), calibrated_source=str(calibrated),
        interpretation='Scoring/original-verification corrections to identical old first responses. Compare only the future seen48 cohort with this calibrated baseline; report new16 separately.')
    freeze = dict(schema=1, suite_sha256=SUITE_SHA256, questions_sha256=QUESTIONS_SHA256,
        scorer_sha256=manifest['scorer_sha256'], input_files=input_hashes,
        calibrated_files={name: base.digest(calibrated/name) for name in ('input-manifest.json', 'input-integrity.json', 'summary.json', 'oracle.json')},
        calibrated_comparisons={row['case_id']: base.digest(calibrated/'comparisons'/(row['case_id']+'.json')) for row in comparisons})
    output.mkdir(parents=True)
    base.write_new(output/'baseline.freeze.json', freeze)
    for comparison in comparisons:
        base.write_new(output/'comparisons'/(comparison['case_id']+'.json'), comparison)
    base.write_new(output/'summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', default=str(ROOT/'.env.upload120'), help='Existing private isolated environment file')
    parser.add_argument('--output', help='Independent output; defaults to evaluation64/run or evaluation64/baseline48')
    parser.add_argument('--prepare-only', action='store_true', help='Freeze final source, originals and oracle without login or paid requests')
    parser.add_argument('--resume', action='store_true', help='Continue only unfinished persisted first requests')
    parser.add_argument('--interval', type=float, default=11)
    parser.add_argument('--matched-baseline-from', help='Extract seen48 from a completed offline calibrated old200 rescore; no runtime or private environment access')
    args = parser.parse_args(argv)
    if not 0 <= args.interval <= 60:
        parser.error('--interval must be in 0..60 seconds')
    try:
        if args.matched_baseline_from:
            if args.prepare_only or args.resume:
                raise ValueError('Matched baseline extraction is separate from preparation/resume')
            result = extract_matched_baseline(args.matched_baseline_from, args.output or ROUND/'baseline48')
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            return 0
        from src.etl.submission_upgrade import load_environment
        env = load_environment(ROOT, args.env)
        guard_environment(env)
        result = evaluate_selected(env, args.output or ROUND/'run', prepare_only=args.prepare_only, resume=args.resume,
            interval=args.interval, progress=lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0
    except (base.FreezeMismatch, base.OracleMissing, base.PendingSubmission, base.RunStopped, FileExistsError, ValueError) as exc:
        print(json.dumps(dict(status=type(exc).__name__, detail=str(exc)), ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:
        # Transport/SQL errors can include private connection strings; keep logs public.
        print(json.dumps(dict(status='stopped', detail=type(exc).__name__,
            recovery='Inspect local progress/service logs, then resume the same command and request identities.'), ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
