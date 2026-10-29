"""发布原子性、旧版本保护及资料归属由程序验证。"""
import pytest
from sqlalchemy import create_engine,select
from src.agent.v3.repository import metadata,stage_release,publish_release,pointer
from src.etl.submission_store import SubmissionStore,SubmissionBusy


def test_releasing_old_version_requires_matching_previous_pointer():
    engine=create_engine('sqlite://')
    metadata.create_all(engine)
    accept={k:True for k in ('facts_verified','reports_verified','index_verified','projections_verified')}
    stage_release(engine,'one',[],[],'one',{})
    publish_release(engine,'one',accept,expected_previous=None)
    stage_release(engine,'two',[],[],'two',{})
    with pytest.raises(ValueError,match='changed'):
        publish_release(engine,'two',accept,expected_previous=None)
    with engine.connect() as conn:assert conn.execute(select(pointer.c.version)).scalar_one()=='one'
    publish_release(engine,'two',accept,expected_previous='one')
    with pytest.raises(ValueError,match='immutable'):
        publish_release(engine,'one',accept,expected_previous='two')


def test_publication_failure_keeps_serving_pointer_and_projection_transaction():
    engine=create_engine('sqlite://');metadata.create_all(engine)
    accept={k:True for k in ('facts_verified','reports_verified','index_verified','projections_verified')}
    stage_release(engine,'one',[],[],'one',{});publish_release(engine,'one',accept)
    stage_release(engine,'two',[],[],'two',{})
    def fail(conn):raise RuntimeError('compatibility write failed')
    with pytest.raises(RuntimeError):publish_release(engine,'two',accept,project=fail,expected_previous='one')
    with engine.connect() as conn:assert conn.execute(select(pointer.c.version)).scalar_one()=='one'


def test_publish_lease_prevents_parallel_review_and_publication(tmp_path):
    store=SubmissionStore(tmp_path/'s.db');id='a'*64
    store.register(id,{})
    with store.publishing([id]) as heartbeat:
        heartbeat()
        with pytest.raises(SubmissionBusy):
            with store.publishing([id]):pass
        with pytest.raises(SubmissionBusy):store.review(id,'reject',reason='check',operator_id=1)
    token=store.claim(id);store.release(id,token)


