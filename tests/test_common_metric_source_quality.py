"""Common source proofs retain physical page, period, unit and scope boundaries."""
from hashlib import sha256
import json

import fitz
import pytest

from src.agent.v3.contracts import Fact, Source
from src.agent.v3.evidence import checked_source
from src.etl.submission_sources import bind_original_source
from tests.test_source_binding_repair import grid, text


def direct_fact(path, *, metric='operating_revenue', row='营业收入', raw='12,345.00',
                value='12345000.00', unit='元', raw_unit='千元', page=1, year=2027,
                period='FY', column='2027年度', table='合并利润表'):
    digest = sha256(path.read_bytes()).hexdigest()
    return Fact(id='common-metric-proof', data_version='proof-test', stock_code='000789',
        company='未预设企业', year=year, period=period, metric=metric, scope='consolidated',
        value=value, unit=unit, status='verified', source=Source(document_id='pdf-'+digest,
            document_version=digest, source_sha256=digest, source_path='data_root/report.pdf',
            page=page, table=table, row=row, column=column, row_index=1, column_index=1,
            raw_value=raw, raw_unit=raw_unit, raw_precision=2,
            literal=f'{row} | {raw} | 11,000.00', extraction='ocr', verification='literal_checked'))


@pytest.mark.parametrize('year,metric,row,raw,value,unit,raw_unit', [
    (2027,'operating_revenue','营业收入','12,345.00','12345000.00','元','千元'),
    (2029,'eps_basic','基本每股收益（元/股）','1.23','1.23','元/股','元/股'),
])
def test_title_at_previous_page_bottom_can_identify_current_page_table(
        tmp_path, monkeypatch, year, metric, row, raw, value, unit, raw_unit):
    path=tmp_path/'data_root/report.pdf';path.parent.mkdir()
    doc=fitz.open()
    previous=doc.new_page(width=620,height=600)
    text(previous,40,550,'合并利润表')
    current=doc.new_page(width=620,height=600)
    text(current,40,40,'单位：千元')
    grid(current,65,[(25,['项目',f'{year}年度',f'{year-1}年度']),
                     (30,[row,raw,'11,000.00'])])
    doc.save(path);doc.close()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    fact=direct_fact(path,metric=metric,row=row,raw=raw,value=value,unit=unit,
        raw_unit=raw_unit,page=2,year=year,column=f'{year}年度')
    bound=bind_original_source(fact,path)
    assert bound is not None
    assert bound.source.scope_proof.caption.page==1
    assert bound.source.original_cell.heading.page==2
    assert bound.source.original_cell.context.page==2
    assert checked_source(bound)


def test_equivalent_parentheses_in_quarter_header_pass_independent_binding(tmp_path,monkeypatch):
    path=tmp_path/'data_root/report.pdf';path.parent.mkdir()
    doc=fitz.open();page=doc.new_page(width=620,height=500)
    text(page,40,55,'合并利润表');text(page,40,80,'单位：千元')
    grid(page,105,[(40,['项目','2027年前三季度\n（1-9月）','2026年前三季度\n（1-9月）']),
                   (30,['营业收入','12,345.00','11,000.00'])])
    doc.save(path);doc.close()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    fact=direct_fact(path,period='Q3',column='2027年前三季度(1-9月)')
    bound=bind_original_source(fact,path)
    assert bound is not None
    assert '（1-9月）' in bound.source.original_cell.heading.text
    assert fact.source.column=='2027年前三季度(1-9月)'
    assert checked_source(bound)


def test_indicator_column_label_does_not_hide_section_unit(tmp_path,monkeypatch):
    path=tmp_path/'data_root/report.pdf';path.parent.mkdir()
    doc=fitz.open();page=doc.new_page(width=620,height=500)
    text(page,40,55,'主要会计数据和财务指标');text(page,40,80,'单位：千元')
    grid(page,105,[(30,['主要会计数据','2027年','2026年']),
                   (40,['归属于上市公司股东的扣除非\n经常性损益的净利润','12,345.00','11,000.00'])])
    doc.save(path);doc.close()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    fact=direct_fact(path,metric='deducted_attributable_net_profit',
        row='归属于上市公司股东的扣除非经常性损益的净利润',column='2027年',
        table='主要会计数据和财务指标')
    bound=bind_original_source(fact,path)
    assert bound is not None
    assert bound.source.scope_proof.caption.text=='主要会计数据和财务指标'
    assert '单位：千元' in bound.source.original_cell.context.text
    assert checked_source(bound)


