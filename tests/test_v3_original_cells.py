from src.etl.canonical_audit import original_row_matches,reported_growth_column,row_unit


def test_ocr_line_break_encoding_does_not_hide_an_original_percent_header():
    from src.etl.canonical_audit import grid
    from src.utils.original_cells import original_literal
    rows=grid(r'<table><tr><td>项目</td><td>本期</td><td>上年同期</td><td>本报告期\n比上年同期增减(%)</td></tr><tr><td>营业收入</td><td>100.00</td><td>80.00</td><td>25.00</td></tr></table>')
    assert reported_growth_column(rows[0],rows[1],1,2024,'HY')==3
    assert original_literal('本报告期\n比上年同期增减(%)',rows[0][3]) is not None
    assert rows[1][1:]==['100.00','80.00','25.00']


def test_annual_stock_rate_accepts_an_explicit_prior_year_end_tail_column():
    headers=['项目','2024年末','2023年末','本年末比上年末增减','2022年末']
    row=['总资产','110','100','10.00%','80']
    assert reported_growth_column(headers,row,1,2024,'FY')==3
    assert reported_growth_column([*headers[:-1],'2021年末'],row,1,2024,'FY') is None
from src.agent.v3.contracts import TimeSelection
from src.agent.v3.executor import resolve_periods
from tests.test_v3_contracts import request

def test_original_row_validation_rejects_ocr_column_swap_even_if_both_values_exist():
    text='营业收入\n1,234.56\n2,345.67\n营业成本\n100\n200'
    assert original_row_matches(text,['营业收入','1,234.56','2,345.67'],['项目','2024年','2023年'])
    assert not original_row_matches(text,['营业收入','2,345.67','1,234.56'],['项目','2024年','2023年'])
    assert original_row_matches('所有者权益（或股东权益）合计\n100\n200',['所有者权益（或股东权益）合计','100','200'],['项目','2024年','2023年'])


def test_missing_ocr_minus_cannot_match_a_negative_original_cell():
    from src.etl.canonical_audit import original_has_value
    from src.utils.original_cells import original_literal
    for negative in ('-1,234.50','−1,234.50','（1,234.50）','(1,234.50)'):
        assert not original_has_value(negative,'1,234.50')
    assert original_literal('本期：1,234.50 元','1234.50',numeric=True)=='1,234.50'
    assert not original_row_matches('净利润\n-1,234.50\n2,000.00',['净利润','1,234.50','2,000.00'],['项目','2024年','2023年'])
    assert original_row_matches('经营现金流\n-423,443,930.33\n-\n253,787,046.40',['经营现金流','-423,443,930.33','-253,787,046.40'],['项目','2023年','2022年'])

def test_growth_header_missing_span_never_labels_prior_asset_amount_as_percent():
    headers=['','本报告期末','上年度末','本报告期末比上年度末增减（%）','']
    row=['总资产','14,749,063,075.81','14,411,774,083.52','14,411,774,083.52','2.34']
    assert reported_growth_column(headers,row) is None
    assert reported_growth_column(['项目','本期','上年','本期同比增减（%）'],['营收','100','80','25.00'])==3


def test_annual_growth_before_older_year_needs_real_header_and_percent_unit():
    headers=['','2024年','2023年','本年比上年增减','2022年']
    row=['营业收入（元）','100','80','25.00%','70']
    assert reported_growth_column(headers,row,1,2024,'FY')==3
    assert reported_growth_column([*headers[:-1],''],row,1,2024,'FY') is None
    assert reported_growth_column(headers,[*row[:3],'25.00','70'],1,2024,'FY') is None


def test_reported_year_end_change_is_not_a_quarterly_yoy():
    from types import SimpleNamespace
    from src.utils.disclosed_rates import checked_disclosed_rate,disclosed_rate_basis
    header='本报告期末比上年度末增减（%）'
    assert disclosed_rate_basis(header,'Q1')=='vs_prior_year_end'
    assert disclosed_rate_basis(header,'FY')=='yoy'
    wrong=SimpleNamespace(metric='total_assets_reported_yoy',period='Q1',source=SimpleNamespace(column=header))
    assert not checked_disclosed_rate(wrong)
    wrong.metric='total_assets_reported_change_vs_year_end'
    assert checked_disclosed_rate(wrong)
    assert disclosed_rate_basis('年初至报告期末比上年同期增减（%）','Q3')=='yoy'


def test_q3_rate_selects_year_to_date_instead_of_single_quarter():
    headers=['项目','本报告期','本报告期比上年同期增减（%）','年初至报告期末','年初至报告期末比上年同期增减（%）']
    row=['营业收入','20','-10.00%','60','5.00%']
    assert reported_growth_column(headers,row,3,2024,'Q3')==4


def test_row_unit_requires_original_full_label_and_never_reuses_share_units():
    from decimal import Decimal
    original='营业收入（元）\n100\n归属于股东的净利润（万元）\n20'
    assert row_unit('营业收入（元）',original)==('元',Decimal(1))
    assert row_unit('归属于股东的净利润（万元）',original)==('万元',Decimal(10000))
    assert row_unit('营业收入（万元）',original) is None
    assert row_unit('每股收益（元/股）',original) is None


def test_statement_unit_comes_from_its_own_original_caption():
    from decimal import Decimal
    from src.etl.canonical_audit import statement_caption_unit
    original='单位：万元\n上一个报表\n合并现金流量表\n2024年1—3月\n单位：元 币种：人民币\n项目 附注 本期'
    assert statement_caption_unit(original,'合并现金流量表')==('元',Decimal(1))
    assert statement_caption_unit(original,'母公司现金流量表') is None

def test_explicit_period_pairs_do_not_expand_to_four_combinations():
    r=request(codes=['600085'],time={'mode':'explicit','pairs':[(2023,'FY'),(2024,'HY')]})
    selected,_=resolve_periods(r,[])
    assert selected=={'600085':[(2023,'FY'),(2024,'HY')]}

def test_six_review_dimensions_cannot_be_overruled_by_self_reported_satisfaction():
    from src.agent.v3.planner import SemanticReview
    review=SemanticReview(goal_requirements=[dict(kind='lookup',goal_ids=['g'],covered=True)],satisfied=True,clarification=[],planner_defects=[],companies_correct=False,
        metrics_and_scope_correct=True,time_correct=True,goals_correct=True,constraints_correct=True,presentation_correct=True)
    assert not review.accepted
    assert all(check.status!='pass' for check in review.checks if check.name=='semantic_companies_correct')


def test_a_source_requirement_is_not_covered_by_a_lookup_even_if_the_model_marks_it_true():
    from src.agent.v3.agent import V3Agent
    from src.agent.v3.planner import SemanticReview
    from src.agent.v3.contracts import Request,Conditions,Goal
    r=Request(turn_id='t',question='只告诉我原文页码',conditions=Conditions(),goals=[Goal(id='g',kind='lookup',text='原文页码')])
    audit=SemanticReview(goal_requirements=[dict(kind='quote',goal_ids=['g'],covered=True)],
        satisfied=True,clarification=[],planner_defects=[],companies_correct=True,metrics_and_scope_correct=True,
        time_correct=True,goals_correct=True,constraints_correct=True,presentation_correct=True)
    V3Agent._review_exact_bindings(r,audit)
    assert not audit.accepted and not audit.goals_correct and not audit.goal_requirements[0].covered
