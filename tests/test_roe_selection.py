"""作品说明：普通 ROE 与扣非 ROE 在 SQL 执行边界保持区分。"""
import pytest

from src.agent.facts import metric_mentions
from src.agent.fallback import extract_slots
from src.agent.orchestrator import _sql_policy_error

TABLE = 'core_performance_indicators_sheet'
FILTER = " WHERE stock_code='600222' AND report_year=2022 AND report_period='FY'"


@pytest.mark.parametrize('metric,field', [
    ('ROE', 'roe'), ('roe', 'roe'), ('加权平均净资产收益率', 'roe'),
    ('扣非roe', 'roe_weighted_excl_non_recurring'),
    ('ROE（扣非）', 'roe_weighted_excl_non_recurring'),
    ('加权平均净资产收益率(扣非)', 'roe_weighted_excl_non_recurring'),
    ('扣除非经常性损益后的加权平均净资产收益率', 'roe_weighted_excl_non_recurring'),
])
def test_metric_identity_and_sql_policy_agree(metric, field):
    question = f'太龙药业2022年全年{metric}是多少？'
    assert {m[2] for m in metric_mentions(question)} == {field}
    assert extract_slots(question)['metric_field'] == field
    assert not _sql_policy_error(question, f'SELECT {field} FROM {TABLE}' + FILTER)
    other = 'roe' if field != 'roe' else 'roe_weighted_excl_non_recurring'
    assert _sql_policy_error(question, f'SELECT {other} FROM {TABLE}' + FILTER)


@pytest.mark.parametrize('sql', [
    f'SELECT roe_weighted_excl_non_recurring FROM {TABLE}' + FILTER,
    f'SELECT roe_weighted_excl_non_recurring AS roe FROM {TABLE}' + FILTER,
    f'SELECT roe_weighted_excl_non_recurring FROM {TABLE}' + FILTER + ' AND roe IS NOT NULL',
    f'SELECT roe_weighted_excl_non_recurring /* roe */ FROM {TABLE}' + FILTER,
])
def test_substrings_aliases_filters_and_comments_cannot_substitute_for_selected_metric(sql):
    assert '不能互相替代' in _sql_policy_error('太龙药业2022年全年加权平均净资产收益率是多少？', sql)


@pytest.mark.parametrize('projection', ['roe', 'c.roe', 'c.roe AS ordinary_roe'])
def test_actual_ordinary_column_is_accepted(projection):
    assert not _sql_policy_error('太龙药业2022年全年ROE是多少？', f'SELECT {projection} FROM {TABLE} c' + FILTER)


def test_two_metric_question_can_fetch_each_metric_separately():
    question = '太龙药业2022年全年普通ROE和扣非ROE分别是多少？'
    for field in ['roe', 'roe_weighted_excl_non_recurring']:
        assert not _sql_policy_error(question, f'SELECT {field} FROM {TABLE}' + FILTER)
