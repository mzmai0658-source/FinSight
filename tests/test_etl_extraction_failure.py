"""作品说明：提取失败不能发布残缺财务快照。"""
from unittest.mock import Mock

import pytest

from src.etl.etl_worker import ETLWorker


def test_metadata_uses_first_report_title_before_later_period_reference():
    worker = ETLWorker(Mock())
    content = (
        "证券代码：600436\n"
        "漳州片仔癀药业股份有限公司 2023 年第三季度报告\n"
        "本报告期基本每股收益与 2023 年半年度报告披露数值相关。"
    )

    meta = worker._extract_metadata(content, "600436_20231017_JSSV.pdf")

    assert meta["report_year"] == 2023
    assert meta["report_period"] == "Q3"


@pytest.mark.parametrize('response', [None, '', 'not json', '[]'])
def test_llm_failure_is_not_rule_success(monkeypatch, response):
    worker = ETLWorker(Mock(chat=Mock(return_value=response)))
    monkeypatch.setattr(worker, '_should_accept_chunk', lambda c: True)
    monkeypatch.setattr(worker, '_extract_with_rules', lambda c: {'net_profit': 123})
    monkeypatch.setattr(worker, '_needs_llm_supplement', lambda c, d: True)
    with pytest.raises(RuntimeError, match='LLM extraction'):
        worker._extract_chunk_data({'text': '<table><tr><td>营业收入</td><td>123</td></tr></table>', 'unit_multiplier': 1})


def test_disabled_optional_llm_keeps_rules_but_requires_review(monkeypatch):
    client = Mock()
    worker = ETLWorker(client, enable_optional_llm_supplement=False)
    monkeypatch.setattr(worker, '_should_accept_chunk', lambda c: True)
    monkeypatch.setattr(worker, '_extract_with_rules', lambda c: {
        'total_operating_revenue': 123,
        'net_profit': 10,
    })
    monkeypatch.setattr(worker, '_needs_llm_supplement', lambda c, d: True)

    data, mode = worker._extract_chunk_data({
        'table_type': 'primary_income',
        'text': '<table><tr><td>营业收入</td><td>123</td></tr></table>',
        'unit_multiplier': 1,
    })

    assert mode == 'rules_supplement_skipped'
    assert data['total_operating_revenue'] == 123
    assert data['_optional_llm_missing_fields'] == ['operating_profit', 'total_profit']
    client.chat.assert_not_called()

    reviewed = worker._attach_pre_save_review(data)
    assert reviewed['_pre_save_review_status'] == 'warn'
    assert reviewed['_requires_manual_review'] == 1
    assert reviewed['_pre_save_review_warnings'] == [
        'optional_llm_supplement_disabled(operating_profit,total_profit)'
    ]


def test_later_rule_result_clears_skipped_supplement_warning():
    worker = ETLWorker(Mock(), enable_optional_llm_supplement=False)
    reviewed = worker._attach_pre_save_review({
        'total_operating_revenue': 123,
        'operating_profit': 12,
        'total_profit': 11,
        '_optional_llm_missing_fields': ['operating_profit', 'total_profit'],
        '_optional_llm_supplement_status': 'disabled',
    })

    assert reviewed['_pre_save_review_status'] == 'pass'
    assert '_optional_llm_missing_fields' not in reviewed
    assert '_optional_llm_supplement_status' not in reviewed


def test_skipped_supplement_missing_fields_merge_and_drop_recovered_values():
    worker = ETLWorker(Mock(), enable_optional_llm_supplement=False)
    merged = worker._merge_last_wins(
        {
            'total_operating_revenue': 123,
            '_optional_llm_missing_fields': ['operating_profit'],
            '_optional_llm_supplement_status': 'disabled',
        },
        {
            'operating_profit': 12,
            '_optional_llm_missing_fields': ['total_profit'],
            '_optional_llm_supplement_status': 'disabled',
        },
    )

    reviewed = worker._attach_pre_save_review(merged)
    assert reviewed['_optional_llm_missing_fields'] == ['total_profit']
    assert reviewed['_pre_save_review_status'] == 'warn'
    assert reviewed['_pre_save_review_warnings'] == [
        'optional_llm_supplement_disabled(total_profit)'
    ]


