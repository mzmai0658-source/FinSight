"""作品说明：建立财务指标身份目录，别名仅表达等价口径，防止用相近指标替换请求。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Scope = Literal['consolidated', 'parent']
Dimension = Literal['money', 'percent', 'per_share']


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    dimension: Dimension
    definition: str
    aliases: tuple[str, ...] = ()
    scopes: tuple[Scope, ...] = ('consolidated', 'parent')
    formula: tuple[str, ...] = ()
    legacy: tuple[tuple[str, str], ...] = ()

    @property
    def unit(self) -> str:
        return {'money': '元', 'percent': '%', 'per_share': '元/股'}[self.dimension]


def amount(id, label, *aliases, legacy=(), definition=None, scopes=('consolidated', 'parent')):
    return Metric(id, label, 'money', definition or label, tuple(aliases), scopes, legacy=legacy)


_METRICS = [
    amount('operating_revenue', '营业收入', '营收', '营业额', definition='利润表营业收入；不等同营业总收入或主营业务收入'),
    amount('total_operating_revenue', '营业总收入', definition='利润表营业总收入，可能还包含利息等收入', legacy=(('income_sheet','total_operating_revenue'),)),
    amount('main_business_revenue', '主营业务收入', definition='报告披露的主营业务收入，不以营业收入替代'),
    amount('net_profit', '净利润合计', '合并净利润', definition='利润表净利润，含归母与少数股东损益', legacy=(('income_sheet', 'net_profit'),)),
    amount('attributable_net_profit', '归母净利润', '归属于母公司所有者的净利润', '归属于母公司股东的净利润', '归属于上市公司股东的净利润', '归母', definition='合并净利润中归属于母公司股东的部分，不含少数股东损益；它包含合并范围内子公司的相应经营成果，不等于母公司单体净利润', scopes=('consolidated',), legacy=(('core_performance_indicators_sheet', 'net_profit_10k_yuan'),)),
    amount('deducted_attributable_net_profit', '扣非归母净利润', '扣非净利润', '扣非归母', '归属于上市公司股东的扣除非经常性损益的净利润', definition='从归母净利润中扣除按披露规则认定的非经常性损益后的净利润，不等于合并净利润或母公司单体净利润', scopes=('consolidated',), legacy=(('core_performance_indicators_sheet', 'net_profit_excl_non_recurring'),)),
    amount('operating_cost', '营业成本', legacy=(('income_sheet', 'operating_expense_cost_of_sales'),)),
    Metric('gross_profit', '毛利额', 'money', '相同公司、期间、范围、文件版本的营业收入减营业成本', ('毛利',), formula=('operating_revenue', 'operating_cost')),
    Metric('gross_margin', '毛利率', 'percent', '毛利额 / 营业收入 × 100，表示毛利占营业收入的比例，单位为%；毛利额是金额，毛利率是比率', ('销售毛利率',), formula=('gross_profit', 'operating_revenue'), legacy=(('core_performance_indicators_sheet', 'gross_profit_margin'),)),
    Metric('net_margin', '净利率', 'percent', '净利润合计 / 营业收入 × 100', ('销售净利率',), formula=('net_profit', 'operating_revenue'), legacy=(('core_performance_indicators_sheet', 'net_profit_margin'),)),
    Metric('eps_basic', '基本每股收益', 'per_share', '归属于普通股股东的净利润 / 发行在外普通股的加权平均股数；金额以元计、股数以股计，元除以股，因此单位为元/股', ('每股收益', 'EPS'), ('consolidated',), legacy=(('core_performance_indicators_sheet', 'eps'),)),
    Metric('eps_diluted', '稀释每股收益', 'per_share', '按稀释潜在普通股调整的每股收益', scopes=('consolidated',)),
    Metric('roe_weighted', '加权平均净资产收益率', 'percent', '归属于公司普通股股东的净利润 / 加权平均归属于普通股股东的净资产 × 100，反映股东资本的盈利水平；净资产变动按在报告期内存续时间加权，单位为%，不同于期末净资产口径', ('ROE', '净资产收益率'), ('consolidated',), legacy=(('core_performance_indicators_sheet', 'roe'),)),
    Metric('roe_weighted_deducted', '扣非加权平均净资产收益率', 'percent', '扣非归母净利润口径的加权平均净资产收益率', ('扣非ROE',), ('consolidated',), legacy=(('core_performance_indicators_sheet', 'roe_weighted_excl_non_recurring'),)),
    Metric('roe_diluted', '全面摊薄净资产收益率', 'percent', '期末归母净资产口径的净资产收益率', scopes=('consolidated',)),
]

_AMOUNTS = [
    ('total_assets', '总资产', 'balance_sheet', 'asset_total_assets', ('资产总额',)),
    ('total_liabilities', '总负债', 'balance_sheet', 'liability_total_liabilities', ('负债合计',)),
    ('total_equity', '股东权益合计', 'balance_sheet', 'equity_total_equity', ('净资产', '所有者权益合计')),
    ('cash', '货币资金', 'balance_sheet', 'asset_cash_and_cash_equivalents', ()),
    ('receivables', '应收账款', 'balance_sheet', 'asset_accounts_receivable', ()),
    ('inventory', '存货', 'balance_sheet', 'asset_inventory', ()),
    ('trading_assets', '交易性金融资产', 'balance_sheet', 'asset_trading_financial_assets', ()),
    ('construction', '在建工程', 'balance_sheet', 'asset_construction_in_progress', ()),
    ('payables', '应付账款', 'balance_sheet', 'liability_accounts_payable', ()),
    ('advances', '预收款项', 'balance_sheet', 'liability_advance_from_customers', ('预收账款',)),
    ('contract_liabilities', '合同负债', 'balance_sheet', 'liability_contract_liabilities', ()),
    ('short_loans', '短期借款', 'balance_sheet', 'liability_short_term_loans', ()),
    ('retained_earnings', '未分配利润', 'balance_sheet', 'equity_unappropriated_profit', ()),
    ('operating_profit', '营业利润', 'income_sheet', 'operating_profit', ()),
    ('total_profit', '利润总额', 'income_sheet', 'total_profit', ()),
    ('other_income', '其他收益', 'income_sheet', 'other_income', ()),
    ('selling_expense', '销售费用', 'income_sheet', 'operating_expense_selling_expenses', ()),
    ('administration_expense', '管理费用', 'income_sheet', 'operating_expense_administrative_expenses', ()),
    ('finance_expense', '财务费用', 'income_sheet', 'operating_expense_financial_expenses', ()),
    ('research_expense', '研发费用', 'income_sheet', 'operating_expense_rnd_expenses', ()),
    ('tax_surcharges', '税金及附加', 'income_sheet', 'operating_expense_taxes_and_surcharges', ()),
    ('total_operating_cost', '营业总成本', 'income_sheet', 'total_operating_expenses', ()),
    ('asset_impairment', '资产减值损失', 'income_sheet', 'asset_impairment_loss', ()),
    ('credit_impairment', '信用减值损失', 'income_sheet', 'credit_impairment_loss', ()),
    ('operating_cash_flow', '经营活动产生的现金流量净额', 'cash_flow_sheet', 'operating_cf_net_amount', ('经营现金流', '经营活动现金流')),
    ('investing_cash_flow', '投资活动产生的现金流量净额', 'cash_flow_sheet', 'investing_cf_net_amount', ('投资现金流',)),
    ('financing_cash_flow', '筹资活动产生的现金流量净额', 'cash_flow_sheet', 'financing_cf_net_amount', ('筹资现金流',)),
    ('net_cash_change', '现金及现金等价物净增加额', 'cash_flow_sheet', 'net_cash_flow', ('净现金流',)),
    ('sales_cash', '销售商品、提供劳务收到的现金', 'cash_flow_sheet', 'operating_cf_cash_from_sales', ()),
    ('investment_cash_paid', '投资支付的现金', 'cash_flow_sheet', 'investing_cf_cash_for_investments', ()),
    ('cash_begin', '期初现金及现金等价物余额', 'cash_flow_sheet', 'cash_beginning_balance', ()),
    ('cash_end', '期末现金及现金等价物余额', 'cash_flow_sheet', 'cash_ending_balance', ()),
]
for id, label, table, field, aliases in _AMOUNTS:
    _METRICS.append(amount(id, label, *aliases, legacy=((table, field),)))
_METRICS += [
    Metric('debt_ratio', '资产负债率', 'percent', '总负债 / 总资产 × 100', formula=('total_liabilities', 'total_assets'), legacy=(('balance_sheet', 'asset_liability_ratio'),)),
    Metric('net_assets_per_share', '每股净资产', 'per_share', '归母净资产 / 期末普通股股数', scopes=('consolidated',), legacy=(('core_performance_indicators_sheet', 'net_asset_per_share'),)),
    Metric('operating_cash_per_share', '每股经营现金流', 'per_share', '经营现金流净额 / 期末普通股股数', scopes=('consolidated',), legacy=(('core_performance_indicators_sheet', 'operating_cf_per_share'),)),
]
for base in ('operating_revenue', 'total_operating_revenue', 'attributable_net_profit', 'net_profit', 'deducted_attributable_net_profit', 'total_assets', 'total_liabilities', 'net_cash_change'):
    m = next(m for m in _METRICS if m.id == base)
    _METRICS.append(Metric(base + '_reported_yoy', m.label + '披露同比增长率', 'percent', '报告原文披露的同比；与程序计算的同比分开保存', scopes=m.scopes))
for base in ('total_assets','total_liabilities'):
    m=next(m for m in _METRICS if m.id==base)
    _METRICS.append(Metric(base+'_reported_change_vs_year_end',m.label+'披露较上年末变动率','percent',
        '本报告期末与上一年度末比较的报告披露变动率；不等同上年同期同比',scopes=m.scopes))

METRICS = {m.id: m for m in _METRICS}
ALIASES = {alias: m.id for m in _METRICS for alias in (m.id, m.label, *m.aliases)}
# 作品说明：指标家族包含多个不同成员；家族名称不能授权模型自行选取，需保留明确口径或询问用户。
AMBIGUOUS_TERMS = {'现金流': ('operating_cash_flow','investing_cash_flow','financing_cash_flow','net_cash_change')}
FAMILY_CHOICES = {'现金流': {'经营活动':'operating_cash_flow','投资活动':'investing_cash_flow','筹资活动':'financing_cash_flow','净增加额':'net_cash_change'}}
LEGACY = {key: m.id for m in _METRICS for key in m.legacy}
PRIMARY_FAMILIES = {m.id: {'income_sheet':'income', 'balance_sheet':'balance', 'cash_flow_sheet':'cashflow'}.get(m.legacy[0][0]) for m in _METRICS if m.legacy}
PRIMARY_FAMILIES.update(operating_revenue='income', total_operating_revenue='income',
    main_business_revenue='income', attributable_net_profit='income', eps_basic='income', eps_diluted='income')


def catalog_for_prompt() -> list[dict]:
    return [dict(id=m.id, name=m.label, unit=m.unit, scopes=m.scopes,
                 definition=m.definition, aliases=m.aliases) for m in _METRICS]


def metric_definition(metric,scope='consolidated'):
    item=METRICS[metric]
    if metric=='net_profit':
        return '母公司单体利润表的净利润；不直接合并子公司的收入和费用，但可能通过投资收益反映子公司分红等，不能替代合并归母净利润' if scope=='parent' else item.definition
    if metric=='net_margin' and scope=='parent':return '同期间母公司净利润 / 母公司营业收入 × 100'
    return item.definition
