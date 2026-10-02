import hashlib
from src.agent.v3.contracts import StatementScopeProof,Source
from src.utils.statement_scope import checked_statement_scope,physical_caption
from tests.test_v3_contracts import fact


def scoped(monkeypatch,tmp_path):
    path=tmp_path/'original.pdf';path.write_bytes(b'physical-test')
    f=fact('600085',2024,'100',metric='operating_revenue')
    region=lambda text:dict(page=2,bbox=(10,100,200,120),text_sha256=hashlib.sha256(text.encode()).hexdigest())
    proof=StatementScopeProof(caption=dict(page=1,bbox=(10,10,200,25),text='合并利润表'),family='利润表',
        label_region=region('营业收入'),value_region=region('100'))
    f.source=Source(document_id='d',document_version='a'*64,source_sha256='a'*64,source_path='data_root/original.pdf',page=2,
        table='合并利润表',row='营业收入',column='2024年',row_index=1,column_index=1,raw_value='100',raw_unit='元',
        raw_precision=0,literal='营业收入 | 100',extraction='ocr',verification='literal_checked',scope_proof=proof)
    monkeypatch.setattr('src.utils.statement_scope.scope_rectangle_texts',lambda *args:('合并利润表','营业收入','100'))
    monkeypatch.setattr('src.utils.statement_scope.physical_titles',lambda *args:(dict(page=1,top=10,scope='consolidated',family='利润表'),))
    return f,path


def test_physical_scope_rejects_unproved_legacy_source_and_wrong_financial_scope(monkeypatch,tmp_path):
    f,path=scoped(monkeypatch,tmp_path)
    assert checked_statement_scope(f,path)
    f.scope='parent'
    assert not checked_statement_scope(f,path)
    f.scope='consolidated';f.source.scope_proof=None
    assert not checked_statement_scope(f,path)


def test_nearest_parent_title_cannot_be_overridden_by_an_older_consolidated_title(monkeypatch,tmp_path):
    f,path=scoped(monkeypatch,tmp_path)
    monkeypatch.setattr('src.utils.statement_scope.physical_titles',lambda *args:(
        dict(page=1,top=10,scope='consolidated',family='利润表'),dict(page=2,top=50,scope='parent',family='利润表')))
    assert not checked_statement_scope(f,path)


def test_original_region_tampering_and_wrong_source_page_are_rejected(monkeypatch,tmp_path):
    f,path=scoped(monkeypatch,tmp_path)
    monkeypatch.setattr('src.utils.statement_scope.scope_rectangle_texts',lambda *args:('合并利润表','营业收入','-100'))
    assert not checked_statement_scope(f,path)
    f.source.page=3
    assert not checked_statement_scope(f,path)


def test_scope_titles_distinguish_report_family_and_indicator_identity():
    assert physical_caption('2、母公司利润表')==dict(scope='parent',family='利润表')
    assert physical_caption('主要会计数据及财务指标')==dict(scope='consolidated',family='indicators')
    assert physical_caption('归属于母公司股东的净利润') is None
    assert physical_caption('母公司现金流量表')==dict(scope='parent',family='现金流量表')


def test_numeric_quote_reads_the_registered_physical_regions_not_a_neighboring_table(monkeypatch,tmp_path):
    from src.agent.v3.evidence import numeric_quote
    f,path=scoped(monkeypatch,tmp_path)
    f.source.raw_value='-100';f.value='-100'
    monkeypatch.setattr('src.agent.v3.evidence.checked_source',lambda f:True)
    monkeypatch.setattr('src.agent.v3.evidence.source_pdf',lambda s:path)
    monkeypatch.setattr('src.agent.v3.evidence._original_page',lambda *args:'其他报表 营业收入 900')
    monkeypatch.setattr('src.agent.v3.evidence.scope_rectangle_texts',lambda *args:('营业收入','-100'))
    quote=numeric_quote(f)
    assert quote['row_literal']=='营业收入' and quote['value_literal']=='-100' and quote['page_start']==2
