import json
from pathlib import Path
from copy import deepcopy
from eval.score_v3_regressions import fixture,score,check_chart

ROOT=Path(__file__).resolve().parents[1]

def evidence():
    folder=ROOT/'data/runtime/v3/regression/dev-11'
    if not folder.exists():
        import pytest
        pytest.skip('Local native regression artifact is not included in a source-only checkout')
    rows=[json.loads(line) for line in (folder/'records.jsonl').read_text(encoding='utf-8').splitlines()]
    definitions=json.loads((ROOT/'eval/regression_v3/expectations.json').read_text(encoding='utf-8'))['expectations']
    gold=fixture(ROOT/'data/runtime/v3/audit-ordered')
    return rows,definitions,{gold['version']:gold}

def test_independent_assertions_reject_an_accurate_value_from_the_wrong_year():
    rows,definitions,references=evidence()
    row=deepcopy(rows[1]);conditions=row['run']['task']['result']['request_contract']['conditions']
    conditions['time']['years']=[2023]
    result=score(row,definitions[row['id']],references)
    assert not result['automated_pass']
    assert 'Requested years not retained' in result['zero_tolerance_violations']

def test_native_self_reported_success_does_not_hide_the_missing_original_location():
    rows,definitions,references=evidence();row=rows[3]
    assert row['run']['task']['result']['outcome']['status']=='answered'
    result=score(row,definitions[row['id']],references)
    assert not result['automated_pass'] and 'Requested original location/quotation missing' in result['zero_tolerance_violations']

def test_negative_chart_values_cannot_be_replaced_with_zero():
    checks=[]
    check_chart({'unit':'元','series':[{'fact_ids':['loss'],'values_exact':['0']}]},
        {'facts':{'loss':{'value':'-10'}}},lambda value,message,zero=False:checks.append((value,message,zero)))
    assert (False,'Wrong chart value, sign or unit',True) in checks