def test_rules_only_skips_rule_empty_candidate_and_marks_review(monkeypatch):
    client = Mock()
    worker = ETLWorker(client, enable_llm_extraction=False)
    monkeypatch.setattr(worker, '_should_accept_chunk', lambda c: True)
    monkeypatch.setattr(worker, '_extract_with_rules', lambda c: {})

    data, mode = worker._extract_chunk_data({
        'table_type': 'core_metrics',
        'text': '<table><tr><td>非经常性损益项目</td><td>123</td></tr></table>',
        'unit_multiplier': 1,
    })

    assert mode == 'rules_only_skipped'
    assert data['_llm_extraction_skipped_table_types'] == ['core_metrics']
    client.chat.assert_not_called()

    reviewed = worker._attach_pre_save_review({**data, 'total_operating_revenue': 123})
    assert reviewed['_pre_save_review_status'] == 'warn'
    assert reviewed['_requires_manual_review'] == 1
    assert reviewed['_pre_save_review_warnings'] == [
        'llm_extraction_disabled_skipped(core_metrics)'
    ]


def test_rules_only_empty_primary_table_surfaces_completeness_failure(monkeypatch):
    client = Mock()
    worker = ETLWorker(client, enable_llm_extraction=False)
    chunk = {
        'table_type': 'primary_income',
        'table_family': 'income',
        'is_combined': True,
        'unit_multiplier': 1,
        'text': '<table><tr><td>合并利润表</td></tr><tr><td>营业收入</td><td>无法识别</td></tr></table>',
    }
    monkeypatch.setattr(worker, '_load_source', lambda path: ('report', [chunk]))
    monkeypatch.setattr(worker, '_extract_metadata', lambda *args: {
        'stock_code': '600080', 'stock_abbr': '金花股份',
        'report_year': 2024, 'report_period': 'Q3',
    })
    monkeypatch.setattr(worker, '_normalize_chunk_for_processing', lambda value: value)
    monkeypatch.setattr(worker, '_is_relevant_chunk', lambda value: True)
    monkeypatch.setattr(worker, '_should_accept_chunk', lambda value: True)
    monkeypatch.setattr(worker, '_extract_with_rules', lambda value: {})
    monkeypatch.setattr(worker, '_extract_for_family_retry', lambda *args: {})

    result = worker.run('report.json', save_to_db=False)

    assert result['status'] == 'error'
    assert result['data']['_missing_required_fields'] == [
        'net_profit', 'total_operating_revenue'
    ]
    assert 'income_incomplete(2/2)' in result['data']['_pre_save_review_warnings']
    assert 'income_critical_missing' in result['data']['_pre_save_review_warnings']
    assert 'llm_extraction_disabled_skipped(primary_income)' in result['data']['_pre_save_review_warnings']
    client.chat.assert_not_called()


def test_valid_empty_object_is_not_a_transport_failure(monkeypatch):
    worker = ETLWorker(Mock(chat=Mock(return_value='{}')))
    monkeypatch.setattr(worker, '_should_accept_chunk', lambda c: True)
    monkeypatch.setattr(worker, '_extract_with_rules', lambda c: {})
    assert worker._extract_chunk_data({'text': '<table><tr><td>其他项目</td><td>123</td></tr></table>', 'unit_multiplier': 1}) == ({}, 'llm')


def test_short_quarter_headers_exclude_annual_total():
    worker = ETLWorker(Mock())
    data = worker._extract_quarter_summary_table('''<table>
    <tr><td>项目</td><td>一季度</td><td>二季度</td><td>三季度</td><td>四季度</td><td>合并</td></tr>
    <tr><td>一、营业收入</td><td>11,919.98</td><td>13,951.95</td><td>16,443.10</td><td>15,622.41</td><td>57,937.45</td></tr>
    </table>''')
    assert data['operating_revenue_qoq_growth'] == pytest.approx((15622.41-16443.10)/16443.10*100, abs=0.0001)
    assert 'total_operating_revenue' not in data


@pytest.mark.parametrize('heading', ['支付其他与经营活动有关的现金', '支付的其他与经营活动有关的现金', '收到的其他与经营活动有关的现金'])
def test_cash_payment_details_are_not_income_expenses(heading):
    worker = ETLWorker(Mock())
    assert not worker._should_accept_chunk({'table_type': 'supporting_financial',
        'text': heading + ' <table><tr><td>销售费用</td><td>280717597.46</td></tr></table>'})


def test_empty_ocr_table_does_not_invoke_model():
    client = Mock()
    worker = ETLWorker(client)
    assert worker._extract_chunk_data({'table_type': 'supporting_financial', 'unit_multiplier': 1,
        'text': '现金流量表补充资料 <table><tr><td>母公司</td><td></td></tr></table>'})[0] == {}
    client.chat.assert_not_called()


