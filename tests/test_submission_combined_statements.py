"""同一张财务表的列维度区分合并/母公司，企业名称不参与解析规则。"""
from decimal import Decimal
import hashlib
import json

import fitz


def test_currency_and_row_identity_accept_explicit_rmb_thousand_unit():
    from src.etl.canonical_audit import unit_in
    from src.utils.financial_row_identity import row_key
    assert unit_in('单位：人民币千元') == ('千元', Decimal(1000))
    assert row_key('归属于上市公司股东的净利润（千元）') == '归属于上市公司股东的净利润'


def test_combined_statement_caption_requires_column_scope_proof():
    from src.utils.statement_scope import physical_caption
    assert physical_caption('合并及公司利润表') == {'scope':'mixed','family':'利润表'}
    assert physical_caption('合并及公司资产负债表（续）') == {'scope':'mixed','family':'资产负债表'}


def test_shared_statement_preserves_separate_consolidated_and_parent_columns(tmp_path, monkeypatch):
    import src.etl.canonical_audit as audit
    from src.etl.submission_sources import attach_generated_proofs
    from src.utils.statement_scope import checked_statement_scope
    path = tmp_path / 'data_root/shared.pdf'
    path.parent.mkdir()
    doc = fitz.open()
    page = doc.new_page(width=680,height=400)
    for value,x,y in [('陌生企业2025年第一季度报告',25,25),('合并及公司利润表',25,55),('单位：人民币千元',25,80),
        ('项目',25,110),('2025年1-3月',220,110),('2024年1-3月',330,110),('2025年1-3月',440,110),('2024年1-3月',550,110),
        ('合并',230,130),('合并',340,130),('公司',450,130),('公司',560,130),
        ('营业收入',25,165),('123.45',235,165),('110.00',345,165),('80.00',455,165),('70.00',565,165),
        ('净利润',25,200),('20.00',235,200),('18.00',345,200),('8.00',455,200),('7.00',565,200)]:
        page.insert_text((x,y),value,fontname='china-s',fontsize=10)
    doc.save(path)
    doc.close()
    payload=[{'markdown':{'text':'陌生企业2025年第一季度报告'},'prunedResult':{'parsing_res_list':[
        dict(block_label='paragraph_title',block_content='合并及公司利润表',block_bbox=[0,40,650,60]),
        dict(block_label='vision_footnote',block_content='单位：人民币千元',block_bbox=[0,60,650,85]),
        dict(block_label='table',block_bbox=[0,95,650,220],block_content='''<table>
        <tr><td>项目</td><td>2025年1-3月</td><td>2024年1-3月</td><td>2025年1-3月</td><td>2024年1-3月</td></tr>
        <tr><td>项目</td><td>合并</td><td>合并</td><td>公司</td><td>公司</td></tr>
        <tr><td>营业收入</td><td>123.45</td><td>110.00</td><td>80.00</td><td>70.00</td></tr>
        <tr><td>净利润</td><td>20.00</td><td>18.00</td><td>8.00</td><td>7.00</td></tr></table>''')
    ]}}]
    cache=tmp_path/'new.json'
    cache.write_text(json.dumps(payload),encoding='utf-8')
    monkeypatch.setattr(audit,'ROOT',tmp_path)
    report=dict(stock_code='000789',company='陌生企业',year=2025,period='Q1',source_path='data_root/shared.pdf',
                document_version=hashlib.sha256(path.read_bytes()).hexdigest())
    facts,ledger,_=audit.audit_report(report,'v',cache_path=cache,reference_annotations=False,
        scope_resolver=lambda values:attach_generated_proofs(values,path))
    observed={(f.metric,f.scope):f.value for f in facts}
    assert observed['operating_revenue','consolidated']=='123450.00'
    assert observed['operating_revenue','parent']=='80000.00'
    assert observed['net_profit','consolidated']=='20000.00'
    assert observed['net_profit','parent']=='8000.00'
    assert all(checked_statement_scope(f,path) for f in facts if f.source)
