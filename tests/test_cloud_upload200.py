"""Offline tests for the frozen NEW-company Java/SSE acceptance runner."""
from __future__ import annotations

import copy
import importlib
import json
from collections import Counter
from pathlib import Path

import pytest


@pytest.fixture
def harness():
    assert importlib.util.find_spec('eval.cloud_upload200') is not None, 'upload200 runner is not implemented'
    return importlib.import_module('eval.cloud_upload200')


def test_sse_client_renews_expired_login_without_changing_request_identity(harness):
    import requests
    def response(status,value):
        r=requests.Response();r.status_code=status;r._content=json.dumps(value).encode();r._content_consumed=True;return r
    replies=[response(200,dict(code=0,data=dict(accessToken='access-a',refreshToken='refresh-a'))),
        response(401,dict(code=401)),response(200,dict(code=0,data=dict(accessToken='access-b',refreshToken='refresh-b'))),
        response(200,dict(code=0,data=None)),response(401,dict(code=401)),
        response(200,dict(code=0,data=dict(accessToken='access-c',refreshToken='refresh-c'))),response(200,dict(code=0,data=None))]
    calls=[]
    class HTTP:
        def __init__(self):self.headers={}
        def request(self,method,url,**kw):
            calls.append(dict(url=url,json=copy.deepcopy(kw.get('json')),headers=kw.get('headers'),
                authorization=self.headers.get('Authorization')))
            return replies.pop(0)
    client=harness.JavaSSEClient('http://127.0.0.1:18280','test_user','test-password',transport=HTTP(),pause=lambda _:None)
    body=dict(question='测试',sessionUid='same-session',clientRequestId='same-request')
    client._request('POST','/api/chat/stream',json=body).close()
    assert calls[1]['json']==calls[3]['json']==body
    assert calls[2]['json']==dict(refreshToken='refresh-a') and calls[2]['headers']=={'Authorization':None}
    assert calls[3]['authorization']=='Bearer access-b'
    client.call('GET','/api/chat/tasks/recover',params=dict(clientRequestId='same-request'))
    assert calls[5]['json']==dict(refreshToken='refresh-b')


@pytest.mark.parametrize('denied',[401,403])
def test_sse_login_refresh_is_bounded_and_preserves_authorization_denial(harness,denied):
    import requests
    def response(status,value):
        r=requests.Response();r.status_code=status;r._content=json.dumps(value).encode();r._content_consumed=True;return r
    replies=[response(200,dict(code=0,data=dict(accessToken='access-a',refreshToken='refresh-a'))),response(denied,dict(code=denied))]
    if denied==401:replies.extend([response(200,dict(code=0,data=dict(accessToken='access-b',refreshToken='refresh-b'))),response(401,dict(code=401))])
    calls=[]
    class HTTP:
        headers={}
        def request(self,method,url,**kw):calls.append(url);return replies.pop(0)
    client=harness.JavaSSEClient('http://127.0.0.1:18280','test_user','test-password',transport=HTTP(),pause=lambda _:None)
    with pytest.raises(harness.RunStopped,match='authentication_or_authorization'):
        client.call('GET','/api/chat/tasks/recover',params=dict(clientRequestId='same-request'))
    assert len(calls)==(4 if denied==401 else 2)


def sample_case(identifier='S01', group=None, turn=1):
    spec = dict(kind='lookup', codes=['000333'], years=[2024], periods=['FY'],
                metrics=['operating_revenue'], scope='consolidated', unit='万元', decimals=2)
    return dict(id=identifier, company_code='000333', partition='development', group=group, turn=turn,
                question='美的集团2024年营业收入是多少？请用万元，保留两位小数。',
                expect=dict(kind='financial', goal_expectations=[spec], no_chart=True))


def sample_data():
    source = dict(source_path='data_root/original.pdf', source_sha256='a'*64, document_version='a'*64,
                  page=3, row='营业收入', raw_value='100,000,000.00', raw_unit='元',
                  table='合并利润表', column='2024年度')
    fact = dict(id='f1', stock_code='000333', company='美的集团', year=2024, period='FY',
                metric='operating_revenue', scope='consolidated', unit='元', value='100000000.00',
                data_version='new120-v1', source=source)
    ref = dict(version='new120-v1', facts={'f1': fact}, reports=[])
    oracle = dict(data_version='new120-v1', cards={'f1': dict(value_exact='100000000.00',
                  source=source, original_row_text='营业收入 100,000,000.00 90,000,000.00',
                  original_page_text='合并利润表 单位：元 营业收入 100,000,000.00 90,000,000.00')})
    return ref, oracle


