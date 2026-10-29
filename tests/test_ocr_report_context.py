"""Report context follows standalone captions, not incidental entity metrics."""
import hashlib
import json

import fitz
import pytest

from src.etl.canonical_audit import audit_report, grid
from src.utils.financial_row_identity import ROW_METRICS, row_key
from src.utils.statement_scope import physical_caption


def text(value, label='paragraph_title'):
    return dict(block_label=label, block_content=value)


def table(*rows):
    return text('<table>' + ''.join('<tr>' + ''.join('<td>' + cell + '</td>' for cell in row) +
                                   '</tr>' for row in rows) + '</table>', 'table')


def extract(tmp_path, monkeypatch, pages, *, fresh=False):
    """Real portable PDF/OCR pair, with independently laid out metadata lines."""
    import src.etl.canonical_audit as audit
    path = tmp_path/'data_root/context.pdf'
    path.parent.mkdir()
    document = fitz.open()
    for index, blocks in enumerate(pages):
        page = document.new_page(width=1000, height=1300)
        y = 28
        if index == 0:
            page.insert_text((20, y), '陌生制造企业股份有限公司2025年年度报告', fontname='china-s', fontsize=10)
            y += 28
        for item in blocks:
            top = y - 12
            rows = grid(item['block_content']) if item['block_label'] == 'table' else [
                [line] for line in item['block_content'].splitlines()]
            for row in rows:
                for column, value in enumerate(row):
                    page.insert_text((20+180*column, y), value, fontname='china-s', fontsize=10)
                y += 28
            item['block_bbox'] = [0, top, 980, y]
    document.save(path)
    document.close()
    cache = tmp_path/'ocr.json'
    cache.write_text(json.dumps([dict(prunedResult=dict(parsing_res_list=[b for b in blocks if not b.get('original_only')])) for blocks in pages]),
                     encoding='utf-8')
    monkeypatch.setattr(audit, 'ROOT', tmp_path)
    report = dict(stock_code='000789', company='陌生制造企业', year=2025, period='FY',
                  source_path='data_root/context.pdf', source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if fresh:
        from src.etl.ocr_quality import extract_fresh_document
        monkeypatch.setattr('src.agent.v3.evidence.ROOT', tmp_path)
        return extract_fresh_document(report, 'test', cache)[:2]
    return audit_report(report, 'test', cache_path=cache, reference_annotations=False,
                        scope_resolver=lambda values:(values, []))[:2]


@pytest.mark.parametrize('packed', [True, False])
def test_standalone_caption_with_company_date_and_unit_keeps_statement_identity(tmp_path, monkeypatch, packed):
    heading = ['陌生制造企业股份有限公司', '合并利润表', '2025年度', '(金额单位：人民币百万元)']
    blocks = [text('\n'.join(heading))] if packed else [text(line) for line in heading]
    facts, ledger = extract(tmp_path, monkeypatch, [blocks + [
        table(['项目', '附注', '2025年度', '2024年度'], ['营业收入', '五、12', '123', '110'],
              ['四、净利润', '', '20', '18'])],
        [text('陌生制造企业股份有限公司\n合并利润表(续)\n2025年度\n(金额单位：人民币百万元)'),
         table(['项目', '附注', '2025年度', '2024年度'],
               ['1. 归属于母公司股东的净利润', '', '15', '13'])]])
    values = {(v.metric, v.scope):v.value for v in facts}
    assert values.get(('operating_revenue', 'consolidated')) == '123000000'
    assert values.get(('net_profit', 'consolidated')) == '20000000'
    assert values.get(('attributable_net_profit', 'consolidated')) == '15000000'
    assert not ledger['conflicts']


def test_packed_shared_statement_keeps_scope_columns(tmp_path, monkeypatch):
    facts, _ = extract(tmp_path, monkeypatch, [[
        text('陌生制造企业股份有限公司\n合并及公司利润表\n2025年度\n(金额单位：人民币百万元)'),
        table(['项目', '2025年度合并', '2024年度合并', '2025年度公司', '2024年度公司'],
              ['营业收入', '123', '110', '80', '70'])]])
    values = {(v.metric, v.scope):v.value for v in facts}
    assert values.get(('operating_revenue', 'consolidated')) == '123000000'
    assert values.get(('operating_revenue', 'parent')) == '80000000'


@pytest.mark.parametrize('heading', ['1. 财务公司主要财务指标', '2. 参股公司主要财务数据'])
def test_other_entity_financial_indicators_do_not_become_group_summary(tmp_path, monkeypatch, heading):
    facts, ledger = extract(tmp_path, monkeypatch, [[
        text('主要会计数据和财务指标'), text('单位：百万元'),
        table(['项目', '2025年', '2024年'], ['营业收入', '123', '110'], ['资产总计', '900', '850']),
        text(heading), text('单位：百万元'),
        table(['项目', '单位', '2025年', '2024年'], ['营业收入', '百万元', '4', '3'],
              ['净利润', '百万元', '2', '1'], ['资产总计', '百万元', '60', '50']),
    ]])
    values = {v.metric:v.value for v in facts}
    assert values.get('operating_revenue') == '123000000'
    assert values.get('total_assets') == '900000000'
    assert 'net_profit' not in values
    assert not ledger['conflicts']


def test_summary_context_does_not_cross_a_page_without_its_table(tmp_path, monkeypatch):
    facts, ledger = extract(tmp_path, monkeypatch, [
        [text('主要会计数据'), text('单位：百万元'),
         table(['项目', '2025年', '2024年'], ['营业收入', '123', '110'])],
        [text('一般业务情况', 'text')],
        [table(['项目', '2025年', '2024年'], ['营业收入', '4', '3'], ['净利润', '2', '1'])],
    ])
    values = {v.metric:v.value for v in facts}
    assert values.get('operating_revenue') == '123000000'
    assert 'net_profit' not in values
    assert not ledger['conflicts']


@pytest.mark.parametrize('label',['paragraph_title','text'])
def test_explanatory_paragraph_between_summary_title_and_table_keeps_its_context(tmp_path,monkeypatch,label):
    facts,ledger=extract(tmp_path,monkeypatch,[[text('六、主要会计数据和财务指标'),
        text('公司是否需追溯调整或重述以前年度会计数据',label),text('是 否',label),
        table(['项目','2025年','2024年'],['营业收入（元）','123','110'],
              ['归属于上市公司股东的净利润（元）','20','18'])]])
    values={value.metric:value.value for value in facts}
    assert values.get('operating_revenue')=='123'
    assert values.get('attributable_net_profit')=='20'
    assert not ledger['conflicts']


@pytest.mark.parametrize('line', ['详见合并利润表及相关附注', '报告说明合并利润表包含营业收入'])
def test_inline_statement_reference_does_not_start_a_statement(tmp_path, monkeypatch, line):
    facts, _ = extract(tmp_path, monkeypatch, [[text('陌生制造企业股份有限公司\n'+line+'\n2025年度'),
        text('单位：百万元'), table(['项目', '2025年', '2024年'], ['营业收入', '999', '888'])]])
    assert not any(v.metric == 'operating_revenue' for v in facts)


@pytest.mark.parametrize('label, metric', [
    ('归属于本公司股东的净利润', 'attributable_net_profit'),
    ('归属于本公司股东的扣除非经常性损益的净利润', 'deducted_attributable_net_profit'),
    ('归属于本公司股东的扣除非经常性损益后的净利润', 'deducted_attributable_net_profit'),
])
def test_explicit_current_company_shareholder_rows_have_exact_metric_identity(label, metric):
    assert ROW_METRICS.get(row_key(label)) == metric


@pytest.mark.parametrize('label', [
    '归属于少数股东的净利润', '本公司净利润', '归属于本公司股东的综合收益总额',
    '归属于本公司股东的净资产', '某子公司归属于本公司股东的净利润',
])
def test_shareholder_aliases_do_not_collapse_other_metric_or_entity_labels(label):
    assert ROW_METRICS.get(row_key(label)) not in {'attributable_net_profit', 'deducted_attributable_net_profit'}


def test_two_genuine_group_summary_amounts_still_conflict(tmp_path, monkeypatch):
    facts, ledger = extract(tmp_path, monkeypatch, [[text('主要会计数据和财务指标'), text('单位：百万元'),
        table(['项目', '2025年', '2024年'], ['营业收入', '123', '110']),
        table(['项目', '2025年', '2024年'], ['营业收入', '456', '400'])]])
    assert not any(v.metric == 'operating_revenue' for v in facts)
    conflict = next(v for v in ledger['conflicts'] if v['metric'] == 'operating_revenue')
    assert {v['value'] for v in conflict['candidates']} == {'123000000', '456000000'}


def test_missing_net_profit_stays_absent(tmp_path, monkeypatch):
    facts, ledger = extract(tmp_path, monkeypatch, [[text('合并利润表'), text('单位：元'),
        table(['项目', '2025年度', '2024年度'], ['营业收入', '123', '110'], ['净利润', '-', '-'])]])
    assert not any(v.metric in {'net_profit', 'net_margin'} for v in facts)
    assert any(v['metric'] == 'net_profit' and v['reason'] == 'empty_or_not_disclosed' for v in ledger['rejected'])


def test_explicit_physical_scope_contradiction_is_removed_before_amount_conflict(tmp_path, monkeypatch):
    missing_ocr_title = {**text('母公司利润表'), 'original_only': True}
    facts, ledger = extract(tmp_path, monkeypatch, [
        [text('合并利润表'), text('单位：元'),
         table(['项目', '2025年度', '2024年度'], ['营业收入', '123', '110'], ['净利润', '20', '18'])],
        [missing_ocr_title, text('单位：元'),
         table(['项目', '2025年度', '2024年度'], ['营业收入', '80', '70'], ['净利润', '8', '7'])],
    ], fresh=True)
    values = {(v.metric, v.scope):v.value for v in facts}
    assert values.get(('operating_revenue', 'consolidated')) == '123'
    assert values.get(('net_profit', 'consolidated')) == '20'
    assert not ledger['conflicts']
    assert any(v['reason'] == 'physical_statement_scope_conflict' and v['page'] == 2 for v in ledger['rejected'])
    assert any(v['source']['page'] == 2 and v['value'] == '80' for v in ledger['source_candidates'])


@pytest.mark.parametrize('unbound', [False, True])
def test_amount_conflict_keeps_candidates_without_a_physical_contradiction(tmp_path, monkeypatch, unbound):
    if unbound:
        monkeypatch.setattr('src.etl.submission_sources.bind_original_source', lambda value, path, **kwargs:None)
    facts, ledger = extract(tmp_path, monkeypatch, [[text('合并利润表'), text('单位：元'),
        table(['项目', '2025年度', '2024年度'], ['营业收入', '123', '110']),
        table(['项目', '2025年度', '2024年度'], ['营业收入', '456', '400'])]], fresh=True)
    assert not any(v.metric == 'operating_revenue' for v in facts)
    conflict = next(v for v in ledger['conflicts'] if v['metric'] == 'operating_revenue')
    assert {v['value'] for v in conflict['candidates']} == {'123', '456'}


@pytest.mark.parametrize('title', [
    '八、截至报告期末公司近两年的主要会计数据和财务指标',
    '近三年主要会计数据和财务指标',
    '公司近2年的主要财务指标',
])
def test_explicit_company_period_qualifiers_are_part_of_summary_captions(title):
    assert physical_caption(title) == dict(scope='consolidated', family='indicators')


@pytest.mark.parametrize('title', [
    '财务公司近两年的主要会计数据和财务指标',
    '参股公司主要财务指标', '子公司近2年的主要财务指标',
    '截至报告期末财务公司近两年的主要会计数据和财务指标',
    '详见公司近2年的主要财务指标',
])
def test_company_period_caption_grammar_excludes_other_entities_and_references(title):
    assert physical_caption(title) is None


def test_internal_stock_header_replaces_the_flow_header_with_its_physical_date(tmp_path, monkeypatch):
    facts, _ = extract(tmp_path, monkeypatch, [[text('主要会计数据'), text('单位：元'),
        table(['项目', '2025年', '2024年'], ['营业收入', '123', '110'],
              ['项目', '2025年末', '2024年末'], ['资产总计', '1000', '900'])]], fresh=True)
    asset = next((v for v in facts if v.metric == 'total_assets'), None)
    assert asset is not None
    assert asset.value == '1000' and asset.status == 'verified'
    assert asset.source.column == '2025年末'


def test_internal_prior_year_header_cannot_inherit_the_current_flow_year(tmp_path, monkeypatch):
    facts, ledger = extract(tmp_path, monkeypatch, [[text('主要会计数据'), text('单位：元'),
        table(['项目', '2025年', '2024年'], ['营业收入', '123', '110'],
              ['项目', '2024年末', '2023年末'], ['资产总计', '900', '800'])]])
    assert not any(v.metric == 'total_assets' for v in facts)
    assert any(v['metric'] == 'total_assets' and v['reason'] == 'column_unknown' for v in ledger['rejected'])


def test_empty_cashflow_row_mentioning_a_subsidiary_does_not_replace_period_headers(tmp_path, monkeypatch):
    facts, _ = extract(tmp_path, monkeypatch, [[text('合并现金流量表'), text('单位：元'),
        table(['项目', '2025年度', '2024年度'],
              ['经营活动产生的现金流量净额', '10', '9'],
              ['其中：子公司吸收少数股东投资收到的现金', '-', '-'],
              ['筹资活动产生的现金流量净额', '-7', '-6'],
              ['期末现金及现金等价物余额', '20', '19'])]])
    values = {v.metric:v.value for v in facts}
    assert values.get('financing_cash_flow') == '-7'
    assert values.get('cash_end') == '20'


@pytest.mark.parametrize('label', [
    '取得子公司及其他营业单位支付的现金净额',
    '处置子公司及其他营业单位收到的现金净额',
])
def test_ignored_money_row_with_year_shaped_amounts_cannot_reset_period_headers(tmp_path, monkeypatch, label):
    facts, _ = extract(tmp_path, monkeypatch, [[text('合并现金流量表'), text('单位：元'),
        table(['项目', '2025年度', '2024年度'], ['经营活动产生的现金流量净额', '10', '9'],
              [label, '2024', '2025'], ['筹资活动产生的现金流量净额', '7', '6'])]], fresh=True)
    financing = next(v for v in facts if v.metric == 'financing_cash_flow')
    assert financing.value == '7'
    assert financing.status == 'verified'
    assert financing.source.column == '2025年度'
