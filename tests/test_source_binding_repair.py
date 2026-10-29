"""Cell proofs come from the PDF geometry; OCR supplies candidates, never evidence."""
from hashlib import sha256
import json
from pathlib import Path

import fitz
import pytest

from src.agent.v3.contracts import Fact, Source
from src.etl.submission_sources import bind_original_source
from src.agent.v3.evidence import checked_source


ROOT = Path(__file__).resolve().parents[1]
LABEL = '归属于上市公司股东的扣除非经常性损益的净利润（千元）'


def text(page, x, y, value, size=9):
    page.insert_text((x, y), value, fontname='china-s', fontsize=size)


def grid(page, top, rows, shift=0):
    xs = [40 + shift, 280 + shift, 420 + shift, 560 + shift]
    ys = [top]
    for height, _ in rows:
        ys.append(ys[-1] + height)
    for x in xs:
        page.draw_line((x, top), (x, ys[-1]), width=.5)
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y), width=.5)
    for ri, (_, cells) in enumerate(rows):
        for ci, value in enumerate(cells):
            for li, line in enumerate(value.split('\n')):
                text(page, xs[ci] + 5, ys[ri] + 15 + li * 12, line)


def make_pdf(path, *, year=2027, continuation=False, shift=0, split=False,
             legal_unit=False, mixed=False, gap=False):
    path.parent.mkdir(exist_ok=True)
    doc = fitz.open()
    first = doc.new_page(width=620, height=500)
    top = 365 if continuation else 130
    text(first, 40, top - 65, '合并及公司利润表' if mixed else '合并利润表')
    text(first, 40, top - 40, '除特别注明外，金额单位为人民币千元' if legal_unit else '单位：千元')
    header = ['项目', f'{year}年度', f'{year-1}年度']
    wrapped = '归属于上市公司股东的扣除非经常性损\n益的净利润（千元）'
    value = '12,345.\n00' if split else '12,345.00'
    if continuation:
        grid(first, top, [(25, header), (40, ['营业收入（千元）', '22,345.00', '21,000.00'])])
        if gap:
            doc.new_page(width=620, height=500)
        page = doc.new_page(width=620, height=500)
        grid(page, 55, [(40, [wrapped, value, '11,000.00']),
                        (30, ['营业成本（千元）', '9,000.00', '8,000.00'])], shift)
    else:
        if mixed:
            header = ['项目', f'{year}年度\n合并', f'{year}年度\n公司']
        if legal_unit:
            wrapped = '营业收入'
        grid(first, top, [(40, header), (40, [wrapped, value, '11,000.00']),
                         (30, ['营业成本（千元）', '9,000.00', '8,000.00'])])
    doc.save(path)
    doc.close()


def candidate(path, *, year=2027, page=1, row=LABEL, column=None, raw='12,345.00',
              scope='consolidated', mixed=False):
    digest = sha256(path.read_bytes()).hexdigest()
    return Fact(id='unknown-company-cell', data_version='repair-test', stock_code='000789',
                company='未预设样本企业', year=year, period='FY',
                metric='operating_revenue' if row == '营业收入' else 'deducted_attributable_net_profit',
                scope=scope, value='12345000.00', unit='元', status='verified',
                source=Source(document_id='pdf-' + digest, document_version=digest,
                    source_sha256=digest, source_path='data_root/report.pdf', page=page,
                    table='合并及公司利润表' if mixed else '合并利润表', row=row,
                    column=column or f'{year}年度', row_index=1, column_index=1,
                    raw_value=raw, raw_unit='千元', raw_precision=2,
                    literal=f'{row} | {raw} | 11,000.00', extraction='ocr', verification='literal_checked'))


@pytest.mark.parametrize('year', [2026, 2028])
def test_wrapped_label_gets_original_cell_proof_without_changing_original_digest(tmp_path, monkeypatch, year):
    path = tmp_path / 'data_root/report.pdf'
    make_pdf(path, year=year)
    before = sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT', tmp_path)
    bound = bind_original_source(candidate(path, year=year), path)
    assert bound is not None
    assert bound.source.original_cell is not None
    assert '\n' in bound.source.original_cell.row.text
    assert checked_source(bound)
    assert bound.source.source_sha256 == before == sha256(path.read_bytes()).hexdigest()