def sample_run(case=None, *, session='session-one', request_id='request-one'):
    item = case or sample_case()
    ref, _ = sample_data()
    conditions = dict(codes=['000333'], metrics=['operating_revenue'], scope='consolidated',
                      time=dict(years=[2024], periods=['FY']),
                      presentation=dict(unit='万元', decimals=2, format='auto'))
    result = dict(answer=dict(content='美的集团2024年全年营业收入为10,000.00万元。'),
                  facts=[dict(ref['facts']['f1'], value_exact='100000000.00')],
                  request_contract=dict(conditions=conditions, goals=[dict(id='internal-z', kind='lookup')]),
                  dialogue_state=dict(conditions=conditions),
                  task_results=[dict(id='internal-z', kind='lookup', status='completed', fact_ids=['f1'])],
                  outcome=dict(status='answered'), data_version='new120-v1',
                  diagnostics=dict(timings=[dict(model='private-model-v1')]),
                  query_trace=[dict(purpose='financial_facts', sql='SELECT * FROM canonical_facts')])
    return dict(question=item['question'], session_uid=session, client_request_id=request_id,
                task_id='task-'+request_id, events=[], seconds=1,
                task=dict(status='completed', saved=True, question=item['question'], session_uid=session,
                          client_request_id=request_id, task_id='task-'+request_id, result=result))


def original_reference(harness, tmp_path, text='Revenue 100,000,000.00 Unit: CNY'):
    from reportlab.pdfgen import canvas
    path = tmp_path/'data_root'/'original.pdf'; path.parent.mkdir(exist_ok=True)
    writer = canvas.Canvas(str(path)); writer.drawString(40, 760, text); writer.save()
    ref, _ = sample_data(); fact = ref['facts']['f1']
    fact['source'].update(source_path='data_root/original.pdf', source_sha256=harness.digest(path),
        document_version=harness.digest(path), page=1, row='Revenue')
    ref['reports'] = [dict(stock_code=fact['stock_code'], company=fact['company'], year=fact['year'],
        period=fact['period'], source_path=fact['source']['source_path'],
        source_sha256=harness.digest(path), document_version=harness.digest(path), page_count=1)]
    return ref


def test_trial_json_narratives_and_index_receipts_are_frozen(harness, tmp_path):
    ref = original_reference(harness, tmp_path)
    artifacts = tmp_path/'release'; artifacts.mkdir()
    for name, value in {'reports.json': ref['reports'], 'facts.json': list(ref['facts'].values()),
            'narratives.json': [], 'manifest.json': dict(version=ref['version'], review_mode='relaxed_ocr'),
            'index.json': dict(version=ref['version'], collection='frozen-collection', accepted=True),
            'publication.json': dict(version=ref['version'], status='trial_ready')}.items():
        (artifacts/name).write_text(json.dumps(value), encoding='utf-8')
    release = harness.load_release(artifacts, root=tmp_path, require_cohort=False)
    assert set(release['artifacts']) == {'reports.json', 'facts.json', 'narratives.json',
        'manifest.json', 'index.json', 'publication.json'}
    assert release['artifacts']['index.json'] == harness.digest(artifacts/'index.json')
    assert len(release['inventory_gaps']) == 119


@pytest.mark.parametrize('field', ['fact_status', 'source_verification'])
def test_explicit_unverified_ocr_never_becomes_originally_verified(harness, tmp_path, field):
    ref = original_reference(harness, tmp_path)
    if field == 'fact_status':
        ref['facts']['f1']['status'] = 'unverified'
    else:
        ref['facts']['f1']['source']['verification'] = 'unverified'
    oracle = harness.build_oracle([sample_case()], ref, root=tmp_path, record_data_gaps=True)
    assert 'f1' not in oracle['cards']
    assert oracle['data_gaps'][0]['reason'] == 'original_audit_incomplete'
    assert oracle['data_gaps'][0]['case_ids'] == ['S01']


@pytest.mark.parametrize('change', ['company', 'stock_code', 'year', 'period'])
def test_original_oracle_binds_fact_to_registered_report_identity(harness, tmp_path, change):
    ref = original_reference(harness, tmp_path); case = sample_case()
    replacement = dict(company='Other company', stock_code='002594', year=2025, period='HY')[change]
    ref['facts']['f1'][change] = replacement
    spec = case['expect']['goal_expectations'][0]
    if change in {'stock_code', 'year', 'period'}:
        spec[dict(stock_code='codes', year='years', period='periods')[change]] = [replacement]
    oracle = harness.build_oracle([case], ref, root=tmp_path, record_data_gaps=True)
    assert 'f1' not in oracle['cards']
    assert oracle['data_gaps'][0]['reason'] == 'original_revalidation_failed'


