-- Original fictional fixture; Apache-2.0. Not real company disclosures.

CREATE DATABASE IF NOT EXISTS `finsight_demo` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE `finsight_demo`;

DROP TABLE IF EXISTS `balance_sheet`;

CREATE TABLE balance_sheet (
	serial_number INTEGER NOT NULL COMMENT '序号' AUTO_INCREMENT,
	stock_code VARCHAR(20) NOT NULL COMMENT '股票代码',
	stock_abbr VARCHAR(50) COMMENT '股票简称',
	asset_cash_and_cash_equivalents DECIMAL(20, 2) COMMENT '资产-货币资金(万元)',
	asset_accounts_receivable DECIMAL(20, 2) COMMENT '资产-应收账款(万元)',
	asset_inventory DECIMAL(20, 2) COMMENT '资产-存货(万元)',
	asset_trading_financial_assets DECIMAL(20, 2) COMMENT '资产-交易性金融资产（万元）',
	asset_construction_in_progress DECIMAL(20, 2) COMMENT '资产-在建工程（万元）',
	asset_total_assets DECIMAL(20, 2) COMMENT '资产-总资产(万元)',
	asset_total_assets_yoy_growth DECIMAL(10, 4) COMMENT '资产-总资产同比(%%)',
	liability_accounts_payable DECIMAL(20, 2) COMMENT '负债-应付账款(万元)',
	liability_advance_from_customers DECIMAL(20, 2) COMMENT '负债-预收账款(万元)',
	liability_total_liabilities DECIMAL(20, 2) COMMENT '负债-总负债(万元)',
	liability_total_liabilities_yoy_growth DECIMAL(10, 4) COMMENT '负债-总负债同比(%%)',
	liability_contract_liabilities DECIMAL(20, 2) COMMENT '负债-合同负债（万元）',
	liability_short_term_loans DECIMAL(20, 2) COMMENT '负债-短期借款（万元）',
	asset_liability_ratio DECIMAL(10, 4) COMMENT '资产负债率(%%)',
	equity_unappropriated_profit DECIMAL(20, 2) COMMENT '股东权益-未分配利润（万元）',
	equity_total_equity DECIMAL(20, 2) COMMENT '股东权益合计(万元)',
	report_period VARCHAR(20) COMMENT '报告期',
	report_year INTEGER COMMENT '报告期-年份',
	created_at DATETIME COMMENT '创建时间',
	updated_at DATETIME COMMENT '更新时间',
	PRIMARY KEY (serial_number),
	CONSTRAINT uq_balance_stock_year_period UNIQUE (stock_code, report_year, report_period)
)COMMENT='资产负债表';

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990001', '星河医药', 2022, 'FY', 20000, 10000);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990001', '星河医药', 2023, 'FY', 24000, 12000);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990001', '星河医药', 2024, 'FY', 30000, 15000);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990002', '晨光医疗', 2022, 'FY', 36000, 18000);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990002', '晨光医疗', 2023, 'FY', 32000, 16000);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990002', '晨光医疗', 2024, 'FY', 28000, 14000);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990003', '青禾生物', 2022, 'FY', 16000, 8000);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990003', '青禾生物', 2023, 'FY', 16000, 8000);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990003', '青禾生物', 2024, 'FY', 18400, 9200);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990004', '远山诊断', 2022, 'FY', 50000, 25000);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990004', '远山诊断', 2023, 'FY', 42000, 21000);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990004', '远山诊断', 2024, 'FY', 44000, 22000);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990005', '云杉科技', 2022, 'FY', 12000, 6000);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990005', '云杉科技', 2023, 'FY', 15000, 7500);

