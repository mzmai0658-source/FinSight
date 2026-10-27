"""经营原因表格须绑定原件同一物理行，拒绝借邻行、分部或子公司说明。"""
import pytest

from src.agent.v3 import evidence
from src.agent.v3.cause_evidence import bound_cause_snippets


REVENUE = ['营业收入', '120.00', '100.00', '20.00%', '主要是产品销量增长所致']
PROFIT = ['归属于上市公司股东的净利润', '12.00', '10.00', '20.00%', '主要是营业收入增加和成本下降所致']
HEADERS = ['科目名称', '本期发生额', '上期发生额', '变动率', '变动原因']


def snippet(text):
    return dict(id='chunk', text=text, stock_code='123456', company='样例股份',
                year=2025, period='Q1', page=1, source_path='registered.pdf',
                document_version='registered-digest')


def table_pdf(tmp_path, rows, *, caption='主要会计数据和财务指标变动情况及原因', previous_caption=None, headers=None):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors

    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    path = tmp_path / 'report.pdf'
    doc = canvas.Canvas(str(path), pagesize=(800, 600))
    if previous_caption is not None:
        doc.setFont('STSong-Light', 10)
        doc.drawString(40, 60, previous_caption)
        doc.showPage()
    doc.setFont('STSong-Light', 10)
    doc.drawString(40, 565, caption)
    table = Table([headers or HEADERS, *rows], colWidths=[190, 90, 90, 70, 280], rowHeights=34)
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'STSong-Light'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), .5, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    _, height = table.wrap(720, 500)
    table.drawOn(doc, 40, 530 - height)
    doc.save()
    return path


def table_candidate(cells, metric='operating_revenue'):
    # 检索候选保留 OCR 字符；原件核验不可将竖线拼接当成逐字原文。
    value = snippet(' | '.join(cells))
    return {**value, 'target_metric': metric, 'cause_table_cells': cells}


@pytest.mark.parametrize('metric,cells', [
    ('operating_revenue', REVENUE),
    ('attributable_net_profit', PROFIT),
    ('operating_cash_flow', ['经营活动产生的现金流量净额', '50', '30', '66.67%',
                             '主要系销售商品、提供劳务收到的现金增加所致']),
    ('operating_revenue', ['营业收入', '-2.67',
                            '受房地产市场持续调整及基础设施投资放缓等因素影响，营业收入同比略有下滑。']),
])
def test_table_subject_binds_only_its_own_reason(metric, cells):
    text = '\n'.join([' | '.join(HEADERS), ' | '.join(cells),
                      '营业成本 | 90 | 80 | 12.5% | 主要是采购成本增加所致'])
    bound = bound_cause_snippets([snippet(text)], {metric})
    assert len(bound) == 1
    assert bound[0]['text'] == ' | '.join(cells)
    assert bound[0]['cause_table_cells'] == cells
    assert bound[0]['target_metric'] == metric
    assert text[bound[0]['original_start']:bound[0]['original_end']] == bound[0]['text']


def test_profit_reason_mentioning_revenue_and_no_change_are_not_revenue_reasons():
    text = ' | '.join(PROFIT) + '\n营业收入 | 100 | 101 | -0.99% | 无重大变化'
    assert not bound_cause_snippets([snippet(text)], {'operating_revenue'})
    assert not bound_cause_snippets([snippet('营业总收入 | 120 | 100 | 20% | 主要是销量增加所致')], {'operating_revenue'})


def test_original_table_quote_uses_verified_row_characters(monkeypatch, tmp_path):
    path = table_pdf(tmp_path, [REVENUE, PROFIT])
    monkeypatch.setattr(evidence, 'source_pdf', lambda _: path)
    checked = evidence.checked_narrative_literal(table_candidate(REVENUE))
    assert checked is not None
    assert checked['location_method'] == 'original_pdf_table_row'
    assert checked['literal_verification'] == 'matched_original_pdf_page'
    assert '|' not in checked['text']
    assert '主要是产品销量增长所致' in checked['text']
    assert '成本下降' not in checked['text']
    assert checked['narrative_scope'] == 'consolidated'
    assert checked['original_table_row']['column_literals'] == REVENUE