@pytest.mark.parametrize('actual', ['-100,000,000.00', '100,000,000.001', '100,000,000.000'])
def test_original_oracle_requires_exact_signed_numeric_token(harness, tmp_path, actual):
    ref = original_reference(harness, tmp_path, 'Revenue '+actual+' Unit: CNY')
    oracle = harness.build_oracle([sample_case()], ref, root=tmp_path, record_data_gaps=True)
    assert 'f1' not in oracle['cards']


def test_original_oracle_rejects_claimed_precision_different_from_original_literal(harness, tmp_path):
    ref = original_reference(harness, tmp_path)
    ref['facts']['f1']['source']['raw_precision'] = 1
    oracle = harness.build_oracle([sample_case()], ref, root=tmp_path, record_data_gaps=True)
    assert 'f1' not in oracle['cards']


def test_original_oracle_rejects_amount_unit_different_from_registered_metric(harness, tmp_path):
    ref = original_reference(harness, tmp_path)
    ref['facts']['f1']['unit'] = '%'
    oracle = harness.build_oracle([sample_case()], ref, root=tmp_path, record_data_gaps=True)
    assert 'f1' not in oracle['cards']


@pytest.mark.parametrize('change', ['unverified_input', 'cross_company', 'wrong_metric', 'blank_inputs'])
def test_derived_oracle_requires_verified_inputs_with_same_identity_and_formula(harness, tmp_path, change):
    ref = original_reference(harness, tmp_path, 'Revenue 100,000,000.00 Cost 40,000,000.00 Unit: CNY')
    base = copy.deepcopy(ref['facts']['f1']); base.update(id='cost', metric='operating_cost', value='40000000.00')
    base['source'].update(row='Cost', raw_value='40,000,000.00')
    ref['facts']['cost'] = base
    derived = dict(ref['facts']['f1'], id='gross', metric='gross_profit', value='60000000.00',
                   status='derived', source=None, inputs=['f1', 'cost'], formula='operating_revenue - operating_cost')
    ref['facts']['gross'] = derived
    case = sample_case(); case['expect']['goal_expectations'][0]['metrics'] = ['gross_profit']
    if change == 'unverified_input': base['status'] = 'unverified'
    elif change == 'cross_company': base['stock_code'] = '002594'
    elif change == 'wrong_metric': base['metric'] = 'net_profit'
    else: derived['inputs'] = []
    oracle = harness.build_oracle([case], ref, root=tmp_path, record_data_gaps=True)
    assert 'gross' not in oracle['cards']
    assert oracle['data_gaps'][0]['reason'] == ('original_audit_incomplete' if change == 'unverified_input'
                                               else 'original_revalidation_failed')


def test_public_freeze_settings_reject_nested_credentials(harness, tmp_path):
    reference = dict(version='v1', artifacts={}, documents={})
    options = dict(root=tmp_path, cases_path=harness.CASE_FILE, model_version='private-model-v1', partition='all')
    with pytest.raises(ValueError, match='credentials'):
        harness._fingerprint(reference, {}, model_policy={'sampling': [{'api_key': 'private-value'}]}, **options)


def test_runtime_snapshot_is_frozen_and_nested_private_fields_are_rejected(harness, tmp_path):
    reference = dict(version='v1', artifacts={}, documents={})
    snapshot = dict(profile='ocr-trial-v1', pointer='ocr_trial', version='v1',
                    collection='collection-one', index_count=123, index_fingerprint='fingerprint-one')
    options = dict(root=tmp_path, cases_path=harness.CASE_FILE, model_version='private-model-v1',
                   model_policy={}, partition='all', runtime_snapshot=snapshot)
    fingerprint = harness._fingerprint(reference, {}, **options)
    assert fingerprint['runtime_snapshot'] == snapshot
    snapshot['collection'] = 'changed-after-freeze'
    assert fingerprint['runtime_snapshot']['collection'] == 'collection-one'
    with pytest.raises(ValueError, match='credentials'):
        harness._fingerprint(reference, {}, **dict(options, runtime_snapshot={'index': {'password': 'private-value'}}))


