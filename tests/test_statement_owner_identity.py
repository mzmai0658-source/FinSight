"""A standalone statement title cannot discard its original preparing entity."""
from hashlib import sha256
import json

import fitz
import pytest

from src.agent.v3.contracts import Fact, Source, OriginalCellProof, StatementScopeProof
from src.agent.v3.evidence import checked_source
from src.etl.submission_sources import bind_original_source, _line_candidates, _captions, _region, _hashed
from src.utils.statement_scope import physical_caption


def document(tmp_path, owner, *, in_ocr=True, packed=True, legal_cover=True, summary_value=None):
    import src.etl.canonical_audit as audit
    path = tmp_path/'data_root/owner.pdf'
    path.parent.mkdir()
    title = ([owner] if owner else []) + ['合并利润表', '2025年度', '单位：元']
    blocks = ([dict(block_label='text', block_content='\n'.join(title))] if packed else
              [dict(block_label='text', block_content=line) for line in title])
    if not in_ocr and owner:
        blocks = [dict(block_label='text', block_content='\n'.join(title[1:]))]
    html = '<table><tr><td>项目</td><td>2025年度</td><td>2024年度</td></tr><tr><td>营业收入</td><td>999</td><td>888</td></tr><tr><td>净利润</td><td>80</td><td>70</td></tr></table>'
    blocks.append(dict(block_label='table', block_content=html))
    summary_html=None
    if summary_value is not None:
        summary_html='<table><tr><td>项目</td><td>2025年度</td><td>2024年度</td></tr><tr><td>营业收入</td><td>'+summary_value+'</td><td>110</td></tr></table>'
        blocks.extend([dict(block_label='paragraph_title',block_content='主要会计数据和财务指标'),
                       dict(block_label='text',block_content='单位：元'),dict(block_label='table',block_content=summary_html)])
    pdf = fitz.open(); page = pdf.new_page(width=650, height=600)
    for y,line in [(25,'陌生制造企业股份有限公司' if legal_cover else '陌生制造企业'), (45,'2025年年度报告')]:
        page.insert_text((20,y),line,fontname='china-s',fontsize=10)
    y=90
    for line in title:
        page.insert_text((20,y),line,fontname='china-s',fontsize=10); y+=25
    for row in audit.grid(html):
        for col,cell in enumerate(row):page.insert_text((20+180*col,y),cell,fontname='china-s',fontsize=10)
        y+=25
    if summary_html:
        for line in ['主要会计数据和财务指标','单位：元']:
            page.insert_text((20,y),line,fontname='china-s',fontsize=10);y+=25
        for row in audit.grid(summary_html):
            for col,cell in enumerate(row):page.insert_text((20+180*col,y),cell,fontname='china-s',fontsize=10)
            y+=25
    pdf.save(path);pdf.close()
    cache=tmp_path/'ocr.json';cache.write_text(json.dumps([dict(prunedResult=dict(parsing_res_list=blocks))]),encoding='utf-8')
    digest=sha256(path.read_bytes()).hexdigest()
    report=dict(stock_code='000789',company='陌生制造企业',year=2025,period='FY',source_path='data_root/owner.pdf',source_sha256=digest)
    fact=Fact(id='owner',data_version='test',stock_code='000789',company=report['company'],year=2025,period='FY',
        metric='operating_revenue',scope='consolidated',value='999',unit='元',status='verified',source=Source(
            document_id='pdf-'+digest,document_version=digest,source_sha256=digest,source_path=report['source_path'],
            page=1,table='合并利润表',row='营业收入',column='2025年度',row_index=1,column_index=1,
            raw_value='999',raw_unit='元',raw_precision=0,literal='营业收入 | 999 | 888',extraction='ocr',verification='literal_checked'))
    return path,cache,report,fact


def unchecked_proof(path, fact):
    """Build valid PDF rectangles directly to exercise the independent checker."""
    stat=path.stat();wanted=physical_caption(fact.source.table)
    value,label,heading,column_scope=next(_line_candidates(fact,path,stat,['999','888'],0,wanted))
    caption=next(c for c in _captions(str(path),stat.st_size,stat.st_mtime_ns) if c['family']=='利润表')
    x0,y0,x1,y1=caption['bbox'];title=_region(path,stat,1,(x0-.3,y0-.3,x1+.3,y1+.3))
    context=_region(path,stat,1,(0,y0-.3,650,value.bbox[3]))
    fact.source.original_cell=OriginalCellProof(value=value,row=label,heading=heading,context=context)
    fact.source.scope_proof=StatementScopeProof(caption=title,family='利润表',value_region=_hashed(value),label_region=_hashed(label))
    return fact


@pytest.mark.parametrize('owner', ['编制单位：其他集团股份有限公司', '异域制造有限责任公司', '陌生制造企业财务有限公司'])
@pytest.mark.parametrize('in_ocr', [True,False])
def test_original_foreign_owner_cannot_become_a_verified_report_fact(tmp_path,monkeypatch,owner,in_ocr):
    from src.etl.ocr_quality import extract_fresh_document
    import src.etl.canonical_audit as audit
    path,cache,report,fact=document(tmp_path,owner,in_ocr=in_ocr)
    monkeypatch.setattr(audit,'ROOT',tmp_path);monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    values,ledger,_=extract_fresh_document(report,'test',cache)
    assert not values
    assert bind_original_source(fact,path) is None
    assert not checked_source(unchecked_proof(path,fact))


@pytest.mark.parametrize('owner', [None,'编制单位：陌生制造企业股份有限公司','陌生制造企业股份有限公司'])
def test_report_cover_legal_owner_or_no_explicit_owner_preserves_real_proofs(tmp_path,monkeypatch,owner):
    path,cache,report,fact=document(tmp_path,owner)
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    bound=bind_original_source(fact,path)
    assert bound is not None
    assert checked_source(bound)


def test_unknown_legal_owner_retains_original_candidates_as_unverified(tmp_path,monkeypatch):
    from src.etl.ocr_quality import extract_fresh_document
    path,cache,report,fact=document(tmp_path,'陌生制造企业股份有限公司',legal_cover=False)
    monkeypatch.setattr('src.etl.canonical_audit.ROOT',tmp_path)
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    values,ledger,_=extract_fresh_document(report,'test',cache)
    revenue=next((value for value in values if value.metric=='operating_revenue'),None)
    assert revenue is not None
    assert revenue.value=='999' and revenue.status=='unverified'
    assert any(row['value']=='999' for row in ledger['source_candidates'])
    assert bind_original_source(fact,path) is None


def test_unknown_owner_does_not_remove_a_genuine_amount_conflict(tmp_path,monkeypatch):
    from src.etl.ocr_quality import extract_fresh_document
    path,cache,report,_=document(tmp_path,'陌生制造企业股份有限公司',legal_cover=False,summary_value='123')
    monkeypatch.setattr('src.etl.canonical_audit.ROOT',tmp_path)
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    values,ledger,_=extract_fresh_document(report,'test',cache)
    assert not any(value.metric=='operating_revenue' for value in values)
    assert {'999','123'} <= {row['value'] for row in ledger['source_candidates'] if row['metric']=='operating_revenue'}
    assert any(row['metric']=='operating_revenue' and row['reason']=='source_conflict' for row in ledger['conflicts'])
