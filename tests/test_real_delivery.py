"""验证真实资料附件不能越界、损坏或替换配置，发布失败不伪报完成。"""
import hashlib,json,zipfile
from pathlib import Path
import pytest
from src.etl import real_delivery as delivery

def archive(tmp_path,name='good.zip',path='data_root/a.pdf',value=b'real original bytes',wrong=False):
    target=tmp_path/name
    row=dict(path=path,bytes=len(value),sha256='0'*64 if wrong else hashlib.sha256(value).hexdigest())
    manifest=dict(schema=1,dataset='real-117-v1',data_version='v1',kind='originals',files=[row])
    with zipfile.ZipFile(target,'w') as out:
        out.writestr(path,value);out.writestr('PACKAGE_MANIFEST.json',json.dumps(manifest))
    return target

@pytest.mark.parametrize('path',['../a','/a','C:/a','a\\b','a/../b','CON.txt','a. /b'])
def test_rejects_windows_zip_escapes(path):
    with pytest.raises(ValueError):delivery.safe_member(path)

def test_bad_digest_rejected_before_any_import(tmp_path,monkeypatch):
    room=tmp_path/'Clean Room';room.mkdir();monkeypatch.setattr(delivery,'ROOT',room)
    bad=archive(tmp_path,wrong=True)
    spec=dict(schema=1,dataset='real-117-v1',data_version='v1',packages=[dict(file=bad.name,sha256=delivery.digest(bad),kind='originals')])
    source=tmp_path/'PACKAGE_SET.json';source.write_text(json.dumps(spec))
    with pytest.raises(ValueError,match='SHA-256'):delivery.import_packages(source)
    assert list(room.iterdir())==[]

def test_import_idempotent_and_cannot_replace_config(tmp_path,monkeypatch):
    room=tmp_path/'Space In Path';room.mkdir();monkeypatch.setattr(delivery,'ROOT',room)
    good=archive(tmp_path)
    spec=dict(schema=1,dataset='real-117-v1',data_version='v1',packages=[dict(file=good.name,sha256=delivery.digest(good),kind='originals')])
    source=tmp_path/'PACKAGE_SET.json';source.write_text(json.dumps(spec))
    delivery.import_packages(source);delivery.import_packages(source)
    assert (room/'data_root/a.pdf').read_bytes()==b'real original bytes'
    malicious=archive(tmp_path,name='config.zip',path='.env')
    spec['packages']=[dict(file=malicious.name,sha256=delivery.digest(malicious),kind='originals')];source.write_text(json.dumps(spec))
    with pytest.raises(ValueError,match='code or configuration'):delivery.import_packages(source)
    assert not (room/'.env').exists()

def test_duplicate_case_windows_path_rejected(tmp_path):
    path=archive(tmp_path)
    with zipfile.ZipFile(path,'a') as out:out.writestr('DATA_ROOT/A.PDF',b'fake')
    with pytest.raises(ValueError,match='Duplicate'):delivery.check_archive(path)

def test_source_model_kind_does_not_hide_scope_conflict():
    from src.agent.v3.proposals import TurnPlan,IntentConceptGoal
    from src.agent.v3.explicit_conditions import bind_explicit
    q='同仁堂2024年净利润必须只用合并口径，同时必须只用母公司口径，不允许拆开。'
    plan=TurnPlan(goals=[IntentConceptGoal(id='g1',kind='concept',concept_mode='difference')])
    result,_=bind_explicit(plan,q,{'600085':'同仁堂'})
    assert result.goals[0].kind=='concept'
    # 不凭关键词把概念改成查询；错误目标应在正式编译时拒绝，而非悄悄转换。
    from src.agent.v3.state import apply_understanding
    from src.agent.v3.contracts import DialogueState
    from tests.test_v3_generalization_boundaries import review
    request,_=apply_understanding(q,'t',result.compile(q,{'600085':'同仁堂'}),DialogueState(),{'600085':'同仁堂'})
    assert not review(request).accepted