def test_runtime_snapshot_cannot_name_a_different_release(harness, tmp_path):
    with pytest.raises(harness.FreezeMismatch, match='runtime'):
        harness._fingerprint(dict(version='v1', artifacts={}, documents={}), {}, root=tmp_path,
            cases_path=harness.CASE_FILE, model_version='private-model-v1', model_policy={}, partition='all',
            runtime_snapshot=dict(version='other-release'))


def test_runtime_change_stops_before_any_java_turn(harness, tmp_path, monkeypatch):
    ref = original_reference(harness, tmp_path)
    ref.update(folder=str(tmp_path), artifacts={}, documents={})
    monkeypatch.setattr(harness, 'load_cases', lambda path: [sample_case()])
    monkeypatch.setattr(harness, 'load_release', lambda *args, **kwargs: ref)
    class NoJavaCalls:
        def call(self, *args, **kwargs): raise AssertionError('runtime drift must stop before a Java call')
        def turn(self, *args, **kwargs): raise AssertionError('runtime drift must stop before a model turn')
    with pytest.raises(harness.FreezeMismatch, match='Active profile'):
        harness.run(tmp_path, tmp_path/'run', root=tmp_path, model_version='private-model-v1',
            client=NoJavaCalls(), interval=0, runtime_snapshot=dict(version=ref['version'], index_count=3),
            verify=lambda: dict(version=ref['version'], index_count=4))
    assert not (tmp_path/'run'/'answers').exists()


def test_cached_oracle_gaps_still_require_explicit_record_data_gaps(harness, tmp_path, monkeypatch):
    ref = dict(version='v1', facts={}, reports=[], folder=str(tmp_path), artifacts={}, documents={})
    monkeypatch.setattr(harness, 'load_cases', lambda path: [sample_case()])
    monkeypatch.setattr(harness, 'load_release', lambda *args, **kwargs: ref)
    options = dict(root=tmp_path, model_version='private-model-v1')
    harness.prepare(tmp_path, tmp_path/'run', record_data_gaps=True, **options)
    with pytest.raises(harness.OracleMissing):
        harness.prepare(tmp_path, tmp_path/'run', record_data_gaps=False, **options)


def test_pre_calibration_oracle_cache_cannot_silently_skip_revalidation(harness, tmp_path, monkeypatch):
    ref = dict(version='v1', facts={}, reports=[], folder=str(tmp_path), artifacts={}, documents={})
    monkeypatch.setattr(harness, 'load_cases', lambda path: [sample_case()])
    monkeypatch.setattr(harness, 'load_release', lambda *args, **kwargs: ref)
    harness.prepare(tmp_path, tmp_path/'run', root=tmp_path, model_version='private-model-v1', record_data_gaps=True)
    path = tmp_path/'run'/'oracle.json'; old = harness.read(path)
    old.pop('calibration_version', None)
    path.write_text(json.dumps(old), encoding='utf-8')
    with pytest.raises(harness.FreezeMismatch, match='oracle'):
        harness.prepare(tmp_path, tmp_path/'run', root=tmp_path, model_version='private-model-v1', record_data_gaps=True)


def test_prepare_reports_missing_facts_separately_from_unaudited_originals(harness, tmp_path, monkeypatch):
    ref = original_reference(harness, tmp_path)
    ref.update(folder=str(tmp_path), artifacts={}, documents={})
    ref['facts']['f1']['status'] = 'unverified'
    cases = [sample_case(), sample_case('S02')]
    cases[1]['company_code'] = '300750'; cases[1]['partition'] = 'holdout'
    cases[1]['expect']['goal_expectations'][0]['codes'] = ['300750']
    monkeypatch.setattr(harness, 'load_cases', lambda path: cases)
    monkeypatch.setattr(harness, 'load_release', lambda *args, **kwargs: ref)
    output = tmp_path/'run'
    harness.prepare(tmp_path, output, root=tmp_path, model_version='private-model-v1', record_data_gaps=True)
    preparation = harness.read(output/'preparation.json')
    assert preparation['formal_total'] == 2
    assert preparation['unavailable_metric_cases'] == 1
    assert preparation['original_audit_incomplete_cases'] == 1
    assert preparation['original_verified_direct_facts'] == 0
    assert preparation['original_oracle_complete'] is False
    assert preparation['no_cloud_request_made'] is True
    assert harness.read(output/'summary.json')['formal_total'] == 2