def test_split_number_is_joined_only_inside_the_ruled_numeric_cell(tmp_path, monkeypatch):
    path = tmp_path / 'data_root/report.pdf'
    make_pdf(path, split=True)
    monkeypatch.setattr('src.agent.v3.evidence.ROOT', tmp_path)
    bound = bind_original_source(candidate(path), path)
    assert bound is not None
    assert bound.source.original_cell.value.text == '12,345.\n00'
    assert checked_source(bound)
    adjacent = candidate(path, raw='12,345.0011,000.00')
    assert bind_original_source(adjacent, path) is None


def test_mysql_coordinate_roundtrip_does_not_include_neighbor_scope_column(tmp_path,monkeypatch):
    from math import nextafter,inf
    path=tmp_path/'data_root/report.pdf'
    make_pdf(path,mixed=True,legal_unit=True)
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    value=candidate(path,row='营业收入',scope='parent',mixed=True,column='2027年度 公司',raw='11,000.00')
    value.value='11000000.00'
    value.source.literal='营业收入 | 12,345.00 | 11,000.00'
    value.source.column_index=2
    bound=bind_original_source(value,path)
    assert bound is not None and checked_source(bound)
    for region in [bound.source.original_cell.value,bound.source.scope_proof.value_region]:
        region.bbox=(nextafter(region.bbox[0],-inf),*region.bbox[1:])
    assert checked_source(bound)
    wrong=bound.model_copy(deep=True);wrong.scope='consolidated'
    assert not checked_source(wrong)


def test_split_cell_remains_physically_bounded_after_mysql_coordinate_roundtrip(tmp_path,monkeypatch):
    from math import nextafter,inf
    path=tmp_path/'data_root/report.pdf'
    make_pdf(path,split=True)
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    bound=bind_original_source(candidate(path),path)
    assert bound is not None and checked_source(bound)
    for region in [bound.source.original_cell.value,bound.source.scope_proof.value_region]:
        region.bbox=(nextafter(region.bbox[0],inf),*region.bbox[1:])
    assert checked_source(bound)
    for region in [bound.source.original_cell.value,bound.source.scope_proof.value_region]:
        region.bbox=(region.bbox[0]+.1,*region.bbox[1:])
    assert not checked_source(bound)


def test_aligned_top_and_bottom_tables_bind_one_previous_page_header(tmp_path, monkeypatch):
    path = tmp_path / 'data_root/report.pdf'
    make_pdf(path, continuation=True)
    monkeypatch.setattr('src.agent.v3.evidence.ROOT', tmp_path)
    bound = bind_original_source(candidate(path, page=2), path)
    assert bound is not None
    assert bound.source.original_cell.heading.page == 1
    assert checked_source(bound)


@pytest.mark.parametrize('change', [dict(shift=18), dict(gap=True)])
def test_header_cannot_jump_a_page_or_follow_a_shifted_unrelated_table(tmp_path, change):
    path = tmp_path / 'data_root/report.pdf'
    make_pdf(path, continuation=True, **change)
    assert bind_original_source(candidate(path, page=3 if change.get('gap') else 2), path) is None


def test_legal_unit_sentence_is_bound_to_the_table_context(tmp_path, monkeypatch):
    path = tmp_path / 'data_root/report.pdf'
    make_pdf(path, legal_unit=True)
    monkeypatch.setattr('src.agent.v3.evidence.ROOT', tmp_path)
    bound = bind_original_source(candidate(path, row='营业收入'), path)
    assert bound is not None
    assert checked_source(bound)
    wrong = candidate(path, row='营业收入')
    wrong.source.raw_unit = '万元'
    assert bind_original_source(wrong, path) is None


def test_mismatched_row_unit_suffix_cannot_hide_behind_a_matching_metric(tmp_path):
    path=tmp_path/'data_root/report.pdf'
    make_pdf(path)
    f=candidate(path,row=LABEL.replace('千元','万元'))
    assert bind_original_source(f,path) is None


