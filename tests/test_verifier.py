"""作品说明：离线以反例验证财务核验的完整身份约束。"""
from copy import deepcopy
import pytest
from src.agent.facts import bind_sql_rows, metadata_matches
from src.agent.verifier import (build_evidence, bind_chart_source, prepend_failure_notice,
    verify_charts, verify_numbers, verify_references, verify_turn)


def row(year=2024, **values):
    return dict(stock_code='603259', stock_abbr='药明康德', report_year=year, report_period='FY', **values)


def test_amount_conversion_and_rounding():
    check = verify_numbers('药明康德2024年净利润约为94.47亿元。', [row(net_profit=944718)])
    assert check['status'] == 'pass'
    assert check['claims'][0]['fact_ids']


def test_percentage_direct_ratio():
    assert verify_numbers('药明康德2024年资产负债率为42.31%。', [row(asset_liability_ratio=42.309)])['status'] == 'pass'


def test_yoy_same_company_metric_period_and_adjacent_year():
    rows = [row(2023, total_operating_revenue=100), row(total_operating_revenue=120)]
    answer = '药明康德2024年营业收入同比增长20.0%。'
    assert verify_numbers(answer, rows)['status'] == 'pass'
    for key, value in [('stock_code', '300347'), ('report_period', 'HY'), ('report_year', 2022)]:
        corrupted = deepcopy(rows)
        corrupted[0][key] = value
        assert verify_numbers(answer, corrupted)['status'] == 'fail'


@pytest.mark.parametrize('answer', [
    '药明康德2023年营业收入为120万元。',
    '泰格医药2024年营业收入为120万元。',
    '药明康德2024年上半年营业收入为120万元。',
    '药明康德2024年净利润为120万元。',
    '药明康德2024年营业收入为120亿元。',
    '药明康德2024年营业收入为999万元。',
])
def test_equal_number_never_crosses_identity_metric_period_or_unit(answer):
    assert verify_numbers(answer, [row(total_operating_revenue=120)])['status'] == 'fail'


@pytest.mark.parametrize('answer', ['药明康德2024年营业收入不是120万元。', '药明康德营业收入为120万元。', '营业收入表现良好。'])
def test_unsupported_semantics_or_missing_scope_never_pass(answer):
    assert verify_numbers(answer, [row(total_operating_revenue=120)])['status'] == 'warn'


def test_missing_row_identity_cannot_pass():
    assert verify_numbers('药明康德2024年净利润为120万元。', [{'net_profit':120}])['status'] != 'pass'


def test_yoy_direction_and_percentage_points_not_interchangeable():
    rows = [row(2023, total_operating_revenue=100), row(total_operating_revenue=80)]
    assert verify_numbers('药明康德2024年营业收入同比下降20%。', rows)['status'] == 'pass'
    assert verify_numbers('药明康德2024年营业收入同比增长20%。', rows)['status'] == 'fail'
    assert verify_numbers('药明康德2024年营业收入同比下降20个百分点。', rows)['status'] == 'fail'


def reference():
    return dict(document_id='doc1', chunk_id='chunk1', document_version='sha256', stock_code='603259', report_year=2024,
        report_period='FY', page_start=8, source_title='年报', text='营业收入增长，主要受海外客户需求恢复带动。')


def test_reference_binds_original_identity_and_excerpt():
    original = reference()
    excerpt = {**original, 'text':'主要受海外客户需求恢复带动……'}
    assert verify_references([excerpt], [original])['status'] == 'pass'
    for key, value in [('stock_code','300347'), ('report_year',2023), ('page_start',9), ('document_version','other'), ('text','明年利润翻倍')]:
        assert verify_references([{**excerpt,key:value}], [original])['status'] == 'fail'
    assert verify_references([{'text':original['text']}], [original])['status'] == 'warn'


def chart_and_rows():
    query_id, rows = bind_sql_rows('SELECT revenue', [row(2023,total_operating_revenue=100000), row(total_operating_revenue=120000)])
    chart = dict(x_data=['2023年','2024年'], y_data=[10.,12.], y_label='营业收入（亿元）', title='药明康德营业收入', data_source=dict(kind='sql_result',query_id=query_id))
    chart['data_source'] = bind_chart_source(chart, rows)
    return chart, rows


def test_chart_binds_every_point_and_exact_labels():
    chart, rows = chart_and_rows()
    assert verify_charts([chart], rows)['status'] == 'pass'
    for changes in [dict(y_data=[12.,10.]), dict(x_data=['2024','2023']), dict(y_data=[10.,999.]), dict(title='泰格医药营业收入'), dict(y_label='净利润（亿元）')]:
        assert verify_charts([{**chart, **changes}], rows)['status'] == 'fail'
    wrong_query = {**chart, 'data_source':{**chart['data_source'],'query_id':'another-query'}}
    assert verify_charts([wrong_query], rows)['status'] == 'fail'


def test_unbound_or_explicit_chart_cannot_pass():
    chart, rows = chart_and_rows()
    for source in [dict(kind='sql_result'), dict(kind='explicit',detail='user input')]:
        assert verify_charts([{**chart,'data_source':source}], rows)['status'] == 'warn'


def test_turn_does_not_claim_explanation_is_proven_by_reference_identity():
    ref = reference()
    check = verify_turn('业务增长来自新品。', [], [ref], [ref], [], question='为什么增长')
    assert check['status'] == 'warn'
    assert any(c['name']=='explanation_support' for c in check['checks'])


def test_failed_claim_is_withheld():
    check = verify_turn('药明康德2024年营业收入为999万元。', [row(total_operating_revenue=120)], [], [], [])
    assert check['status'] == 'fail'
    assert '999' not in prepend_failure_notice('营业收入999万元', check)


def test_build_evidence_keeps_source_types():
    evidence = build_evidence([dict(sql='SELECT 1', rows=[{'value':1}],columns=['value'])], [reference()], [dict(x_data=['2024'],y_data=[1])])
    assert [item['type'] for item in evidence] == ['sql','reference','chart']
    assert evidence[1]['page_start'] == 8


def test_staged_or_wrong_company_metadata_is_not_retrievable():
    scope = dict(stock_codes=['603259'], report_years=[2024], report_period='FY')
    assert metadata_matches(row(), scope)
    assert not metadata_matches({**row(),'stock_code':'300347'}, scope)
    assert not metadata_matches({**row(),'publication_status':'staged'}, scope)


def test_rounding_tolerance_follows_written_precision_not_percentage_of_value():
    rows=[row(net_profit=944718)]
    assert verify_numbers('药明康德2024年净利润为94.47亿元。',rows)['status']=='pass'
    assert verify_numbers('药明康德2024年净利润为944000万元。',rows)['status']=='fail'
    assert verify_numbers('药明康德2024年净利润为94.4717亿元。',rows)['status']=='fail'