def test_fixed_questions_have_200_turns_120_80_split_and_20_whole_dialogues(harness):
    cases = harness.load_cases()
    assert len(cases) == 200
    assert Counter(c['partition'] for c in cases) == dict(development=120, holdout=80)
    assert sum(c['group'] is None for c in cases) == 120
    groups = {c['group'] for c in cases if c['group']}
    assert len(groups) == 20
    for group in groups:
        rows = [c for c in cases if c['group'] == group]
        assert [c['turn'] for c in rows] == [1, 2, 3, 4]
        assert len({c['partition'] for c in rows}) == 1
        assert len({c['company_code'] for c in rows}) == 1
    assert Counter(c['company_code'] for c in cases) == {code: 20 for code in harness.COHORT}
    for code in harness.COHORT:
        assert sum(c['company_code'] == code and c['group'] is None for c in cases) == 12


def test_suite_bytes_are_pinned_before_any_run(harness, tmp_path):
    path = tmp_path/'cases.json'
    path.write_bytes(harness.CASE_FILE.read_bytes()+b'\n')
    with pytest.raises(harness.FreezeMismatch):
        harness.load_cases(path)


def test_explicit_data_gap_mode_retains_failed_cases_instead_of_rewriting_no_data(harness):
    case=sample_case();ref,oracle=sample_data()
    ref['facts']={}
    original=harness.build_oracle([case],ref,record_data_gaps=True)
    assert original['data_gaps'][0]['reason']=='metric_not_extracted'
    assert original['data_gaps'][0]['case_id']==case['id']
    scored=harness.score_case(case,sample_run(case),ref,original,model_version='private-model-v1')
    assert not scored['passed'] and scored['status']=='oracle_missing'
    assert case['expect']['kind']=='financial'


def test_single_questions_cover_all_120_company_reports(harness):
    cases = harness.load_cases()
    covered = set()
    for case in cases:
        if case['group'] is None:
            for spec in case['expect']['goal_expectations']:
                for code, year, period, metric, scope in harness.spec_identities(spec):
                    covered.add((code, year, period))
    assert covered == {(code, year, period) for code in harness.COHORT
                       for year in (2023, 2024, 2025) for period in ('FY', 'HY', 'Q1', 'Q3')}


def test_semantic_goal_matching_ignores_ids_and_order(harness):
    a = sample_case()['expect']['goal_expectations'][0]
    b = dict(a, codes=['002594'], years=[2023])
    def observed(spec, identifier):
        return dict(id=identifier, kind='lookup', context_conditions=dict(
            codes=spec['codes'], metrics=spec['metrics'], scope=spec['scope'],
            time=dict(years=spec['years'], periods=spec['periods']),
            presentation=dict(unit='万元', decimals=2)))
    matched, errors = harness.match_goals([a, b], dict(goals=[observed(b, 'z'), observed(a, 'x')]))
    assert not errors
    assert [row['id'] for row in matched] == ['x', 'z']


def test_valid_fact_is_scored_independently_of_server_pass(harness):
    case = sample_case(); ref, oracle = sample_data(); run = sample_run(case)
    run['task']['result']['verification_v3'] = dict(passed=False)
    assert harness.score_case(case, run, ref, oracle, model_version='private-model-v1')['passed']


@pytest.mark.parametrize('field,bad', [('stock_code', '002594'), ('year', 2025), ('value_exact', '1'),
                                      ('unit', '亿元'), ('scope', 'parent')])
def test_wrong_company_year_amount_unit_or_scope_fails(harness, field, bad):
    case = sample_case(); ref, oracle = sample_data(); run = sample_run(case)
    run['task']['result']['facts'][0][field] = bad
    run['task']['result']['verification_v3'] = dict(passed=True)
    assert not harness.score_case(case, run, ref, oracle, model_version='private-model-v1')['passed']


def test_wrong_source_page_fails_despite_correct_number(harness):
    case = sample_case(); ref, oracle = sample_data(); run = sample_run(case)
    run['task']['result']['facts'][0]['source']['page'] = 9
    verdict = harness.score_case(case, run, ref, oracle, model_version='private-model-v1')
    assert not verdict['passed']
    assert 'wrong_source' in verdict['errors']


def test_failed_server_outcome_and_false_display_identity_do_not_pass(harness):
    case = sample_case(); ref, oracle = sample_data(); raw = sample_run(case)
    raw['task']['result']['outcome']['status'] = 'failed'
    assert not harness.score_case(case, raw, ref, oracle, model_version='private-model-v1')['passed']
    raw = sample_run(case)
    raw['task']['result']['answer']['content'] = '比亚迪2025年全年营业收入为10,000.00万元。'
    assert not harness.score_case(case, raw, ref, oracle, model_version='private-model-v1')['passed']


