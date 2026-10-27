"""User-run frozen cloud batches through Java/SSE, with immutable first answers."""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PRIOR = ROOT/'.local_runtime/cloud_calculation6_20261020'
AUDIT = ROOT/'.local_runtime/Real Rebuild Room/FinSight/data/runtime/real-rebuild-ocr'
MODEL = 'deepseek-flash'
SUITE_SHA256 = '354ebd4bc3150f6b6404f3be1890ae8d43627187b1dcad80452ce1320a9fff3a'
SCORER_SHA256 = {
    'scoring_review_v2.py': '83e867cebbd3e4c99da56b557a5ccc8bd32cead72742df7baf5362e6635a4c45',
    'scoring.py': '58aa6129cdc8e50190c092d49c9d686b7476b7ec151b09e84716a2eb3c4501f9',
    'support.py': 'a841f0e7817fe0f922b5bb2cd9ecf4447d87843887722913723f8330b3790e25',
}
FOCUSED_ORDER = [
    ('P13V1', None), ('P13V2', None), ('P18V2', None),
    *[(f'G5T{i}', 'G5') for i in range(1, 5)], ('H06V2', None),
    *[(f'V3T{i}', 'V3') for i in range(1, 5)],
    *[(f'J12T{i}', 'J12') for i in range(1, 5)],
    *[(f'E01T{i}', 'E01') for i in range(1, 5)],
    ('K05V1', None), ('K05V2', None), ('K05V3', None), ('K06V2', None),
    *[(f'M03T{i}', 'M03') for i in range(1, 5)],
]
FOLLOWUP_SHA256 = '82e9c67b02182e0935064537d4becdb50105d8164f36f618da529112afa7aca0'
FOLLOWUP_ORDER = [(f'F36S{i:02}',None) for i in range(1,13)] + [
    (f'F36{group}T{i}',f'F36{group}') for group in 'ABCDEF' for i in range(1,5)]


class FreezeMismatch(RuntimeError):
    pass


