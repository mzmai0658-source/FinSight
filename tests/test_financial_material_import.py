import json
from sqlalchemy import create_engine,text
from fastapi import HTTPException
import pytest
from scripts.import_local_financial_materials import identify,publish_metadata
from src.api.materials import catalogue,read_pages
from src.etl.provenance_store import current


def test_identity_uses_report_title_not_publication_date_and_keeps_summary():
    result=identify('600080_20230819_abcd.pdf','# 金花股份\n2023 年半年度报告摘要',{'600080':'金花股份'},{})
    assert result['report_year']==2023 and result['report_period']=='HY' and result['report_kind']=='summary'
    result=identify('600080_20230428_abcd.pdf','2022 年年度报告',{'600080':'金花股份'},{})
    assert result['report_year']==2022 and result['report_kind']=='full'
    assert identify('600080_20230428_abcd.pdf','没有报告标题',{}, {})['report_year'] is None


def test_identity_does_not_treat_backup_file_summary_reference_as_summary():
    cover = '''
    # 陕西盘龙药业集团股份有限公司
    # 2024 年半年度报告
    第一节 重要提示、目录和释义
    备查文件目录：载有公司法定代表人签字和公司盖章的半年度报告摘要及全文。
    '''
    result=identify('盘龙药业：2024年半年度报告.pdf',cover,{'002864':'盘龙药业'},{'盘龙药业':'002864'})
    assert result['report_year']==2024 and result['report_period']=='HY'
    assert result['report_kind']=='full'
    assert identify('盘龙药业：2024年半年度报告摘要.pdf',cover,{'002864':'盘龙药业'},{'盘龙药业':'002864'})['report_kind']=='summary'


def test_identity_accepts_exchange_summary_disclosure_in_first_title_region():
    cover='漳州片仔癀药业股份有限公司 2022年年度报告 2/10 第一节重要提示 1 本年度报告摘要来自年度报告全文。'
    result=identify('600436_20230415_I64P.pdf',cover,{'600436':'片仔癀'},{})
    assert result['report_year']==2022 and result['report_kind']=='summary'


def test_identity_preserves_leading_zero_and_reads_html_code():
    assert identify('九 芝 堂：2023年年度报告.pdf','',{'000989':'九芝堂'},{'九芝堂':'000989'})['stock_code']=='000989'
    result=identify('旧公司名：2023年半年度报告.pdf','<table><tr><td>股票代码</td><td>000423</td></tr></table>',{'000423':'东阿阿胶'},{})
    assert result['stock_code']=='000423' and result['company']=='东阿阿胶'
    result=identify('长药控股：2023年年度报告.pdf','目录说明'*5000+'<td>股票代码</td><td>300391</td>',{}, {})
    assert result['stock_code']=='300391' and result['company']=='长药控股'


def test_catalogue_keeps_distinct_documents_and_only_published_hash_is_structured():
    engine=create_engine('sqlite://')
    current.create(engine)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE financial_report_material (id INTEGER,stock_code TEXT,company TEXT,report_year INTEGER,report_period TEXT,report_kind TEXT,identity_status TEXT,file_name TEXT,sha256 TEXT,page_count INTEGER,text_page_count INTEGER,content_source TEXT,content_state TEXT,notes TEXT,pages JSON)'))
        for i,kind,sha in [(1,'full','full-hash'),(2,'summary','summary-hash')]:
            conn.execute(text('INSERT INTO financial_report_material VALUES (:id,"600080","金花股份",2023,"HY",:kind,"identified",:name,:sha,2,2,"ocr","ready","",:pages)'),
                         {'id':i,'kind':kind,'name':kind+'.pdf','sha':sha,'pages':json.dumps(['第一页','第二页'])})
        conn.execute(current.insert().values(stock_code='600080',report_year=2023,report_period='HY',payload={
            'facts':[{'stock_abbr':'金花股份'}],'document':{'source_path':'data_root/full.pdf','source_sha256':'full-hash'}}))
    result=catalogue(engine)
    assert result['total']==2 and result['stats']['structured']==1
    by_id={r['materialId']:r for r in result['records']}
    assert by_id[1]['structured'] and not by_id[2]['structured']
    assert catalogue(engine,keyword='%')['total']==0
    assert catalogue(engine,keyword='金花',size=1,page=2)['records'][0]['materialId']==2
    assert read_pages(engine,2,2)['markdown']=='第二页'
    assert 'source_path' not in read_pages(engine,2)
    with pytest.raises(HTTPException):read_pages(engine,2,3)
    engine.dispose()


def test_metadata_only_publish_changes_identity_fields_without_touching_pages_or_facts():
    engine=create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE financial_report_material (source_key TEXT PRIMARY KEY,stock_code TEXT,company TEXT,report_year INTEGER,report_period TEXT,report_kind TEXT,identity_status TEXT,pages JSON,sha256 TEXT)'))
        conn.execute(text('CREATE TABLE core_performance_indicators_sheet (id INTEGER PRIMARY KEY,roe REAL)'))
        conn.execute(text('INSERT INTO financial_report_material VALUES (:key,:code,:company,2024,"HY","summary","identified",:pages,:sha)'),
                     {'key':'key-1','code':'002864','company':'盘龙药业','pages':'["原正文"]','sha':'same-hash'})
        conn.execute(text('INSERT INTO core_performance_indicators_sheet VALUES (1,1.23)'))
    record={'source_key':'key-1','stock_code':'002864','company':'盘龙药业','report_year':2024,
            'report_period':'HY','report_kind':'full','identity_status':'identified'}
    assert publish_metadata(engine,[record])==1
    with engine.connect() as conn:
        material=conn.execute(text('SELECT report_kind,pages,sha256 FROM financial_report_material')).one()
        fact=conn.execute(text('SELECT roe FROM core_performance_indicators_sheet')).scalar_one()
    assert material==('full','["原正文"]','same-hash') and fact==1.23
    with pytest.raises(RuntimeError):
        publish_metadata(engine,[{**record,'source_key':'missing'}])
    engine.dispose()
