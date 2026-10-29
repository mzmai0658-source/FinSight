"""Read-only company views from the same published snapshot selected by chat.

No legacy-table fallback: a different release must never masquerade as the
selected catalogue. Amounts use 万元 for the existing portal chart contract.
"""
from collections import defaultdict
from decimal import Decimal

from src.agent.v3.catalog import METRICS


CORE = {
    'revenue': 'operating_revenue',
    'revenue_yoy': 'operating_revenue_reported_yoy',
    'net_profit': 'attributable_net_profit',
    'net_profit_yoy': 'attributable_net_profit_reported_yoy',
    'net_profit_excl': 'deducted_attributable_net_profit',
    'net_profit_excl_yoy': 'deducted_attributable_net_profit_reported_yoy',
    'eps': 'eps_basic', 'roe': 'roe_weighted',
    'gross_profit_margin': 'gross_margin', 'net_profit_margin': 'net_margin',
    'net_asset_per_share': 'net_assets_per_share',
    'operating_cf_per_share': 'operating_cash_per_share',
}
BALANCE = {
    'total_assets': 'total_assets', 'total_liabilities': 'total_liabilities',
    'asset_liability_ratio': 'debt_ratio', 'cash': 'cash',
}
CASHFLOW = {
    'operating_cf': 'operating_cash_flow', 'investing_cf': 'investing_cash_flow',
    'financing_cf': 'financing_cash_flow', 'net_cash_flow': 'net_cash_change',
}
PERIOD_ORDER = {'Q1': 0, 'HY': 1, 'Q3': 2, 'FY': 3}


def snapshot(repository):
    reports = repository.report_catalog()
    grouped = defaultdict(list)
    for report in reports:
        grouped[report['stock_code']].append(report)
    experimental = repository.release_status == 'experimental'
    dataset = dict(dataVersion=repository.version, experimental=experimental,
                   label='试验财报库' if experimental else '已验收财报库',
                   reportCount=len(reports), companyCount=len(grouped),
                   sourceAuditComplete=repository.manifest.get('acceptance', {}).get('source_audit_complete') is True)
    companies, details, series, examples = [], {}, {}, []
    annual = defaultdict(list)
    metrics = sorted(set(CORE.values()) | set(BALANCE.values()) | set(CASHFLOW.values()))
    for code, catalog in sorted(grouped.items()):
        names = {r['company'] for r in catalog}
        if len(names) != 1:
            raise ValueError('Published company identity is ambiguous')
        name = names.pop()
        years = sorted({r['year'] for r in catalog})
        fy = sorted({r['year'] for r in catalog if r['period'] == 'FY'})
        # Bound each query by company; do not mix FY with quarterly or parent data.
        values = repository.query([code], {code: [(y, 'FY') for y in fy]}, metrics, 'consolidated')
        by_year = defaultdict(dict)
        for fact in values:
            if fact.unit != METRICS[fact.metric].unit:
                raise ValueError('Canonical metric unit mismatch')
            if fact.metric in by_year[fact.year]:
                raise ValueError('Ambiguous canonical metric')
            by_year[fact.year][fact.metric] = fact

        def rows(mapping):
            result = []
            for year in fy:
                row = {'year': year}
                for field, metric in mapping.items():
                    fact = by_year[year].get(metric)
                    value = None if fact is None else fact.decimal
                    if value is not None and fact.unit == '元':
                        value /= Decimal(10000)
                    row[field] = None if value is None else format(value, 'f')
                if any(row[field] is not None for field in mapping):
                    result.append(row)
            return result

        core = rows(CORE)
        profile = dict(stockCode=code, abbr=name, fullName='', exchange='', board='',
                       industry='', region='', datasetTag=dataset['label'], dataStatus='imported',
                       firstYear=min(years), lastYear=max(years))
        companies.append(profile)
        coverage = [dict(year=year, periods=','.join(sorted(
            {r['period'] for r in catalog if r['year'] == year}, key=PERIOD_ORDER.get))) for year in years]
        details[code] = dict(profile=profile, enName='', regCapital='', employees=None,
                             latestCore=core[-1] if core else {}, coverage=coverage, dataset=dataset)
        series[code] = dict(stockCode=code, core=core, balance=rows(BALANCE), cashflow=rows(CASHFLOW))
        for row in core:
            annual[row['year']].append(dict(code=code, abbr=name, **row))
        available = [row for row in core if row['revenue'] is not None]
        if available:
            examples.append(dict(id='ex-' + code, type='查年报',
                                 question=f"{name}{available[-1]['year']}年合并营业收入是多少？请给出原件页码。"))

    yearly = []
    for year, rows_for_year in sorted(annual.items()):
        row = dict(year=year, company_count=len(rows_for_year))
        for field, output in [('revenue', 'revenue_sum'), ('net_profit', 'net_profit_sum'), ('roe', 'roe_avg')]:
            values = [Decimal(r[field]) for r in rows_for_year if r[field] is not None]
            row[field + '_count'] = len(values)
            value = sum(values, Decimal(0)) if values else None
            if value is not None and field == 'roe':
                value /= len(values)
            row[output] = None if value is None else format(value, 'f')
        yearly.append(row)
    latest = max(annual, default=None)
    rank = sorted((r for r in annual.get(latest, []) if r['revenue'] is not None),
                  key=lambda r: (-Decimal(r['revenue']), r['code']))
    summary = dict(importedCompanies=len(companies), pendingCompanies=0, latestYear=latest,
                   industryDistribution=[], yearlyAggregate=yearly, companyRank=rank,
                   hotCompanies=[], dataset=dataset)
    if not examples:
        examples = [dict(id='ex-help', type='使用说明', question='如何指定公司、年份、报告期和指标来查询财报？')]
    return dict(companies=companies, summary=summary, details=details, series=series, examples=examples[:4])