def test_unit_below_previous_page_title_belongs_to_following_table(tmp_path,monkeypatch):
    path=tmp_path/'data_root/report.pdf';path.parent.mkdir()
    doc=fitz.open();previous=doc.new_page(width=620,height=600)
    text(previous,40,535,'合并现金流量表');text(previous,40,565,'单位：千元')
    current=doc.new_page(width=620,height=600)
    grid(current,65,[(25,['项目','2027年度','2026年度']),
                     (30,['经营活动产生的现金流量净额','12,345.00','11,000.00'])])
    doc.save(path);doc.close()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    fact=direct_fact(path,metric='operating_cash_flow',row='经营活动产生的现金流量净额',
        page=2,table='合并现金流量表')
    bound=bind_original_source(fact,path)
    assert bound is not None
    assert bound.source.original_cell.context.page==1
    assert '单位：千元' in bound.source.original_cell.context.text
    assert checked_source(bound)


def intervening_statement_fixture(tmp_path,monkeypatch):
    path=tmp_path/'data_root/report.pdf';path.parent.mkdir()
    doc=fitz.open();previous=doc.new_page(width=620,height=600)
    text(previous,40,50,'合并利润表');text(previous,40,80,'单位：千元')
    grid(previous,110,[(25,['项目','2027年度','2026年度']),
                      (30,['营业成本','9,000.00','8,000.00'])])
    text(previous,40,260,'母公司现金流量表');text(previous,40,290,'单位：万元')
    grid(previous,320,[(25,['项目','2027年度','2026年度']),
                      (30,['经营活动产生的现金流量净额','15,000.00','14,000.00'])])
    current=doc.new_page(width=620,height=600)
    grid(current,65,[(25,['项目','2027年度','2026年度']),
                     (30,['营业收入','12,345.00','11,000.00'])])
    caption_box=tuple(doc[0].search_for('合并利润表')[0])
    unit_box=tuple(doc[0].search_for('单位：万元')[0])
    doc.save(path);doc.close()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    fact=direct_fact(path,page=2,raw_unit='万元',value='123450000.00')
    return path,fact,caption_box,unit_box


def test_previous_caption_cannot_borrow_unit_across_intervening_statement(tmp_path,monkeypatch):
    path,fact,_,_=intervening_statement_fixture(tmp_path,monkeypatch)
    assert bind_original_source(fact,path) is None


def test_independent_proof_rejects_unit_after_intervening_statement_caption(tmp_path,monkeypatch):
    """Every rectangle is real PDF text; the association between them is invalid."""
    import pdfplumber
    from src.agent.v3.contracts import OriginalRegion,OriginalCellProof,StatementScopeProof,HashedOriginalRegion
    path,fact,caption_box,unit_box=intervening_statement_fixture(tmp_path,monkeypatch)
    with pdfplumber.open(path) as pdf:
        def region(number,box):
            return OriginalRegion(page=number,bbox=box,text=pdf.pages[number-1].crop(box).extract_text() or '')
        caption=region(1,(caption_box[0]-.3,caption_box[1]-.3,caption_box[2]+.3,caption_box[3]+.3))
        value=region(2,(280.,90.,420.,120.))
        label=region(2,(40.,90.,279.9,120.))
        heading=region(2,(280.,65.,420.,90.))
        context=region(1,(0.,unit_box[1]-.25,620.,600.))
    hashed=lambda r:HashedOriginalRegion(page=r.page,bbox=r.bbox,text_sha256=sha256(r.text.encode()).hexdigest())
    fact.source.original_cell=OriginalCellProof(value=value,row=label,heading=heading,context=context)
    fact.source.scope_proof=StatementScopeProof(caption=caption,family='利润表',
        value_region=hashed(value),label_region=hashed(label))
    assert '单位：万元' in context.text
    assert not checked_source(fact)