def test_clear_company_does_not_rebind_literal_old_name():
    from src.agent.v3.proposals import TurnPlan,IntentGeneralGoal
    from src.agent.v3.explicit_conditions import bind_explicit
    q='不要查同仁堂了，清空公司。'
    plan=TurnPlan(continuity='continue',goals=[IntentGeneralGoal(id='g1',kind='lookup')])
    bound,applied=bind_explicit(plan,q,{'600085':'同仁堂'})
    assert 'codes' not in applied

def test_quick_route_does_not_require_optional_ocr_archive(tmp_path,monkeypatch):
    room=tmp_path/'Quick Room';room.mkdir();monkeypatch.setattr(delivery,'ROOT',room)
    original=archive(tmp_path)
    spec=dict(schema=1,dataset='real-117-v1',data_version='v1',packages=[
        dict(file=original.name,sha256=delivery.digest(original),kind='originals'),
        dict(file='optional-ocr-not-downloaded.zip',sha256='0'*64,kind='ocr')])
    source=tmp_path/'PACKAGE_SET.json';source.write_text(json.dumps(spec))
    delivery.import_packages(source,include_ocr=False)
    assert (room/'data_root/a.pdf').exists()
    with pytest.raises(FileNotFoundError):delivery.import_packages(source,include_ocr=True)

def test_ocr_narrative_keeps_table_and_excludes_picture_markup():
    from src.etl.canonical_narratives import ocr_narrative
    page={'markdown':{'text':'## 营业收入\n![picture](images/a.jpg)\n<table><tr><td>营业收入</td><td>123.45</td></tr></table>\n主要由于产品销售增长。'}}
    text=ocr_narrative(page)
    assert '营业收入' in text and '123.45' in text and '产品销售增长' in text
    assert '<td>' not in text and 'images/a.jpg' not in text

def test_repeat_index_keeps_first_build_digest_and_records_zero_new(tmp_path):
    from scripts.build_canonical_index import save_index_ledger
    first=dict(version='v1',collection='c1',chunks=3,reports=1,coverage=[{'chunks':3}],accepted=True,new_vectors=3,reused_vectors=0,reuse_source=None)
    save_index_ledger(tmp_path,first);sha=delivery.digest(tmp_path/'index_coverage.json')
    repeat={**first,'new_vectors':0}
    save_index_ledger(tmp_path,repeat)
    assert delivery.digest(tmp_path/'index_coverage.json')==sha
    assert json.loads((tmp_path/'index_last_run.json').read_text())['new_vectors']==0
    with pytest.raises(ValueError,match='differs'):save_index_ledger(tmp_path,{**repeat,'chunks':4})

@pytest.mark.parametrize('material_exists',[False,True])
def test_reader_defers_only_unmigrated_financial_table(monkeypatch,material_exists):
    from types import SimpleNamespace
    import pymysql
    import config.db_config as db
    sql=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,query,args=None):sql.append(query)
        def fetchall(self):return [('GRANT USAGE ON *.* TO `fs_real_test`@`localhost`',)]
        def fetchone(self):return [int(material_exists)]
    class Connection:
        def cursor(self):return Cursor()
        def close(self):pass
    monkeypatch.setattr(db,'get_db_config',lambda:SimpleNamespace(database='finsight_real_test',host='localhost',port=3306,user='owner',password='test-placeholder'))
    monkeypatch.setattr(db,'get_readonly_db_config',lambda:SimpleNamespace(user='fs_real_test',password='test-placeholder'))
    monkeypatch.setattr(pymysql,'connect',lambda **kwargs:Connection())
    result=delivery.create_reader();grants=[q for q in sql if q.startswith('GRANT')]
    assert len(grants)==(12 if material_exists else 11)
    assert result['deferred_until_java_migration']==([] if material_exists else ['financial_report_material'])
    assert all(q.startswith('GRANT SELECT ON') and 'sys_user' not in q and 'chat_' not in q and '.*' not in q for q in grants)
