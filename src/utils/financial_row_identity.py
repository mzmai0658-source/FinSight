"""作品说明：提取和来源核验共用财务单元格身份定义。"""
import re
from src.agent.v3.catalog import METRICS
from src.utils.disclosed_rates import disclosed_rate_basis


def compact(value):
    return re.sub(r'\s+','',value).replace('，',',').replace('－','-').replace('−','-')


def row_key(label):
    text=compact(label).replace('：',':')
    text=re.sub(r'^[一二三四五六七八九十\d]+[、.．]','',text)
    text=re.sub(r'^[（(][一二三四五六七八九十\d]+[）)]','',text)
    text=re.sub(r'^(?:其中|减|加)[:：]','',text)
    text=re.sub(r'[（(](?:亏损|净亏损|亏损总额|损失|收益|净损失|亏损以).*$', '',text)
    return re.sub(r'[（(](?:元/股|%|元|万元)[）)]$','',text)


ROW_METRICS={row_key(label):metric.id for metric in METRICS.values() for label in (metric.label,*metric.aliases)}
ROW_METRICS.update({
    '净利润':'net_profit','资产总计':'total_assets','负债合计':'total_liabilities',
    '所有者权益（或股东权益）合计':'total_equity','所有者权益(或股东权益)合计':'total_equity',
    '所有者权益合计':'total_equity','负债总额':'total_liabilities',
    '归属于母公司所有者的净利润':'attributable_net_profit',
    '归属于母公司股东的净利润':'attributable_net_profit',
    '归属于公司普通股股东的净利润':'attributable_net_profit',
    '期初现金及现金等价物余额':'cash_begin','期末现金及现金等价物余额':'cash_end',
    '现金及现金等价物净增加额':'net_cash_change',
    '扣除非经常性损益后的加权平均净资产收益率':'roe_weighted_deducted',
    '主营业务收入合计':'main_business_revenue','主营业务':'main_business_revenue',
})


def checked_row_identity(fact):
    if not fact.source:return False
    base=re.sub(r'_reported_(?:yoy|change_vs_year_end)$','',fact.metric)
    return ROW_METRICS.get(row_key(fact.source.row))==base


def checked_period_column(fact):
    """作品说明：报告披露增长率与基础金额属于不同列。"""
    if not fact.source:return False
    heading=compact(fact.source.column)
    if fact.metric.endswith('_reported_yoy'):
        return disclosed_rate_basis(heading,fact.period)=='yoy'
    if fact.metric.endswith('_reported_change_vs_year_end'):
        return disclosed_rate_basis(heading,fact.period)=='vs_prior_year_end'
    if any(word in heading for word in ('同比','增减','上年','上期','年初余额','期初余额')):return False
    years={int(value) for value in re.findall(r'(?<!\d)(20\d{2})(?!\d)',heading)}
    if years and years!={fact.year}:return False
    dates=re.findall(r'(?:20\d{2}年)?(\d{1,2})月(\d{1,2})日',heading)
    if dates and any((int(month),int(day))!={'FY':(12,31),'HY':(6,30),'Q1':(3,31),'Q3':(9,30)}[fact.period] for month,day in dates):return False
    if not heading:return False
    explicit_periods=set()
    for period,patterns in {'FY':('年度','全年','12月31日'),'HY':('半年度','上半年','1-6','1—6','6月30日'),
                           'Q1':('第一季度','一季度','1-3','1—3','3月31日'),'Q3':('前三季度','1-9','1—9','年初至报告期末','9月30日')}.items():
        if any(pattern in heading for pattern in patterns):explicit_periods.add(period)
    # 作品说明：半年度包含年度字样，解析时优先匹配更具体的半年度期间。
    if 'HY' in explicit_periods:explicit_periods.discard('FY')
    return not explicit_periods or explicit_periods=={fact.period}


def checked_scope_title(fact,title):
    """作品说明：来源表题必须明确绑定报表范围。"""
    text=compact(title)
    if fact.scope=='parent':return '母公司' in text and any(word in text for word in ('报表','利润表','资产负债表','现金流量表','注释','附注'))
    return '合并' in text and any(word in text for word in ('报表','利润表','资产负债表','现金流量表','注释','附注')) or '主要会计数据和财务指标' in text