def test_yuan_is_not_a_substring_match_for_hundred_million_yuan(harness):
    case = sample_case(); case['expect']['goal_expectations'][0]['unit'] = '元'
    ref, oracle = sample_data(); raw = sample_run(case)
    raw['task']['result']['request_contract']['conditions']['presentation']['unit'] = '元'
    raw['task']['result']['answer']['content'] = '美的集团2024年全年营业收入为100000000.00亿元。'
    assert not harness.score_case(case, raw, ref, oracle, model_version='private-model-v1')['passed']


def test_native_chart_series_exact_values_are_checked(harness):
    case = sample_case(); spec = case['expect']['goal_expectations'][0]
    spec.update(kind='chart', chart='bar'); case['expect']['no_chart'] = False
    ref, oracle = sample_data(); raw = sample_run(case); result = raw['task']['result']
    result['request_contract']['goals'][0]['kind'] = 'chart'
    result['request_contract']['conditions']['presentation']['chart_type'] = 'bar'
    result['task_results'][0]['kind'] = 'chart'
    result['chart_data_list'] = [dict(chart_type='bar', unit='万元', decimals=2, metric='operating_revenue',
        x=['2024 全年'], source_fact_ids=['f1'], series=[dict(name='美的集团', fact_ids=['f1'],
        values=[10000.0], values_exact=['10000.00'], values_formatted=['10000.00 万元'])])]
    assert harness.score_case(case, raw, ref, oracle, model_version='private-model-v1')['passed']
    result['chart_data_list'][0]['series'][0]['values_exact'][0] = '1'
    assert not harness.score_case(case, raw, ref, oracle, model_version='private-model-v1')['passed']


def test_native_derived_value_schema_and_abs_negative_base(harness):
    case = sample_case(); spec = case['expect']['goal_expectations'][0]
    spec.update(kind='compare', years=[2023, 2024], calculation='yoy', axis='years')
    ref, oracle = sample_data()
    first = copy.deepcopy(ref['facts']['f1']); first.update(id='base', year=2023, value='-100000000')
    ref['facts']['base'] = first
    oracle['cards']['base'] = dict(oracle['cards']['f1'], value_exact='-100000000')
    raw = sample_run(case); result = raw['task']['result']
    result['request_contract']['goals'][0]['kind'] = 'compare'
    result['task_results'][0]['kind'] = 'compare'
    result['request_contract']['conditions'].update(calculation='yoy', comparison_axis='years')
    result['request_contract']['conditions']['time']['years'] = [2023, 2024]
    result['facts'].append(dict(first, value_exact='-100000000'))
    result['answer']['content'] = '美的集团2023年全年-10000.00万元；2024年全年10000.00万元；同比增长200.00%。'
    result['derived_facts'] = [dict(inputs=['base', 'f1'], value='200', unit='%',
                                   formula='(current-base)/abs(base)*100', status='verified')]
    assert harness.score_case(case, raw, ref, oracle, model_version='private-model-v1')['passed']
    result['derived_facts'][0]['value'] = '-200'
    assert not harness.score_case(case, raw, ref, oracle, model_version='private-model-v1')['passed']


@pytest.mark.parametrize('change', ['empty', 'extra', 'forbidden_chart', 'forbidden_query', 'wrong_visible_unit'])
def test_invalid_output_and_negative_constraints_fail(harness, change):
    case = sample_case(); ref, oracle = sample_data(); run = sample_run(case)
    result = run['task']['result']
    if change == 'empty':
        result['facts'] = []
    elif change == 'extra':
        result['facts'].append(dict(result['facts'][0], id='unrequested-fact', year=2023))
    elif change == 'forbidden_chart':
        result['chart_data_list'] = [dict(id='chart')]
    elif change == 'forbidden_query':
        case['expect']['no_query'] = True
    else:
        result['answer']['content'] = '美的集团2024年营业收入为10,000.00亿元。'
    assert not harness.score_case(case, run, ref, oracle, model_version='private-model-v1')['passed']


def test_cause_and_concept_prose_stay_pending_review(harness):
    case = sample_case(); ref, oracle = sample_data(); run = sample_run(case)
    case['expect']['manual_review'] = ['cause_claims_against_original']
    score = harness.score_case(case, run, ref, oracle, model_version='private-model-v1')
    assert score['automated_pass']
    assert not score['passed']
    assert score['review_status'] == 'pending_review'