def test_mixed_table_binds_actual_period_and_column_scope(tmp_path, monkeypatch):
    path = tmp_path / 'data_root/report.pdf'
    make_pdf(path, mixed=True)
    monkeypatch.setattr('src.agent.v3.evidence.ROOT', tmp_path)
    f = candidate(path, mixed=True, column='2027年度 合并')
    bound = bind_original_source(f, path)
    assert bound is not None
    assert bound.source.scope_proof.column_scope.text == '合并'
    assert checked_source(bound)
    f.scope = 'parent'
    assert bind_original_source(f, path) is None


def test_borderless_mixed_headers_keep_notes_separate_and_bind_all_date_lines(tmp_path, monkeypatch):
    path = tmp_path / 'data_root/report.pdf'
    path.parent.mkdir()
    doc = fitz.open()
    page = doc.new_page(width=680, height=500)
    for x,y,value in [(40,50,'合并及公司利润表'),(40,75,'金额单位为人民币千元'),
                       (40,125,'项目'),(210,125,'附注'),
                       (280,125,'2029'),(280,140,'年度'),(295,155,'合并'),
                       (380,125,'2028年度'),(395,155,'合并'),
                       (480,125,'2029年度'),(495,155,'公司'),
                       (580,125,'2028年度'),(595,155,'公司'),
                       (40,195,'营业收入'),(210,195,'四(17)'),(280,195,'12,345.00'),
                       (380,195,'11,000.00'),(480,195,'8,000.00'),(580,195,'7,000.00')]:
        text(page,x,y,value)
    doc.save(path)
    doc.close()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    f=candidate(path,year=2029,row='营业收入',mixed=True,column='2029年度 合并')
    f.source.literal='营业收入 | 四(17) | 12,345.00 | 11,000.00 | 8,000.00 | 7,000.00'
    f.source.column_index=2
    bound=bind_original_source(f,path)
    assert bound is not None
    assert checked_source(bound)
    assert '四(17)' not in bound.source.original_cell.row.text


@pytest.mark.parametrize('validation', ['binding', 'checked_source'])
@pytest.mark.parametrize('case', ['unrelated_header', 'restarted_header', 'restarted_bare_year', 'percentage_header'])
def test_independent_proof_rejects_other_table_headers_and_money_in_percent_columns(tmp_path, monkeypatch, case, validation):
    from src.agent.v3.contracts import OriginalCellProof, StatementScopeProof
    from src.etl.submission_sources import _region, _hashed, _captions
    path=tmp_path/'data_root/report.pdf'
    path.parent.mkdir()
    doc=fitz.open()
    page=doc.new_page(width=620,height=500)
    text(page,40,65,'合并利润表')
    text(page,40,90,'单位：千元')
    column='2027年度（%）' if case=='percentage_header' else '2027年度'
    rows=[(30,['项目',column,'2026年度']),
                   (35,['营业收入（千元）' if case=='percentage_header' else '营业成本（千元）',
                        '12,345.00' if case=='percentage_header' else '22,345.00',
                        '11,000.00' if case=='percentage_header' else '21,000.00'])]
    if case in {'restarted_header','restarted_bare_year'}:
        rows.extend([(30,['项目','2026' if case=='restarted_bare_year' else '2026年度',
                                  '2025' if case=='restarted_bare_year' else '2025年度']),
                     (35,['营业收入（千元）','12,345.00','11,000.00'])])
    grid(page,130,rows)
    if case=='unrelated_header':
        grid(page,250,[(30,['项目','2026年度','2025年度']),
                       (35,['营业收入（千元）','12,345.00','11,000.00'])])
    doc.save(path)
    doc.close()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    f=candidate(path,row='营业收入（千元）',column=column)
    f.metric='operating_revenue'
    if validation=='binding':
        diagnostics=[]
        assert bind_original_source(f,path,diagnostics=diagnostics) is None
        assert any(item['classification']=='contradiction' for item in diagnostics)
    else:
        # The independent checker must reject correctly transcribed rectangles
        # whose header belongs to another table, or denotes a percentage column.
        stat=path.stat()
        top,bottom={'percentage_header':(160,195),'restarted_header':(225,260),'restarted_bare_year':(225,260),
                    'unrelated_header':(280,315)}[case]
        value=_region(path,stat,1,(280,top,420,bottom))
        row=_region(path,stat,1,(40,top,279.9,bottom))
        heading=_region(path,stat,1,(280,130,420,160))
        caption=_captions(str(path),stat.st_size,stat.st_mtime_ns)[0]
        x0,y0,x1,y1=caption['bbox']
        title=_region(path,stat,1,(x0-.3,y0-.3,x1+.3,y1+.3))
        context=_region(path,stat,1,(0,y0-.3,620,bottom))
        f.source.original_cell=OriginalCellProof(value=value,row=row,heading=heading,context=context)
        f.source.scope_proof=StatementScopeProof(caption=title,family='利润表',
            value_region=_hashed(value),label_region=_hashed(row))
        assert not checked_source(f)


