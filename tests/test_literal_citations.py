import pytest

from src.agent.citations import literal_passage, requested_section, section_matches
from src.agent.facts import document_identity
from src.agent.verifier import verify_turn


def test_section_constraint_ignores_cross_references_and_other_sections():
    question = '摘引一段“经营情况讨论与分析”的原文'
    section = requested_section(question)
    assert section == '经营情况讨论与分析'
    assert section_matches(section, '正文', {'section_title': '一、经营情况讨论与分析'})
    assert section_matches(section, '页眉\n一、经营情况讨论与分析\n报告期内公司推进研发。', {})
    assert not section_matches(section, '详见经营情况讨论与分析，本节为分部信息。', {'section_title': '分部信息'})


def test_literal_passage_preserves_words_and_numbers():
    source = '2023 年年度报告\n一、经营情况讨论与分析\n营业收入为 100 万元。\n公司推进研发。'
    assert literal_passage(source, '经营情况讨论与分析') == '营业收入为 100 万元。 公司推进研发。'


def citation_case():
    ref = document_identity({'stock_code': '600080', 'report_year': 2023, 'report_period': 'FY',
                             'page_start': 9, 'source_sha256': 'original-hash', 'source_title': '年报',
                             'paper_path': 'report.pdf', 'text': '营业收入为 100 万元。公司推进研发。'})
    answer = '原文摘引【1】（600080，2023年全年，PDF 第 9 页）：\n\n> ' + ref['text'] + '\n\n简要说明：公司推进研发。'
    return ref, answer


def test_exact_quote_is_checked_against_source_not_misclassified_as_calculation():
    ref, answer = citation_case()
    result = verify_turn(answer, [], [ref], [ref], [], question='金花股份6000802023年全年请摘引原文')
    assert next(c for c in result['checks'] if c['name'] == 'literal_quotes')['status'] == 'pass'
    assert next(c for c in result['checks'] if c['name'] == 'numbers_grounded')['status'] == 'pass'
    assert result['status'] == 'warn'  # 作品说明：经营解释仍需人工审阅。


@pytest.mark.parametrize('before,after', [('100', '999'), ('第 9 页', '第 10 页'), ('2023年', '2022年'), ('【1】', '【2】')])
def test_changed_quote_or_identity_is_rejected(before, after):
    ref, answer = citation_case()
    result = verify_turn(answer.replace(before, after), [], [ref], [ref], [], question='6000802023年全年摘引原文')
    assert result['status'] == 'fail'


def test_quote_cannot_authorize_unverified_number_in_summary():
    ref, answer = citation_case()
    result = verify_turn(answer + '营业收入为999万元。', [], [ref], [ref], [], question='6000802023年全年摘引原文')
    check = next(c for c in result['checks'] if c['name'] == 'numbers_grounded')
    assert check['unmatched'] or check['unverified']


def test_changed_source_hash_fails_reference_verification():
    ref, answer = citation_case()
    result = verify_turn(answer, [], [{**ref, 'source_sha256': 'other'}], [ref], [], question='6000802023年全年摘引原文')
    assert result['status'] == 'fail'
