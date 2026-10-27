from fastapi.testclient import TestClient
import pytest
import src.api.main as api


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api,'internal_api_token',lambda:'private-test-internal-token')
    return TestClient(api.app,headers={'X-Internal-Token':'private-test-internal-token'})


def test_new_candidate_routes_require_internal_credential():
    client=TestClient(api.app)
    for method,path in [('get','/internal/financial-submissions/'+'a'*64),
                        ('post','/internal/financial-submissions/'+'a'*64+'/review'),
                        ('post','/internal/financial-submissions/publish')]:
        assert getattr(client,method)(path).status_code in {401,503}


def test_financial_etl_uses_candidate_chain_and_never_publishes(client,tmp_path,monkeypatch):
    import src.etl.submission_service as service
    path=tmp_path/'data_root/report.pdf';path.parent.mkdir();path.write_bytes(b'%PDF-1.7')
    monkeypatch.setattr(api,'ROOT_DIR',tmp_path)
    called=[]
    def run(path,ingest_rag):
        called.append((path,ingest_rag))
        return dict(status='success',review_status='WAITING',candidate_id='a'*64)
    monkeypatch.setattr(service,'run_financial_submission',run)
    response=client.post('/internal/etl/run',json=dict(file_path=str(path),file_type='financial',ingest_rag=True))
    assert response.status_code==200 and response.json()['review_status']=='WAITING'
    assert called==[(path,True)]


def test_review_and_publish_reject_extra_fields_boolean_operator_and_duplicates(client):
    for body in [dict(decision='reject',operator_id=True,reason='check'),
                 dict(decision='approve',operator_id=1),
                 dict(decision='reject',operator_id=1,reason='check',facts_verified=True)]:
        assert client.post('/internal/financial-submissions/'+'a'*64+'/review',json=body).status_code==422
    assert client.post('/internal/financial-submissions/publish',json=dict(candidate_ids=['a'*64,'a'*64],operator_id=1)).status_code==422


def test_invalid_state_does_not_leak_driver_configuration(client,monkeypatch):
    import src.etl.submission_service as service
    def fail(*args):raise RuntimeError('password=secret database endpoint')
    monkeypatch.setattr(service,'get_candidate',fail)
    response=client.get('/internal/financial-submissions/'+'a'*64)
    assert response.status_code==503 and 'secret' not in response.text