INSERT INTO `balance_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `asset_total_assets`, `liability_total_liabilities`) VALUES ('990005', '云杉科技', 2024, 'FY', 18000, 9000);

DROP TABLE IF EXISTS `cash_flow_sheet`;

CREATE TABLE cash_flow_sheet (
	serial_number INTEGER NOT NULL COMMENT '序号' AUTO_INCREMENT,
	stock_code VARCHAR(20) NOT NULL COMMENT '股票代码',
	stock_abbr VARCHAR(50) COMMENT '股票简称',
	net_cash_flow DECIMAL(20, 2) COMMENT '净现金流(元)',
	net_cash_flow_yoy_growth DECIMAL(10, 4) COMMENT '净现金流-同比增长(%%)',
	operating_cf_net_amount DECIMAL(20, 2) COMMENT '经营性现金流-现金流量净额(万元)',
	operating_cf_ratio_of_net_cf DECIMAL(10, 4) COMMENT '经营性现金流-净现金流占比(%%)',
	operating_cf_cash_from_sales DECIMAL(20, 2) COMMENT '经营性现金流-销售商品收到的现金（万元）',
	investing_cf_net_amount DECIMAL(20, 2) COMMENT '投资性现金流-现金流量净额(万元)',
	investing_cf_ratio_of_net_cf DECIMAL(10, 4) COMMENT '投资性现金流-净现金流占比(%%)',
	investing_cf_cash_for_investments DECIMAL(20, 2) COMMENT '投资性现金流-投资支付的现金（万元）',
	investing_cf_cash_from_investment_recovery DECIMAL(20, 2) COMMENT '投资性现金流-收回投资收到的现金（万元）',
	financing_cf_cash_from_borrowing DECIMAL(20, 2) COMMENT '融资性现金流-取得借款收到的现金（万元）',
	financing_cf_cash_for_debt_repayment DECIMAL(20, 2) COMMENT '融资性现金流-偿还债务支付的现金（万元）',
	financing_cf_net_amount DECIMAL(20, 2) COMMENT '融资性现金流-现金流量净额(万元)',
	financing_cf_ratio_of_net_cf DECIMAL(10, 4) COMMENT '融资性现金流-净现金流占比(%%)',
	report_period VARCHAR(20) COMMENT '报告期',
	report_year INTEGER COMMENT '报告期-年份',
	created_at DATETIME COMMENT '创建时间',
	updated_at DATETIME COMMENT '更新时间',
	PRIMARY KEY (serial_number),
	CONSTRAINT uq_cashflow_stock_year_period UNIQUE (stock_code, report_year, report_period)
)COMMENT='现金流量表';

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990001', '星河医药', 2022, 'FY', 1200);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990001', '星河医药', 2023, 'FY', 1400);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990001', '星河医药', 2024, 'FY', 2000);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990002', '晨光医疗', 2022, 'FY', 2000);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990002', '晨光医疗', 2023, 'FY', 1000);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990002', '晨光医疗', 2024, 'FY', 0);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990003', '青禾生物', 2022, 'FY', 600);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990003', '青禾生物', 2023, 'FY', 200);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990003', '青禾生物', 2024, 'FY', 660);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990004', '远山诊断', 2022, 'FY', 2700);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990004', '远山诊断', 2023, 'FY', -800);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990004', '远山诊断', 2024, 'FY', 1300);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990005', '云杉科技', 2022, 'FY', 500);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990005', '云杉科技', 2023, 'FY', 800);

INSERT INTO `cash_flow_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `operating_cf_net_amount`) VALUES ('990005', '云杉科技', 2024, 'FY', 1100);

DROP TABLE IF EXISTS `core_performance_indicators_sheet`;

