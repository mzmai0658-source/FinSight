from decimal import Decimal
import hashlib
import pytest
from src.utils.financial_numbers import cell_decimal,normalize_cell
from src.agent.v3.contracts import Fact,Source
from src.agent.v3.evidence import checked_source

@pytest.fixture(autouse=True)
def separately_verified_scope(monkeypatch):
    # 作品说明：用特意构造的非 PDF 字节样例隔离数值、单位、身份和归档检查；物理口径另有专门测试。
    monkeypatch.setattr('src.agent.v3.evidence.checked_statement_scope',lambda *args:True)

def test_unit_normalization_keeps_amount_share_ratio_and_wrapped_precision_distinct():
    assert normalize_cell('1,234.50','万元','元')==Decimal('12345000')
    assert normalize_cell('1,003,471,054\n.23','元','元')==Decimal('1003471054.23')
    assert cell_decimal('（1,234.50）')==Decimal('-1234.50')
    assert normalize_cell('0.0033','元/股','元') is None
    assert normalize_cell('10.76','万元','%') is None
    assert normalize_cell('1 2','元','元')==Decimal(12)  # 作品说明：只核验已定位的单个单元格。
    assert cell_decimal('不适用') is None

def test_original_number_presence_does_not_certify_a_different_normalized_value(monkeypatch,tmp_path):
    root=tmp_path/'data_root';root.mkdir();path=root/'source.pdf';path.write_bytes(b'%PDF-fixture')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    monkeypatch.setattr('src.agent.v3.evidence._original_page',lambda *args:'营业收入 1,234.50 单位：万元')
    source=Source(document_id='pdf-'+digest,document_version=digest,source_sha256=digest,source_path='data_root/source.pdf',
        page=1,table='income',row='营业收入',column='2024年',row_index=1,column_index=1,
        raw_value='1,234.50',raw_unit='万元',raw_precision=2,literal='营业收入 | 1,234.50',extraction='ocr',verification='literal_checked')
    fact=Fact(id='f',data_version='v',stock_code='600085',company='同仁堂',year=2024,period='FY',metric='operating_revenue',
        scope='consolidated',value='12345000',unit='元',status='verified',source=source)
    assert checked_source(fact)
    fact.value='1234.50'
    assert not checked_source(fact)
    fact.value='12345000';fact.source.raw_precision=1
    assert not checked_source(fact)
    fact.source.raw_precision=2;fact.source.document_version='different-original'
    assert not checked_source(fact)


def test_source_value_cannot_certify_another_metric_or_report_period(monkeypatch,tmp_path):
    root=tmp_path/'data_root';root.mkdir();path=root/'source.pdf';path.write_bytes(b'%PDF-fixture')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    monkeypatch.setattr('src.agent.v3.evidence._original_page',lambda *args:'营业收入 1,234.50 单位：万元')
    source=Source(document_id='pdf-'+digest,document_version=digest,source_sha256=digest,source_path='data_root/source.pdf',
        page=1,table='合并利润表',row='营业收入',column='2024年',row_index=1,column_index=1,
        raw_value='1,234.50',raw_unit='万元',raw_precision=2,literal='营业收入 | 1,234.50',extraction='ocr',verification='literal_checked')
    fact=Fact(id='f',data_version='v',stock_code='600085',company='同仁堂',year=2024,period='FY',metric='operating_revenue',
        scope='consolidated',value='12345000',unit='元',status='verified',source=source)
    assert checked_source(fact)
    fact.metric='total_revenue';assert not checked_source(fact)
    fact.metric='operating_revenue';fact.year=2023;assert not checked_source(fact)


def test_retained_original_revalidates_quotes_after_upload_is_replaced(monkeypatch,tmp_path):
    from src.utils.original_archive import retain_original
    from src.agent.v3.evidence import numeric_quote
    root=tmp_path/'data_root';root.mkdir();path=root/'source.pdf';path.write_bytes(b'%PDF-original-fixture')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    retain_original(path,root=root/'original_versions')
    path.write_bytes(b'%PDF-new-version')
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    monkeypatch.setattr('src.agent.v3.evidence._original_page',lambda *args:'营业收入 1,234.50 单位：万元 2024年')
    source=Source(document_id='pdf-'+digest,document_version=digest,source_sha256=digest,source_path='data_root/source.pdf',
        page=1,table='合并利润表',row='营业收入',column='2024年',row_index=1,column_index=1,
        raw_value='1,234.50',raw_unit='万元',raw_precision=2,literal='营业收入 | 1,234.50',extraction='ocr',verification='literal_checked')
    fact=Fact(id='f',data_version='v',stock_code='600085',company='同仁堂',year=2024,period='FY',metric='operating_revenue',
        scope='consolidated',value='12345000',unit='元',status='verified',source=source)
    assert checked_source(fact) and numeric_quote(fact)['document_version']==digest
    path.unlink()
    assert checked_source(fact) and numeric_quote(fact)['value_literal']=='1,234.50'