@pytest.mark.parametrize('validation',['binding','checked_source'])
def test_nearest_physical_scope_cannot_be_skipped_for_an_older_matching_marker(tmp_path, monkeypatch, validation):
    from src.agent.v3.contracts import OriginalCellProof,StatementScopeProof
    from src.etl.submission_sources import _region,_hashed,_captions
    path=tmp_path/'data_root/report.pdf'
    path.parent.mkdir()
    doc=fitz.open()
    page=doc.new_page(width=620,height=500)
    text(page,40,65,'合并及公司利润表')
    text(page,40,90,'单位：千元')
    grid(page,130,[(30,['项目','2027年度','2027年度']),
                   (25,['项目','合并','公司']),
                   (35,['营业成本（千元）','22,345.00','21,000.00']),
                   (25,['项目','公司','合并']),
                   (35,['营业收入（千元）','12,345.00','11,000.00'])])
    doc.save(path)
    doc.close()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    f=candidate(path,row='营业收入（千元）',mixed=True,column='2027年度 合并')
    f.metric='operating_revenue'
    if validation=='binding':
        diagnostics=[]
        assert bind_original_source(f,path,diagnostics=diagnostics) is None
        assert any(item['reason']=='physical_column_scope_conflict' for item in diagnostics)
    else:
        stat=path.stat()
        value=_region(path,stat,1,(280,245,420,280))
        row=_region(path,stat,1,(40,245,279.9,280))
        heading=_region(path,stat,1,(280,130,420,160))
        old_scope=_region(path,stat,1,(280,160,420,185))
        caption=_captions(str(path),stat.st_size,stat.st_mtime_ns)[0]
        x0,y0,x1,y1=caption['bbox']
        title=_region(path,stat,1,(x0-.3,y0-.3,x1+.3,y1+.3))
        context=_region(path,stat,1,(0,y0-.3,620,280))
        f.source.original_cell=OriginalCellProof(value=value,row=row,heading=heading,context=context)
        f.source.scope_proof=StatementScopeProof(caption=title,family='利润表',column_scope=old_scope,
            value_region=_hashed(value),label_region=_hashed(row))
        assert not checked_source(f)


@pytest.mark.parametrize('amount',['2026','2027'])
def test_a_financial_row_amount_that_looks_like_a_year_is_not_a_new_header(tmp_path, monkeypatch, amount):
    path=tmp_path/'data_root/report.pdf'
    path.parent.mkdir()
    doc=fitz.open()
    page=doc.new_page(width=620,height=500)
    text(page,40,65,'合并利润表')
    text(page,40,90,'单位：千元')
    grid(page,130,[(30,['项目','2027年度','2026年度']),
                   (35,['营业成本（千元）',amount,'2025']),
                   (35,['营业收入（千元）','12,345.00','11,000.00'])])
    doc.save(path)
    doc.close()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    f=candidate(path,row='营业收入（千元）')
    f.metric='operating_revenue'
    bound=bind_original_source(f,path)
    assert bound is not None and checked_source(bound)


