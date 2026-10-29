"""Table identity comes from bounded titles, column roles and local unit evidence."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import fitz
import pytest

from src.etl.canonical_audit import audit_report, unit_in, statement_caption_unit, select_current_column
from src.utils.statement_scope import physical_caption


def block(text, label='text'):
    return dict(block_label=label, block_content=text)


def table(*rows):
    return block('<table>' + ''.join('<tr>' + ''.join('<td>' + value + '</td>' for value in row) + '</tr>' for row in rows) + '</table>', 'table')


def extract(tmp_path, monkeypatch, pages, period='FY'):
    """Use a real portable Chinese PDF and OCR payload; inspect pre-proof candidates."""
    import src.etl.canonical_audit as audit
    from src.etl.canonical_audit import grid
    path = tmp_path / 'data_root/context.pdf'
    path.parent.mkdir(exist_ok=True)
    pdf = fitz.open()
    for index, blocks in enumerate(pages):
        page = pdf.new_page(width=920, height=1200)
        y = 24
        if index == 0:
            page.insert_text((15, y), '陌生制造企业2025年' + ('第一季度报告' if period == 'Q1' else '年度报告'), fontname='china-s', fontsize=10)
            y += 24
        for item in blocks:
            item['block_bbox'] = [0, y - 12, 900, y + 12]
            lines = grid(item['block_content']) if item['block_label'] == 'table' else [[item['block_content']]]
            for row in lines:
                for column, value in enumerate(row):
                    page.insert_text((15 + column * 160, y), value, fontname='china-s', fontsize=10)
                y += 24
    pdf.save(path)
    pdf.close()
    cache = tmp_path / 'ocr.json'
    cache.write_text(json.dumps([{'prunedResult': {'parsing_res_list': blocks}} for blocks in pages]), encoding='utf-8')
    monkeypatch.setattr(audit, 'ROOT', tmp_path)
    return audit_report(dict(stock_code='000789', company='陌生制造企业', year=2025, period=period,
        source_path='data_root/context.pdf', source_sha256=hashlib.sha256(path.read_bytes()).hexdigest()),
        'table-test', cache_path=cache, reference_annotations=False, scope_resolver=lambda facts: (facts, []))[:2]


@pytest.mark.parametrize('text', ['金额单位为人民币千元', '单位为：千元', '单位为千元', '单位：人民币千元'])
def test_explicit_currency_unit_sentences(text):
    assert unit_in(text) == ('千元', Decimal(1000))


def test_conflicting_unit_declarations_are_ambiguous():
    assert unit_in('单位：元；另表单位：千元') is None


def test_per_share_declaration_is_not_an_amount_unit():
    assert unit_in('单位：元/股') is None


def test_caption_unit_does_not_cross_an_unrelated_numeric_table():
    original = '单位：万元\n其他项目 8765 4321\n合并利润表\n项目 2025年度 2024年度'
    assert statement_caption_unit(original, '合并利润表') is None


@pytest.mark.parametrize('text, expected', [
    ('2025年度合并及公司利润表', dict(scope='mixed', family='利润表')),
    ('2025年合并现金流量表（续）', dict(scope='consolidated', family='现金流量表')),
    ('5、公司资产负债表', dict(scope='parent', family='资产负债表')),
    ('参见合并利润表', None),
    ('后附财务报表附注为本财务报表的组成部分', None),
])
def test_physical_caption_accepts_dated_headings_only(text, expected):
    assert physical_caption(text) == expected


def test_statement_footer_does_not_end_later_income_and_cashflow(tmp_path, monkeypatch):
    facts, _ = extract(tmp_path, monkeypatch, [
        [block('合并资产负债表'), block('单位：元'), table(['项目', '2025年12月31日', '2024年12月31日'], ['资产总计', '1000', '900']),
         block('后附财务报表附注为本财务报表的组成部分', 'vision_footnote')],
        [block('合并利润表'), block('单位：元'), table(['项目', '2025年度', '2024年度'], ['营业收入', '1200', '1000'])],
        [block('合并现金流量表'), block('单位：元'), table(['项目', '2025年度', '2024年度'], ['经营活动产生的现金流量净额', '150', '130'])],
    ])
    values = {f.metric: f.value for f in facts if f.scope == 'consolidated'}
    assert values.get('operating_revenue') == '1200'
    assert values.get('operating_cash_flow') == '150'


def test_cross_reference_does_not_start_income_statement(tmp_path, monkeypatch):
    facts, _ = extract(tmp_path, monkeypatch, [[
        block('合并资产负债表'), block('单位：元'), table(['项目', '2025年度', '2024年度'], ['资产总计', '1000', '900']),
        block('参见合并利润表'), block('单位：元'), table(['项目', '2025年度', '2024年度'], ['营业收入', '999', '888']),
    ]])
    assert not any(f.metric == 'operating_revenue' for f in facts)


@pytest.mark.parametrize('heading', ['三、合并财务报表附注', '三、合并会计报表注释'])
def test_real_notes_heading_ends_primary_statement_context(tmp_path, monkeypatch, heading):
    facts, _ = extract(tmp_path, monkeypatch, [[
        block('合并资产负债表'), block('单位：元'), table(['项目', '2025年度', '2024年度'], ['资产总计', '1000', '900']),
        block(heading), block('合并利润表'), block('单位：元'),
        table(['项目', '2025年度', '2024年度'], ['营业收入', '999', '888']),
    ]])
    assert not any(f.metric == 'operating_revenue' for f in facts)


def test_unit_before_title_is_local_to_its_statement(tmp_path, monkeypatch):
    facts, _ = extract(tmp_path, monkeypatch, [[
        block('金额单位为人民币千元'), block('合并利润表'),
        table(['项目', '2025年度', '2024年度'], ['营业收入', '123', '110']),
        block('母公司利润表'), block('单位：元'),
        table(['项目', '2025年度', '2024年度'], ['营业收入', '75', '70']),
    ]])
    values = {(f.metric, f.scope): f.value for f in facts}
    assert values.get(('operating_revenue', 'consolidated')) == '123000'
    assert values.get(('operating_revenue', 'parent')) == '75'


def test_declared_statement_section_unit_is_bounded_by_notes(tmp_path, monkeypatch):
    facts, _ = extract(tmp_path, monkeypatch, [
        [block('二、财务报表'), block('财务附注中报表的单位为：千元'), block('合并资产负债表'),
         table(['项目', '2025年度', '2024年度'], ['资产总计', '1000', '900'])],
        [block('合并现金流量表'), table(['项目', '2025年度', '2024年度'], ['经营活动产生的现金流量净额', '150', '130'])],
    ])
    assert next((f.value for f in facts if f.metric == 'operating_cash_flow'), None) == '150000'


@pytest.mark.parametrize('headings', [
    [['项目', '2025年度合并', '2024年度合并', '2025年度公司', '2024年度公司']],
    [['项目', '2025年度', '2024年度', '2025年度', '2024年度'], ['项目', '合并', '合并', '公司', '公司']],
    [['项目', '合并', '合并', '公司', '公司'], ['项目', '2025年度', '2024年度', '2025年度', '2024年度']],
])
def test_mixed_columns_keep_period_and_scope_axes(tmp_path, monkeypatch, headings):
    facts, _ = extract(tmp_path, monkeypatch, [[block('2025年度合并及公司利润表'), block('单位为：千元'),
        table(*headings, ['营业收入', '123', '110', '75', '70']),
    ]])
    values = {(f.metric, f.scope): f.value for f in facts}
    assert values.get(('operating_revenue', 'consolidated')) == '123000'
    assert values.get(('operating_revenue', 'parent')) == '75000'
    assert all('2025年度' in f.source.column for f in facts if f.source)


@pytest.mark.parametrize('description_header', ['变动比例（%）', '2025年度变动比例（%）'])
def test_description_table_cannot_inherit_amount_columns(tmp_path, monkeypatch, description_header):
    facts, ledger = extract(tmp_path, monkeypatch, [
        [block('主要会计数据和财务指标'), block('单位：千元'),
         table(['项目', '2025年度', '2024年度'], ['营业收入', '123000', '110000'])],
        [table(['项目', description_header, '主要原因'], ['营业收入', '8.1', '业务增加'])],
    ])
    assert next((f.value for f in facts if f.metric == 'operating_revenue'), None) == '123000000'
    assert not any(item['metric'] == 'operating_revenue' for item in ledger['conflicts'])


def test_genuine_amount_conflict_is_still_rejected(tmp_path, monkeypatch):
    facts, ledger = extract(tmp_path, monkeypatch, [[block('主要会计数据和财务指标'), block('单位：元'),
        table(['项目', '2025年度', '2024年度'], ['营业收入', '123', '110']),
        table(['项目', '2025年度', '2024年度'], ['营业收入', '456', '400']),
    ]])
    assert not any(f.metric == 'operating_revenue' for f in facts)
    assert any(item['metric'] == 'operating_revenue' for item in ledger['conflicts'])


def test_amount_and_reported_growth_columns_can_share_one_table(tmp_path, monkeypatch):
    facts, ledger = extract(tmp_path, monkeypatch, [[block('主要会计数据和财务指标'), block('单位：千元'),
        table(['项目', '2025年度', '2024年度', '同比增减（%）'], ['营业收入', '123', '110', '11.82%']),
    ]])
    values = {f.metric: f.value for f in facts}
    assert values['operating_revenue'] == '123000'
    assert values['operating_revenue_reported_yoy'] == '11.82'
    assert not ledger['conflicts']


def test_disclosed_rate_uses_confirmed_leaf_of_multilevel_heading(tmp_path, monkeypatch):
    facts, _ = extract(tmp_path, monkeypatch, [[block('主要会计数据和财务指标'), block('单位：千元'),
        table(['项目', '本报告期末', '上年度末', '本报告期末比上年度末'],
              ['项目', '本报告期末', '上年度末', '上年度末增减变动幅度（%）'],
              ['总资产', '100', '90', '11.11']),
    ]], period='Q1')
    growth = next((f for f in facts if f.metric == 'total_assets_reported_change_vs_year_end'), None)
    assert growth is not None
    assert growth.value == '11.11'
    assert growth.source.column == '上年度末增减变动幅度（%）'


def test_continued_summary_keeps_original_preceding_page_rate_heading(tmp_path, monkeypatch):
    facts, _ = extract(tmp_path, monkeypatch, [
        [block('主要会计数据和财务指标'), block('单位：千元'),
         table(['项目', '2025年度', '2024年度', '本年比上年增减（%）'], ['营业收入', '123', '110', '11.82%'])],
        [table(['归属于上市公司股东的净利润', '20', '18', '11.11%'])],
    ])
    growth = next((f for f in facts if f.metric == 'attributable_net_profit_reported_yoy'), None)
    assert growth is not None and growth.value == '11.11'


def test_mixed_scope_in_one_column_is_not_inferred_from_title(tmp_path, monkeypatch):
    facts, _ = extract(tmp_path, monkeypatch, [[block('合并及公司利润表'), block('单位：千元'),
        table(['项目', '2025年度合并及公司', '2024年度合并及公司'], ['营业收入', '123', '110']),
    ]])
    assert not any(f.metric == 'operating_revenue' for f in facts)


def test_explicit_row_and_table_unit_conflict_is_still_rejected(tmp_path, monkeypatch):
    facts, ledger = extract(tmp_path, monkeypatch, [[block('合并利润表'), block('单位：千元'),
        table(['项目', '2025年度', '2024年度'], ['营业收入（万元）', '123', '110']),
    ]])
    assert not any(f.metric == 'operating_revenue' for f in facts)
    assert any(item['reason'] == 'unit_conflict' for item in ledger['rejected'])


def test_a_column_with_conflicting_years_is_not_current_period():
    assert select_current_column(['项目', '2025年度/2024年度'], 2025, 'FY', 'operating_revenue') is None


def test_signed_expense_is_preserved_without_wrong_gross_profit_derivation(tmp_path, monkeypatch):
    facts, ledger = extract(tmp_path, monkeypatch, [[block('合并利润表'), block('单位：元'),
        table(['项目', '2025年度', '2024年度'], ['营业收入', '123', '110'],
              ['营业成本', '(90)', '(80)'], ['净利润', '(8)', '(7)']),
    ]])
    values = {f.metric: f.value for f in facts}
    assert values['operating_cost'] == '-90'
    assert values['net_profit'] == '-8'
    assert values['net_margin'].startswith('-6.504')
    assert 'gross_profit' not in values and 'gross_margin' not in values
    assert any(item['metric'] == 'gross_profit' and item['reason'] == 'signed_expense_convention_unsupported'
               for item in ledger['rejected'])


@pytest.mark.parametrize('report_id, metric, raw_value, source_page', [
    ('002594_2024_FY', 'sales_cash', '774,347,395', 149),
    ('000333_2023_FY', 'total_assets', '486,038,184', 159),
    ('601668_2023_Q1', 'operating_revenue', '524,828,043', 9),
])
def test_available_original_pdf_and_ocr_table_regression(report_id, metric, raw_value, source_page):
    root = Path(__file__).resolve().parents[1]
    current_path = root / 'data/runtime/submissions/ocr_trial/current.json'
    if not current_path.exists():
        pytest.skip('Local original-report assets are unavailable')
    current = json.loads(current_path.read_text(encoding='utf-8'))
    reports = json.loads((root / current['work'] / 'reports.json').read_text(encoding='utf-8'))
    report = next((item for item in reports if f"{item['stock_code']}_{item['year']}_{item['period']}" == report_id), None)
    if report is None:
        pytest.skip('Representative original report is unavailable')
    cache = root / 'data/runtime/submissions/assets' / report['document_version'] / 'ocr.json'
    if not cache.exists() or not (root / report['source_path']).exists():
        pytest.skip('Representative original PDF or OCR is unavailable')
    facts, _, _ = audit_report(report, 'table-regression', cache_path=cache, reference_annotations=False,
        scope_resolver=lambda facts: (facts, []))
    found = next((f for f in facts if f.metric == metric and f.scope == 'consolidated'), None)
    assert found is not None
    assert found.source.raw_value == raw_value
    assert found.source.page == source_page
