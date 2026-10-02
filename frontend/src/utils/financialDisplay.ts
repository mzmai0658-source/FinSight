export const periods: Record<string, string> = { FY: '全年', HY: '上半年', Q1: '第一季度', Q3: '前三季度' };
const fields: Record<string, string> = {
  stock_code: '股票代码', stock_abbr: '公司', report_year: '年份', report_period: '报告期',
  roe: '普通加权平均净资产收益率', roe_weighted_excl_non_recurring: '扣非加权平均净资产收益率',
  eps: '基本每股收益', total_operating_revenue: '营业总收入（利润表）', net_profit: '净利润合计（利润表）',
  net_profit_10k_yuan: '归母净利润', operating_cf_net_amount: '经营活动现金流量净额',
  investing_cf_net_amount: '投资活动现金流量净额', financing_cf_net_amount: '筹资活动现金流量净额',
  net_cash_flow: '现金及现金等价物净增加额', total_assets: '资产总计', total_liabilities: '负债合计',
  gross_profit_margin: '毛利率', net_profit_margin: '净利率', asset_liability_ratio: '资产负债率',
  total_profit: '利润总额', operating_profit: '营业利润', net_profit_excl_non_recurring: '扣非归母净利润',
  operating_expense_cost_of_sales: '营业成本', operating_expense_selling_expenses: '销售费用',
  operating_expense_administrative_expenses: '管理费用', operating_expense_financial_expenses: '财务费用',
  operating_expense_rnd_expenses: '研发费用', asset_cash_and_cash_equivalents: '货币资金',
  asset_accounts_receivable: '应收账款', asset_inventory: '存货', asset_total_assets: '资产总计',
  liability_total_liabilities: '负债合计', equity_total_equity: '股东权益合计',
};
export function fieldLabel(field: string) { return fields[field] || '财务指标（名称待补充）'; }
export function reportPeriod(year: unknown, period?: string) { return `${year == null ? '年份未登记 · ' : `${year}年`}${periods[period || ''] || '报告期未登记'}`; }
export function displayCell(field: string, value: unknown) {
  if (value === null || value === undefined) return '暂无核实数据';
  if (field === 'report_period') return periods[String(value)] || String(value);
  return String(value);
}
export function fileName(path: unknown) { return String(path || '').split(/[\\/]/).pop() || '原件名称未登记'; }
