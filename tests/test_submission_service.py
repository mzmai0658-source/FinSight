"""候选处理允许漏项，但失败、审批与重试不能偷偷发布数据。"""
import hashlib
import json

import fitz
import pytest

from src.etl.submission_store import SubmissionStore


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    import src.etl.submission_service as service
    root=tmp_path
    path=root/'data_root/input.pdf'
    path.parent.mkdir()
    with fitz.open() as doc:
        page=doc.new_page()
        page.insert_text((30,40),'股票代码：000789 测试企业2025年年度报告',fontname='china-s')
        doc.save(path)
    store=SubmissionStore(root/'data/runtime/submissions/state.sqlite3')
    monkeypatch.setattr(service,'ROOT',root)
    monkeypatch.setattr(service,'get_store',lambda:store)
    monkeypatch.setattr(service,'pipeline_digest',lambda:'pipeline-one')
    monkeypatch.setattr(service,'validate_facts',lambda facts,reports,**kwargs:facts)
    def ocr(pdf,id,store,folder,**kwargs):
        folder.mkdir(parents=True,exist_ok=True)
        cache=folder/'ocr.json'; cache.write_text('[{}]',encoding='utf-8')
        md=folder/'ocr.md'; md.write_text('测试企业2025年年度报告',encoding='utf-8')
        return dict(json=cache,markdown=md,pages=1,job_id='fresh-job')
    monkeypatch.setattr(service,'fresh_ocr',ocr)
    monkeypatch.setattr(service,'freeze_artifacts',lambda paths,**kw:{'test':'hash'})
    monkeypatch.setattr(service,'verify_artifacts',lambda *a,**kw:None)
    monkeypatch.setattr(service,'build_index',lambda *a,**kw:dict(accepted=True,collection='candidate',chunks=1,reports=1))
    monkeypatch.setattr(service,'extract_candidate',lambda report,version,cache:([
        dict(id='fact',status='verified',metric='operating_revenue',value='12')],
        dict(rejected=[dict(reason='ocr_literal_not_confirmed')],conflicts=[],missing=[]),
        [dict(text='经营情况',stock_code='000789',company='测试企业',year=2025,period='FY')]))
    return service,path,store


class Model:
    is_configured=True
    last_diagnostics=dict(model='deepseek-flash')
    def __init__(self):self.calls=0
    def chat_json(self,*a,**kw):
        self.calls+=1
        return dict(stock_code='000789',company='测试企业',year=2025,period='FY')


def test_verified_subset_can_wait_for_review_but_not_auto_publish(runtime):
    service,path,store=runtime
    result=service.run_financial_submission(path,llm_client=Model())
    assert result['status']=='success' and result['review_status']=='WAITING'
    candidate=service.get_candidate(result['candidate_id'])
    assert candidate['quality']['verified_count']==1 and len(candidate['excluded'])==1
    assert 'publication' not in candidate or not candidate['publication']
    approved=service.review_financial_submission(result['candidate_id'],dict(
        decision='approve',operator_id=3,identity=dict(stock_code='000789',company='测试企业',year=2025,period='FY')))
    assert approved['review_status']=='APPROVED'


def test_embedding_failure_retains_ocr_and_proposal_and_retry_reuses_them(runtime,monkeypatch):
    service,path,store=runtime
    model=Model()
    def fail(*a,**kw):raise ConnectionError('private endpoint must not be logged')
    monkeypatch.setattr(service,'build_index',fail)
    first=service.run_financial_submission(path,llm_client=model)
    assert first['status']=='failed' and first['review_status']!='APPROVED'
    assert 'private endpoint' not in json.dumps(first)
    monkeypatch.setattr(service,'build_index',lambda *a,**kw:dict(accepted=True,collection='candidate',chunks=1,reports=1))
    result=service.run_financial_submission(path,llm_client=model)
    assert result['status']=='success' and model.calls==1


def test_stale_processing_invalidates_approval_instead_of_inheriting_it(runtime,monkeypatch):
    service,path,store=runtime
    result=service.run_financial_submission(path,llm_client=Model())
    service.review_financial_submission(result['candidate_id'],dict(decision='approve',operator_id=3,
        identity=dict(stock_code='000789',company='测试企业',year=2025,period='FY')))
    monkeypatch.setattr(service,'pipeline_digest',lambda:'pipeline-two')
    refreshed=service.run_financial_submission(path,llm_client=Model())
    assert refreshed['review_status']=='WAITING' and refreshed['approval_invalidated'] is True


def test_bad_identity_never_reaches_extraction(runtime,monkeypatch):
    service,path,store=runtime
    class Wrong(Model):
        def chat_json(self,*a,**kw):return dict(stock_code='000789',company='别家公司',year=2025,period='FY')
    monkeypatch.setattr(service,'extract_candidate',lambda *a:pytest.fail('wrong company must be blocked'))
    result=service.run_financial_submission(path,llm_client=Wrong())
    assert result['status']=='failed'
    assert not result.get('facts')


def test_skipping_index_does_not_make_reviewable_candidate(runtime):
    service,path,store=runtime
    result=service.run_financial_submission(path,ingest_rag=False,llm_client=Model())
    assert result['status']=='failed' and result['review_status']!='WAITING'
