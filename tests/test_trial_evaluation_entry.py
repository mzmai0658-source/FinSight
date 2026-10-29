"""评测入口必须测试当前问答资料，不误选严格小库。"""
from types import SimpleNamespace
import json
import pytest

from src.etl import submission_upgrade as upgrade


def evaluation_setup(tmp_path, monkeypatch):
    selected=SimpleNamespace(version='trial-version',collection='trial-collection',pointer_name='ocr_trial',
        manifest={'export_path':'data/runtime/submissions/ocr_trial/trial-version','review_mode':'relaxed_ocr',
                  'source_audit_complete':False})
    monkeypatch.setattr(upgrade,'ROOT',tmp_path)
    env={'LLM_MODEL':'deepseek-flash','UPLOAD200_USERNAME':'tester','UPLOAD200_PASSWORD':'private-test',
         'FINSIGHT_OCR_TRIAL':'true','FINSIGHT_DATA_PROFILE':'ocr-trial-v1'}
    monkeypatch.setattr(upgrade,'load_environment',lambda *args:env)
    monkeypatch.setattr(upgrade,'_configure_process',lambda env:None)
    class Engine:
        def dispose(self):pass
    monkeypatch.setattr('sqlalchemy.create_engine',lambda *args,**kwargs:Engine())
    monkeypatch.setattr('config.db_config.get_readonly_db_config',lambda:SimpleNamespace(connection_string='sqlite://'))
    monkeypatch.setattr('src.api.readiness.selected_repository',lambda engine:selected)
    monkeypatch.setattr('src.agent.v3.repository.CanonicalRepository',lambda engine:SimpleNamespace(version='strict-version'))
    provider=SimpleNamespace(provider='deepseek',model='deepseek-flash',base_url='https://one.example/v1',max_tokens=3072,timeout_seconds=60,max_retries=0)
    monkeypatch.setattr('src.agent.providers.resolve_chat_provider',lambda:provider)
    embedding=SimpleNamespace(fingerprint='cloud-embedding:1024',dimensions=1024)
    monkeypatch.setattr('src.agent.providers.resolve_embedding_function',lambda purpose:embedding)
    def get(**kwargs):
        identifiers=kwargs.get('ids',['chunk-a','chunk-b','chunk-c'])[:kwargs.get('limit',3)]
        return {'ids':identifiers,'documents':['原件段落 '+key for key in identifiers],
                'metadatas':[{'page':1} for key in identifiers],'embeddings':[[0.0]*1024 for key in identifiers]}
    collection=SimpleNamespace(metadata={'embedding_fingerprint':embedding.fingerprint,'data_version':selected.version},
        count=lambda:3,get=get)
    client=SimpleNamespace(get_collection=lambda *args,**kwargs:collection)
    monkeypatch.setattr('chromadb.PersistentClient',lambda *args,**kwargs:client)
    selected.test_collection=collection
    folder=tmp_path/selected.manifest['export_path'];folder.mkdir(parents=True)
    for name,value in [('manifest.json',{'version':selected.version}),
                       ('index.json',{'accepted':True,'collection':selected.collection,'chunks':3,
                                      'fingerprint':'cloud-embedding:1024'})]:
        (folder/name).write_text(json.dumps(value),encoding='utf-8')
    return selected,folder


def test_evaluate_uses_same_active_repository_as_chat(tmp_path,monkeypatch):
    selected,folder=evaluation_setup(tmp_path,monkeypatch)
    captured={}
    def run(artifacts,output,**kwargs):
        captured.update(artifacts=artifacts,**kwargs)
        return {'status':'test-recorded'}
    monkeypatch.setattr('eval.cloud_upload200.run',run)
    assert upgrade.main(['evaluate','--output',str(tmp_path/'results')])==0
    assert captured['artifacts']==folder
    assert captured['runtime_snapshot']['pointer']==selected.pointer_name
    assert captured['runtime_snapshot']['version']==selected.version


def test_evaluate_can_prepare_without_login_or_paid_calls(tmp_path,monkeypatch):
    selected,folder=evaluation_setup(tmp_path,monkeypatch)
    captured={}
    def prepare(artifacts,output,**kwargs):
        captured.update(artifacts=artifacts,**kwargs)
        return output
    monkeypatch.setattr('eval.cloud_upload200.prepare',prepare)
    monkeypatch.setattr('eval.cloud_upload200.run',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('must not login')))
    assert upgrade.main(['evaluate','--prepare-only','--output',str(tmp_path/'results')])==0
    assert captured['artifacts']==folder
    assert captured['runtime_snapshot']['version']==selected.version


def test_legacy_isolated_trial_snapshot_records_actual_profile(tmp_path,monkeypatch):
    selected,folder=evaluation_setup(tmp_path,monkeypatch)
    del upgrade.load_environment()['FINSIGHT_DATA_PROFILE']
    selected.manifest['dataset_profile']={'id':'ocr-trial-v1'}
    captured={}
    def run(artifacts,output,**kwargs):
        captured.update(kwargs)
        return {'status':'test-recorded'}
    monkeypatch.setattr('eval.cloud_upload200.run',run)
    assert upgrade.main(['evaluate','--output',str(tmp_path/'results')])==0
    assert captured['runtime_snapshot']['profile']=='ocr-trial-v1'


