"""The library catalogue and its PDF route must follow chat's selected release."""
import hashlib

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from src.api import assets, materials
from src.agent.v3.repository import metadata, pointer, stage_release, publish_release
from src.etl.ocr_trial import publish_trial
from src.etl.provenance_store import current


@pytest.fixture
def selected_catalogue(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, 'ASSET_ROOTS', (tmp_path,))
    monkeypatch.setattr('src.utils.original_archive.ARCHIVE', tmp_path/'archive')
    monkeypatch.setenv('FINSIGHT_DATA_PROFILE', 'ocr-trial-v1')
    monkeypatch.setenv('FINSIGHT_OCR_TRIAL', 'true')
    engine=create_engine('sqlite://')
    metadata.create_all(engine)
    current.create(engine)
    def report(code, year, period):
        path=tmp_path/f'{code}_{year}_{period}.pdf'
        path.write_bytes(f'%PDF-1.4\n{code}-{year}-{period}'.encode())
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        return dict(stock_code=code, company='公司'+code, year=year, period=period,
                    source_path=str(path), source_sha256=digest, document_version=digest, page_count=10)
    strict=[report(f'{i:06}',2024,'Q1') for i in range(1,4)]
    trial=[report(f'{i:06}',year,period) for year in (2023,2024,2025)
           for period in ('Q1','HY','Q3','FY') for i in range(1,11)
           if (i,year,period)!=(10,2023,'Q1')]
    stage_release(engine,'strict',[],strict,'strict-index',{})
    publish_release(engine,'strict',dict(facts_verified=True,reports_verified=True,index_verified=True,projections_verified=True))
    stage_release(engine,'trial',[],trial,'trial-index',dict(review_mode='relaxed_ocr',
        dataset_profile=dict(id='ocr-trial-v1',kind='real',reports=119,companies=10,inventory=trial)))
    publish_trial(engine,'trial',dict(inventory_checked=True,index_verified=True))
    with engine.begin() as conn:
        for row in strict:
            conn.execute(current.insert().values(stock_code=row['stock_code'],report_year=row['year'],
                report_period=row['period'],payload={'document':row,'facts':[{'stock_abbr':row['company']}]}))
    yield engine,trial
    engine.dispose()


def test_library_returns_the_selected_119_reports_and_ten_companies_on_first_page(selected_catalogue):
    engine,_=selected_catalogue
    result=materials.catalogue(engine)
    assert result['total']==119
    assert result['stats']['companyCount']==10
    assert len(result['records'])==12
    assert len({r['stockCode'] for r in result['records']})==10
    assert all(r['reportYear']==2025 and r['reportPeriod']=='FY' for r in result['records'][:10])
    assert result['dataset']['experimental'] and result['dataset']['dataVersion']=='trial'
    assert result['dataset']['reportCount']==119 and result['dataset']['sourceAuditComplete'] is False
    assert all(r['experimental'] and r['qualityStatus']=='warn' and r['pdfAvailable'] for r in result['records'])
    assert not any('source_path' in row for row in result['records'])
    assert materials.catalogue(engine,keyword='000010')['total']==11
    assert materials.catalogue(engine,keyword='%')['total']==0
    all_rows=[r for page in range(1,11) for r in materials.catalogue(engine,page=page)['records']]
    assert len({(r['stockCode'],r['reportYear'],r['reportPeriod'],r['sha256']) for r in all_rows})==119


def test_original_route_uses_selected_identity_and_rejects_changed_bytes(selected_catalogue,monkeypatch):
    engine,reports=selected_catalogue
    original=materials.original_pdf(engine,'000010',2025,'FY')
    expected=next(r for r in reports if (r['stock_code'],r['year'],r['period'])==('000010',2025,'FY'))
    assert hashlib.sha256(original.read_bytes()).hexdigest()==expected['source_sha256']
    original.write_bytes(b'%PDF-1.4\nchanged document')
    with pytest.raises(HTTPException) as changed:
        materials.original_pdf(engine,'000010',2025,'FY')
    assert changed.value.status_code==404
    monkeypatch.setenv('FINSIGHT_OCR_TRIAL','false')
    result=materials.catalogue(engine)
    assert result['total']==3 and result['dataset']['dataVersion']=='strict'
    assert not any(r['experimental'] for r in result['records'])
    with pytest.raises(HTTPException): materials.original_pdf(engine,'000010',2025,'FY')


def test_missing_selected_release_cannot_fall_back_to_three_legacy_records(selected_catalogue):
    engine,_=selected_catalogue
    with engine.begin() as conn:
        conn.execute(pointer.update().where(pointer.c.name=='ocr_trial').values(version='missing'))
    for action in (lambda:materials.catalogue(engine),lambda:materials.original_pdf(engine,'000001',2024,'Q1')):
        with pytest.raises(HTTPException) as failure:action()
        assert failure.value.status_code==503


def test_an_upload_with_wrong_identity_cannot_hide_a_published_report(selected_catalogue):
    from sqlalchemy import text
    engine,reports=selected_catalogue
    expected=next(r for r in reports if r['stock_code']=='000010' and r['year']==2025 and r['period']=='FY')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE financial_report_material (id INTEGER,stock_code TEXT,company TEXT,'
            'report_year INTEGER,report_period TEXT,report_kind TEXT,identity_status TEXT,file_name TEXT,'
            'sha256 TEXT,page_count INTEGER,text_page_count INTEGER,content_source TEXT,content_state TEXT,notes TEXT)'))
        conn.execute(text('INSERT INTO financial_report_material VALUES (1,"999999","待确认",2025,"FY",'
            '"full","unresolved","upload.pdf",:sha,10,10,"ocr","ready","")'),{'sha':expected['source_sha256']})
    result=materials.catalogue(engine,keyword='000010')
    assert result['total']==11
    assert any(r['sha256']==expected['source_sha256'] and r['status']=='imported' for r in result['records'])
    upload=materials.catalogue(engine,keyword='999999')['records'][0]
    assert upload['status']=='text_only' and not upload['pdfAvailable']
