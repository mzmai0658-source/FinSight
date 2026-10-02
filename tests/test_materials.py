import hashlib
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from src.api import assets, materials
import src.api.main as api
from src.etl.provenance_store import current


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, 'ASSET_ROOTS', (tmp_path,))
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    current.create(engine)
    path = tmp_path / '600080_2023.pdf'
    path.write_bytes(b'%PDF-1.4\nunit test bytes')
    doc = {'source_path': str(path), 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'page_count': 127}
    def insert(code='600080', year=2023, name='金花股份', document=None, quality=None):
        payload = {'document': document or doc, 'facts': [{'stock_abbr': name}]}
        if quality is not None:
            payload['quality'] = quality
        with engine.begin() as conn:
            conn.execute(current.insert().values(stock_code=code, report_year=year, report_period='HY', payload={
                **payload}))
    insert()
    yield engine, path, insert
    engine.dispose()


def test_catalog_identity_search_pagination_and_no_path_leak(catalog):
    engine, path, insert = catalog
    insert('000001', 2024, '另一家公司')
    result = materials.catalogue(engine, size=1)
    assert result['total'] == 2 and result['records'][0]['stockCode'] == '000001'
    assert materials.catalogue(engine, page=2, size=1)['records'][0]['company'] == '金花股份'
    result = materials.catalogue(engine, keyword='金花')
    row = result['records'][0]
    assert result['total'] == 1 and row['reportPeriod'] == 'HY' and row['pageCount'] == 127
    assert row['pdfAvailable'] and row['fileName'] == path.name
    assert str(path.parent) not in str(result)
    assert materials.catalogue(engine, keyword='%')['total'] == 0
    assert materials.catalogue(engine, keyword='missing')['records'] == []


def test_catalog_exposes_published_quality_without_internal_review_fields(catalog):
    engine, _, insert = catalog
    insert('000002', 2025, '待复核公司', quality={
        'review_status': 'warn',
        'warnings': ['cashflow_incomplete(2/4)'],
        'blockers': ['internal-only detail'],
        'missing_required_fields': ['investing_cf_net_amount'],
        'optional_missing_fields': ['operating_profit'],
        'requires_manual_review': True,
    })
    result = materials.catalogue(engine, keyword='待复核公司')
    assert result['total'] == 1
    row = result['records'][0]
    assert row['qualityStatus'] == 'warn'
    assert row['qualityWarnings'] == ['cashflow_incomplete(2/4)']
    assert row['missingFields'] == ['investing_cf_net_amount']
    assert 'blockers' not in row and 'optional_missing_fields' not in row


def test_pdf_requires_exact_published_identity_and_hash(catalog):
    engine, path, _ = catalog
    assert materials.original_pdf(engine, '600080', 2023, 'HY') == path
    with pytest.raises(HTTPException): materials.original_pdf(engine, '600080', 2023, 'FY')
    path.write_bytes(b'changed')
    with pytest.raises(HTTPException): materials.original_pdf(engine, '600080', 2023, 'HY')
    path.unlink()
    assert not materials.catalogue(engine)['records'][0]['pdfAvailable']


def test_catalog_never_serves_outside_approved_roots(catalog, monkeypatch):
    engine, path, _ = catalog
    monkeypatch.setattr(assets, 'ASSET_ROOTS', (path.parent / 'different',))
    assert not materials.catalogue(engine)['records'][0]['pdfAvailable']
    with pytest.raises(HTTPException): materials.original_pdf(engine, '600080', 2023, 'HY')


def test_empty_installation_is_empty():
    engine = create_engine('sqlite://')
    assert materials.catalogue(engine)['total'] == 0
    with pytest.raises(HTTPException): materials.original_pdf(engine, '600080', 2023, 'HY')
    engine.dispose()


def test_material_routes_auth_validation_and_pdf(catalog, monkeypatch):
    engine, path, insert = catalog
    insert('000002', 2025, '待复核公司', quality={
        'review_status': 'warn',
        'warnings': ['cashflow_incomplete(2/4)'],
        'missing_required_fields': ['investing_cf_net_amount'],
    })
    monkeypatch.setattr(api, '_get_health_engine', lambda: engine)
    monkeypatch.setenv('INTERNAL_API_TOKEN', 'test-materials-internal-token-1234567890')
    client = TestClient(api.app)
    url = '/internal/materials/financial'
    assert client.get(url).status_code == 401
    assert client.get(url + '/600080/2023/HY/file').status_code == 401
    client.headers['X-Internal-Token'] = 'test-materials-internal-token-1234567890'
    assert client.get(url, params={'keyword': '金花'}).json()['records'][0]['company'] == '金花股份'
    quality = client.get(url, params={'keyword': '待复核公司'}).json()['records'][0]
    assert quality['qualityStatus'] == 'warn'
    assert quality['qualityWarnings'] == ['cashflow_incomplete(2/4)']
    assert quality['missingFields'] == ['investing_cf_net_amount']
    assert client.get(url, params={'size': 51}).status_code == 422
    assert client.get(url + '/600080/2023/invalid/file').status_code == 404
    response = client.get(url + '/600080/2023/HY/file')
    assert response.content == path.read_bytes()
    assert response.headers['content-type'] == 'application/pdf'