@pytest.mark.parametrize('validation',['binding','checked_source'])
def test_unit_from_an_older_table_cannot_override_the_nearest_local_declaration(tmp_path, monkeypatch, validation):
    from src.agent.v3.contracts import OriginalCellProof,StatementScopeProof
    from src.etl.submission_sources import _region,_hashed,_captions
    path=tmp_path/'data_root/report.pdf'
    path.parent.mkdir()
    doc=fitz.open()
    page=doc.new_page(width=620,height=500)
    text(page,40,65,'合并利润表')
    text(page,40,90,'单位：千元')
    grid(page,130,[(30,['项目','2027年度','2026年度']),
                   (35,['营业成本','22,345.00','21,000.00'])])
    text(page,40,240,'单位：万元')
    grid(page,260,[(30,['项目','2027年度','2026年度']),
                   (35,['营业收入','12,345.00','11,000.00'])])
    doc.save(path)
    doc.close()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    f=candidate(path,row='营业收入')
    if validation=='binding':
        diagnostics=[]
        assert bind_original_source(f,path,diagnostics=diagnostics) is None
        assert any(item['reason']=='physical_unit_conflict' for item in diagnostics)
        correct=f.model_copy(deep=True)
        correct.source.raw_unit='万元'
        correct.value='123450000.00'
        bound=bind_original_source(correct,path)
        assert bound is not None and checked_source(bound)
    else:
        stat=path.stat()
        value=_region(path,stat,1,(280,290,420,325))
        row=_region(path,stat,1,(40,290,279.9,325))
        heading=_region(path,stat,1,(280,260,420,290))
        caption=_captions(str(path),stat.st_size,stat.st_mtime_ns)[0]
        x0,y0,x1,y1=caption['bbox']
        title=_region(path,stat,1,(x0-.3,y0-.3,x1+.3,y1+.3))
        wrong_context=_region(path,stat,1,(0,y0-.3,620,160))
        f.source.original_cell=OriginalCellProof(value=value,row=row,heading=heading,context=wrong_context)
        f.source.scope_proof=StatementScopeProof(caption=title,family='利润表',
            value_region=_hashed(value),label_region=_hashed(row))
        assert not checked_source(f)


@pytest.mark.parametrize('identity,metric', [
    ('000333_2023_FY', 'deducted_attributable_net_profit'),
    ('300750_2024_FY', 'operating_revenue'),
    ('002594_2024_FY', 'attributable_net_profit'),
])
def test_real_reviewed_cells_bind_automatically_and_pass_strict_source(identity, metric):
    replay = ROOT / '.local_runtime/upgrade120/quality-review-20261021' / f'replay-{identity}.json'
    if not replay.exists():
        pytest.skip('Optional local original report is not distributed with tests')
    saved = json.loads(replay.read_text(encoding='utf8'))
    f = Fact.model_validate(next(f for f in saved['extra_facts']
                           if f['metric'] == metric and f['scope'] == 'consolidated'))
    path = ROOT / f.source.source_path
    if not path.exists():
        pytest.skip('Optional local PDF is not distributed with tests')
    before = sha256(path.read_bytes()).hexdigest()
    bound = bind_original_source(f, path)
    assert bound is not None
    assert bound.source.original_cell is not None
    assert checked_source(bound)
    assert before == sha256(path.read_bytes()).hexdigest() == bound.source.source_sha256


def test_company_code_is_an_explicit_security_identity_and_blocks_wrong_company_fallback():
    from src.utils.report_identity import original_report_identity
    report = dict(stock_code='600789', company='登记企业全称', year=2027, period='FY')
    good = original_report_identity(report, '公司代码：600789 公司简称：短名 2027年年度报告')
    assert good['company'] and good['codes_found'] == ['600789']
    wrong = original_report_identity(report, '登记企业全称 公司代码：600788 2027年年度报告')
    assert not wrong['company']
    arbitrary = original_report_identity(report, '登记企业全称 项目金额600788 2027年年度报告')
    assert arbitrary['company'] and arbitrary['codes_found'] == []