@pytest.fixture
def publication(tmp_path,monkeypatch):
    import json
    import src.etl.submission_publish as pub
    from src.agent.v3.contracts import Fact
    from tests.test_submission_index import Embedding
    store=SubmissionStore(tmp_path/'store.db')
    engine=create_engine('sqlite://');metadata.create_all(engine)
    monkeypatch.setattr(pub,'ROOT',tmp_path)
    monkeypatch.setattr(pub,'pipeline_digest',lambda:'frozen-pipeline')
    monkeypatch.setattr(pub,'verify_artifacts',lambda *a,**kw:None)
    monkeypatch.setattr(pub,'file_digest',lambda path:path.stem*64 if path.suffix=='.pdf' else
        __import__('hashlib').sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(pub,'validate_facts',lambda facts,*a,**kw:[v if isinstance(v,Fact) else Fact.model_validate(v) for v in facts])
    monkeypatch.setattr(pub,'_project_release',lambda *a:None)
    monkeypatch.setattr(pub,'build_index',lambda rows,version,**kw:dict(accepted=True,collection='release_'+version[:16],chunks=len(rows)))
    embedding=Embedding()
    def candidate(letter,code):
        id=letter*64;folder=tmp_path/'data/runtime/submissions/assets'/id;folder.mkdir(parents=True)
        report=dict(stock_code=code,company='企业'+letter,year=2025,period='FY',document_version=id,
            source_path='data_root/'+letter+'.pdf',page_count=1)
        n=folder/'narratives.jsonl';n.write_text(json.dumps({**report,'page':1,'text':'经营情况'},ensure_ascii=False)+'\n',encoding='utf-8')
        fact=dict(id=letter+'-fact',data_version=id,stock_code=code,company=report['company'],year=2025,period='FY',
            metric='operating_revenue',scope='consolidated',value='123.45',unit='元',status='verified')
        store.register(id,dict(source_path=report['source_path'],report=report,identity=report,
            review_status='APPROVED',pipeline_sha256='frozen-pipeline',artifact_hashes={},facts=[fact],
            narratives_file=n.relative_to(tmp_path).as_posix(),index=dict(accepted=True,collection='candidate_'+letter,
            embedding_fingerprint=embedding.fingerprint),quality={k:True for k in
            ('identity_verified','reports_verified','facts_verified','index_verified')}))
        return id
    return pub,store,engine,embedding,candidate


def test_publication_keeps_existing_reports_and_retry_reconciles_same_release(publication):
    pub,store,engine,embedding,candidate=publication
    a=candidate('a','000789')
    first=pub.publish_candidates([a],1,store=store,engine=engine,embedding=embedding)
    b=candidate('b','000790')
    second=pub.publish_candidates([b],1,store=store,engine=engine,embedding=embedding)
    assert first['reports']==1 and second['reports']==2 and second['facts']==2
    assert store.get(a)['published_version']==first['version']
    retry=pub.publish_candidates([b],1,store=store,engine=engine,embedding=embedding)
    assert retry['version']==second['version'] and retry['idempotent'] is True


def test_cloud_index_failure_does_not_switch_pointer_or_approve_candidate(publication,monkeypatch):
    pub,store,engine,embedding,candidate=publication
    a=candidate('a','000789')
    first=pub.publish_candidates([a],1,store=store,engine=engine,embedding=embedding)
    b=candidate('b','000790')
    def fail(*a,**kw):raise ConnectionError('cloud unavailable')
    monkeypatch.setattr(pub,'build_index',fail)
    with pytest.raises(ConnectionError):pub.publish_candidates([b],1,store=store,engine=engine,embedding=embedding)
    with engine.connect() as conn:assert conn.execute(select(pointer.c.version)).scalar_one()==first['version']
    assert store.get(b)['review_status']=='APPROVED'


def test_retry_of_old_batch_never_rolls_back_a_new_corrected_report(publication):
    pub,store,engine,embedding,candidate=publication
    a=candidate('a','000789')
    first=pub.publish_candidates([a],1,store=store,engine=engine,embedding=embedding)
    b=candidate('b','000789');c=candidate('c','000790')
    newer=pub.publish_candidates([b,c],1,store=store,engine=engine,embedding=embedding)
    replay=pub.publish_candidates([a],1,store=store,engine=engine,embedding=embedding)
    assert replay['version']==first['version'] and replay['idempotent']
    with engine.connect() as conn:assert conn.execute(select(pointer.c.version)).scalar_one()==newer['version']


def test_receipt_save_failure_can_recover_after_another_batch_advanced(publication,monkeypatch):
    pub,store,engine,embedding,candidate=publication
    a=candidate('a','000789');mark=store.mark_published
    monkeypatch.setattr(store,'mark_published',lambda *a:(_ for _ in ()).throw(RuntimeError('state disk unavailable')))
    with pytest.raises(RuntimeError,match='committed'):pub.publish_candidates([a],1,store=store,engine=engine,embedding=embedding)
    first=pub._current(engine).version
    monkeypatch.setattr(store,'mark_published',mark)
    b=candidate('b','000789');c=candidate('c','000790')
    newer=pub.publish_candidates([b,c],1,store=store,engine=engine,embedding=embedding)
    recovered=pub.publish_candidates([a],1,store=store,engine=engine,embedding=embedding)
    assert recovered['version']==first and store.get(a)['review_status']=='PUBLISHED'
    assert pub._current(engine).version==newer['version']


def test_company_name_changes_keep_historical_literal_under_one_security_code(publication):
    pub,store,engine,embedding,candidate=publication
    a=candidate('a','000789')
    pub.publish_candidates([a],1,store=store,engine=engine,embedding=embedding)
    b=candidate('b','000789');row=store.get(b)
    row['report']['year']=2024;row['facts'][0]['year']=2024
    path=pub.ROOT/row['narratives_file'];n=__import__('json').loads(path.read_text(encoding='utf-8'));n['year']=2024
    path.write_text(__import__('json').dumps(n)+'\n',encoding='utf-8');store.save(b,row)
    result=pub.publish_candidates([b],1,store=store,engine=engine,embedding=embedding)
    reports=pub._load_json(pub.ROOT/'data/runtime/submissions/releases'/result['version']/'reports.json')
    assert {r['company'] for r in reports}=={'企业a'}
    assert {r['original_company'] for r in reports}=={'企业a','企业b'}
