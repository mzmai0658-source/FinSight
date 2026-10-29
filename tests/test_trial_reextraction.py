"""全量重提取不复用旧字段子集，已核验与待核验来源分别保存。"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from tests.test_v3_contracts import fact


def sourced(metric,value):
    from src.agent.v3.contracts import Source
    from src.agent.v3.catalog import METRICS
    result=fact('600085',2024,value,metric=metric)
    result.source=Source(document_id='a'*64,document_version='a'*64,source_sha256='a'*64,
        source_path='data_root/report.pdf',page=1,table='合并利润表',row=METRICS[metric].label,column='2024年度',
        row_index=1,column_index=1,raw_value=value,raw_unit='元',raw_precision=0,
        literal=value,extraction='ocr',verification='literal_checked')
    return result


def test_fresh_extraction_retains_only_genuine_source_proofs_as_verified(monkeypatch):
    from src.etl.ocr_quality import extract_fresh_document
    values=[sourced('operating_revenue','100'),sourced('operating_cost','60')]
    for item in values:
        assert item.status=='verified'
    calls=[]
    def audit(report,version,**kwargs):
        calls.append(kwargs)
        resolved,rejected=kwargs['scope_resolver'](deepcopy(values))
        return resolved,dict(rejected=rejected,conflicts=[]),[dict(text='新OCR全文')]
    monkeypatch.setattr('src.etl.canonical_audit.audit_report',audit)
    monkeypatch.setattr('src.etl.submission_sources.bind_original_source',lambda value,path,**kwargs:value if value.metric=='operating_revenue' else None)
    monkeypatch.setattr('src.agent.v3.evidence.checked_source',lambda value:True)
    result=extract_fresh_document(dict(stock_code='600085',company='同仁堂',year=2024,period='FY',
        source_path='data_root/report.pdf',document_version='a'*64),'v',Path('fresh.json'))
    assert len(calls)==1 and calls[0]['reference_annotations'] is False
    assert calls[0]['cache_path']==Path('fresh.json')
    assert [value.status for value in result[0]]==['verified','unverified']
    assert result[0][1].source.verification=='unverified'
    assert result[1]['source_binding']['verified']==1
    assert result[1]['source_binding']['unverified']==1
    assert result[1]['source_binding']['remaining'][0]['metric']=='operating_cost'


def test_binder_success_is_not_enough_without_independent_source_check(monkeypatch):
    from src.etl.ocr_quality import extract_fresh_document
    value=sourced('operating_revenue','100')
    def audit(report,version,**kwargs):
        values,_=kwargs['scope_resolver']([deepcopy(value)])
        return values,dict(rejected=[],conflicts=[]),[]
    monkeypatch.setattr('src.etl.canonical_audit.audit_report',audit)
    monkeypatch.setattr('src.etl.submission_sources.bind_original_source',lambda value,path,**kwargs:value)
    monkeypatch.setattr('src.agent.v3.evidence.checked_source',lambda value:False)
    values,ledger,_=extract_fresh_document(dict(source_path='data_root/report.pdf'),'v',Path('fresh.json'))
    assert values[0].status=='unverified'
    assert ledger['source_binding']['remaining'][0]['reason']=='bound_source_check_failed'


def script():
    path=Path(__file__).resolve().parents[1]/'scripts/ocr_trial.py'
    spec=importlib.util.spec_from_file_location('trial_reextract_script',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_prepare_reextracts_previously_successful_reports_and_resumes(tmp_path,monkeypatch):
    module=script()
    monkeypatch.setattr(module,'ROOT',tmp_path)
    original=tmp_path/'data_root/report.pdf';original.parent.mkdir();original.write_bytes(b'original')
    digest=module.sha(original)
    asset_folder=tmp_path/'data/runtime/submissions/assets'/digest;asset_folder.mkdir(parents=True)
    ocr=asset_folder/'ocr.json';ocr.write_text('{}')
    module.write(str(ocr)+'.meta.json',dict(pdf_sha256=digest,cache_sha256=module.sha(ocr),origin='remote_ocr'))
    value=sourced('operating_revenue','100')
    value.source.document_version=digest;value.source.source_sha256=digest
    report=dict(report_id='600085_2024_FY',stock_code='600085',company='同仁堂',year=2024,period='FY',
                page_count=1,sha256=digest)
    module.write(tmp_path/'data/runtime/cloud_upload120_sources.json',dict(reports=[report]))
    old_narrative=tmp_path/'old-narratives.jsonl';old_narrative.write_text('{}\n')
    class Store:
        def list(self):
            return [dict(candidate_id=digest,source_path='data_root/report.pdf',facts=[value.model_dump(mode='json')],
                         narratives_file='old-narratives.jsonl')]
        def ocr_submission(self,*args):return dict(job_id='existing-paid-job')
    monkeypatch.setattr('src.etl.submission_service.get_store',lambda:Store())
    monkeypatch.setattr(module,'extraction_implementation',lambda:dict(test='frozen'))
    calls=[]
    def extract(report,version,cache_path):
        calls.append(report)
        fresh=value.model_copy(update=dict(data_version=version))
        narrative={**report,'text':'全量新抽取','page':1,'ocr_sha256':module.sha(ocr)}
        return [fresh],dict(rejected=[],conflicts=[],missing=[]),[narrative]
    monkeypatch.setattr('src.etl.ocr_quality.extract_fresh_document',extract)
    monkeypatch.setattr('src.agent.v3.evidence.checked_source',lambda value:True)
    class Embedding:
        fingerprint='test-embedding'
    monkeypatch.setattr('src.agent.providers.resolve_embedding_function',lambda *_:Embedding())
    monkeypatch.setattr('src.agent.providers.embedding_fingerprint',lambda value:'test-embedding')
    writer=create_engine('sqlite://');reader=create_engine('sqlite://')
    from src.agent.v3.repository import metadata
    metadata.create_all(writer)
    monkeypatch.setattr(module,'engines',lambda:(writer,reader))
    monkeypatch.setattr(writer,'dispose',lambda:None)
    monkeypatch.setattr(reader,'dispose',lambda:None)
    prepared=module.prepare()
    assert len(calls)==1
    document=module.read(tmp_path/prepared['work']/'documents'/(digest+'.json'))
    assert document['mode']=='fresh_ocr_full_reextraction'
    assert document['narratives'][0]['text']=='全量新抽取'
    resumed=module.prepare()
    assert resumed['version']==prepared['version'] and len(calls)==1
    document['ocr_sha256']='wrong'
    module.write(tmp_path/prepared['work']/'documents'/(digest+'.json'),document)
    with pytest.raises(ValueError,match='cached|Cached|fingerprint|identity'):
        module.prepare()


def test_trial_validation_checks_candidates_and_derived_inputs(monkeypatch):
    from src.agent.v3.contracts import Fact
    from src.etl.ocr_quality import validate_trial_dataset,field_coverage
    revenue=sourced('operating_revenue','100');cost=sourced('operating_cost','60')
    for value in (revenue,cost):
        value.status='unverified';value.source.verification='unverified'
    report=dict(stock_code='600085',company='A',year=2024,period='FY',source_path='data_root/report.pdf',document_version='a'*64)
    gross=Fact(id='gross',data_version='v',stock_code='600085',company='A',year=2024,period='FY',
        metric='gross_profit',scope='consolidated',value='40',unit='元',status='derived',
        formula='operating_revenue - operating_cost',inputs=[revenue.id,cost.id])
    monkeypatch.setattr('src.etl.ocr_trial.trial_source_ok',lambda value:True)
    assert validate_trial_dataset([revenue,cost,gross],[report],'v')['facts']==3
    assert next(row for row in field_coverage([revenue,cost,gross],{}) if row['metric']=='gross_profit'
                and row['scope']=='consolidated')['status']=='derived_unverified'
    with pytest.raises(ValueError,match='missing'):
        validate_trial_dataset([revenue,gross],[report],'v')
    gross.value='41'
    with pytest.raises(ValueError,match='incorrect'):
        validate_trial_dataset([revenue,cost,gross],[report],'v')
    gross.value='40';revenue.unit='%'
    with pytest.raises(ValueError,match='unit'):
        validate_trial_dataset([revenue,cost,gross],[report],'v')


def test_legacy_excluded_entries_are_not_all_treated_as_numeric_conflicts():
    from src.etl.ocr_quality import field_coverage
    ledger=dict(conflicts=[dict(metric='operating_revenue',scope='consolidated',reason='physical_cell_or_scope_unverified'),
                           dict(metric='attributable_net_profit',scope='consolidated',reason='source_conflict')])
    fields={(item['metric'],item['scope']):item for item in field_coverage([],ledger)}
    assert fields['operating_revenue','consolidated']['status']=='unresolved'
    assert fields['attributable_net_profit','consolidated']['status']=='conflict'


def test_smoke_pauses_for_provider_failures_not_financial_no_data():
    module=script()
    assert module.cloud_failure(dict(task=dict(result=dict(outcome=dict(reason_codes=['model_timeout'])))))=='model_timeout'
    assert module.cloud_failure(dict(reason='cloud_connection_or_response_failed'))=='cloud_connection_or_response_failed'
    assert module.cloud_failure(dict(result=dict(outcome=dict(reason_codes=['no_verified_fact'])))) is None
    assert module.cloud_failure(dict(reason='unknown arbitrary exception text')) is None


def test_trial_validator_rejects_mixed_direct_and_derived_lineage(monkeypatch):
    from src.etl.ocr_quality import validate_trial_dataset
    value=sourced('operating_revenue','100')
    report=dict(stock_code='600085',company='A',year=2024,period='FY',source_path='data_root/report.pdf',document_version='a'*64)
    monkeypatch.setattr('src.agent.v3.evidence.checked_source',lambda value:True)
    value.inputs=['missing'];value.formula='invented'
    with pytest.raises(ValueError,match='lineage'):
        validate_trial_dataset([value],[report],'v')
    value.inputs=[];value.formula=None;value.status='derived'
    with pytest.raises(ValueError,match='source'):
        validate_trial_dataset([value],[report],'v')


def test_staged_publication_rechecks_payload_and_index_columns(monkeypatch):
    from sqlalchemy import select
    from src.agent.v3.repository import metadata,stage_release,facts
    from src.etl.ocr_quality import validate_staged_snapshot
    engine=create_engine('sqlite://');metadata.create_all(engine)
    value=sourced('operating_revenue','100')
    report=dict(stock_code='600085',company='A',year=2024,period='FY',source_path='data_root/report.pdf',document_version='a'*64)
    manifest=dict(version='v');info=dict(version='v',facts=1,reports=1,collection='index')
    exports=[value.model_dump(mode='json')]
    monkeypatch.setattr('src.agent.v3.evidence.checked_source',lambda value:True)
    stage_release(engine,'v',[value],[report],'index',manifest)
    with engine.begin() as conn:
        assert validate_staged_snapshot(conn,info,manifest,exports,[report])['facts']==1
        mutated=deepcopy(exports[0]);mutated['value']='101'
        conn.execute(facts.update().values(payload=mutated))
        with pytest.raises(ValueError,match='frozen'):
            validate_staged_snapshot(conn,info,manifest,exports,[report])
        conn.execute(facts.update().values(payload=exports[0],stock_code='000333'))
        with pytest.raises(ValueError,match='columns'):
            validate_staged_snapshot(conn,info,manifest,exports,[report])


def test_failed_in_transaction_validation_cannot_move_trial_pointer():
    from sqlalchemy import select
    from tests.test_ocr_trial import prepared
    from src.etl.ocr_trial import publish_trial
    from src.agent.v3.repository import pointer,releases
    engine,_,_=prepared()
    with engine.begin() as conn:conn.execute(pointer.insert().values(name='ocr_trial',version='old'))
    def reject(conn):raise ValueError('snapshot changed')
    with pytest.raises(ValueError,match='snapshot changed'):
        publish_trial(engine,'v',dict(inventory_checked=True,index_verified=True),validate_snapshot=reject)
    with engine.connect() as conn:
        assert conn.execute(select(pointer.c.version)).scalar_one()=='old'
        assert conn.execute(select(releases.c.status)).scalar_one()=='staged'


def test_snapshot_allows_only_single_ulp_pdf_coordinate_roundtrip():
    from math import nextafter,inf
    from src.etl.ocr_quality import _same_staged_fact
    exported=dict(value='100.00000000000001',source=dict(original_cell=dict(
        value=dict(bbox=[132.17200000000003,10.,200.,20.],text='100'))))
    saved=deepcopy(exported)
    saved['source']['original_cell']['value']['bbox'][0]=nextafter(132.17200000000003,inf)
    assert _same_staged_fact(exported,saved)
    saved['value']='100.00000000000002'
    assert not _same_staged_fact(exported,saved)
    saved=deepcopy(exported)
    saved['source']['original_cell']['value']['bbox'][0]=nextafter(nextafter(132.17200000000003,inf),inf)
    assert not _same_staged_fact(exported,saved)
    saved=deepcopy(exported);saved['source']['original_cell']['value']['text']='101'
    assert not _same_staged_fact(exported,saved)
    saved=deepcopy(exported);saved['source']['original_cell']['value']['bbox'][0]=float('nan')
    assert not _same_staged_fact(exported,saved)
    assert not _same_staged_fact(dict(other=dict(bbox=[132.17200000000003])),
                                 dict(other=dict(bbox=[nextafter(132.17200000000003,inf)])))


def test_staged_publication_tolerates_storage_coordinate_but_checks_actual_source(monkeypatch):
    from math import nextafter,inf
    from src.agent.v3.contracts import OriginalCellProof,OriginalRegion
    from src.agent.v3.repository import metadata,stage_release,facts
    from src.etl.ocr_quality import validate_staged_snapshot
    engine=create_engine('sqlite://');metadata.create_all(engine)
    value=sourced('operating_revenue','100')
    region=OriginalRegion(page=1,bbox=(132.17200000000003,10.,200.,20.),text='100')
    value.source.original_cell=OriginalCellProof(row=region,value=region,context=region,heading=region)
    report=dict(stock_code='600085',company='A',year=2024,period='FY',source_path='data_root/report.pdf',document_version='a'*64)
    manifest=dict(version='v');info=dict(version='v',facts=1,reports=1,collection='index')
    exports=[value.model_dump(mode='json')]
    checks=[]
    monkeypatch.setattr('src.agent.v3.evidence.checked_source',lambda actual:checks.append(actual) or True)
    stage_release(engine,'v',[value],[report],'index',manifest)
    with engine.begin() as conn:
        saved=deepcopy(exports[0])
        saved['source']['original_cell']['value']['bbox'][0]=nextafter(region.bbox[0],inf)
        conn.execute(facts.update().values(payload=saved))
        assert validate_staged_snapshot(conn,info,manifest,exports,[report])['facts']==1
        assert checks and checks[-1].source.original_cell.value.bbox[0]!=region.bbox[0]
        with pytest.raises(ValueError,match='frozen'):
            validate_staged_snapshot(conn,info,manifest,[],[report])
        monkeypatch.setattr('src.agent.v3.evidence.checked_source',lambda actual:False)
        with pytest.raises(ValueError,match='source failed'):
            validate_staged_snapshot(conn,info,manifest,exports,[report])


def test_staged_source_validation_groups_reports_without_dropping_facts(monkeypatch):
    from src.agent.v3.repository import metadata,stage_release
    from src.etl.ocr_quality import validate_staged_snapshot
    engine=create_engine('sqlite://');metadata.create_all(engine)
    values=[sourced('operating_revenue','100'),sourced('operating_cost','60'),sourced('operating_revenue','200')]
    for value,identifier,code in zip(values,['1','3','2'],['600085','600085','000333']):
        value.id=identifier;value.stock_code=code
    reports=[dict(stock_code=code,company='A',year=2024,period='FY') for code in ['600085','000333']]
    info=dict(version='v',facts=3,reports=2,collection='index');manifest=dict(version='v')
    stage_release(engine,'v',values,reports,'index',manifest)
    checked=[]
    def validate(actual,actual_reports,version,**kwargs):
        checked.extend((value.stock_code,value.year,value.period,value.id) for value in actual)
        return dict(facts=len(actual))
    monkeypatch.setattr('src.etl.ocr_quality.validate_trial_dataset',validate)
    with engine.begin() as conn:
        assert validate_staged_snapshot(conn,info,manifest,[v.model_dump(mode='json') for v in values],reports)['facts']==3
    assert len({row[3] for row in checked})==3
    assert [row[:3] for row in checked]==sorted(row[:3] for row in checked)


def test_known_wrong_period_cannot_be_kept_as_merely_unverified(monkeypatch):
    from src.etl.ocr_quality import extract_fresh_document,validate_trial_dataset
    value=sourced('operating_revenue','100');value.source.column='2023年度'
    def audit(report,version,**kwargs):
        values,rejected=kwargs['scope_resolver']([value])
        return values,dict(rejected=rejected,conflicts=[]),[]
    monkeypatch.setattr('src.etl.canonical_audit.audit_report',audit)
    monkeypatch.setattr('src.etl.submission_sources.bind_original_source',lambda value,path,**kwargs:None)
    values,ledger,_=extract_fresh_document(dict(source_path='data_root/report.pdf'),'v',Path('fresh.json'))
    assert values==[] and ledger['rejected'][0]['reason']=='candidate_row_or_period_invalid'
    value.status='unverified';value.source.verification='unverified'
    report=dict(stock_code='600085',company='A',year=2024,period='FY',source_path='data_root/report.pdf',document_version='a'*64)
    monkeypatch.setattr('src.etl.ocr_trial.trial_source_ok',lambda value:True)
    with pytest.raises(ValueError,match='row|period'):
        validate_trial_dataset([value],[report],'v')


def test_definite_native_contradiction_is_excluded_not_downgraded(monkeypatch):
    from src.etl.ocr_quality import extract_fresh_document
    value=sourced('operating_revenue','100')
    def audit(report,version,**kwargs):
        values,rejected=kwargs['scope_resolver']([value])
        return values,dict(rejected=rejected,conflicts=[]),[]
    def contradict(value,path,*,diagnostics):
        diagnostics.append(dict(classification='contradiction',reason='physical_header_period_conflict'))
        return None
    monkeypatch.setattr('src.etl.canonical_audit.audit_report',audit)
    monkeypatch.setattr('src.etl.submission_sources.bind_original_source',contradict)
    values,ledger,_=extract_fresh_document(dict(source_path='data_root/report.pdf'),'v',Path('fresh.json'))
    assert values==[] and ledger['rejected'][0]['reason']=='physical_header_period_conflict'
    assert ledger['source_binding']['unverified']==0


def test_paused_smoke_returns_nonzero_for_resumable_command(monkeypatch):
    import sys
    module=script()
    monkeypatch.setattr(module,'environment',lambda:{})
    monkeypatch.setattr(module,'smoke',lambda env:dict(status='paused',turns=2,planned=9))
    monkeypatch.setattr(sys,'argv',['ocr_trial.py','smoke'])
    assert module.main()==2
