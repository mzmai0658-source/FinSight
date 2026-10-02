"""作品说明：统一定义图表能力边界与限制。"""
MIN_VALID_POINTS = 2

CAPABILITIES = {
    'trend': {'minimum_valid_points': MIN_VALID_POINTS, 'same_company_metric_period_unit': True},
    'comparison': {'minimum_valid_points': MIN_VALID_POINTS, 'same_report_metric_unit': True},
    'missing_points': '允许缺失报告期，图上保留空位，不填零、不连接跨缺失期折线；不要求所有报告期连续且完整',
    'different_units': '不同指标或单位分开画图；部分指标不足时，展示能画的部分',
    'numbers': '只使用本轮已核对事实，模型不能指定数值或生成图点',
    'latest_reports': '最近/最新几份年报先查已入库范围，显示实际年份，不保证库中有日历上的最新年报',
}
