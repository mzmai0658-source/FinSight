"""作品说明：人工独立定义多轮与留出问题的条件预期。"""
MAIN=['total_operating_revenue','net_profit','gross_profit_margin','roe']
R=['total_operating_revenue']; N=['net_profit']; G=['gross_profit_margin']

def e(codes='',years=(),fields=(),period='FY'):
    return dict(codes=codes.split(',') if codes else [],years=list(years),fields=list(fields),period=period)

DIALOGS=[
    [e('002082',[2024],R),e('002082',[2024],N),e('002864',[2024],N),e('002864',[2023],N)],
    [e('002821',[2023,2024],MAIN),e('002821',[2023,2024],MAIN),e('600085',[2022,2023,2024],MAIN),e('600085',[2022,2023,2024],G)],
    [e('600085',[2024],R,'HY'),e('600085',[2024],R),e('600085',[2025],R),e('002082',[2025],R)],
    [e('002821',[2024],R,'Q3'),*[e('002821',[2023,2024],R,'Q3') for _ in range(3)]],
    [e('*'),e('002082'),e('002082',[2022,2023,2024],R),e('002082',[2022,2023,2024],R)],
    [e('002864',[2023],['liability_total_liabilities'],'Q1'),e('002864',[2023],['asset_total_assets'],'Q1'),e('002864',[2024],['asset_total_assets'],'Q1'),e('002821',[2024],R)],
    [e('002082',[2024],N),e('600085',[2022,2023,2024],MAIN),e('600085',[2022],R),e('600080',[2024],R)],
    [e('002821',[2024],R),e('002821',[2023],R),e('002821',[2023],G),e('002821',[2024],G,'HY')],
    [*[e('002082,002864',[2024],R) for _ in range(3)],e('002864',[2023],R)],
    [e('600085',[2024],R),e('600085',[2024]),e('600085',[2024]),e('002082',[2024],['liability_total_liabilities'])],
    [e(),e(period='clarify'),e('002082',period='clarify'),e('002082',[2024],R)],
    [e('600085',[2025],R,'HY'),e('600085',[2025],R,'Q3'),e('600085',[2024,2025],R,'Q3'),e('600085',[2024,2025],period='Q3')],
]

HOLDOUT=[
    e('002082',[2024],R),e('002737',[2024],R),e('002821',[2022,2023],MAIN),e('600085',[2022,2023,2024],R),
    e('002082',[2024],R),e('002864',[2023],['liability_total_liabilities'],'Q1'),e('002821',[2024],R,'HY'),e('600085',[2024],R,'Q3'),
    e('600085',[2024],R,'unsupported'),e('002082',[2023,2024],N),e('002082,600129',[2024],R),e('600252',[2022,2023,2024],N),
    e('002390',[2024],['gross_profit_margin','roe']),e('002737',[2024],['operating_cf_net_amount']),e('600222',[2023],period='Q3'),
    e('600080',[2024],['eps']),e('600085',[2023],['net_profit','net_profit_excl_non_recurring']),e('002082',[2023,2024],R),
    e('600085',[2023,2024],G),e('002821',[2024],R),e('*'),e('600129',[2025],period='mixed'),
    e('600085',[2024],R),e('002082',[2024]),e('002821',[2023,2024],R),e('600222',[2024],['total_operating_revenue','net_profit']),
    e(period='unsupported'),e(period='unsupported'),e(period='unsupported'),e(),
]

HOLDOUT_FRESH=[
    e('002082',[2024],R,'Q1'),e('002821',[2024],R,'HY'),e('600085',[2025],N,'Q3'),e('600222',[2023],period='Q3'),
    e('002082',[2022,2023],R),e('600129',[2024],N),e('600080',[2022,2023,2024],N),e('002737',[2023,2024],G),
    e('600085',[2023],['net_profit','net_profit_excl_non_recurring']),e('002082',[2022,2024],N),
    e('002821',[2024],['total_operating_revenue','net_profit'],'HY'),
    e('600129',[2025],['asset_total_assets','liability_total_liabilities'],'Q3'),
    e('600085',[2023],['investing_cf_net_amount'],'Q1'),e('002082',[2023,2024],R),
    e('002864',[2023,2024],R),e('002082',[2024],['net_profit_yoy_growth']),e('600085',[2022,2023,2024],R),
    e('002082,002864',[2024],N),e('002821,600085',[2024],R),e('600222',[2023],R,'Q3'),
    e('*'),e('002082',[2025],period='mixed'),e('600085',[2024],R),e('600129',[2025]),
    e('600085',[2024],R,'unsupported'),e(period='unsupported'),
    e('600085',[2024],R),e('600085',[2025],R,'HY'),e('600085',[2024],R,'HY'),e('600085',[2024],N,'HY'),
]

HOLDOUT_FINAL=[
    e('002864',[2024],['asset_total_assets'],'Q1'),e('002821',[2024],R,'HY'),
    e('600085',[2025],['operating_cf_net_amount'],'Q3'),
    e('002082',[2024],['net_profit_yoy_growth']),
    e('600085',[2024],['operating_revenue_yoy_growth']),
    e('002082',[2023,2024],N),e('600085',[2022,2023,2024],R),e('002864',[2023,2024],N),
    e('600080',[2022,2023],R),
    e('600129',[2025],['asset_total_assets','liability_total_liabilities'],'Q3'),
    e('002821',[2024],G),e('002082',[2024],['net_profit','total_operating_revenue'],'Q1'),
    e('600085',[2024],['eps']),e('002082',[2024],['operating_cf_net_amount']),
    e('600085',[2023],['investing_cf_net_amount'],'Q1'),
    e('002082',[2024],['liability_total_liabilities']),e('002082',[2022,2023,2024],R),
    e('002082,002864',[2024],R),e('002821,600085',[2024],R),e('600222',[2023],period='Q3'),
    e('*'),e('600085',[2025],period='mixed'),e('002082',[2024],R),
    e('600080',[2022],['operating_cf_net_amount']),e('600085',[2024],R),
    e('600085',[2024],N,'unsupported'),e(period='unsupported'),
    e('002082',[2024],N),e('002864',[2024],N),e('002864',[2023],N),
]

def expected_for(case):
    if case['id']=='safety-6':
        return e('600085',[2024],R,'unsupported')
    if case.get('expected'): return case['expected']
    if case['id'].startswith('dialog-'):
        _,group,turn=case['id'].split('-')
        return DIALOGS[int(group)-1][int(turn)-1]
    if case['id'].startswith('h') and case['id'][1:].isdigit(): return HOLDOUT[int(case['id'][1:])-1]
    if case['id'].startswith('j') and case['id'][1:].isdigit(): return HOLDOUT_FRESH[int(case['id'][1:])-1]
    if case['id'].startswith('k') and case['id'][1:].isdigit(): return HOLDOUT_FINAL[int(case['id'][1:])-1]
    return {}
