"""Company pages must not silently read a different release from chat."""
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from src.agent.v3.contracts import Fact
from src.agent.v3.repository import metadata, stage_release, publish_release
from src.etl.ocr_trial import publish_trial
from src.api.readiness import selected_repository
from src.api.market import snapshot


def publish(engine, version, specs, *, trial=False):
    reports = {}
    facts = []
    for index, (code, year, period, metric, value, scope, status) in enumerate(specs):
        reports[code, year, period] = dict(stock_code=code, company='公司'+code, year=year, period=period, document_version='a'*64)
        facts.append(Fact(id=f'{version}-{index}', data_version=version, stock_code=code,
                          company='公司'+code, year=year, period=period, metric=metric,
                          value=value, scope=scope, status=status, unit='%' if metric=='roe_weighted' else '元'))
    manifest = dict(dataset_profile=dict(id='ocr-trial-v1', kind='real', reports=len(reports),
                    companies=len({r['stock_code'] for r in reports.values()}), inventory=list(reports.values())),
                    review_mode='relaxed_ocr') if trial else {}
    stage_release(engine, version, facts, list(reports.values()), 'index-'+version, manifest)
    if trial:
        publish_trial(engine, version, dict(inventory_checked=True, index_verified=True))
    else:
        publish_release(engine, version, dict(facts_verified=True, reports_verified=True,
                                             index_verified=True, projections_verified=True))


def test_selected_trial_catalogue_includes_ten_companies_and_keeps_strict_separate(monkeypatch):
    engine = create_engine('sqlite://')
    metadata.create_all(engine)
    strict = [(f'{i:06}', 2025, 'Q1', 'operating_revenue', '10000', 'consolidated', 'verified') for i in range(1,4)]
    trial = [(f'{i:06}', 2024, 'FY', 'operating_revenue', str(i*100000000), 'consolidated', 'unverified') for i in range(1,11)]
    publish(engine, 'strict', strict)
    publish(engine, 'trial', trial, trial=True)
    monkeypatch.setenv('FINSIGHT_DATA_PROFILE', 'ocr-trial-v1')
    monkeypatch.setenv('FINSIGHT_OCR_TRIAL', 'true')
    result = snapshot(selected_repository(engine))
    assert len(result['companies']) == 10
    assert len(result['summary']['companyRank']) == 10
    assert result['summary']['dataset'] == dict(dataVersion='trial', experimental=True,
        label='试验财报库', reportCount=10, companyCount=10, sourceAuditComplete=False)
    assert result['summary']['companyRank'][0]['code'] == '000010'
    assert len(result['examples']) == 4
    assert all('2024年合并营业收入' in e['question'] for e in result['examples'])
    monkeypatch.setenv('FINSIGHT_OCR_TRIAL', 'false')
    strict_result = snapshot(selected_repository(engine))
    assert len(strict_result['companies']) == 3
    assert strict_result['summary']['yearlyAggregate'] == []
    assert strict_result['summary']['companyRank'] == []
    assert strict_result['summary']['latestYear'] is None
    assert strict_result['examples'][0]['id'] == 'ex-help'


def test_annual_metrics_do_not_mix_period_scope_or_revenue_definition(monkeypatch):
    engine = create_engine('sqlite://')
    metadata.create_all(engine)
    publish(engine, 'strict', [
        ('000001', 2024, 'FY', 'operating_revenue', '407149600000', 'consolidated', 'verified'),
        ('000001', 2024, 'FY', 'total_operating_revenue', '999999999999', 'consolidated', 'verified'),
        ('000001', 2024, 'FY', 'operating_revenue', '88888', 'parent', 'verified'),
        ('000001', 2025, 'Q1', 'operating_revenue', '77777', 'consolidated', 'verified'),
        ('000001', 2024, 'FY', 'attributable_net_profit', '0', 'consolidated', 'verified'),
        ('000002', 2024, 'FY', 'operating_revenue', '10000', 'consolidated', 'unverified'),
        ('000002', 2024, 'FY', 'roe_weighted', '12.5', 'consolidated', 'verified'),
    ])
    monkeypatch.setenv('FINSIGHT_OCR_TRIAL', 'false')
    result = snapshot(selected_repository(engine))
    row = result['summary']['yearlyAggregate'][0]
    assert Decimal(row['revenue_sum']) == Decimal('40714960')
    assert row['revenue_count'] == 1 and row['roe_count'] == 1 and row['company_count'] == 2
    assert row['net_profit_sum'] == '0' and row['net_profit_count'] == 1
    assert result['summary']['latestYear'] == 2024
    assert result['details']['000002']['latestCore']['revenue'] is None
    assert result['details']['000001']['coverage'] == [dict(year=2024, periods='FY'), dict(year=2025, periods='Q1')]
    assert result['series']['000001']['balance'] == []
    assert len(result['examples']) == 1


def test_market_endpoint_requires_internal_auth_and_fails_closed(monkeypatch):
    from fastapi.testclient import TestClient
    from src.api import main
    monkeypatch.setenv('INTERNAL_API_TOKEN', 'offline-private-test-credential-1234567890')
    client = TestClient(main.app)
    assert client.get('/internal/market/snapshot').status_code == 401
    monkeypatch.setattr(main, '_get_health_engine', lambda: None)
    def unavailable(_): raise ValueError('private database details')
    monkeypatch.setattr(main, 'selected_repository', unavailable)
    response = client.get('/internal/market/snapshot', headers={'X-Internal-Token':'offline-private-test-credential-1234567890'})
    assert response.status_code == 503
    assert 'private' not in response.text
