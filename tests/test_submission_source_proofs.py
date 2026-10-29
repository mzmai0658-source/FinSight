"""通用物理来源绑定使用单元测试夹具，正式语料仍全部来自真实新财报。"""
from hashlib import sha256

import fitz
import pytest

from src.agent.v3.contracts import Fact, Source


def pdf_fixture(path, second=False):
    doc = fitz.open()
    page = doc.new_page(width=600, height=500)
    for title, top, amount in [('合并利润表', 40, '1,234.50'), *([('母公司利润表', 240, '1,234.50')] if second else [])]:
        page.insert_text((40, top), title, fontname='china-s', fontsize=11)
        page.insert_text((40, top + 25), '单位：元', fontname='china-s', fontsize=11)
        page.insert_text((40, top + 55), '项目', fontname='china-s', fontsize=11)
        page.insert_text((250, top + 55), '2025年度', fontname='china-s', fontsize=11)
        page.insert_text((400, top + 55), '2024年度', fontname='china-s', fontsize=11)
        page.insert_text((40, top + 90), '营业收入', fontname='china-s', fontsize=11)
        page.insert_text((250, top + 90), amount, fontsize=11)
        page.insert_text((400, top + 90), '1,100.00', fontsize=11)
        page.insert_text((40, top + 125), '营业成本', fontname='china-s', fontsize=11)
        page.insert_text((250, top + 125), '900.00', fontsize=11)
        page.insert_text((400, top + 125), '800.00', fontsize=11)
    doc.save(path)
    doc.close()


def fact(path, scope='consolidated', raw='1,234.50', column='2025年度', unit='元'):
    digest = sha256(path.read_bytes()).hexdigest()
    return Fact(id='f', data_version='v', stock_code='000789', company='未预设的企业', year=2025,
                period='FY', metric='operating_revenue', scope=scope, value='1234.50', unit='元', status='verified',
                source=Source(document_id='pdf-' + digest, document_version=digest, source_sha256=digest,
                    source_path='data_root/report.pdf', page=1, table=('母公司' if scope=='parent' else '合并')+'利润表',
                    row='营业收入', column=column, row_index=1, column_index=1,
                    raw_value=raw, raw_unit=unit, raw_precision=2, literal=f'营业收入 | {raw} | 1,100.00',
                    extraction='ocr', verification='literal_checked'))


def test_auto_scope_proof_is_generated_from_unknown_document_and_preserves_scope(tmp_path):
    from src.etl.submission_sources import bind_original_source
    from src.utils.statement_scope import checked_statement_scope
    path = tmp_path / 'report.pdf'
    pdf_fixture(path, second=True)
    for scope in ('consolidated', 'parent'):
        result = bind_original_source(fact(path, scope), path)
        assert result is not None
        assert result.source.scope_proof is not None
        assert checked_statement_scope(result, path)


@pytest.mark.parametrize('change', [dict(raw='900.00'), dict(column='2023年度'), dict(unit='万元')])
def test_wrong_row_column_or_unit_is_not_rescued_by_matching_number_elsewhere(tmp_path, change):
    from src.etl.submission_sources import bind_original_source
    path = tmp_path / 'report.pdf'
    pdf_fixture(path)
    assert bind_original_source(fact(path, **change), path) is None