@pytest.mark.parametrize('note', ['受限原因', '期末外币余额', '合并日被合并方', '应收、应付关联方'])
def test_subset_notes_cannot_supply_whole_company_assets(note):
    worker = ETLWorker(Mock())
    assert not worker._should_accept_chunk({'table_type': 'supporting_financial',
        'text': note + '<table><tr><td>货币资金</td><td>10000</td></tr></table>'})


def test_primary_eps_keeps_disclosed_precision():
    worker = ETLWorker(Mock())
    common = {'table_type': 'core_metrics', 'unit_multiplier': 1, 'is_combined': None}
    main = worker._annotate_field_sources({'eps': 0.0896}, {**common, 'text': '主要财务指标 基本每股收益', 'page_index': 6}, 0, 'rules')
    note = worker._annotate_field_sources({'eps': 0.09}, {**common, 'text': '净资产收益率及每股收益', 'page_index': 170}, 1, 'rules')
    merged = worker._merge_last_wins(main, note)
    assert merged['eps'] == 0.0896
    assert merged['_field_sources']['eps']['page_start'] == 6


def test_core_asset_continuation_with_one_row():
    worker = ETLWorker(Mock())
    value = worker._extract_core_metrics_table('''<table><tr><td>主要会计数据</td><td>2022年</td><td>2021年</td></tr>
    <tr><td>总资产</td><td>27,044,491,889.50</td><td>25,072,835,362.31</td></tr></table>''')
    assert value['asset_total_assets'] == 27044491889.50


def test_multitable_statement_preserves_later_field_semantics():
    worker = ETLWorker(Mock())
    data = worker._extract_balance_table('''
    <table>
      <tr><td>项目</td><td>2022年12月31日</td><td>2022年1月1日</td></tr>
      <tr><td>资产总计</td><td>1,200</td><td>1,000</td></tr>
    </table>
    <table>
      <tr><td>项目</td><td>2022年12月31日</td><td>2022年1月1日</td></tr>
      <tr><td>预收账款</td><td></td><td></td></tr>
      <tr><td>负债合计</td><td>600</td><td>500</td></tr>
    </table>
    ''')

    assert data['asset_total_assets_yoy_growth'] == 20.0
    assert data['liability_total_liabilities_yoy_growth'] == 20.0
    assert data['liability_advance_from_customers'] == 0.0
    semantics = data['_field_semantics']
    assert semantics['asset_total_assets_yoy_growth']['value_semantics'] == 'derived'
    assert semantics['liability_total_liabilities_yoy_growth']['value_semantics'] == 'derived'
    assert semantics['liability_advance_from_customers']['value_semantics'] == 'defaulted'


def test_basic_eps_is_not_adjusted_or_diluted_eps():
    worker = ETLWorker(Mock())
    row_map = worker._core_metric_row_map()
    assert worker._match_row_alias('基本每股收益（元／股）', row_map) == 'eps'
    assert worker._match_row_alias('扣除非经常性损益后的基本每股收益（元／股）', row_map) is None
    assert worker._match_row_alias('稀释每股收益（元／股）', row_map) is None
    note = worker._extract_roe_eps_special_rows(
        [['归属于公司普通股股东的净利润', '2.0', '0.08']],
        ['报告期利润', '加权平均净资产收益率', '稀释每股收益'])
    assert note['roe'] == 2.0
    assert 'eps' not in note


def test_failed_chunk_stops_before_snapshot_replacement(monkeypatch):
    worker = ETLWorker(Mock())
    chunks = [{'text': '<table>value</table>', 'unit_multiplier': 1, 'is_combined': True}]
    monkeypatch.setattr(worker, '_load_source', lambda p: ('report', chunks))
    monkeypatch.setattr(worker, '_extract_metadata', lambda *a: {'stock_code': '600080', 'report_year': 2022, 'report_period': 'FY'})
    monkeypatch.setattr(worker, '_chunk_markdown', lambda c: [])
    monkeypatch.setattr(worker, '_normalize_chunk_for_processing', lambda c: c)
    monkeypatch.setattr(worker, '_is_relevant_chunk', lambda c: True)
    monkeypatch.setattr(worker, '_extract_chunk_data', Mock(side_effect=RuntimeError('model disconnected')))
    post_process = Mock()
    monkeypatch.setattr(worker, '_post_process', post_process)
    result = worker.run('report.json', save_to_db=True)
    assert result['status'] == 'error'
    assert result['extraction_failures'] == [{'chunk_index': 0, 'error': 'model disconnected'}]
    post_process.assert_not_called()