def test_evaluation_does_not_read_artifacts_outside_workspace(tmp_path,monkeypatch):
    selected,folder=evaluation_setup(tmp_path,monkeypatch)
    selected.manifest['export_path']='../unrelated-release'
    monkeypatch.setattr('eval.cloud_upload200.run',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('must stop before login')))
    assert upgrade.main(['evaluate','--output',str(tmp_path/'results')])==2


def test_prepare_only_cannot_accidentally_submit_ocr(monkeypatch):
    monkeypatch.setattr(upgrade,'load_environment',lambda *args:(_ for _ in ()).throw(AssertionError('must stop before OCR configuration')))
    with pytest.raises(SystemExit) as error:
        upgrade.main(['ocr','--prepare-only'])
    assert error.value.code==2


def test_strict_selected_release_keeps_its_index_coverage_artifact(tmp_path,monkeypatch):
    selected,folder=evaluation_setup(tmp_path,monkeypatch)
    selected.pointer_name='serving'
    selected.manifest['dataset_profile']={'id':'user-financial-v1'}
    (folder/'index.json').rename(folder/'index_coverage.json')
    captured={}
    def run(artifacts,output,**kwargs):
        captured.update(kwargs)
        return {'status':'test-recorded'}
    monkeypatch.setattr('eval.cloud_upload200.run',run)
    assert upgrade.main(['evaluate','--output',str(tmp_path/'results')])==0
    assert captured['runtime_snapshot']['profile']=='user-financial-v1'
    assert captured['runtime_snapshot']['index_file']=='index_coverage.json'


@pytest.mark.parametrize('defect',['count','dimension','fingerprint','version'])
def test_evaluate_checks_live_collection_instead_of_only_receipt(tmp_path,monkeypatch,defect):
    selected,folder=evaluation_setup(tmp_path,monkeypatch)
    collection=selected.test_collection
    if defect=='count':collection.count=lambda:2
    if defect=='dimension':collection.get=lambda **kwargs:{'embeddings':[[0.0]*8]}
    if defect=='fingerprint':collection.metadata['embedding_fingerprint']='incompatible-embedding:1024'
    if defect=='version':collection.metadata['data_version']='other-version'
    monkeypatch.setattr('eval.cloud_upload200.run',lambda *args,**kwargs:{'status':'unexpected_run'})
    assert upgrade.main(['evaluate','--output',str(tmp_path/'results')])==2


def test_runtime_snapshot_binds_cloud_endpoint_without_exposing_it(tmp_path,monkeypatch):
    selected,folder=evaluation_setup(tmp_path,monkeypatch)
    before=upgrade.evaluation_target(None,upgrade.load_environment())[1]
    from src.agent.providers import resolve_chat_provider
    resolve_chat_provider().base_url='https://two.example/v1'
    after=upgrade.evaluation_target(None,upgrade.load_environment())[1]
    assert before!=after
    assert 'https://' not in json.dumps(after)


@pytest.mark.parametrize('change',['vector','document'])
def test_runtime_snapshot_binds_actual_index_contents(tmp_path,monkeypatch,change):
    selected,folder=evaluation_setup(tmp_path,monkeypatch)
    before=upgrade.evaluation_target(None,upgrade.load_environment())[1]
    previous=selected.test_collection.get
    def get(**kwargs):
        value=previous(**kwargs)
        if change=='vector':value['embeddings'][0][0]=0.25
        else:value['documents'][0]='变更后的原件段落'
        return value
    selected.test_collection.get=get
    after=upgrade.evaluation_target(None,upgrade.load_environment())[1]
    assert before!=after


def test_runtime_digest_is_stable_when_vector_store_returns_another_row_order(tmp_path,monkeypatch):
    selected,folder=evaluation_setup(tmp_path,monkeypatch)
    before=upgrade.evaluation_target(None,upgrade.load_environment())[1]
    previous=selected.test_collection.get
    def get(**kwargs):
        return {key:list(reversed(value)) for key,value in previous(**kwargs).items()}
    selected.test_collection.get=get
    after=upgrade.evaluation_target(None,upgrade.load_environment())[1]
    assert before==after


@pytest.mark.parametrize('change',[{'accepted':False},{'collection':'other-collection'}])
def test_evaluate_rejects_unaccepted_or_other_index(tmp_path,monkeypatch,change):
    selected,folder=evaluation_setup(tmp_path,monkeypatch)
    index=json.loads((folder/'index.json').read_text(encoding='utf-8'));index.update(change)
    (folder/'index.json').write_text(json.dumps(index),encoding='utf-8')
    monkeypatch.setattr('eval.cloud_upload200.run',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('must stop before login')))
    assert upgrade.main(['evaluate','--output',str(tmp_path/'results')])==2
