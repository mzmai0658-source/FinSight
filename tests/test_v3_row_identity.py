from types import SimpleNamespace
from src.utils.financial_row_identity import checked_row_identity,checked_period_column,checked_scope_title


def fact(metric,row='营业收入',column='2024年度',period='FY',scope='consolidated'):
    return SimpleNamespace(metric=metric,source=SimpleNamespace(row=row,column=column),year=2024,period=period,scope=scope)


def test_real_numeric_cell_cannot_support_a_different_metric_identity():
    assert checked_row_identity(fact('operating_revenue','一、营业收入'))
    assert not checked_row_identity(fact('main_business_revenue','一、营业收入'))
    assert not checked_row_identity(fact('attributable_net_profit','五、净利润（净亏损以“－”号填列）'))
    assert checked_row_identity(fact('net_profit','五、净利润（净亏损以“－”号填列）'))
    assert checked_row_identity(fact('attributable_net_profit','1.归属于母公司股东的净利润'))


def test_current_amount_cannot_bind_prior_year_growth_or_another_cumulative_period():
    for heading in ['2023年度','本年比上年增减（%）','2024年半年度','2024年6月30日']:
        assert not checked_period_column(fact('operating_revenue',column=heading))
    assert checked_period_column(fact('operating_revenue',column='2024年半年度',period='HY'))
    assert checked_period_column(fact('operating_revenue',column='2024年前三季度（1-9月）',period='Q3'))
    assert checked_period_column(fact('total_assets_reported_yoy',row='总资产（元）',column='本年末比上年末增减'))
    assert not checked_period_column(fact('total_assets_reported_yoy',row='总资产（元）',column='本报告期末比上年度末增减',period='Q3'))


def test_parent_and_consolidated_statement_captions_are_not_interchangeable():
    assert checked_scope_title(fact('net_profit',scope='parent'),'母公司利润表')
    assert not checked_scope_title(fact('net_profit',scope='parent'),'合并利润表')
    assert checked_scope_title(fact('main_business_revenue'),'合并财务报表项目注释')
    assert not checked_scope_title(fact('main_business_revenue'),'母公司财务报表附注')
