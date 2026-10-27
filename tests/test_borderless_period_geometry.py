"""Blank note cells must not widen amount columns over the caption's date."""
from hashlib import sha256

import fitz
import pytest

from src.agent.v3.contracts import Fact, Source
from src.agent.v3.evidence import checked_source
from src.etl.submission_sources import bind_original_source


def candidate_pdf(tmp_path, *, actual_year=2027, first=400, second=510):
    path = tmp_path/'data_root/report.pdf'
    path.parent.mkdir()
    document = fitz.open()
    page = document.new_page(width=650, height=500)
    for x,y,value in [(40,40,'陌生制造企业2027年年度报告'), (250,70,'合并利润表'),
                       (265,95,'2027年度'), (240,120,'(金额单位：人民币千元)'),
                       (40,170,'项目'), (285,170,'附注'),
                       (first,170,f'{actual_year}年度'), (second,170,'2026年度'),
                       (40,220,'净利润'), (first,220,'12,345.00'), (second,220,'11,000.00')]:
        page.insert_text((x,y), value, fontname='china-s', fontsize=10)
    document.save(path)
    document.close()
    digest = sha256(path.read_bytes()).hexdigest()
    fact = Fact(id='borderless', data_version='test', stock_code='000789', company='陌生制造企业',
        year=2027, period='FY', metric='net_profit', scope='consolidated', value='12345000.00',
        unit='元', status='verified', source=Source(document_id='pdf-'+digest, document_version=digest,
            source_sha256=digest, source_path='data_root/report.pdf', page=1, table='合并利润表',
            row='净利润', column='2027年度', row_index=1, column_index=2, raw_value='12,345.00',
            raw_unit='千元', raw_precision=2, literal='净利润 |  | 12,345.00 | 11,000.00',
            extraction='ocr', verification='literal_checked'))
    return path, fact


@pytest.mark.parametrize('positions', [(400,510), (350,490)])
def test_blank_note_cell_binds_actual_aligned_period_instead_of_centered_caption_date(tmp_path, monkeypatch, positions):
    path, fact = candidate_pdf(tmp_path, first=positions[0], second=positions[1])
    monkeypatch.setattr('src.agent.v3.evidence.ROOT', tmp_path)
    diagnostics = []
    bound = bind_original_source(fact, path, diagnostics=diagnostics)
    assert bound is not None, diagnostics
    assert bound.source.original_cell.heading.bbox[1] > 150
    assert checked_source(bound)


def test_matching_caption_date_cannot_hide_an_actual_previous_year_column(tmp_path):
    path, fact = candidate_pdf(tmp_path, actual_year=2026)
    assert bind_original_source(fact, path) is None
