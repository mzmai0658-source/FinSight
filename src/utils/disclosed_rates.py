"""作品说明：以比较基准区分报告披露的百分比。"""
import re


def disclosed_rate_basis(heading,period):
    text=re.sub(r'\s+','',heading)
    year_end=any(word in text for word in ('上年度末','上年末','上年年末'))
    same_period=any(word in text for word in ('同比','上年同期','去年同期'))
    if year_end and same_period and period!='FY':return None
    if year_end:return 'yoy' if period=='FY' else 'vs_prior_year_end'
    if same_period:return 'yoy'
    if period=='FY' and any(word in text for word in ('本年比上年','本年度比上年度','本期比上期')):return 'yoy'
    return None


def checked_disclosed_rate(fact):
    if fact.metric.endswith('_reported_yoy'):
        return bool(fact.source) and disclosed_rate_basis(fact.source.column,fact.period)=='yoy'
    if fact.metric.endswith('_reported_change_vs_year_end'):
        return bool(fact.source) and disclosed_rate_basis(fact.source.column,fact.period)=='vs_prior_year_end'
    return True
