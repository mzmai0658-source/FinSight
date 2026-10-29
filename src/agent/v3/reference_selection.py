"""从真实保存并重新核验的引用中，按当前目标条件选择事实。"""

def fact_matches(conditions,fact):
    c=conditions;t=c.time
    return (bool(c.codes) and fact.stock_code in c.codes and bool(c.metrics) and fact.metric in c.metrics
        and fact.scope==c.scope and fact.stock_code not in c.restrictions.excluded_codes
        and fact.metric not in c.restrictions.excluded_metrics
        and ((fact.year,fact.period) in t.pairs if t.pairs else
             fact.period in t.periods and (not t.years or fact.year in t.years)))

def computed_matches(conditions,value,companies):
    t=conditions.time
    return (value.metric in conditions.metrics and value.scope==conditions.scope
        and value.metric not in conditions.restrictions.excluded_metrics
        and value.company in [companies.get(code,code) for code in conditions.codes if code not in conditions.restrictions.excluded_codes]
        and ((value.year,value.period) in t.pairs if t.pairs else value.period in t.periods and (not t.years or value.year in t.years)))