def test_previous_indicator_title_ignores_current_table_column_caption(tmp_path,monkeypatch):
    path=tmp_path/'data_root/report.pdf';path.parent.mkdir()
    doc=fitz.open();previous=doc.new_page(width=620,height=600)
    text(previous,40,550,'主要会计数据和财务指标')
    current=doc.new_page(width=620,height=600);text(current,40,40,'单位：千元')
    grid(current,65,[(20,['主要会计数据','','']),
                     (25,['','2027年','2026年']),
                     (30,['营业收入','12,345.00','11,000.00'])])
    doc.save(path);doc.close()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    fact=direct_fact(path,page=2,column='2027年',table='主要会计数据和财务指标')
    bound=bind_original_source(fact,path)
    assert bound is not None and checked_source(bound)
    assert bound.source.scope_proof.caption.page==1


@pytest.mark.parametrize('cover_year,verified',[(2027,True),(2026,False)])
def test_halfyear_current_report_column_uses_original_report_identity(
        tmp_path,monkeypatch,cover_year,verified):
    path=tmp_path/'data_root/report.pdf';path.parent.mkdir()
    doc=fitz.open();cover=doc.new_page(width=620,height=600)
    text(cover,40,55,f'证券代码：000789 未预设企业 {cover_year}年半年度报告')
    text(cover,40,550,'主要会计数据和财务指标')
    current=doc.new_page(width=620,height=600)
    grid(current,65,[(25,['项目','本报告期','上年同期']),
                     (30,['基本每股收益（元/股）','1.23','11,000.00'])])
    doc.save(path);doc.close()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    fact=direct_fact(path,metric='eps_basic',row='基本每股收益（元/股）',raw='1.23',
        value='1.23',unit='元/股',raw_unit='元/股',page=2,period='HY',
        column='本报告期',table='主要会计数据和财务指标')
    bound=bind_original_source(fact,path)
    assert (bound is not None)==verified
    if bound is not None:assert checked_source(bound)


def extract_two_eps_sources(tmp_path,monkeypatch,*,summary='1.2300',formal='1.2300',explicit_unit=True,
                            fullwidth_slash=False,summary_metric='eps_basic'):
    """The primary statement has no disclosed per-share unit; the summary can prove it."""
    from src.etl.ocr_quality import extract_fresh_document
    path=tmp_path/'data_root/report.pdf';path.parent.mkdir()
    label='基本每股收益' if summary_metric=='eps_basic' else '稀释每股收益'
    summary_row=label+'（元/股）' if explicit_unit else label
    pdf_summary_row=summary_row.replace('/','／') if fullwidth_slash else summary_row
    doc=fitz.open();first=doc.new_page(width=620,height=600)
    text(first,40,30,'证券代码：000789 未预设企业 2027年年度报告')
    text(first,40,65,'主要会计数据和财务指标')
    grid(first,100,[(30,['项目','2027年','2026年']),
                    (30,[pdf_summary_row,summary,'1.1000'])])
    second=doc.new_page(width=620,height=600)
    text(second,40,55,'合并利润表');text(second,40,80,'单位：千元')
    grid(second,110,[(30,['项目','2027年度','2026年度']),
                     (30,['（一）基本每股收益',formal,'1.1000'])])
    doc.save(path);doc.close()
    def block(content,y,label='text'):
        return dict(block_label=label,block_content=content,block_bbox=[40,y,560,y+20])
    def table(rows):
        return '<table>'+''.join('<tr>'+''.join('<td>'+c+'</td>' for c in row)+'</tr>' for row in rows)+'</table>'
    cache=tmp_path/'ocr.json'
    pages=[
        [block('主要会计数据和财务指标',55),block(table([
            ['项目','2027年','2026年'],[summary_row,summary,'1.1000']]),100,'table')],
        [block('合并利润表',45),block('单位：千元',70),block(table([
            ['项目','2027年度','2026年度'],['（一）基本每股收益',formal,'1.1000']]),110,'table')],
    ]
    cache.write_text(json.dumps([{'prunedResult':{'parsing_res_list':items}} for items in pages]),encoding='utf-8')
    monkeypatch.setattr('src.etl.canonical_audit.ROOT',tmp_path)
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    digest=sha256(path.read_bytes()).hexdigest()
    return extract_fresh_document(dict(stock_code='000789',company='未预设企业',year=2027,period='FY',
        source_path='data_root/report.pdf',document_version=digest,source_sha256=digest),'source-choice',cache)


