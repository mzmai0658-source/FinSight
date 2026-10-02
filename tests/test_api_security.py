from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from src.api import assets
import src.api.main as api

TOKEN = 'offline-private-test-credential-1234567890'

@pytest.fixture()
def asset_store(tmp_path, monkeypatch):
    folder=tmp_path/'public'
    folder.mkdir()
    monkeypatch.setattr(assets,'ROOT_DIR',tmp_path)
    monkeypatch.setattr(assets,'ASSET_ROOTS',(folder,))
    monkeypatch.setattr(assets,'REGISTRY_PATH',tmp_path/'registry.sqlite3')
    monkeypatch.setenv('INTERNAL_API_TOKEN',TOKEN)
    return folder, TestClient(api.app, headers={'X-Internal-Token':TOKEN})


def test_internal_service_rejects_missing_or_wrong_token(monkeypatch):
    monkeypatch.setenv('INTERNAL_API_TOKEN',TOKEN)
    client=TestClient(api.app)
    for path in ['/internal/metrics','/internal/assets/unknown','/internal/health']:
        assert client.get(path).status_code == 401
        assert client.get(path,headers={'X-Internal-Token':'wrong'}).status_code == 401


def test_unconfigured_token_fails_closed(monkeypatch):
    monkeypatch.setattr(api,'internal_api_token',lambda:'')
    assert TestClient(api.app).get('/internal/metrics').status_code == 503


def test_path_endpoint_and_direct_static_assets_removed(asset_store):
    folder,client=asset_store
    for path in ['/api/assets?path=.env', '/results/secret.pdf']:
        assert client.get(path).status_code == 404
    assert not assets.register_asset(folder.parent/'.env')
    secret=folder/'.env'
    secret.write_text('private')
    assert not assets.register_asset(secret)
    other=folder.parent/'private.pdf'
    other.write_bytes(b'private')
    assert not assets.register_asset(other)


def test_registered_asset_persists_and_is_version_bound(asset_store):
    folder,client=asset_store
    source=folder/'report.md'
    source.write_text('# original source', encoding='utf-8')
    asset=assets.register_asset(str(source))
    assert asset == assets.register_asset(str(source))
    response=client.get('/internal/assets/'+asset['asset_id'])
    assert response.status_code == 200 and response.text == '# original source'
    assert response.headers['x-content-type-options'] == 'nosniff'
    source.write_text('# changed source', encoding='utf-8')
    assert client.get('/internal/assets/'+asset['asset_id']).status_code == 404
    assert assets.register_asset(str(source))['asset_id'] != asset['asset_id']


def test_registered_pdf_remains_the_same_version_after_upload_replacement_or_removal(asset_store,monkeypatch):
    folder,client=asset_store
    monkeypatch.setattr('src.utils.original_archive.ARCHIVE',folder/'original_versions')
    source=folder/'report.pdf';original=b'%PDF-1.7\nfirst financial original'
    source.write_bytes(original);asset=assets.register_asset(str(source))
    source.write_bytes(b'%PDF-1.7\nreplacement original')
    newer=assets.register_asset(str(source))
    assert newer['asset_id']!=asset['asset_id']
    source.unlink()
    response=client.get('/internal/assets/'+asset['asset_id'])
    assert response.status_code==200 and response.content==original
    assert response.headers['content-type']=='application/pdf'


def test_done_fact_provenance_is_clickable_and_persisted(asset_store):
    folder,client=asset_store
    source=folder/'source.md'
    source.write_text('synthetic evidence')
    result=dict(facts=[dict(query_id='q1',field='net_profit',source=dict(source_path=str(source),page_start=1))],evidence=[dict(type='sql',query_id='q1')])
    payload=api._internal_done_payload('question',result)
    fact=payload['facts'][0]
    assert fact['source']['asset_id']
    assert payload['validation']['facts'] == payload['facts']
    assert payload['evidence'][0]['facts'] == payload['facts']
    assert 'asset_id' not in result['facts'][0]['source']


def test_advisor_bypass_disabled(asset_store):
    _,client=asset_store
    response=client.post('/internal/advisor/report',json={'stock_codes':['603259'],'risk_level':'moderate'})
    assert response.status_code in (404,422)


def test_done_preserves_synthetic_identity_without_aliasing(asset_store):
    profile = dict(id='synthetic-demo-v3', kind='synthetic', reports=15, companies=5)
    payload = api._internal_done_payload('question', dict(dataset_profile=profile))
    assert payload['dataset_profile'] == profile
    payload['dataset_profile']['kind'] = 'changed'
    assert profile['kind'] == 'synthetic'


def test_source_manifest_requires_matching_value_and_content_hash(tmp_path, monkeypatch):
    import hashlib,json
    import src.utils.provenance as provenance
    folder=tmp_path/'demo';folder.mkdir()
    report=folder/'report.md';report.write_text('original statement',encoding='utf-8')
    fact=dict(stock_code='990001',report_year=2024,report_period='FY',table='income_sheet',field='net_profit',value=120,
              source_path='demo/report.md',source_sha256=hashlib.sha256(report.read_bytes()).hexdigest(),document_id='doc',page_start=1)
    manifest=folder/'facts.json';manifest.write_text(json.dumps({'facts':[fact]}),encoding='utf-8')
    monkeypatch.setattr(provenance,'ROOT',tmp_path)
    monkeypatch.setenv('FINANCIAL_FACTS_MANIFEST',str(manifest))
    lineage={'net_profit':dict(table='income_sheet',field='net_profit')}
    scope=dict(stock_code='990001',report_year=2024,report_period='FY')
    assert provenance.attach_provenance([{'net_profit':120}],lineage,scope)[0]['_provenance']['net_profit']['page_start']==1
    assert '_provenance' not in provenance.attach_provenance([{'net_profit':121}],lineage,scope)[0]
    report.write_text('changed statement',encoding='utf-8')
    assert '_provenance' not in provenance.attach_provenance([{'net_profit':120}],lineage,scope)[0]
