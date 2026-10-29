"""Offline calibration preserves immutable raw answers, expectations and scores."""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
import socket

import pytest

from eval import cloud_upload200 as harness
from tests.test_cloud_upload200 import original_reference, sample_case, sample_run


def frozen_run(tmp_path):
    root = tmp_path/'candidate'; root.mkdir()
    reference = original_reference(harness, root)
    artifacts = root/'release'; artifacts.mkdir()
    for name, value in {'reports.json': reference['reports'], 'facts.json': list(reference['facts'].values()),
                         'manifest.json': dict(version=reference['version']), 'narratives.json': []}.items():
        harness.write_new(artifacts/name, value)
    reference = harness.load_release(artifacts, root=root, require_cohort=False)
    case = sample_case(); raw = sample_run(case)
    raw['task']['result']['facts'][0]['source'] = copy.deepcopy(reference['facts']['f1']['source'])
    oracle = harness.build_oracle([case], reference, root=root)
    run = tmp_path/'immutable'; run.mkdir()
    questions = dict(suite_sha256='fixture-suite', questions_sha256='fixture-questions', cases=[case])
    fingerprint = dict(artifacts=reference['artifacts'], originals=reference['documents'], data_version=reference['version'],
        model=dict(version='private-model-v1'), suite_sha256=questions['suite_sha256'], questions_sha256=questions['questions_sha256'])
    harness.write_new(run/'freeze.json', dict(fingerprint=fingerprint, cases_sha256=harness.value_digest([case])))
    harness.write_new(run/'questions.freeze.json', questions)
    harness.write_new(run/'oracle.json', oracle)
    harness.write_new(run/'answers'/f'{case["id"]}.json', dict(case=case, run=raw, sha256=harness.value_digest(dict(case=case, run=raw))))
    before = dict(status='failed', passed=False, automated_pass=False, review_status='not_required', errors=['old_structure_check'])
    harness.write_new(run/'scores'/f'{case["id"]}.json', dict(score=before, sha256=harness.value_digest(before), answer_sha256=harness.value_digest(raw)))
    history = dict(messages=[dict(role='user', content=case['question']), dict(role='assistant', content=raw['task']['result']['answer']['content'], metadata=raw['task']['result'])])
    harness.write_new(run/'history'/f'{case["id"]}.json', history)
    harness.write_new(run/'requests'/f'{case["id"]}.json', dict(case=case, before=dict(messages=[])))
    harness.write_new_text(run/'records.jsonl', json.dumps(dict(case=case, run=raw, answer_sha256=harness.value_digest(raw)))+'\n')
    harness.write_new(run/'summary.json', dict(formal_total=1, passed=0))
    return root, artifacts, run, case, before


def rescorer():
    assert importlib.util.find_spec('eval.rescore_cloud_upload200') is not None, 'offline rescore command is missing'
    return importlib.import_module('eval.rescore_cloud_upload200')


def manifest(folder):
    return {p.relative_to(folder).as_posix(): harness.digest(p) for p in folder.rglob('*') if p.is_file()}


def test_rescore_is_offline_and_preserves_before_after_and_input_bytes(tmp_path, monkeypatch):
    root, artifacts, run, case, before = frozen_run(tmp_path); initial = manifest(run)
    def no_network(*args, **kwargs):
        raise AssertionError('Offline rescore must not access the network')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(harness.RunLedger, '__init__', lambda *args, **kwargs: pytest.fail('Do not instantiate the mutating RunLedger'))
    output = tmp_path/'calibrated'
    summary = rescorer().rescore(run, artifacts, output, root=root)
    comparison = harness.read(output/'comparisons'/f'{case["id"]}.json')
    assert comparison['before'] == before
    assert comparison['after']['passed'], comparison['after']
    assert comparison['raw_answer_sha256'] == harness.read(run/'scores'/f'{case["id"]}.json')['answer_sha256']
    assert comparison['rationale']['removed_errors'] == ['old_structure_check']
    assert summary['no_model_call_made'] is True and summary['score_only_change'] is True
    assert summary['before']['passed'] == 0 and summary['after']['passed'] == 1
    assert summary['original_run_unchanged'] is True
    assert manifest(run) == initial
    assert not (output/'answers').exists()


@pytest.mark.parametrize('location', ['same', 'inside', 'ancestor'])
def test_rescore_rejects_output_that_can_overlap_original_results(tmp_path, location):
    root, artifacts, run, _, _ = frozen_run(tmp_path)
    output = dict(same=run, inside=run/'new', ancestor=run.parent)[location]
    with pytest.raises(ValueError, match='overlap'):
        rescorer().rescore(run, artifacts, output, root=root)


@pytest.mark.parametrize('change', ['answer', 'score', 'case', 'release', 'records'])
def test_rescore_rejects_changed_frozen_inputs_before_revalidation(tmp_path, monkeypatch, change):
    root, artifacts, run, case, _ = frozen_run(tmp_path)
    path = run/'answers'/f'{case["id"]}.json'
    if change == 'answer':
        row = harness.read(path); row['run']['seconds'] = 99
    elif change == 'score':
        path = run/'scores'/f'{case["id"]}.json'; row = harness.read(path); row['score']['passed'] = True
    elif change == 'case':
        path = run/'questions.freeze.json'; row = harness.read(path); row['cases'][0]['question'] = 'changed'
    elif change == 'release':
        path = artifacts/'facts.json'; row = harness.read(path); row[0]['value'] = '1'
    else:
        (run/'records.jsonl').write_text('', encoding='utf-8'); row = None
    if row is not None:
        path.write_text(json.dumps(row), encoding='utf-8')
    monkeypatch.setattr(harness, 'build_oracle', lambda *args, **kwargs: pytest.fail('Validate immutable inputs before rebuilding the oracle'))
    with pytest.raises(harness.FreezeMismatch):
        rescorer().rescore(run, artifacts, tmp_path/'calibrated', root=root)