def test_formatter_keeps_real_zero_and_parenthesized_negative(harness):
    assert harness.format_number('0', '万元', 2) == '0.00'
    assert harness.format_number('100000000', '万元', 2) == '10000.00'
    assert harness.parse_decimal('(1,234.50)') == -harness.Decimal('1234.50')


def test_missing_oracle_is_not_relabelled_as_expected_no_data(harness):
    case = sample_case(); ref, oracle = sample_data(); oracle['cards'] = {}
    verdict = harness.score_case(case, sample_run(case), ref, oracle, model_version='private-model-v1')
    assert verdict['status'] == 'oracle_missing'
    assert not verdict['passed']
    assert case['expect']['kind'] == 'financial'


def test_sse_parser_preserves_real_multiline_json_and_terminal(harness):
    lines = [': keepalive', 'event: session', 'data: {"task_id":"t",',
             'data: "session_uid":"s"}', '', 'event: answer',
             'data: {"content":"零为0，中文"}', '', 'event: done', 'data: {"status":"completed"}', '']
    events = list(harness.parse_sse(lines))
    assert events[0] == dict(type='session', data=dict(task_id='t', session_uid='s'))
    assert events[1]['data']['content'] == '零为0，中文'
    assert events[-1]['type'] == 'done'


def test_sse_parser_rejects_malformed_data(harness):
    with pytest.raises(ValueError):
        list(harness.parse_sse(['event: answer', 'data: <script>', '']))


def test_first_answer_and_append_only_record_cannot_be_replaced(harness, tmp_path):
    item = sample_case()
    ledger = harness.RunLedger(tmp_path/'run', dict(model='v1'), [item])
    raw = sample_run(item)
    ledger.save_raw(item, raw)
    original = (ledger.folder/'answers'/f'{item["id"]}.json').read_bytes()
    records = (ledger.folder/'records.jsonl').read_bytes()
    with pytest.raises(FileExistsError):
        ledger.save_raw(item, dict(raw, seconds=99))
    assert (ledger.folder/'answers'/f'{item["id"]}.json').read_bytes() == original
    assert (ledger.folder/'records.jsonl').read_bytes() == records


def test_resume_rejects_changed_model_data_source_or_case(harness, tmp_path):
    item = sample_case()
    freeze = dict(source='one', data='one', model='v1')
    harness.RunLedger(tmp_path/'run', freeze, [item])
    for field in freeze:
        with pytest.raises(harness.FreezeMismatch):
            harness.RunLedger(tmp_path/'run', dict(freeze, **{field: 'changed'}), [item], resume=True)
    with pytest.raises(harness.FreezeMismatch):
        harness.RunLedger(tmp_path/'run', freeze, [dict(item, question='changed')], resume=True)


def test_html_escapes_answer_question_and_raw_schema(harness, tmp_path):
    item = sample_case(); item['question'] = '<img src=x onerror=alert(1)>'
    ledger = harness.RunLedger(tmp_path/'run', dict(version='v1'), [item])
    raw = sample_run(item); raw['task']['result']['answer']['content'] = '<script>alert(1)</script>&'
    ledger.save_raw(item, raw)
    ledger.export()
    content = (ledger.folder/'answers.html').read_text(encoding='utf-8')
    assert '<script>' not in content and '<img src=x' not in content
    assert '&lt;script&gt;' in content and '&lt;img' in content
    assert (ledger.folder/'text'/f'{item["id"]}.txt').is_file()
    assert (ledger.folder/'alltrace'/f'{item["id"]}.json').is_file()


