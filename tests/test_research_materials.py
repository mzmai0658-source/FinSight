import json
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from src.api.research_materials import read_pages
from scripts.import_local_research_materials import prepare, filename_key
from src.etl.research_metadata import ResearchMeta, normalize_title
from src.etl.research_metadata import _clean


@pytest.mark.parametrize('value', [None, float('nan'), 'None', 'null', '<NA>'])
def test_missing_metadata_is_not_displayed_as_literal_none(value):
    assert _clean(value)==''
    assert _clean(0)=='0'


def test_filename_replacement_matches_only_normalized_metadata(tmp_path):
    from pypdf import PdfWriter
    folder=tmp_path/'行业研报';folder.mkdir()
    pdf=folder/'PD-1_VEGF研究.pdf'
    writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.write(pdf)
    cache=folder/(pdf.name+'_by_PaddleOCR-VL-1.5.json')
    cache.write_text(json.dumps([{'result':{'layoutParsingResults':[{'markdown':{'text':'# 既有正文'}}]}}]),encoding='utf-8')
    meta=ResearchMeta(title='PD-1/VEGF研究',report_type='industry')
    records=prepare(tmp_path,{normalize_title(meta.title):meta})
    assert len(records)==1 and records[0]['metadata_match']=='filename_normalized'
    assert records[0]['pages']==['# 既有正文'] and records[0]['page_count']==1
    assert filename_key('PD-1/VEGF研究')==filename_key('PD-1_VEGF研究')
    with pytest.raises(ValueError, match='ambiguous'): prepare(tmp_path,{})


def test_paged_snapshot_is_readable_without_exposing_disk_paths():
    engine=create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE research_report_material (report_id INTEGER, file_name TEXT, sha256 TEXT, ocr_sha256 TEXT, pages JSON, page_count INTEGER)'))
        conn.execute(text('INSERT INTO research_report_material VALUES (7,:name,:sha,:ocr,:pages,2)'),
                     {'name':'研报.pdf','sha':'pdf-hash','ocr':'ocr-hash','pages':json.dumps(['第一页','第二页'])})
    assert read_pages(engine,7,2)['markdown']=='第二页'
    assert read_pages(engine,7)['sha256']=='pdf-hash'
    assert 'source_path' not in read_pages(engine,7)
    for report_id,page in [(7,3),(7,0),(8,1)]:
        with pytest.raises(HTTPException):read_pages(engine,report_id,page)
    engine.dispose()