def test_exact_equal_alternate_source_can_supply_independent_per_share_proof(tmp_path,monkeypatch):
    facts,ledger,_=extract_two_eps_sources(tmp_path,monkeypatch)
    value=next(v for v in facts if v.metric=='eps_basic')
    assert value.status=='verified' and checked_source(value)
    assert value.value=='1.2300' and value.source.raw_value=='1.2300'
    assert value.source.page==1 and value.source.row=='基本每股收益（元/股）'
    assert ledger['source_binding']['remaining']==[]
    selection=next(r for r in ledger['source_selection'] if r['metric']=='eps_basic')
    assert len(selection['candidates'])==2
    assert {r['source']['page'] for r in selection['candidates']}=={1,2}
    assert selection['selected_source']['page']==1


def test_conflicting_eps_disclosures_are_retained_before_source_selection(tmp_path,monkeypatch):
    facts,ledger,_=extract_two_eps_sources(tmp_path,monkeypatch,summary='1.9900')
    assert not any(v.metric=='eps_basic' for v in facts)
    conflict=next(r for r in ledger['conflicts'] if r['metric']=='eps_basic')
    assert {r['value'] for r in conflict['candidates']}=={'1.2300','1.9900'}


@pytest.mark.parametrize('summary,explicit_unit',[('1.2',True),('1.2300',False)])
def test_rounding_or_missing_per_share_unit_does_not_upgrade_original_candidate(
        tmp_path,monkeypatch,summary,explicit_unit):
    facts,ledger,_=extract_two_eps_sources(tmp_path,monkeypatch,summary=summary,explicit_unit=explicit_unit)
    value=next(v for v in facts if v.metric=='eps_basic')
    assert value.status=='unverified' and value.value=='1.2300'
    assert value.source.page==2 and value.source.original_cell is None
    assert ledger['source_binding']['unverified']==1
    assert len(ledger['source_binding']['remaining'])==1
    assert ledger['source_binding']['remaining'][0]['page']==2
    candidates=[r for r in ledger.get('source_candidates',[]) if r['metric']=='eps_basic']
    assert len(candidates)==2
    assert {r['source']['page'] for r in candidates}=={1,2}


def test_fullwidth_per_share_slash_is_proved_without_rewriting_source_text(tmp_path,monkeypatch):
    facts,ledger,_=extract_two_eps_sources(tmp_path,monkeypatch,fullwidth_slash=True)
    value=next(v for v in facts if v.metric=='eps_basic')
    assert value.status=='verified' and checked_source(value)
    assert value.source.row=='基本每股收益（元/股）'
    assert value.source.original_cell.row.text=='基本每股收益（元／股）'


def test_diluted_eps_proof_cannot_upgrade_equal_number_basic_eps(tmp_path,monkeypatch):
    facts,ledger,_=extract_two_eps_sources(tmp_path,monkeypatch,summary_metric='eps_diluted')
    basic=next(v for v in facts if v.metric=='eps_basic')
    diluted=next(v for v in facts if v.metric=='eps_diluted')
    assert basic.status=='unverified' and basic.source.page==2
    assert diluted.status=='verified' and diluted.source.page==1 and checked_source(diluted)
    assert basic.decimal==diluted.decimal
