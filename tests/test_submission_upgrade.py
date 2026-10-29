import json
from pathlib import Path
import pytest


def test_private_configuration_reuses_only_services_and_keeps_library_isolated(tmp_path):
    from src.etl.submission_upgrade import configure,load_environment
    original=tmp_path/'old.env'
    original.write_text('DB_PASSWORD="test-private-db"\nDB_NAME=daily\nLLM_API_KEY="test-private-cloud"\nPADDLEOCR_API_TOKEN="test-private-ocr"\nCHROMA_DB_PATH=daily/index\nUNRELATED_SECRET=never-copy\n',encoding='utf-8')
    root=tmp_path/'candidate';root.mkdir()
    path=configure(root,original,prompts=False)
    text=path.read_text(encoding='utf-8')
    assert 'never-copy' not in text and 'DB_NAME="daily"' not in text
    env=load_environment(root,path)
    assert env['DB_NAME']=='finsight_upload120_20261021'
    assert env['MYSQL_DATABASE']==env['SQL_DB_NAME']==env['DB_NAME']
    assert env['LLM_API_KEY']=='test-private-cloud' and env['PADDLEOCR_API_TOKEN']=='test-private-ocr'
    assert env['SQL_DB_USER']!=env['DB_USER']
    assert env['REDIS_PORT']=='6391' and env['RABBITMQ_PORT']=='5673'
    assert env['CHAT_QUOTA_PER_DAY']=='500' and env['CHAT_MAX_SESSIONS_PER_USER']=='200'
    assert configure(root,original,prompts=False).read_bytes()==path.read_bytes()


def test_runner_rejects_environment_that_points_at_daily_library(tmp_path):
    from src.etl.submission_upgrade import configure,load_environment
    root=tmp_path/'candidate';root.mkdir()
    env=configure(root,prompts=False)
    env.write_text(env.read_text(encoding='utf-8').replace('finsight_upload120_20261021','daily'),encoding='utf-8')
    with pytest.raises(ValueError,match='isolat'):load_environment(root,env)


def test_all_selected_originals_are_hash_verified_before_upload(tmp_path):
    from src.etl.submission_upgrade import selected_originals
    p=tmp_path/'a.pdf';p.write_bytes(b'changed')
    manifest=tmp_path/'m.json'
    manifest.write_text(json.dumps(dict(complete=True,reports=[dict(report_id='one',status='verified',
        stock_code='000789',company='测试企业',year=2025,period='FY',local_path=str(p),sha256='a'*64)])),encoding='utf-8')
    with pytest.raises(ValueError,match='hash'):selected_originals(manifest,root=tmp_path)


def test_first_test_user_is_registered_for_real_java_login_error_code(monkeypatch):
    from src.etl.submission_upgrade import SubmissionAPI
    from types import SimpleNamespace
    responses=[SimpleNamespace(status_code=400,json=lambda:dict(code=41002)),
        SimpleNamespace(status_code=200),
        SimpleNamespace(status_code=200,json=lambda:dict(code=0,data=dict(accessToken='test-private-token')),raise_for_status=lambda:None)]
    calls=[]
    class HTTP:
        headers={}
        def post(self,url,**kw):calls.append(url);return responses.pop(0)
    monkeypatch.setattr('requests.Session',HTTP)
    api=SubmissionAPI(dict(SERVER_PORT='18280',UPLOAD200_USERNAME='new_test',UPLOAD200_PASSWORD='test-pass'))
    assert calls==['http://127.0.0.1:18280/api/auth/login','http://127.0.0.1:18280/api/auth/register','http://127.0.0.1:18280/api/auth/login']


def test_source_listing_respects_server_page_limit_and_keeps_all_reports():
    from src.etl.submission_upgrade import SubmissionAPI
    api=object.__new__(SubmissionAPI);requests=[]
    def call(method,path,params):
        requests.append(params)
        start=(params['page']-1)*params['size']
        return dict(records=[dict(id=i) for i in range(start,min(start+params['size'],120))])
    api.call=call
    assert len(api.listing())==120
    assert requests==[dict(page=p,size=50) for p in (1,2,3)]


def test_expired_upload_login_rotates_tokens_and_rewinds_the_original_pdf(monkeypatch):
    import io
    import requests
    from src.etl.submission_upgrade import SubmissionAPI
    def response(status,value):
        r=requests.Response();r.status_code=status;r._content=json.dumps(value).encode();r._content_consumed=True;return r
    replies=[response(200,dict(code=0,data=dict(accessToken='access-a',refreshToken='refresh-a'))),
        response(401,dict(code=401)),response(200,dict(code=0,data=dict(accessToken='access-b',refreshToken='refresh-b'))),
        response(200,dict(code=0,data=dict(id=7))),response(401,dict(code=401)),
        response(200,dict(code=0,data=dict(accessToken='access-c',refreshToken='refresh-c'))),response(200,dict(code=0,data=dict(id=7)))]
    calls=[]
    class HTTP:
        def __init__(self):self.headers={}
        def request(self,method,url,**kw):
            stream=(kw.get('files') or {}).get('file')
            calls.append(dict(url=url,body=stream[1].read() if stream else None,json=kw.get('json'),
                authorization=self.headers.get('Authorization'),headers=kw.get('headers')))
            return replies.pop(0)
        def post(self,url,**kw):return self.request('POST',url,**kw)
    monkeypatch.setattr(requests,'Session',HTTP)
    api=SubmissionAPI(dict(SERVER_PORT='18280',UPLOAD200_USERNAME='test_user',UPLOAD200_PASSWORD='test-password'))
    stream=io.BytesIO(b'%PDF-original-identity')
    assert api.call('POST','/api/financial-submissions',files={'file':('one.pdf',stream,'application/pdf')})==dict(id=7)
    assert calls[1]['body']==calls[3]['body']==b'%PDF-original-identity'
    assert calls[2]['json']==dict(refreshToken='refresh-a')
    assert calls[2]['headers']=={'Authorization':None}
    assert calls[3]['authorization']=='Bearer access-b'
    assert api.call('GET','/api/financial-submissions/7')==dict(id=7)
    assert calls[5]['json']==dict(refreshToken='refresh-b')
    assert api.http.headers['Authorization']=='Bearer access-c'


@pytest.mark.parametrize('denied',[401,403])
def test_login_refresh_is_bounded_and_does_not_retry_forbidden_operations(monkeypatch,denied):
    import requests
    from src.etl.submission_upgrade import SubmissionAPI
    def response(status,value):
        r=requests.Response();r.status_code=status;r._content=json.dumps(value).encode();r._content_consumed=True;return r
    replies=[response(200,dict(code=0,data=dict(accessToken='access-a',refreshToken='refresh-a'))),response(denied,dict(code=denied))]
    if denied==401:replies.extend([response(200,dict(code=0,data=dict(accessToken='access-b',refreshToken='refresh-b'))),response(401,dict(code=401))])
    calls=[]
    class HTTP:
        headers={}
        def request(self,method,url,**kw):calls.append(url);return replies.pop(0)
        def post(self,url,**kw):return self.request('POST',url,**kw)
    monkeypatch.setattr(requests,'Session',HTTP)
    api=SubmissionAPI(dict(SERVER_PORT='18280',UPLOAD200_USERNAME='test_user',UPLOAD200_PASSWORD='test-password'))
    with pytest.raises(requests.HTTPError):api.call('GET','/api/financial-submissions/7')
    assert len(calls)==(4 if denied==401 else 2)