CREATE TABLE core_performance_indicators_sheet (
	serial_number INTEGER NOT NULL COMMENT '序号' AUTO_INCREMENT,
	stock_code VARCHAR(20) NOT NULL COMMENT '股票代码',
	stock_abbr VARCHAR(50) COMMENT '股票简称',
	eps DECIMAL(10, 4) COMMENT '每股收益(元)',
	total_operating_revenue DECIMAL(20, 2) COMMENT '营业总收入(万元)',
	operating_revenue_yoy_growth DECIMAL(10, 4) COMMENT '营业总收入-同比增长(%%)',
	operating_revenue_qoq_growth DECIMAL(10, 4) COMMENT '营业总收入-季度环比增长(%%)',
	net_profit_10k_yuan DECIMAL(20, 2) COMMENT '净利润(万元)',
	net_profit_yoy_growth DECIMAL(10, 4) COMMENT '净利润-同比增长(%%)',
	net_profit_qoq_growth DECIMAL(10, 4) COMMENT '净利润-季度环比增长(%%)',
	net_asset_per_share DECIMAL(10, 4) COMMENT '每股净资产(元)',
	roe DECIMAL(10, 4) COMMENT '净资产收益率(%%)',
	operating_cf_per_share DECIMAL(10, 4) COMMENT '每股经营现金流量(元)',
	net_profit_excl_non_recurring DECIMAL(20, 2) COMMENT '扣非净利润（万元）',
	net_profit_excl_non_recurring_yoy DECIMAL(10, 4) COMMENT '扣非净利润同比增长（%%）',
	gross_profit_margin DECIMAL(10, 4) COMMENT '销售毛利率(%%)',
	net_profit_margin DECIMAL(10, 4) COMMENT '销售净利率（%%）',
	roe_weighted_excl_non_recurring DECIMAL(10, 4) COMMENT '加权平均净资产收益率（扣非）（%%）',
	report_period VARCHAR(20) COMMENT '报告期',
	report_year INTEGER COMMENT '报告期-年份',
	created_at DATETIME COMMENT '创建时间',
	updated_at DATETIME COMMENT '更新时间',
	PRIMARY KEY (serial_number),
	CONSTRAINT uq_core_stock_year_period UNIQUE (stock_code, report_year, report_period)
)COMMENT='核心业绩指标表';

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`) VALUES ('990001', '星河医药', 2022, 'FY', 1000, 5, 1.0, 30, 10000);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990001', '星河医药', 2023, 'FY', 1200, 6, 1.2, 31, 12000, 20.0, 20.0);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990001', '星河医药', 2024, 'FY', 1800, 8, 1.8, 32, 15000, 25.0, 50.0);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`) VALUES ('990002', '晨光医疗', 2022, 'FY', 1800, 6, 1.8, 31, 18000);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990002', '晨光医疗', 2023, 'FY', 800, 7, 0.8, 32, 16000, -11.1111, -55.5556);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990002', '晨光医疗', 2024, 'FY', -200, 9, -0.2, 33, 14000, -12.5, -125.0);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`) VALUES ('990003', '青禾生物', 2022, 'FY', 400, 7, 0.4, 32, 8000);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990003', '青禾生物', 2023, 'FY', 0, 8, 0.0, 33, 8000, 0.0, -100.0);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`, `operating_revenue_yoy_growth`) VALUES ('990003', '青禾生物', 2024, 'FY', 460, 10, 0.46, 34, 9200, 15.0);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`) VALUES ('990004', '远山诊断', 2022, 'FY', 2500, 8, 2.5, 33, 25000);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990004', '远山诊断', 2023, 'FY', -1000, 9, -1.0, 34, 21000, -16.0, -140.0);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990004', '远山诊断', 2024, 'FY', 1100, 11, 1.1, 35, 22000, 4.7619, 210.0);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`) VALUES ('990005', '云杉科技', 2022, 'FY', 300, 9, 0.3, 34, 6000);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990005', '云杉科技', 2023, 'FY', 600, 10, 0.6, 35, 7500, 25.0, 100.0);