@pytest.mark.parametrize('cells,metric', [
    ([*REVENUE[:1], '121.00', *REVENUE[2:]], 'operating_revenue'),
    ([*REVENUE[:-1], PROFIT[-1]], 'operating_revenue'),
    (REVENUE, 'attributable_net_profit'),
    ([*REVENUE[:1], '100.00', '120.00', *REVENUE[3:]], 'operating_revenue'),
])
def test_table_verification_rejects_changed_number_neighbor_reason_or_metric(monkeypatch, tmp_path, cells, metric):
    path = table_pdf(tmp_path, [REVENUE, PROFIT])
    monkeypatch.setattr(evidence, 'source_pdf', lambda _: path)
    assert evidence.checked_narrative_literal(table_candidate(cells, metric)) is None


@pytest.mark.parametrize('caption', [
    '本集团合并抵销前煤炭分部经营成果',
    '分产品经营情况',
    '财务公司主要财务指标',
    '下属子公司主要会计数据',
])
def test_segment_or_subsidiary_table_is_not_report_wide_evidence(monkeypatch, tmp_path, caption):
    path = table_pdf(tmp_path, [REVENUE], caption=caption)
    monkeypatch.setattr(evidence, 'source_pdf', lambda _: path)
    assert evidence.checked_narrative_literal(table_candidate(REVENUE)) is None


def test_parent_table_records_scope_in_original_proof(monkeypatch, tmp_path):
    path = table_pdf(tmp_path, [REVENUE], caption='母公司利润表项目变动原因')
    monkeypatch.setattr(evidence, 'source_pdf', lambda _: path)
    checked = evidence.checked_narrative_literal(table_candidate(REVENUE))
    assert checked is not None and checked['narrative_scope'] == 'parent'


def test_duplicate_physical_rows_are_ambiguous(monkeypatch, tmp_path):
    path = table_pdf(tmp_path, [REVENUE, REVENUE])
    monkeypatch.setattr(evidence, 'source_pdf', lambda _: path)
    assert evidence.checked_narrative_literal(table_candidate(REVENUE)) is None


@pytest.mark.parametrize('caption,allowed', [
    ('本集团合并抵销前铁路分部经营成果如下：', False),
    ('母公司利润表项目变动原因', True),
    ('主要会计数据和财务指标发生变动情况及原因', True),
])
def test_page_start_table_preserves_scope_from_previous_page(monkeypatch, tmp_path, caption, allowed):
    path = table_pdf(tmp_path, [REVENUE], caption='', previous_caption=caption)
    monkeypatch.setattr(evidence, 'source_pdf', lambda _: path)
    checked = evidence.checked_narrative_literal({**table_candidate(REVENUE), 'page': 2})
    assert (checked is not None) == allowed
    if checked is not None and caption.startswith('母公司'):
        assert checked['narrative_scope'] == 'parent'


@pytest.mark.parametrize('caption,previous,first_header,expected_scope', [
    ('母公司主要会计数据和财务指标变动情况及原因', None, '科目名称', 'parent'),
    ('主要会计数据和财务指标变动情况及原因', None, '母公司科目名称', 'parent'),
    ('主要会计数据和财务指标变动情况及原因', None, '煤炭分部科目名称', None),
    ('合并利润表项目变动原因', '母公司利润表项目变动原因', '科目名称', 'consolidated'),
    ('母公司利润表项目变动原因', '合并利润表项目变动原因', '科目名称', 'parent'),
    ('合并利润表项目变动原因', '本集团合并抵销前煤炭分部经营成果', '科目名称', 'consolidated'),
])
def test_nearest_explicit_caption_or_header_owns_table_scope(monkeypatch, tmp_path, caption, previous, first_header, expected_scope):
    path = table_pdf(tmp_path, [REVENUE], caption=caption, previous_caption=previous,
                     headers=[first_header, *HEADERS[1:]])
    monkeypatch.setattr(evidence, 'source_pdf', lambda _: path)
    candidate = {**table_candidate(REVENUE), 'page': 2 if previous else 1}
    checked = evidence.checked_narrative_literal(candidate)
    if expected_scope is None:
        assert checked is None
    else:
        assert checked is not None and checked['narrative_scope'] == expected_scope


