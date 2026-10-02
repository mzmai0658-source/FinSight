from unittest.mock import Mock
import pytest
from src.etl.etl_worker import ETLWorker


def cashflow_chunk(extra=''):
    return {'table_type': 'primary_cashflow', 'table_family': 'cashflow', 'is_combined': True,
            'title_context': ['合并现金流量表'], 'unit_multiplier': 1,
            'text': '''合并现金流量表<table><tr><td>项目</td><td>本期金额</td></tr>
            <tr><td>经营活动产生的现金流量净额</td><td>10</td></tr>
            <tr><td>投资活动产生的现金流量净额</td><td>0</td></tr>
            <tr><td>筹资活动产生的现金流量净额</td><td>-2</td></tr>
            <tr><td>现金及现金等价物净增加额</td><td>8</td></tr>''' + extra + '</table>'}


@pytest.mark.parametrize('label', ['支付其他与经营活动有关的现金', '收到的其他与经营活动有关的现金'])
def test_main_cashflow_is_not_an_other_cash_note(label):
    worker = ETLWorker(Mock())
    chunk = cashflow_chunk(f'<tr><td>{label}</td><td>1</td></tr>')
    assert worker._should_accept_chunk(chunk)
    values, mode = worker._extract_chunk_data(chunk)
    assert mode == 'rules'
    assert values['investing_cf_net_amount'] == 0
    assert values['financing_cf_net_amount'] == -2
    assert not worker._should_accept_chunk({**chunk, 'is_combined': False})


def test_recovered_zero_and_other_fields_clear_stale_missing_flags():
    worker = ETLWorker(Mock())
    values = worker._retry_incomplete_required_fields(
        {'operating_cf_net_amount': 10, '_missing_required_fields': ['investing_cf_net_amount', 'financing_cf_net_amount', 'net_cash_flow'],
         '_completeness_flags': ['cashflow_incomplete(3/4)', 'cashflow_critical_missing', 'manual_review_needed']},
        [(0, cashflow_chunk())])
    assert values['investing_cf_net_amount'] == 0
    assert not values.get('_missing_required_fields')
    assert values['_completeness_flags'] == ['manual_review_needed']
    assert set(values['_retry_recovered_fields']) >= {'investing_cf_net_amount', 'financing_cf_net_amount', 'net_cash_flow'}


@pytest.mark.parametrize('table_type', ['supporting_financial', 'primary_cashflow'])
def test_other_cash_note_heading_inside_html_is_still_excluded(table_type):
    worker = ETLWorker(Mock())
    chunk = {'table_type': table_type, 'is_combined': True,
             'text': '<table><tr><td>支付其他与经营活动有关的现金</td></tr><tr><td>销售费用</td><td>12</td></tr></table>'}
    assert not worker._should_accept_chunk(chunk)


def test_already_complete_values_clear_old_warning_without_retry(monkeypatch):
    worker = ETLWorker(Mock())
    retry = Mock(side_effect=AssertionError('No extraction needed'))
    monkeypatch.setattr(worker, '_extract_for_family_retry', retry)
    values = worker._retry_incomplete_required_fields(
        {'operating_cf_net_amount': 10, 'investing_cf_net_amount': 0, 'financing_cf_net_amount': -2, 'net_cash_flow': 8,
         '_missing_required_fields': ['investing_cf_net_amount'], '_completeness_flags': ['cashflow_critical_missing']}, [(0, cashflow_chunk())])
    assert not values.get('_missing_required_fields')
    assert not values.get('_completeness_flags')
    retry.assert_not_called()


def test_unrecovered_fields_still_warn(monkeypatch):
    worker = ETLWorker(Mock())
    monkeypatch.setattr(worker, '_extract_for_family_retry', lambda *args: {'investing_cf_net_amount': 0})
    values = worker._retry_incomplete_required_fields({'operating_cf_net_amount': 10}, [(0, cashflow_chunk())])
    assert values['_missing_required_fields'] == ['financing_cf_net_amount', 'net_cash_flow']
    assert 'cashflow_incomplete(2/4)' in values['_completeness_flags']
    assert 'cashflow_critical_missing' in values['_completeness_flags']


def test_conflicting_consolidated_cashflows_do_not_select_last_value():
    chunks = [cashflow_chunk(), cashflow_chunk()]
    worker = ETLWorker(Mock())
    conflict = worker._cashflow_source_conflicts(
        [(0, {'operating_cf_net_amount': 100}, True), (1, {'operating_cf_net_amount': 20}, True)], chunks)
    assert conflict == [{'field': 'operating_cf_net_amount', 'first_chunk': 0, 'conflicting_chunk': 1,
                         'first_value': 100, 'conflicting_value': 20}]
    assert not worker._cashflow_source_conflicts(
        [(0, {'operating_cf_net_amount': 100}, True), (1, {'operating_cf_net_amount': 100}, True)], chunks)


def test_conflicting_statement_blocks_publication(monkeypatch):
    worker = ETLWorker(Mock())
    chunks = [cashflow_chunk(), cashflow_chunk()]
    monkeypatch.setattr(worker, '_load_source', lambda path: ('report', chunks))
    monkeypatch.setattr(worker, '_extract_metadata', lambda *args: {'stock_code': '600080', 'report_year': 2023, 'report_period': 'FY'})
    monkeypatch.setattr(worker, '_extract_chunk_data', Mock(side_effect=[({'operating_cf_net_amount': 100}, 'rules'), ({'operating_cf_net_amount': 20}, 'rules')]))
    publish = Mock()
    monkeypatch.setattr(worker, '_post_process', publish)
    result = worker.run('report.json', save_to_db=True)
    assert result['status'] == 'error'
    assert result['cashflow_source_conflicts']
    publish.assert_not_called()


def test_cash_increase_yuan_storage_keeps_source_units_without_mutating_input():
    worker = ETLWorker(Mock())
    source = {'normalized_value': 2500.25, 'unit_multiplier': 0.0001, 'raw_value': 25002500, 'page_start': 42}
    result = worker._derive_metrics({'net_cash_flow': 2500.25, '_field_sources': {'net_cash_flow': source}})
    assert result['net_cash_flow'] == 25002500
    converted = result['_field_sources']['net_cash_flow']
    assert converted['normalized_value'] == result['net_cash_flow']
    assert converted['unit_multiplier'] == 1
    assert converted['raw_value'] == 25002500 and converted['page_start'] == 42
    assert source['normalized_value'] == 2500.25 and source['unit_multiplier'] == 0.0001


def test_cash_increase_unit_change_does_not_hide_mismatching_source():
    worker = ETLWorker(Mock())
    result = worker._derive_metrics({'net_cash_flow': 2500.25, '_field_sources': {
        'net_cash_flow': {'normalized_value': 3000, 'unit_multiplier': 0.0001}}})
    assert result['_field_sources']['net_cash_flow']['normalized_value'] == 30000000
    assert result['_field_sources']['net_cash_flow']['normalized_value'] != result['net_cash_flow']


def test_cash_increase_without_source_does_not_gain_fabricated_provenance():
    result = ETLWorker(Mock())._derive_metrics({'net_cash_flow': 2500.25})
    assert not result.get('_field_sources', {}).get('net_cash_flow')
