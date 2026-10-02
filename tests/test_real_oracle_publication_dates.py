import pytest
from eval.real.oracle import expected_values_supported

FACT = {'stock_code': '600085', 'stock_abbr': '同仁堂', 'report_year': 2023,
        'report_period': 'FY', 'field': 'total_operating_revenue', 'metric_label': '营业收入',
        'value': 12345.67, 'unit': '万元', 'tolerance': .01}
ANSWER = '同仁堂2023年全年营业收入为12,345.67万元。'

@pytest.mark.parametrize('verb', ['发布', '披露', '公告'])
def test_explicit_publication_date_does_not_change_report_period(verb):
    assert expected_values_supported(ANSWER + f'数据来源：2024年3月28日{verb}的年度报告。', [FACT])

def test_wrong_financial_year_is_not_hidden_by_correct_publication_date():
    assert not expected_values_supported('同仁堂2022年全年营业收入为12,345.67万元。数据来源：2023年3月28日发布的年度报告。', [FACT])

def test_publication_date_cannot_supply_missing_financial_year():
    assert not expected_values_supported('同仁堂2023年3月28日发布的年报：营业收入为12,345.67万元。', [FACT])

def test_unlabelled_date_still_requires_scope_review():
    assert not expected_values_supported(ANSWER + '2024年3月28日营业收入仍为12,345.67万元。', [FACT])

def test_publication_date_fix_does_not_allow_additional_wrong_amount():
    assert not expected_values_supported(ANSWER + '数据来源：2024年3月28日发布的年度报告。营业收入为90万元。', [FACT])