def test_resume_uses_actual_saved_state_and_same_pending_request(harness, tmp_path):
    cases = [sample_case('G1T1', 'G1', 1), sample_case('G1T2', 'G1', 2), sample_case('S1')]
    ref, oracle = sample_data()
    ledger = harness.RunLedger(tmp_path/'run', dict(version='v1'), cases)
    class LocalJava:
        def __init__(self, fail=False):
            self.calls = []; self.fail = fail; self.messages = {}
        def call(self, method, path, **kwargs):
            if method == 'POST' and path == '/api/chat/sessions':
                uid = 'created-'+str(len(self.messages)); self.messages[uid] = []
                return dict(sessionUid=uid)
            if '/sessions/' in path:
                return dict(messages=self.messages[path.rsplit('/', 1)[-1]])
            raise AssertionError(path)
        def turn(self, question, uid=None, client_id=None, **kwargs):
            self.calls.append(dict(question=question, uid=uid, client_id=client_id))
            if self.fail and len(self.calls) == 2:
                raise ConnectionError('connection dropped after admission')
            raw = sample_run(dict(question=question), session=uid, request_id=client_id)
            result = raw['task']['result']
            self.messages[uid].extend([dict(role='user', content=question),
                dict(role='assistant', content=result['answer']['content'], metadata=result)])
            return raw
    first = LocalJava(fail=True)
    with pytest.raises(harness.PendingSubmission):
        harness.run_cases(cases, ledger, first, ref, oracle, model_version='private-model-v1')
    raw_bytes = (ledger.folder/'answers'/'G1T1.json').read_bytes()
    request = first.calls[1]['client_id']
    resumed = harness.RunLedger(ledger.folder, dict(version='v1'), cases, resume=True)
    next_client = LocalJava(); next_client.messages = first.messages
    harness.run_cases(cases, resumed, next_client, ref, oracle, model_version='private-model-v1')
    assert len(next_client.calls) == 2
    assert next_client.calls[0]['uid'] == first.calls[0]['uid']
    assert next_client.calls[0]['client_id'] == request
    assert next_client.calls[1]['uid'] != next_client.calls[0]['uid']
    assert (ledger.folder/'answers'/'G1T1.json').read_bytes() == raw_bytes
    assert len((ledger.folder/'records.jsonl').read_text(encoding='utf-8').splitlines()) == 3


def test_summary_has_no_threshold_and_pending_does_not_pass(harness, tmp_path):
    item = sample_case(); ledger = harness.RunLedger(tmp_path/'run', dict(v=1), [item])
    ledger.save_raw(item, sample_run(item))
    ledger.save_score(item, dict(automated_pass=True, passed=False, review_status='pending_review',
                                status='pending_review', errors=[]))
    summary = ledger.export()
    assert summary['passed'] == 0 and summary['pending_review'] == 1
    assert summary['no_accuracy_threshold'] is True
    assert 'acceptance_passed' not in summary


def test_original_pdf_oracle_revalidates_value_and_blocks_missing_fact(harness, tmp_path):
    from reportlab.pdfgen import canvas
    path = tmp_path/'data_root'/'original.pdf'; path.parent.mkdir()
    writer = canvas.Canvas(str(path)); writer.drawString(40, 760, 'Revenue 100,000,000.00 Unit: CNY')
    writer.save()
    ref, _ = sample_data(); fact = ref['facts']['f1']
    fact['source'].update(source_path='data_root/original.pdf', source_sha256=harness.digest(path),
                          document_version=harness.digest(path), page=1, row='Revenue')
    ref['reports'] = [dict(stock_code=fact['stock_code'], company=fact['company'], year=fact['year'],
        period=fact['period'], source_path=fact['source']['source_path'], document_version=harness.digest(path))]
    oracle = harness.build_oracle([sample_case()], ref, root=tmp_path)
    assert oracle['cards']['f1']['value_exact'] == '100000000.00'
    bad = copy.deepcopy(ref); bad['facts']['f1']['value'] = '1'
    with pytest.raises(harness.OracleMissing):
        harness.build_oracle([sample_case()], bad, root=tmp_path)
    with pytest.raises(harness.OracleMissing) as missing:
        harness.build_oracle([sample_case()], dict(ref, facts={}), root=tmp_path)
    assert missing.value.gaps[0]['reason'] == 'metric_not_extracted'


def test_literal_quote_checks_actual_page_and_text(harness):
    case = sample_case(); spec = case['expect']['goal_expectations'][0]
    spec['kind'] = 'quote'; spec['quote_mode'] = 'literal'
    ref, oracle = sample_data(); run = sample_run(case); result = run['task']['result']
    result['request_contract']['goals'][0]['kind'] = 'quote'
    result['task_results'][0]['kind'] = 'quote'
    result['evidence'] = [dict(fact_id='f1', document_version='a'*64, page_start=3,
                               value_literal='100,000,000.00', text='营业收入 100,000,000.00')]
    result['answer']['content'] += '原件PDF第3页：“营业收入 100,000,000.00”。原表单位：元。'
    assert harness.score_case(case, run, ref, oracle, model_version='private-model-v1')['passed']
    result['evidence'][0]['value_literal'] = 'invented quote'
    assert not harness.score_case(case, run, ref, oracle, model_version='private-model-v1')['passed']