class PendingSubmission(RuntimeError):
    """A request may exist remotely; resume its exact request ID, never replace it."""


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def value_digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def write_new(path, value):
    """Exclusive, flushed first-result write; a later run cannot overwrite it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2))
        stream.flush()
        os.fsync(stream.fileno())


def replace_derived(path, text):
    path = Path(path)
    pending = path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    with pending.open('x', encoding='utf-8') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)


def validate_suite(cases, *, profile='focused28'):
    order={'focused28':FOCUSED_ORDER,'followup36':FOLLOWUP_ORDER}[profile]
    if [(c.get('id'), c.get('group')) for c in cases] != order:
        raise ValueError('Expected the unchanged ordered '+profile+' suite')


def load_suite(path, *, profile='focused28'):
    expected={'focused28':SUITE_SHA256,'followup36':FOLLOWUP_SHA256}[profile]
    if digest(path) != expected:
        raise FreezeMismatch('Frozen '+profile+' suite bytes changed')
    cases = read(path)['cases']
    validate_suite(cases,profile=profile)
    return cases


def freeze_inputs(root, suite, scorer_dir, audit, oracle):
    root, scorer_dir, audit = Path(root), Path(scorer_dir), Path(audit)
    suffixes = {'.py', '.java', '.json', '.xml', '.yml', '.yaml', '.ts', '.vue', '.ps1', '.txt'}
    source = {}
    for directory in ('src', 'config', 'shared', 'backend-java/src', 'frontend/src', 'eval', 'scripts'):
        for path in sorted((root/directory).rglob('*')):
            if path.is_file() and path.suffix in suffixes and '__pycache__' not in path.parts:
                source[path.relative_to(root).as_posix()] = digest(path)
    for name in ('requirements.txt', 'requirements.lock.txt', 'backend-java/pom.xml',
                 'frontend/package.json', 'frontend/package-lock.json'):
        if (root/name).is_file():
            source[name] = digest(root/name)
    data = {name: digest(audit/name) for name in (
        'facts.json', 'acceptance.json', 'reports.json', 'manifest.json', 'index_coverage.json', 'narratives.jsonl')}
    scorer = {name: digest(scorer_dir/name) for name in (*SCORER_SHA256, 'freeze.json')}
    return {'schema': 1, 'source': source, 'data': data, 'scorer': scorer,
            'suite_sha256': digest(suite), 'oracle_sha256': digest(oracle),
            'model_policy': {'model': MODEL, 'temperature': 0, 'thinking': 'disabled',
                             'max_tokens': 3072, 'timeout_seconds': 60, 'max_retries': 0}}


def load_frozen_scorer(folder):
    """Import only the frozen score entry point; never call old setup/check_frozen."""
    folder = Path(folder)
    for name, expected in SCORER_SHA256.items():
        if digest(folder/name) != expected:
            raise FreezeMismatch('Frozen scorer bytes changed: '+name)
    previous = {name: sys.modules.get(name) for name in ('support', 'scoring')}
    previous_model = os.environ.get('FINSIGHT_EVAL_MODEL')
    os.environ['FINSIGHT_EVAL_MODEL'] = MODEL
    try:
        modules = {}
        for filename, name in (('support.py', 'support'), ('scoring.py', 'scoring'),
                               ('scoring_review_v2.py', '_finsight_focused_review')):
            spec = importlib.util.spec_from_file_location(name, folder/filename)
            module = importlib.util.module_from_spec(spec)
            if name in previous:
                sys.modules[name] = module
            spec.loader.exec_module(module)
            modules[name] = module
        return modules['_finsight_focused_review'].score
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if previous_model is None:
            os.environ.pop('FINSIGHT_EVAL_MODEL', None)
        else:
            os.environ['FINSIGHT_EVAL_MODEL'] = previous_model


class RunLedger:
    def __init__(self, folder, fingerprint, cases, *, resume=False):
        self.folder, self.cases = Path(folder), cases
        self.definition = {'fingerprint': fingerprint, 'cases_sha256': value_digest(cases)}
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder/'freeze.json'
        if path.exists():
            if not resume:
                raise FileExistsError('Existing run requires --resume')
            if read(path) != self.definition:
                raise FreezeMismatch('Source/data/suite/scorer/oracle freeze differs; use a new run directory')
        else:
            if resume:
                raise FreezeMismatch('Cannot resume an output without a frozen definition')
            if any((self.folder/name).exists() for name in ('answers', 'scores', 'requests', 'records.jsonl')):
                raise FreezeMismatch('Unidentified existing results in output')
            write_new(path, self.definition)
        missing_seen = False
        for case in cases:
            run = self.raw(case)
            if run is None:
                missing_seen = True
            elif missing_seen:
                raise FreezeMismatch('First results are not an ordered suite prefix')
            if self._path('scores', case).exists():
                self.score(case)
        self.sessions()  # Reject contradictory persisted conversation identities.

    def _path(self, category, case):
        identifier = case['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
            raise ValueError('Unsafe case identifier')
        return self.folder/category/(identifier+'.json')

    def raw(self, case):
        path = self._path('answers', case)
        if not path.exists():
            return None
        row = read(path)
        if row.get('case') != case or row.get('sha256') != value_digest({'case': row['case'], 'run': row['run']}):
            raise FreezeMismatch('Saved first answer changed: '+case['id'])
        return row['run']

    def save_raw(self, case, run):
        row = {'case': case, 'run': run}
        write_new(self._path('answers', case), {**row, 'sha256': value_digest(row), 'recorded_at': time.time()})
        self.export()

    def score(self, case):
        path = self._path('scores', case)
        if not path.exists():
            return None
        row = read(path)
        raw = self.raw(case)
        if raw is None or row.get('answer_sha256') != value_digest(raw) or row.get('case_id') != case['id']:
            raise FreezeMismatch('Score does not belong to the immutable answer: '+case['id'])
        if row.get('sha256') != value_digest(row['score']):
            raise FreezeMismatch('Saved first score changed: '+case['id'])
        return row['score']

    def save_score(self, case, score):
        raw = self.raw(case)
        if raw is None:
            raise ValueError('Save the complete raw answer before scoring')
        write_new(self._path('scores', case), {'case_id': case['id'], 'answer_sha256': value_digest(raw),
                  'score': score, 'sha256': value_digest(score)})
        self.export()

    def sessions(self):
        result = {}
        for case in self.cases:
            run = self.raw(case)
            group = case.get('group')
            if run and group:
                session = run.get('session_uid')
                if not session or (group in result and result[group] != session):
                    raise FreezeMismatch('A conversation group changed saved sessions')
                result[group] = session
        return result

    def intent(self, case):
        path = self._path('requests', case)
        session = self.sessions().get(case.get('group')) if case.get('group') else None
        if path.exists():
            intent = read(path)
            if intent['case'] != case or intent['session_uid'] != session:
                raise FreezeMismatch('Pending request differs from its frozen question/session')
            return intent
        intent = {'case': case, 'session_uid': session, 'client_request_id': uuid.uuid4().hex}
        write_new(path, intent)
        return intent

    def export(self):
        records = []
        for case in self.cases:
            run = self.raw(case)
            if run is not None:
                records.append({'case': case, 'run': run, 'score': self.score(case)})
        completed = [row for row in records if row['score'] is not None]
        summary = {'formal_total': len(self.cases), 'answers_saved': len(records), 'scored': len(completed),
                   'automated_passed': sum(bool(row['score'].get('automated_pass')) for row in completed),
                   'confirmed_passed': 0, 'pending_review': len(completed),
                   'status': 'awaiting_independent_review' if len(completed) == len(self.cases) else 'incomplete',
                   'lifecycle': 'separate; excluded from formal score', 'model': MODEL}
        checkpoint = {'freeze_sha256': value_digest(self.definition), 'sessions': self.sessions(),
                      'completed_ids': [row['case']['id'] for row in completed],
                      'answers_sha256': {row['case']['id']: value_digest(row['run']) for row in records}}
        for name, value in (('summary.json', summary), ('checkpoint.json', checkpoint)):
            replace_derived(self.folder/name, json.dumps(value, ensure_ascii=False, indent=2))
        replace_derived(self.folder/'records.jsonl', ''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in records))
        cards = []
        for row in records:
            answer = ((row['run'].get('task') or {}).get('result') or {}).get('answer') or {}
            cards.append('<article><h2>'+html.escape(row['case']['id'])+'</h2><p>'+
                         html.escape(row['case']['question'])+'</p><pre>'+html.escape(answer.get('content', ''))+
                         '</pre><details><summary>Full saved result and first score</summary><pre>'+
                         html.escape(json.dumps(row, ensure_ascii=False, indent=2))+'</pre></details></article>')
        page = ('<!doctype html><html lang="zh"><meta charset="utf-8"><title>FinSight cloud answers</title>'
                '<style>body{max-width:1050px;margin:2rem auto;font:16px system-ui;padding:1rem}'
                'article{border-top:1px solid #ddd;padding:1rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
                f'<h1>FinSight {len(self.cases)}轮首发回答</h1><p>自动检查完成后仍待独立正文复核。'
                '生命周期检查单列，不计入问答成绩。</p>'+''.join(cards)+'</html>')
        replace_derived(self.folder/'answers.html', page)


def saved_condition_errors(case,run):
    """Check persistent state independently; a correct visible number cannot hide a wrong next-turn order."""
    state=(((run.get('task') or {}).get('result') or {}).get('dialogue_state') or {})
    conditions=state.get('conditions') or {}
    paths={'codes':('codes',),'metrics':('metrics',),'years':('time','years'),'periods':('time','periods'),
           'scope':('scope',),'unit':('presentation','unit'),'decimals':('presentation','decimals'),
           'format':('presentation','format')}
    errors=[]
    for field,expected in case.get('expected_state',{}).items():
        actual=conditions
        for key in paths[field]:
            actual=actual.get(key) if isinstance(actual,dict) else None
        if isinstance(expected,list) and isinstance(actual,list):
            equal=set(expected)==set(actual)
        else:equal=actual==expected
        if not equal:errors.append('saved_condition_'+field)
    preferences={item['field']:item.get('value') for item in state.get('session_preferences',[])
                 if item.get('lifetime')=='session'}
    for field,expected in case['expect'].get('session_preferences',{}).items():
        if preferences.get(field)!=expected:errors.append('saved_session_preference_'+field)
    return errors


def calculation_input_errors(case,run,reference,oracle):
    """Explicitly requested input values are checked against PDF cards, independently of the executor."""
    from decimal import Decimal,ROUND_HALF_UP,localcontext
    from eval.common300_scoring import effective,match_goals
    from eval.real200_acceptance import FACTORS,wanted
    result=(run.get('task') or {}).get('result') or {}
    body=((result.get('answer') or {}).get('content') or '').replace(',','')
    errors=[]
    for spec,(observed,_) in zip(case['expect']['goal_expectations'],
                                 match_goals(case['expect']['goal_expectations'],result.get('request_contract') or {})):
        if not spec.get('include_inputs'):continue
        if not observed or not effective(observed,result['request_contract']).get('presentation',{}).get('include_inputs'):
            errors.append('requested_calculation_inputs_not_selected')
        for fact in wanted({**spec,'goals':[spec['kind']]},reference):
            unit=spec.get('unit') or ('万元' if fact['unit']=='元' else fact['unit'])
            with localcontext() as ctx:
                ctx.prec=50
                value=(Decimal(oracle['cards'][fact['id']]['value_exact'])/(FACTORS[unit] if fact['unit']=='元' else 1)).quantize(
                    Decimal(1).scaleb(-spec.get('decimals',2)),rounding=ROUND_HALF_UP)
            if format(value,'f') not in body:errors.append('requested_calculation_input_not_displayed')
    return sorted(set(errors))


def run_cases(cases, ledger, client, scorer, reference, oracle, *, verify=lambda: None):
    from eval.common300_scoring import expected_facts, validated_visible_context
    actual, expected = {}, {}
    for index, case in enumerate(cases):
        verify()
        group = case.get('group')
        before_actual = actual.get(group, []) if group else []
        before_expected = expected.get(group, []) if group else []
        run = ledger.raw(case)
        if run is None:
            intent = ledger.intent(case)
            try:
                run = client.turn(case['question'], uid=intent['session_uid'],
                                  client_id=intent['client_request_id'], disconnect=index == 0)
            except Exception as exc:
                incidents = ledger.folder/'incidents'
                write_new(incidents/(str(time.time_ns())+'.json'), {'case_id': case['id'],
                          'client_request_id': intent['client_request_id'], 'error_type': type(exc).__name__,
                          'status': 'ambiguous_submission; resume the same request ID'})
                raise PendingSubmission(case['id']) from None
            ledger.save_raw(case, run)  # Always precedes scorer imports/calls and failure handling.
        if ledger.score(case) is None:
            try:
                verdict = copy.deepcopy(scorer(case, run, reference, oracle, before_actual, before_expected, entry='java'))
                additional=saved_condition_errors(case,run)+calculation_input_errors(case,run,reference,oracle)
                verdict['errors']=sorted(set(verdict.get('errors',[])+additional))
                verdict['automated_pass']=not verdict['errors']
            except Exception as exc:
                verdict = {'automated_pass': False, 'errors': ['scorer_error_'+type(exc).__name__], 'critical': [],
                           'review_required': ['independent_scoring_failure_review']}
            verdict['review_required'] = sorted(set(verdict.get('review_required', [])+['independent_final_answer_review']+
                (['original_input_values_and_units'] if case['id'] in {'K05V1', 'K05V2', 'K05V3'} else [])))
            verdict['passed'] = False
            verdict['review_status'] = 'pending'
            verdict['entry'] = 'java'
            ledger.save_score(case, verdict)
        if group:
            actual[group] = validated_visible_context(run, reference, oracle, before_actual)
            values = expected_facts(case, reference)
            expected[group] = ([{**f, 'value_exact': oracle['cards'][f['id']]['value_exact']} for f in values.values()]
                               if values else before_expected)
            if case['expect'].get('clear_company') or case['expect']['kind'] in {'clarify', 'missing'}:
                expected[group] = []
        ledger.export()
        verdict = ledger.score(case) or {}
        label = '自动检查通过，待正文复核' if verdict.get('automated_pass') else '存在差异，保留首发结果'
        print(f"[{index+1:02d}/{len(cases)}] {case['id']} | {label} | {run.get('seconds', '?')}秒", flush=True)
    verify()


def load_reference(audit, oracle):
    acceptance = read(audit/'acceptance.json')
    if not all(acceptance.get(k) for k in ('facts_verified', 'reports_verified', 'index_verified', 'projections_verified')):
        raise FreezeMismatch('Real-data audit is not accepted')
    for name, expected in acceptance.get('artifacts', {}).items():
        path = (audit/name).resolve()
        if not path.is_relative_to(audit.resolve()) or digest(path) != expected:
            raise FreezeMismatch('Accepted audit artifact changed')
    facts = read(audit/'facts.json')
    version = {f['data_version'] for f in facts}
    if version != {oracle['data_version']}:
        raise FreezeMismatch('Oracle and accepted real facts do not share one data version')
    return {'version': oracle['data_version'], 'facts': {f['id']: f for f in facts}, 'reports': read(audit/'reports.json')}


def validate_isolation(env, output):
    chat = 'finsight_cloud_alignment_20261020'
    if env.get('DB_NAME') != chat or env.get('MYSQL_DATABASE') != chat:
        raise ValueError('Focused tests require their dedicated chat database')
    if env.get('SQL_DB_NAME') != 'finsight_real_rebuild_20261002':
        raise ValueError('Finance SQL_DB_NAME must remain the accepted real read-only database')
    if not env.get('SQL_DB_USER') or env['SQL_DB_USER'] in {'root', env.get('DB_USER')}:
        raise ValueError('Finance queries require a separate read-only account')
    redis = env.get('REDIS_DATABASE', '')
    if not redis.isdigit() or not 1 <= int(redis) <= 15:
        raise ValueError('Select a distinct Redis index in 1..15')
    if env.get('RABBITMQ_VHOST') != '/finsight-cloud-alignment-20261020':
        raise ValueError('Focused tests require their dedicated RabbitMQ vhost')
    task = Path(env.get('FINSIGHT_TASK_DB', '')).resolve()
    if not env.get('FINSIGHT_TASK_DB') or not task.is_relative_to(Path(output).resolve()):
        raise ValueError('Task store must stay inside this test output')
    if env.get('EMBEDDING_PROVIDER') != 'bge_local' or env.get('EMBEDDING_MODEL') != 'BAAI/bge-small-zh-v1.5':
        raise ValueError('Preserve local BGE and its existing vector space')
    return {'chat_db': chat, 'finance_db': env['SQL_DB_NAME'], 'redis_database': int(redis),
            'rabbitmq_vhost': env['RABBITMQ_VHOST'], 'task_db': str(task)}


def cancel_probe(client, runtime, *, pause=lambda: time.sleep(1), request_id=None):
    run = client.turn('请简要解释营业收入与归母净利润的区别，不查询数字。',
                      client_id=request_id or uuid.uuid4().hex, cancel_after=.2)
    status = (run.get('task') or {}).get('status')
    observations = []
    for index in range(15):
        pause()
        task = client.call('GET', '/api/chat/tasks/'+run['task_id'])
        observations.append({'task': task, 'runtime': runtime()})
        model = observations[-1]['runtime'].get('model') or {}
        if index >= 2 and model.get('active') == 0 and model.get('waiting') == 0:
            break
    if status == 'completed':
        return {'status': 'inconclusive_completion_race', 'passed': False, 'run': run, 'observations': observations,
                'note': 'The first result is retained; no replacement cancellation attempt was made'}
    errors = []
    if status != 'cancelled':
        errors.append('cancel_terminal_not_observed')
    for observed in [{'task': run.get('task') or {}}, *observations]:
        task = observed['task']
        if task.get('status') != 'cancelled' or task.get('saved') is not True:
            errors.append('cancel_not_saved_or_late_terminal_change')
        result = task.get('result') or {}
        if any(result.get(k) for k in ('facts', 'derived_facts', 'chart_data_list', 'evidence', 'default_reference')):
            errors.append('cancelled_task_published_financial_result')
    if ((observations[-1]['runtime'].get('model') or {}).get('active') != 0 or
            (observations[-1]['runtime'].get('model') or {}).get('waiting') != 0):
        errors.append('local_model_scheduler_not_idle')
    return {'status': 'passed' if not errors else 'failed', 'passed': not errors, 'errors': sorted(set(errors)),
            'run': run, 'observations': observations,
            'scope': 'Local cancellation/persistence/late-publication/admission only; no provider billing or GPU claim'}


def audit_summary(path):
    path = Path(path)
    rows, malformed = [], 0
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                source = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            # Only data emitted by the identity/usage logger is allowed into this export.
            row = {k: source[k] for k in ('event', 'id', 'at', 'model', 'kind', 'seconds', 'error') if k in source}
            if isinstance(source.get('usage'), dict):
                row['usage'] = {k: v for k, v in source['usage'].items()
                                if k in {'prompt_tokens', 'completion_tokens', 'total_tokens'} and isinstance(v, int)}
            rows.append(row)
    starts = {r.get('id'): r for r in rows if r.get('event') == 'start'}
    usage = {key: sum(r.get('usage', {}).get(key, 0) for r in rows if r.get('event') == 'end')
             for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    return {'starts': len(starts), 'ends': sum(r.get('event') == 'end' for r in rows),
            'titles': sum('title' in str(r.get('kind', '')).lower() for r in starts.values()),
            'by_kind': {kind: sum(r.get('kind') == kind for r in starts.values())
                        for kind in sorted({str(r.get('kind', 'unknown')) for r in starts.values()})},
            'response_models': sorted({r['model'] for r in rows if r.get('event') == 'end' and r.get('model')}),
            'usage': usage, 'malformed_rows': malformed, 'rows': rows,
            'pricing': 'No price inference; consult actual provider usage and applicable account terms'}


def saved_session_checks(client, ledger):
    """Read the stored sessions twice, as refresh/switch does, without another generation."""
    sessions = {}
    for case in ledger.cases:
        run = ledger.raw(case)
        if run:
            sessions.setdefault(run['session_uid'], []).append((case, run))
    checks = []
    for session, turns in sessions.items():
        detail = client.call('GET', '/api/chat/sessions/'+session)
        messages = detail.get('messages') or []
        errors = []
        for case, run in turns:
            if not any(m.get('role') == 'user' and m.get('content') == case['question'] for m in messages):
                errors.append('saved_user_turn_missing_'+case['id'])
            answer = (((run.get('task') or {}).get('result') or {}).get('answer') or {}).get('content', '')
            if run.get('task', {}).get('saved') and answer and not any(
                    m.get('role') == 'assistant' and m.get('content') == answer for m in messages):
                errors.append('saved_assistant_turn_missing_'+case['id'])
            task = client.call('GET', '/api/chat/tasks/'+run['task_id'])
            if task.get('status') != run.get('task', {}).get('status') or task.get('saved') is not True:
                errors.append('task_poll_terminal_changed_'+case['id'])
        checks.append({'session_uid': session, 'case_ids': [c['id'] for c, _ in turns],
                       'passed': not errors, 'errors': errors, 'detail': detail})
    # Switch through a different session, then fetch the original to check persistence.
    if sessions:
        first = next(iter(sessions))
        restored = client.call('GET', '/api/chat/sessions/'+first)
        if restored != checks[0]['detail']:
            # An asynchronous title can legitimately change while messages stay identical.
            if restored.get('messages') != checks[0]['detail'].get('messages'):
                checks[0]['errors'].append('session_switch_changed_saved_messages')
                checks[0]['passed'] = False
    return {'scope': 'API refresh, task polling and session switching; browser rendering not tested',
            'passed': bool(checks) and all(c['passed'] for c in checks), 'sessions': checks}


@contextlib.contextmanager
def runner_lock(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder/'runner.lock').open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def verify_agent_version(value, root=ROOT):
    paths = sorted((Path(root)/'src/agent/v3').glob('*.py'))
    expected = hashlib.sha256(b''.join(p.read_bytes() for p in paths)).hexdigest()
    if not paths or value.get('dialogue_version') != 3 or value.get('build') != expected:
        raise FreezeMismatch('The running Python agent does not match the current v3 source')


def verify_launch_source(marker, source):
    if marker.get('source_sha256') != value_digest(source):
        raise FreezeMismatch('The test services were launched before the current source was frozen')


def verify_stack(path, base, agent_base, namespaces, source):
    """A saved marker alone is insufficient: verify current listener PID/start identity."""
    import subprocess
    from urllib.parse import urlsplit
    marker = read(path)
    verify_launch_source(marker, source)
    if Path(marker.get('root', '')).resolve() != ROOT.resolve() or marker.get('namespaces') != namespaces:
        raise ValueError('Stack does not belong to this source root and isolated namespaces')
    if marker.get('java_base') != base or marker.get('agent_base') != agent_base:
        raise ValueError('Stack endpoint identity differs')
    for name, url in (('java', base), ('agent', agent_base)):
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port:
            raise ValueError('Only explicit loopback test endpoints are permitted')
        if parsed.port in {8000, 8080, 18000, 18080, 19000, 19100}:
            raise ValueError('Do not attach focused tests to daily or historical endpoints')
        expected = marker['listeners'][name]
        command = (f'$p=Get-NetTCPConnection -State Listen -LocalPort {parsed.port} -ErrorAction Stop | '
                   'Select-Object -ExpandProperty OwningProcess -Unique; '
                   'if (@($p).Count -ne 1) { throw "Ambiguous listener" }; '
                   '$v=Get-Process -Id $p -ErrorAction Stop; '
                   '@{pid=$v.Id;started_ticks=$v.StartTime.ToUniversalTime().Ticks.ToString()} | ConvertTo-Json -Compress')
        checked = subprocess.run(['powershell', '-NoProfile', '-Command', command],
                                 capture_output=True, text=True, check=True)
        if json.loads(checked.stdout) != expected:
            raise ValueError('Occupied test endpoint has an unknown process identity')


def prepare_chat_database(output):
    validate_isolation(os.environ, output)
    from sqlalchemy import create_engine, text
    from config.db_config import get_db_config
    engine = create_engine(get_db_config().connection_string_no_db)
    try:
        with engine.begin() as connection:
            connection.execute(text('CREATE DATABASE IF NOT EXISTS `finsight_cloud_alignment_20261020` '
                                    'CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'))
    finally:
        engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite-profile', choices=['focused28','followup36'], default='focused28')
    parser.add_argument('--suite', type=Path)
    parser.add_argument('--scorer-dir', type=Path, default=PRIOR)
    parser.add_argument('--oracle', type=Path, default=PRIOR/'oracle.json')
    parser.add_argument('--audit', type=Path, default=AUDIT)
    parser.add_argument('--assets-root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base', default='http://127.0.0.1:19380')
    parser.add_argument('--agent-base', default='http://127.0.0.1:19300')
    parser.add_argument('--stack', type=Path)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--check-only', action='store_true', help='Hash local assets only; no services or API calls')
    parser.add_argument('--source-digest', action='store_true', help='Print only the launch source digest; no services or API calls')
    parser.add_argument('--prepare-chat-db', action='store_true', help='Create only the dedicated chat database, then exit')
    parser.add_argument('--skip-cancel-probe', action='store_true')
    parser.add_argument('--skip-extraction-smoke', action='store_true')
    args = parser.parse_args(argv)
    if args.suite is None:
        args.suite=ROOT/'eval/cloud_followup36_cases.json' if args.suite_profile=='followup36' else PRIOR/'cases_focused28.json'
    if args.suite_profile=='followup36':args.skip_extraction_smoke=True
    output = args.output.resolve()
    if args.prepare_chat_db:
        prepare_chat_database(output)
        print('Dedicated chat database prepared; finance database unchanged.')
        return 0
    cases = load_suite(args.suite,profile=args.suite_profile)
    counts={'formal_turns':len(cases),'single_questions':sum(not c.get('group') for c in cases),
            'four_turn_groups':len({c['group'] for c in cases if c.get('group')})}
    oracle = read(args.oracle)
    reference = load_reference(args.audit.resolve(), oracle)
    fingerprint = freeze_inputs(ROOT, args.suite, args.scorer_dir, args.audit, args.oracle)
    scorer = load_frozen_scorer(args.scorer_dir)
    if args.source_digest:
        print(value_digest(fingerprint['source']))
        return 0
    if args.check_only:
        if (output/'freeze.json').exists():
            # The runtime scope is checked by the normal --resume command, without a live call here.
            frozen = read(output/'freeze.json')['fingerprint']
            if any(frozen.get(k) != v for k, v in fingerprint.items()):
                raise FreezeMismatch('Saved source/data/suite/scorer/oracle differs')
        from eval.cloud_extraction import run_probe
        extraction = run_probe(args.audit, args.assets_root, generate=False)
        print(json.dumps({'local_check': 'passed',**counts,'profile':args.suite_profile,
                          'data_version': reference['version'], 'api_calls': 0}))
        print(json.dumps({'ocr_sample': extraction['status'], 'api_calls': 0}))
        return 0
    namespaces = validate_isolation(os.environ, output)
    fingerprint['runtime'] = {'base': args.base, 'agent_base': args.agent_base, 'namespaces': namespaces}
    with runner_lock(output):
        ledger = RunLedger(output, fingerprint, cases, resume=args.resume)
        verify_stack(args.stack or output/'stack.json', args.base, args.agent_base, namespaces, fingerprint['source'])
        import requests
        token = os.environ.get('INTERNAL_API_TOKEN', '')
        if not token:
            raise ValueError('Private internal token required for resource checks')
        headers = {'X-Internal-Token': token}
        version = requests.get(args.agent_base+'/internal/agent-version', headers=headers, timeout=12)
        version.raise_for_status()
        verify_agent_version(version.json())

        def runtime():
            response = requests.get(args.agent_base+'/internal/runtime', headers={'X-Internal-Token': token}, timeout=12)
            response.raise_for_status()
            return response.json()

        # Re-check the accepted release via the read-only account; never clone/reimport finance.
        from sqlalchemy import create_engine
        from config.db_config import get_readonly_db_config
        from src.agent.v3.repository import CanonicalRepository
        engine = create_engine(get_readonly_db_config().connection_string)
        try:
            repository = CanonicalRepository(engine)
            if repository.version != reference['version']:
                raise FreezeMismatch('Running finance release differs from the frozen oracle')
        finally:
            engine.dispose()
        from eval.v3_native_client import NativeClient
        client = NativeClient(args.base, output/'.private'/'native-credentials.json')

        def verify():
            current = freeze_inputs(ROOT, args.suite, args.scorer_dir, args.audit, args.oracle)
            current['runtime'] = fingerprint['runtime']
            if current != fingerprint:
                raise FreezeMismatch('Frozen source/data/suite/scorer/oracle changed during the run')

        run_cases(cases, ledger, client, scorer, reference, oracle, verify=verify)
        history_path = output/'lifecycle-sessions.json'
        if not history_path.exists():
            write_new(history_path, saved_session_checks(client, ledger))
        cancel_path = output/'lifecycle-cancel.json'
        if not cancel_path.exists() and not args.skip_cancel_probe:
            intent_path = output/'cancel-request.json'
            if not intent_path.exists():
                write_new(intent_path, {'client_request_id': uuid.uuid4().hex})
            write_new(cancel_path, cancel_probe(client, runtime, request_id=read(intent_path)['client_request_id']))
        extraction_path = output/'extraction-smoke.json'
        if not extraction_path.exists() and not args.skip_extraction_smoke:
            intent = output/'extraction-intent.json'
            if intent.exists():
                write_new(extraction_path, {'status': 'interrupted_first_attempt_not_retried',
                    'note': 'The prior OCR attempt may have reached the provider; no replacement was sent.'})
            else:
                write_new(intent, {'started_at': time.time(), 'generation_limit': 2})
                from eval.cloud_extraction import run_probe
                write_new(extraction_path, run_probe(args.audit, args.assets_root, generate=True))
        ledger.export()
        audit = audit_summary(os.environ.get('FINSIGHT_MODEL_AUDIT_PATH') or output/'model-audit.jsonl')
        replace_derived(output/'model-usage.json', json.dumps(audit, ensure_ascii=False, indent=2))
        print(json.dumps({'status': 'formal_answers_saved_pending_review',**counts,'profile':args.suite_profile,
                          'session_checks': read(history_path)['passed'],
                          'cancel_probe': read(cancel_path)['status'] if cancel_path.exists() else 'not_run',
                          'ocr_sample': read(extraction_path)['status'] if extraction_path.exists() else 'not_run',
                          'audit_calls': audit['starts'], 'title_calls': audit['titles'],
                          'viewer': str(output/'answers.html')}, ensure_ascii=False))
        return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('Interrupted; keep the output and resume the same frozen run. No completed answer will be replaced.')
        raise SystemExit(130)
    except Exception as exc:
        # Driver/provider exception strings can contain private details; keep only the type.
        print(json.dumps({'status': 'stopped', 'error_type': type(exc).__name__,
                          'hint': 'Inspect the preserved checkpoint; do not overwrite answers or change frozen inputs.'}))
        raise SystemExit(2)
