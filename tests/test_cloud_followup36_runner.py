"""新问法与原件预期先冻结，用户运行后保留首发回答与状态检查。"""
from copy import deepcopy
from pathlib import Path

import pytest

from eval import cloud_focused as runner

SUITE = Path(__file__).resolve().parents[1] / 'eval/cloud_followup36_cases.json'


def test_followup_profile_is_fixed_and_does_not_relabel_the_previous_questions():
    cases = runner.load_suite(SUITE, profile='followup36')
    assert len(cases) == 36 and sum(not c['group'] for c in cases) == 12
    groups = {c['group'] for c in cases if c['group']}
    assert len(groups) == 6
    assert all([c['turn'] for c in cases if c['group'] == g] == [1, 2, 3, 4] for g in groups)
    assert len({c['question'] for c in cases}) == 36
    assert all(not c['seen_before'] for c in cases)


def test_a_new_question_or_expectation_cannot_be_substituted_after_freezing(tmp_path):
    changed = runner.read(SUITE)
    changed['cases'][0]['expect']['goal_expectations'][0]['years'] = [2024]
    path = tmp_path / 'changed.json'
    path.write_text(__import__('json').dumps(changed), encoding='utf-8')
    with pytest.raises(runner.FreezeMismatch):
        runner.load_suite(path, profile='followup36')
    with pytest.raises(runner.FreezeMismatch):
        runner.load_suite(SUITE)


def test_saved_state_checks_compare_the_persistent_order_and_session_preferences():
    item = {'expected_state': {'codes': ['600085'], 'years': [2023], 'unit': '万元', 'decimals': 2, 'format': 'table'},
            'expect': {'session_preferences': {'presentation.unit': '万元'}}}
    result = {'dialogue_state': {'conditions': {'codes': ['600085'], 'time': {'years': [2023]},
        'presentation': {'unit': '万元', 'decimals': 2, 'format': 'table'}},
        'session_preferences': [{'field': 'presentation.unit', 'value': '万元', 'lifetime': 'session'}]}}
    run = {'task': {'result': result}}
    assert runner.saved_condition_errors(item, run) == []
    bad = deepcopy(run)
    bad['task']['result']['dialogue_state']['conditions']['presentation']['unit'] = '亿元'
    assert 'saved_condition_unit' in runner.saved_condition_errors(item, bad)
    bad = deepcopy(run)
    bad['task']['result']['dialogue_state']['session_preferences'][0]['lifetime'] = 'turn'
    assert 'saved_session_preference_presentation.unit' in runner.saved_condition_errors(item, bad)


def test_saved_state_checks_catch_the_right_answer_with_the_wrong_next_turn_year():
    item = {'expected_state': {'years': [2022]}, 'expect': {}}
    run = {'task': {'result': {'dialogue_state': {'conditions': {'time': {'years': [2023]}}}}}}
    assert runner.saved_condition_errors(item, run) == ['saved_condition_years']
