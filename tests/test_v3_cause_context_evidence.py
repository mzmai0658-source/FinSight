"""Cause contexts are original-PDF candidates, never a deterministic causal conclusion."""
import hashlib

import fitz
import pytest

from src.agent.v3 import cause_evidence as causes
from src.agent.v3 import evidence


METRIC = 'attributable_net_profit'
PARAGRAPH = ('报告期内，公司归属于上市公司股东的净利润同比下降。'
             '市场竞争加剧，产品价格走低。上述因素共同影响本期盈利水平。')


def snippet(text, **changes):
    return dict(id='chunk', text=text, stock_code='123456', company='样例股份',
                year=2025, period='FY', page=1, offset=0, chunk_index=0,
                source_path='data_root/source.pdf', document_version='digest', **changes)


def candidates(rows, metrics=(METRIC,)):
    return causes.cause_evidence_candidates(rows, metrics)


def physical_context(monkeypatch, tmp_path, lines, *, page_two=None):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    directory = tmp_path / 'data_root'
    directory.mkdir()
    path = directory / 'source.pdf'
    doc = canvas.Canvas(str(path), pagesize=(800, 600))
    for page_lines in (lines, page_two) if page_two is not None else (lines,):
        doc.setFont('STSong-Light', 10)
        for number, line in enumerate(page_lines):
            doc.drawString(30, 565 - number * 18, line)
        doc.showPage()
    doc.save()
    monkeypatch.setattr(evidence, 'ROOT', tmp_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with fitz.open(path) as document:
        original = document[0].get_text()
    return path, digest, original


def verified_context(monkeypatch, tmp_path, lines, text=PARAGRAPH, **changes):
    path, digest, original = physical_context(monkeypatch, tmp_path, lines)
    row = snippet(text)
    row.update(document_version=digest, **changes)
    found = candidates([row])
    assert found, 'The relevant context must survive candidate selection before source review'
    return evidence.checked_narrative_literal(found[0]), original


def test_cross_sentence_context_is_unreviewed_and_preserves_input_range():
    row = snippet(PARAGRAPH)
    assert not causes.bound_cause_snippets([row], {METRIC})
    found = candidates([row])
    assert len(found) == 1
    value = found[0]
    assert value['text'] == PARAGRAPH
    assert value['target_metric'] == METRIC
    assert value['cause_binding_type'] == 'context_candidate'
    assert value['support_status'] == 'unreviewed'
    assert value['evidence_purpose'] == 'cause_candidate'
    assert value['requires_direct_support_review'] is True
    assert value['source_ranges'] == [dict(chunk_id='chunk', start=0, end=len(PARAGRAPH))]


@pytest.mark.parametrize('text', [
    '盈利能力大幅提升：得益于市场需求持续回暖与降本增效，公司实现归属母公司净利润同比增长。',
    '公司归属于上市公司股东的净利润同比增长。公司持续改善产品结构，推进精益运营。',
])
def test_causal_word_order_and_no_causal_keyword_do_not_clear_context(text):
    found = candidates([snippet(text)])
    assert len(found) == 1 and found[0]['text'] == text
    assert found[0]['support_status'] == 'unreviewed'


def test_reason_for_another_metric_is_not_promoted_to_target_context():
    text = '扣非归母净利润增长，主要原因为营业收入增加和成本下降所致。'
    assert not candidates([snippet(text)], ['operating_revenue'])


def test_adjacent_same_page_chunks_retain_cross_chunk_context_and_provenance():
    first, second = PARAGRAPH.split('市场', 1)
    second = '市场' + second
    rows = [snippet(first), {**snippet(second), 'id': 'neighbor', 'offset': len(first), 'chunk_index': 1}]
    found = candidates(rows)
    assert len(found) == 1
    assert found[0]['text'] == PARAGRAPH
    assert found[0]['source_ranges'] == [dict(chunk_id='chunk', start=0, end=len(first)),
                                         dict(chunk_id='neighbor', start=0, end=len(second))]


@pytest.mark.parametrize('key,value', [
    ('stock_code', '654321'), ('year', 2024), ('period', 'HY'), ('page', 2),
    ('document_version', 'different-digest'), ('source_path', 'data_root/other.pdf'),
])
def test_adjacent_context_never_crosses_report_identity(key, value):
    first = PARAGRAPH.split('市场')[0]
    second = PARAGRAPH[len(first):]
    rows = [snippet(first), {**snippet(second), 'id': 'neighbor', 'offset': len(first),
                            'chunk_index': 1, key: value}]
    assert all('市场竞争' not in item['text'] for item in candidates(rows))


def test_prose_candidates_do_not_bypass_physical_table_row_verification():
    text = '归属于上市公司股东的净利润 | 12 | 10 | 20% | 主要是营业收入增加所致'
    found = candidates([snippet(text)])
    assert len(found) == 1
    assert found[0]['cause_table_cells'][-1] == '主要是营业收入增加所致'
    assert found[0]['cause_binding_type'] == 'explicit_relation_candidate'


def test_context_source_proof_has_original_range_and_preserves_all_sentences(monkeypatch, tmp_path):
    checked, original = verified_context(monkeypatch, tmp_path, [PARAGRAPH[:26], PARAGRAPH[26:]])
    assert checked is not None
    assert checked['literal_verification'] == 'matched_original_pdf_page'
    assert checked['narrative_scope'] == 'consolidated'
    proof = checked['original_context']
    assert proof['page'] == 1
    assert original[proof['start']:proof['end']] == checked['text']
    assert checked['support_status'] == 'unreviewed'


def test_context_anchor_recovery_delivers_native_pdf_wording_not_ocr(monkeypatch, tmp_path):
    original = PARAGRAPH.replace('产品价格走低', '销售价格承压')
    checked, page = verified_context(monkeypatch, tmp_path, [original])
    assert checked is not None
    assert '销售价格承压' in checked['text'] and '产品价格走低' not in checked['text']
    proof = checked['original_context']
    assert page[proof['start']:proof['end']] == checked['text']


def test_context_never_cuts_off_original_negation(monkeypatch, tmp_path):
    checked, original = verified_context(monkeypatch, tmp_path,
                                         ['前一段已经结束。', '并非' + PARAGRAPH])
    assert checked is not None
    assert checked['text'].startswith('并非')


@pytest.mark.parametrize('replace', [
    ('-321.45', '321.45'), ('321.45', '321.46'),
])
def test_context_relocation_cannot_change_quantity_or_sign(monkeypatch, tmp_path, replace):
    text = PARAGRAPH.replace('同比下降', '为-321.45万元，同比下降')
    original = text.replace(*replace)
    checked, _ = verified_context(monkeypatch, tmp_path, [original], text=text)
    assert checked is None


def test_duplicate_original_context_is_not_a_unique_source_range(monkeypatch, tmp_path):
    checked, _ = verified_context(monkeypatch, tmp_path, [PARAGRAPH, PARAGRAPH])
    assert checked is None


@pytest.mark.parametrize('caption,expected', [
    ('母公司利润表项目变动原因', 'parent'),
    ('本集团合并抵销前煤炭分部经营成果', None),
    ('主要控股、参股公司分析', None),
    ('主要子公司情况', None),
])
def test_original_prose_scope_is_physical_and_cannot_be_overridden_by_metadata(monkeypatch, tmp_path, caption, expected):
    checked, _ = verified_context(monkeypatch, tmp_path, [caption, PARAGRAPH], narrative_scope='consolidated')
    if expected is None:
        assert checked is None
    else:
        assert checked is not None and checked['narrative_scope'] == expected


def test_nearest_group_caption_resets_previous_parent_scope(monkeypatch, tmp_path):
    checked, _ = verified_context(monkeypatch, tmp_path,
                                 ['母公司利润表项目变动原因', '合并经营情况讨论与分析', PARAGRAPH])
    assert checked is not None and checked['narrative_scope'] == 'consolidated'


@pytest.mark.parametrize('prefix', ['2023年，', '2025年第一季度，'])
def test_original_context_with_explicit_conflicting_current_period_is_rejected(monkeypatch, tmp_path, prefix):
    text = prefix + PARAGRAPH
    checked, _ = verified_context(monkeypatch, tmp_path, [text], text=text)
    assert checked is None


def test_context_reviewer_receives_unreviewed_status_and_exact_source_proof():
    explanation = evidence.Explanations(claims=[dict(goal_id='cause', text='价格下降影响利润。', evidence_ids=['s'])],
                                        incomplete_goals=[])
    value = dict(id='s', text=PARAGRAPH, target_metric=METRIC, company='样例股份', year=2025, period='FY',
                 evidence_purpose='cause_candidate', cause_binding_type='context_candidate',
                 support_status='unreviewed', metric_relation='named_metric_context',
                 original_context=dict(page=1, start=10, end=100))
    _, payload = evidence.explanation_review_input(explanation, dict(goals=[dict(id='cause', kind='cause')],
         snippets=[value], allowed_evidence_ids={'cause': ['s']}))
    cited = payload['items'][0]['cited_evidence'][0]
    assert cited['support_status'] == 'unreviewed'
    assert cited['cause_binding_type'] == 'context_candidate'
    assert cited['original_context'] == value['original_context']


@pytest.mark.parametrize('text', [PARAGRAPH,
    '盈利能力大幅提升：得益于市场需求持续回暖与降本增效，公司实现归属母公司净利润同比增长。'])
def test_deterministic_retrieval_keeps_metric_context_without_exact_causal_pattern(text):
    class Embedding:
        def embed_query(self, _):
            return []

    class Collection:
        def query(self, **kwargs):
            return dict(ids=[[]], documents=[[]], metadatas=[[]], distances=[[]])

        def get(self, **kwargs):
            row = snippet(text)
            return dict(ids=[row.pop('id')], documents=[row.pop('text')], metadatas=[row])

    retriever = evidence.NarrativeRetriever.__new__(evidence.NarrativeRetriever)
    retriever.embedding = Embedding()
    retriever.collection = Collection()
    found = retriever.retrieve('解释样例股份2025年归母净利润变动原因', {'123456': [(2025, 'FY')]})
    assert len(found) == 1 and found[0]['text'] == text


def test_context_stops_at_last_complete_sentence_before_table():
    text = PARAGRAPH + '其中，分部数据如下：\n营业收入 | 100 | 90 | 无重大变化'
    found = candidates([snippet(text)])
    assert len(found) == 1 and found[0]['text'] == PARAGRAPH


@pytest.mark.parametrize('caption,scope', [
    ('母公司利润表项目变动原因', 'parent'),
    ('本集团合并抵销前铁路分部经营成果', None),
])
def test_original_scope_survives_long_previous_page(monkeypatch, tmp_path, caption, scope):
    lines = [caption] + ['其他背景资料' + '背景说明' * 16] * 30
    path, digest, _ = physical_context(monkeypatch, tmp_path, lines, page_two=[PARAGRAPH])
    with fitz.open(path) as document:
        assert len(document[0].get_text()) > 2000
    row = snippet(PARAGRAPH)
    row.update(document_version=digest, page=2)
    checked = evidence.checked_narrative_literal(candidates([row])[0])
    if scope is None:
        assert checked is None
    else:
        assert checked is not None and checked['narrative_scope'] == scope


def test_enumerated_parent_heading_is_still_a_scope_boundary(monkeypatch, tmp_path):
    checked, _ = verified_context(monkeypatch, tmp_path,
                                 ['（五）母公司利润表项目变动原因', PARAGRAPH])
    assert checked is not None and checked['narrative_scope'] == 'parent'


def test_complete_original_paragraph_does_not_pull_in_previous_numeric_table(monkeypatch, tmp_path):
    checked, _ = verified_context(monkeypatch, tmp_path,
                                 ['科目 2025年 2024年 2023年', '营业收入 120.00 100.00 90.00', PARAGRAPH])
    assert checked is not None and ''.join(checked['text'].split()) == PARAGRAPH


def test_adjacent_paragraph_supplies_cross_sentence_context_without_causal_keyword():
    first = '报告期内，公司归属于上市公司股东的净利润同比下降。'
    second = '市场竞争持续加剧，产品销售价格走低，企业盈利水平承压。'
    found = candidates([snippet(first + '\n\n' + second)])
    assert len(found) == 1
    assert found[0]['text'] == first + '\n\n' + second
    assert found[0]['support_status'] == 'unreviewed'


def test_context_never_merges_report_wide_result_with_neighboring_segment_reason():
    group = '报告期内，公司归属于上市公司股东的净利润同比下降，相关经营指标变动较大。'
    segment = '煤炭分部经营成果\n本分部净利润下降，主要是煤炭销售价格下降所致。'
    found = candidates([snippet(group + '\n\n' + segment)])
    assert all('煤炭销售价格' not in row['text'] or '相关经营指标' not in row['text'] for row in found)


def test_cause_review_keeps_requested_target_and_base_year_context():
    question = '解释样例股份2025年归母净利润相较2024年变化的原因。'
    goals = [dict(id='cause', kind='cause', text=question)]
    facts = [dict(id='f', metric=METRIC, company='样例股份', year=2025, period='FY', scope='consolidated')]
    value = dict(id='old', text='2024年，产品结构改善，归母净利润同比增长。', company='样例股份',
                 year=2024, period='FY', target_metric=METRIC)
    explanation = evidence.Explanations(claims=[dict(goal_id='cause', text='产品结构改善促进利润增长。',
         evidence_ids=['old'])], incomplete_goals=[])
    _, payload = evidence.explanation_review_input(explanation, dict(question=question, goals=goals,
        verified_facts=facts, snippets=[value], allowed_evidence_ids={'cause': ['old']}))
    assert payload['question'] == question
    assert payload['goals'] == goals
    assert payload['verified_facts'] == facts


EXPLICIT_REVENUE = '营业收入同比下降，主要是产品销售数量减少和销售价格下降所致。'


@pytest.mark.parametrize('caption,expected_scope', [
    ('母公司利润表项目变动原因', 'parent'),
    ('本集团合并抵销前煤炭分部经营成果', None),
    ('主要子公司情况', None),
])
def test_explicit_prose_candidates_share_physical_scope_checks(monkeypatch, tmp_path, caption, expected_scope):
    path, digest, original = physical_context(monkeypatch, tmp_path, [caption, EXPLICIT_REVENUE])
    row = snippet(EXPLICIT_REVENUE)
    row.update(document_version=digest, narrative_scope='consolidated')
    found = candidates([row], ['operating_revenue'])
    assert found and found[0]['cause_binding_type'] == 'explicit_relation_candidate'
    checked = evidence.checked_narrative_literal(found[0])
    if expected_scope is None:
        assert checked is None
    else:
        assert checked is not None and checked['narrative_scope'] == expected_scope
        proof = checked['original_context']
        assert original[proof['start']:proof['end']] == checked['text']


@pytest.mark.parametrize('prefix', ['2023年，', '2025年第一季度，'])
def test_explicit_prose_candidates_share_original_period_checks(monkeypatch, tmp_path, prefix):
    text = EXPLICIT_REVENUE.replace('同比下降', '于' + prefix.rstrip('，') + '同比下降')
    path, digest, original = physical_context(monkeypatch, tmp_path, [text])
    row = snippet(text)
    row.update(document_version=digest)
    found = candidates([row], ['operating_revenue'])
    assert found and found[0]['cause_binding_type'] == 'explicit_relation_candidate'
    assert evidence.checked_narrative_literal(found[0]) is None


def test_explicit_prose_candidates_share_unique_original_range_checks(monkeypatch, tmp_path):
    path, digest, original = physical_context(monkeypatch, tmp_path, [EXPLICIT_REVENUE, EXPLICIT_REVENUE])
    row = snippet(EXPLICIT_REVENUE)
    row.update(document_version=digest)
    found = candidates([row], ['operating_revenue'])
    assert found and found[0]['cause_binding_type'] == 'explicit_relation_candidate'
    assert evidence.checked_narrative_literal(found[0]) is None


@pytest.mark.parametrize('headings', [
    ['母公司利润表项目变动原因'],
    ['本集团合并抵销前煤炭分部经营成果'],
    ['本集团合并抵销前煤炭分部经营成果', '合并经营情况讨论与分析'],
])
def test_original_range_rejects_later_scope_boundary_missing_from_ocr(monkeypatch, tmp_path, headings):
    first = PARAGRAPH.split('市场')[0]
    rest = PARAGRAPH[len(first):]
    checked, _ = verified_context(monkeypatch, tmp_path, [first, *headings, rest])
    assert checked is None


def test_explicit_reason_heading_keeps_its_proven_boundary_after_vertical_numeric_table(monkeypatch, tmp_path):
    text = ('营业收入变动原因说明\n2025年本集团营业收入同比下降，主要原因是：\n'
            '受市场供需形势影响，产品销售数量和平均价格下降。')
    lines = ['营业成本', '12,000', '10,000', '20.0', '（1）' + text.splitlines()[0], *text.splitlines()[1:]]
    path, digest, original = physical_context(monkeypatch, tmp_path, lines)
    row = snippet(text)
    row.update(document_version=digest)
    found = candidates([row], ['operating_revenue'])
    assert found[0]['cause_binding_type'] == 'explicit_relation_candidate'
    checked = evidence.checked_narrative_literal(found[0])
    assert checked is not None
    assert checked['text'] == text
    proof = checked['original_context']
    assert original[proof['start']:proof['end']] == text