def generic_period_fixture(path, *, year, period, company, code, title, column, row):
    path.parent.mkdir(exist_ok=True)
    doc=fitz.open()
    cover=doc.new_page(width=620,height=500)
    report_label={'Q1':'第一季度报告','Q3':'第三季度报告','FY':'年度报告'}[period]
    text(cover,40,55,f'证券代码：{code}')
    text(cover,40,90,f'{company}{year}年{report_label}')
    page=doc.new_page(width=620,height=500)
    text(page,40,65,title)
    text(page,40,90,'单位：千元')
    grid(page,130,[(30,['项目',column,'上期发生额']),
                   (35,[row,'12,345.00','11,000.00'])])
    doc.save(path)
    doc.close()


@pytest.mark.parametrize('company,code,year,period,title,column,row,metric',[
    ('未预设甲企业','000789',2031,'Q1','合并利润表','本期发生额','营业收入','operating_revenue'),
    ('未预设乙企业','600321',2032,'Q3','合并资产负债表','本报告期末','资产总计','total_assets'),
    ('未预设丙企业','300456',2033,'Q3','合并年初到报告期末利润表','本期发生额','营业收入','operating_revenue'),
])
def test_generic_current_columns_use_original_report_and_cumulative_table_provenance(
        tmp_path,monkeypatch,company,code,year,period,title,column,row,metric):
    path=tmp_path/'data_root/report.pdf'
    generic_period_fixture(path,year=year,period=period,company=company,code=code,title=title,column=column,row=row)
    monkeypatch.setattr('src.agent.v3.evidence.ROOT',tmp_path)
    f=candidate(path,year=year,page=2,row=row,column=column)
    f.company=company;f.stock_code=code;f.period=period;f.metric=metric;f.source.table=title
    bound=bind_original_source(f,path)
    assert bound is not None
    assert bound.source.original_cell.scope_context is not None
    assert bound.source.original_cell.scope_context.page==1
    assert checked_source(bound)
    wrong_year=bound.model_copy(deep=True)
    wrong_year.year+=1
    assert bind_original_source(wrong_year,path) is None
    assert not checked_source(wrong_year)
    wrong_company=bound.model_copy(deep=True)
    wrong_company.stock_code='600999'
    assert bind_original_source(wrong_company,path) is None
    assert not checked_source(wrong_company)


@pytest.mark.parametrize('actual_period,wanted_period,title,column',[
    ('Q3','Q1','合并利润表','本期发生额'),
    ('Q1','Q3','合并年初到报告期末利润表','本期发生额'),
    ('Q3','Q3','合并利润表','本报告期'),
    ('Q3','Q3','合并利润表','本期发生额'),
])
def test_generic_period_does_not_assume_a_quarter_or_expand_single_quarter_flow(
        tmp_path,actual_period,wanted_period,title,column):
    path=tmp_path/'data_root/report.pdf'
    generic_period_fixture(path,year=2031,period=actual_period,company='未预设甲企业',code='000789',
                           title=title,column=column,row='营业收入')
    f=candidate(path,year=2031,page=2,row='营业收入',column=column)
    f.company='未预设甲企业';f.period=wanted_period;f.source.table=title
    assert bind_original_source(f,path) is None


def test_real_byd_q1_formal_current_period_cell_uses_original_report_identity():
    relative=Path('data_root/financial_reports/submissions/6ae70e06d80b96f3ae790583c5eb861d01e08cb738eee1152ef1269ad2609db6.pdf')
    path=ROOT/relative
    if not path.exists():pytest.skip('Optional local original report is not distributed with tests')
    f=candidate(path,year=2025,page=7,row='其中：营业收入',column='本期发生额',raw='170,360,448,000.00')
    f.company='比亚迪';f.stock_code='002594';f.period='Q1';f.metric='operating_revenue';f.value='170360448000.00'
    f.source.raw_unit='元';f.source.source_path=relative.as_posix()
    f.source.literal='其中：营业收入 | 170,360,448,000.00 | 124,944,397,000.00'
    bound=bind_original_source(f,path)
    assert bound is not None
    assert checked_source(bound)
