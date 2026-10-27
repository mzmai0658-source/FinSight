"""新报告候选不读取旧逐事实台账，仍使用同一原件核验。"""
from hashlib import sha256
import json

import fitz


def test_new_report_extracts_with_explicit_fresh_cache_and_generated_proofs(tmp_path, monkeypatch):
    import src.etl.canonical_audit as audit
    from src.etl.submission_sources import attach_generated_proofs
    path = tmp_path / 'data_root/report.pdf'
    path.parent.mkdir()
    doc = fitz.open()
    page = doc.new_page(width=600, height=500)
    page.insert_text((40, 20), '测试企业2025年年度报告', fontname='china-s', fontsize=11)
    for text, x, y in [('合并利润表',40,50),('单位：元',40,75),('项目',40,105),
                       ('2025年度',250,105),('2024年度',400,105),('营业收入',40,140),
                       ('1,234.50',250,140),('1,100.00',400,140),('营业成本',40,175),
                       ('900.00',250,175),('800.00',400,175)]:
        page.insert_text((x,y),text,fontname='china-s',fontsize=11)
    doc.save(path)
    doc.close()
    cache = tmp_path / 'fresh-ocr.json'
    blocks = [dict(block_label='paragraph_title',block_content='合并利润表',block_bbox=[0,30,500,60]),
              dict(block_label='text',block_content='单位：元',block_bbox=[0,60,500,80]),
              dict(block_label='table',block_bbox=[0,90,550,190],block_content='''<table>
              <tr><th>项目</th><th>2025年度</th><th>2024年度</th></tr>
              <tr><td>营业收入</td><td>1,234.50</td><td>1,100.00</td></tr>
              <tr><td>营业成本</td><td>900.00</td><td>800.00</td></tr></table>''')]
    cache.write_text(json.dumps([{'markdown':{'text':'测试企业2025年年度报告'},'prunedResult':{'parsing_res_list':blocks}}]),encoding='utf-8')
    monkeypatch.setattr(audit,'ROOT',tmp_path)
    monkeypatch.setattr(audit,'audited_annotations',lambda *a: (_ for _ in ()).throw(AssertionError('旧台账不能参与新公司抽取')))
    report=dict(stock_code='000789',company='测试企业',year=2025,period='FY',source_path='data_root/report.pdf',
                document_version=sha256(path.read_bytes()).hexdigest())
    facts, ledger, narratives = audit.audit_report(report,'new-version',cache_path=cache,
        reference_annotations=False,scope_resolver=lambda values:attach_generated_proofs(values,path))
    values={f.metric:f.value for f in facts}
    assert values['operating_revenue']=='1234.50'
    assert values['operating_cost']=='900.00'
    assert values['gross_profit']=='334.50'
    assert ledger['reviewed_cells']==0
    assert narratives[0]['ocr_sha256']==sha256(cache.read_bytes()).hexdigest()