INSERT INTO `core_performance_indicators_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `net_profit_10k_yuan`, `roe`, `eps`, `gross_profit_margin`, `total_operating_revenue`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990005', '云杉科技', 2024, 'FY', 900, 12, 0.9, 36, 9000, 20.0, 50.0);

DROP TABLE IF EXISTS `income_sheet`;

CREATE TABLE income_sheet (
	serial_number INTEGER NOT NULL COMMENT '序号' AUTO_INCREMENT,
	stock_code VARCHAR(20) NOT NULL COMMENT '股票代码',
	stock_abbr VARCHAR(50) COMMENT '股票简称',
	net_profit DECIMAL(20, 2) COMMENT '净利润(万元)',
	net_profit_yoy_growth DECIMAL(10, 4) COMMENT '净利润同比(%%)',
	other_income DECIMAL(20, 2) COMMENT '其他收益（万元）',
	total_operating_revenue DECIMAL(20, 2) COMMENT '营业总收入(万元)',
	operating_revenue_yoy_growth DECIMAL(10, 4) COMMENT '营业总收入同比(%%)',
	operating_expense_cost_of_sales DECIMAL(20, 2) COMMENT '营业总支出-营业支出(万元)',
	operating_expense_selling_expenses DECIMAL(20, 2) COMMENT '营业总支出-销售费用(万元)',
	operating_expense_administrative_expenses DECIMAL(20, 2) COMMENT '营业总支出-管理费用(万元)',
	operating_expense_financial_expenses DECIMAL(20, 2) COMMENT '营业总支出-财务费用(万元)',
	operating_expense_rnd_expenses DECIMAL(20, 2) COMMENT '营业总支出-研发费用（万元）',
	operating_expense_taxes_and_surcharges DECIMAL(20, 2) COMMENT '营业总支出-税金及附加（万元）',
	total_operating_expenses DECIMAL(20, 2) COMMENT '营业总支出(万元)',
	operating_profit DECIMAL(20, 2) COMMENT '营业利润(万元)',
	total_profit DECIMAL(20, 2) COMMENT '利润总额(万元)',
	asset_impairment_loss DECIMAL(20, 2) COMMENT '资产减值损失（万元）',
	credit_impairment_loss DECIMAL(20, 2) COMMENT '信用减值损失（万元）',
	report_period VARCHAR(20) COMMENT '报告期',
	report_year INTEGER COMMENT '报告期-年份',
	created_at DATETIME COMMENT '创建时间',
	updated_at DATETIME COMMENT '更新时间',
	PRIMARY KEY (serial_number),
	CONSTRAINT uq_income_stock_year_period UNIQUE (stock_code, report_year, report_period)
)COMMENT='利润表';

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`) VALUES ('990001', '星河医药', 2022, 'FY', 10000, 1000);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990001', '星河医药', 2023, 'FY', 12000, 1200, 20.0, 20.0);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990001', '星河医药', 2024, 'FY', 15000, 1800, 25.0, 50.0);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`) VALUES ('990002', '晨光医疗', 2022, 'FY', 18000, 1800);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990002', '晨光医疗', 2023, 'FY', 16000, 800, -11.1111, -55.5556);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990002', '晨光医疗', 2024, 'FY', 14000, -200, -12.5, -125.0);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`) VALUES ('990003', '青禾生物', 2022, 'FY', 8000, 400);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990003', '青禾生物', 2023, 'FY', 8000, 0, 0.0, -100.0);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`, `operating_revenue_yoy_growth`) VALUES ('990003', '青禾生物', 2024, 'FY', 9200, 460, 15.0);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`) VALUES ('990004', '远山诊断', 2022, 'FY', 25000, 2500);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990004', '远山诊断', 2023, 'FY', 21000, -1000, -16.0, -140.0);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990004', '远山诊断', 2024, 'FY', 22000, 1100, 4.7619, 210.0);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`) VALUES ('990005', '云杉科技', 2022, 'FY', 6000, 300);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990005', '云杉科技', 2023, 'FY', 7500, 600, 25.0, 100.0);

INSERT INTO `income_sheet` (`stock_code`, `stock_abbr`, `report_year`, `report_period`, `total_operating_revenue`, `net_profit`, `operating_revenue_yoy_growth`, `net_profit_yoy_growth`) VALUES ('990005', '云杉科技', 2024, 'FY', 9000, 900, 20.0, 50.0);