@pytest.mark.parametrize('current_header,year,period,allowed', [
    ('2023年1-3月', 2025, 'Q1', False),
    ('2025年1-6月', 2025, 'Q1', False),
    ('2025年度', 2025, 'Q1', False),
    ('2025年第三季度', 2025, 'Q1', False),
    ('2025年10-12月', 2025, 'Q1', False),
    ('2025年1-12月', 2025, 'Q1', False),
    ('2025年1-3月', 2025, 'Q1', True),
    ('2025年前三季度', 2025, 'Q3', True),
    ('2025年', 2025, 'Q1', True),
    ('本期发生额', 2025, 'Q1', True),
])
def test_explicit_current_column_period_must_match_candidate(monkeypatch, tmp_path, current_header, year, period, allowed):
    path = table_pdf(tmp_path, [REVENUE], headers=[
        '科目名称', current_header, '2022年1-3月', '变动率', '变动原因'])
    monkeypatch.setattr(evidence, 'source_pdf', lambda _: path)
    checked = evidence.checked_narrative_literal({**table_candidate(REVENUE), 'year': year, 'period': period})
    assert (checked is not None) == allowed


@pytest.mark.parametrize('requested_scope,original_scope,expected_calls', [
    ('consolidated', 'parent', 0),
    ('parent', 'consolidated', 0),
    ('parent', 'parent', 2),
    ('consolidated', 'consolidated', 2),
])
def test_goal_cannot_use_a_table_from_another_statement_scope(monkeypatch, requested_scope, original_scope, expected_calls):
    import asyncio
    import time
    from types import SimpleNamespace
    from src.agent.v3.agent import V3Agent
    from src.agent.v3.contracts import Conditions, DialogueState, Goal, Request
    from src.agent.v3.executor import ExecutionResult
    from src.agent.v3.model import Budget

    calls = []

    class Retriever:
        def retrieve(self, *args):
            return [snippet(' | '.join(REVENUE))]

    class Model:
        async def structured(self, schema, system, payload, budget):
            calls.append(schema)
            if schema is evidence.Explanations:
                return evidence.Explanations(claims=[dict(goal_id='cause', text='主要原因是产品销量增长。',
                    evidence_ids=payload['allowed_evidence_ids']['cause'])], incomplete_goals=[])
            return evidence.EvidenceReview(judgments=[dict(goal_id='cause', claim_index=0,
                supported=True, detail='同公司、期间和原文支持')])

    monkeypatch.setattr(evidence, 'checked_narrative_literal', lambda value: {
        **value, 'narrative_scope': original_scope, 'literal_verification': 'matched_original_pdf_page'})

    async def emit(*args):
        pass

    request = Request(turn_id='cause', question='解释样例股份营业收入变动原因',
        conditions=Conditions(codes=['123456'], metrics=['operating_revenue'], scope=requested_scope,
                              time=dict(mode='explicit', years=[2025], periods=['Q1'])),
        goals=[Goal(id='cause', kind='cause', text='解释营业收入变动原因')])
    agent = V3Agent(None, {'123456': '样例股份'}, model=Model(), retriever_factory=lambda _: Retriever())
    state = dict(request=request, executed=ExecutionResult(selections={'123456': [(2025, 'Q1')]}),
                 previous=DialogueState(), repository=SimpleNamespace(collection='index'),
                 emit=emit, budget=Budget(time.time() + 60))
    result = asyncio.run(agent._evidence(state))
    assert len(calls) == expected_calls
    assert result['executed'].goals[0].status == ('completed' if expected_calls else 'no_data')
