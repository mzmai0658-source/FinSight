"""作品说明：覆盖真实开发问题的回归，不访问模型或候选标准答案。"""
import pytest

from src.agent.fallback import extract_slots
from src.agent.orchestrator import _TurnState, _deterministic_tool_answer


def state_for(question, **values):
    state = _TurnState('real-regression', 1, question)
    state.sql_rows = [dict(stock_code='600080', stock_abbr='金花股份',
                           report_year=2022, report_period='FY', **values)]
    return state


@pytest.mark.parametrize('metric,field', [
    ('经营活动产生的现金流量净额', 'operating_cf_net_amount'),
    ('投资活动产生的现金流量净额', 'investing_cf_net_amount'),
    ('筹资活动产生的现金流量净额', 'financing_cf_net_amount'),
])
def test_full_cash_flow_names_do_not_fall_back_to_total_cash_flow(metric, field):
    assert extract_slots(f'金花股份2022年全年{metric}是多少？')['metric_field'] == field


def test_cash_flow_answer_preserves_report_period_and_unit():
    state = state_for('金花股份（600080）2022年全年的经营活动产生的现金流量净额是多少？请注明单位并给出原始财报证据。',
                      operating_cf_net_amount=5257.9)
    answer = _deterministic_tool_answer(state)
    assert '2022年全年' in answer
    assert '5,257.9万元' in answer


def test_incidental_retrieval_does_not_replace_requested_sql_fact():
    state = state_for('金花股份2022年全年的营业收入是多少？请给出原始财报证据。',
                      total_operating_revenue=10000, roe=5.5)
    state.rag_results = [{'content': '其他指标同比增长 25%，并不是本题所问。'}]
    answer = _deterministic_tool_answer(state)
    assert '10,000万元' in answer
    assert '25%' not in answer and '5.5' not in answer
    assert len(state.rag_results) == 1


@pytest.mark.parametrize('suffix', ['是多少？请解释变化原因。', '为什么增长？', '是多少？请逐字引用原文。'])
def test_explanations_and_verbatim_requests_cannot_be_satisfied_by_number_only(suffix):
    state = state_for('金花股份2022年全年的营业收入' + suffix, total_operating_revenue=10000)
    assert _deterministic_tool_answer(state) == ''


def test_two_metrics_in_single_row_must_both_be_rendered():
    state = state_for('金花股份2022年全年的营业收入和经营活动产生的现金流量净额分别是多少？',
                      total_operating_revenue=10000, operating_cf_net_amount=5257.9)
    answer = _deterministic_tool_answer(state)
    assert '| 公司 | 年份 | 报告期 | 指标 | 数值 |' in answer
    assert '| 金花股份 | 2022年 | 全年 | 营业收入 | 10,000万元 |' in answer
    assert '| 金花股份 | 2022年 | 全年 | 经营活动产生的现金流量净额 | 5,257.9万元 |' in answer


def test_small_verified_chart_also_lists_its_values_in_text():
    state = _TurnState('real-regression', 1, '葵花药业2022年至2024年营业收入分别是多少？')
    state.chart_data = [{
        'title': '葵花药业2022-2024年营业收入趋势',
        'x_data': ['2022年', '2023年', '2024年'],
        'y_data': [509451.13, 570028.67, 337704.77],
        'y_label': '营业收入（万元）',
    }]

    answer = _deterministic_tool_answer(state)

    assert '2022年 509,451.13万元' in answer
    assert '2023年 570,028.67万元' in answer
    assert '2024年 337,704.77万元' in answer
    assert '已生成图表' in answer


def test_missing_requested_metric_is_not_silently_omitted():
    state = state_for('金花股份2022年全年的营业收入和基本每股收益分别是多少？',
                      total_operating_revenue=10000)
    answer = _deterministic_tool_answer(state)
    assert '10,000万元' in answer
    assert '| 金花股份 | 2022年 | 全年 | 每股收益 | 未披露 |' in answer


def test_report_identity_cannot_be_relabelled_to_match_question():
    state = state_for('金花股份2023年上半年营业收入是多少？', total_operating_revenue=10000)
    answer = _deterministic_tool_answer(state)
    assert '10,000万元' not in answer
    assert '2023年半年报' in answer and '暂不提供' in answer


def test_parenthetical_adjustment_is_not_erased_from_metric_identity():
    from src.agent.facts import field_specs, metric_mentions
    assert '扣非' in field_specs()['roe_weighted_excl_non_recurring']['label']
    assert {m[2] for m in metric_mentions('加权平均净资产收益率是多少？')} == {'roe'}
    assert {m[2] for m in metric_mentions('加权平均净资产收益率（扣非）是多少？')} == {'roe_weighted_excl_non_recurring'}
    state = state_for('金花股份2022年全年加权平均净资产收益率是多少？', roe=2, roe_weighted_excl_non_recurring=.23)
    assert '为 2%' in _deterministic_tool_answer(state)
    from src.agent.verifier import verify_numbers
    assert verify_numbers('金花股份2022年全年加权平均净资产收益率为0.23%。', state.sql_rows,
                          question=state.original_question)['status'] == 'fail'
