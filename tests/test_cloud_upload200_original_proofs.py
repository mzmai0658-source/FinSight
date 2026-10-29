"""Original oracle regressions use real physical cells, never answer-derived expectations."""
from pathlib import Path

import pytest

from eval import cloud_upload200 as harness
from tests.test_cloud_upload200 import sample_case, sample_data, sample_run
from tests.test_common_metric_source_quality import direct_fact


def physical_source(tmp_path, monkeypatch, mode):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    from src.agent.v3.evidence import checked_source
    from src.etl.submission_sources import bind_original_source

    font = Path('C:/Windows/Fonts/simhei.ttf')
    if not font.exists():
        pytest.skip('Physical CJK PDF fixture requires the installed SimHei TrueType font')
    pdfmetrics.registerFont(TTFont('upload200-original-proof', str(font)))
    path = tmp_path/'data_root/report.pdf'; path.parent.mkdir()
    writer = canvas.Canvas(str(path), pagesize=(620, 600))
    def text(x, y, value):
        writer.setFont('upload200-original-proof', 9)
        writer.drawString(x, 600-y, value)
    def grid(top, rows):
        xs, ys = [40, 280, 420, 560], [top]
        for height, _ in rows:
            ys.append(ys[-1]+height)
        for x in xs:
            writer.line(x, 600-top, x, 600-ys[-1])
        for y in ys:
            writer.line(xs[0], 600-y, xs[-1], 600-y)
        for ri, (_, cells) in enumerate(rows):
            for ci, value in enumerate(cells):
                for li, line in enumerate(value.split('\n')):
                    text(xs[ci]+5, ys[ri]+15+li*12, line)
    if mode == 'crosspage_unit':
        text(40, 150, '母公司利润表'); text(40, 180, '单位：万元')
        text(40, 535, '合并现金流量表'); text(40, 565, '单位：千元')
        writer.showPage()
        grid(65, [(25, ['项目', '2024年度', '2023年度']),
                  (30, ['经营活动产生的现金流量净额', '12,345.00', '11,000.00'])])
        options = dict(metric='operating_cash_flow', row='经营活动产生的现金流量净额',
                       page=2, table='合并现金流量表')
    elif mode == 'dash_label':
        text(40, 55, '合并利润表'); text(40, 80, '单位：千元')
        label = '净利润（净亏损以\n“－”号填列）'
        grid(105, [(40, ['项目', '2024年度', '2023年度']),
                   (40, [label, '12,345.00', '11,000.00'])])
        options = dict(metric='net_profit', row=label)
    else:
        text(40, 55, '合并利润表'); text(40, 80, '单位：千元')
        grid(105, [(40, ['项目', '2024年度', '2023年度']),
                   (40, ['营业收入', '12,345.\n00', '11,000.00'])])
        options = {}
    writer.save()
    monkeypatch.setattr('src.agent.v3.evidence.ROOT', tmp_path)
    fact = direct_fact(path, year=2024, column='2024年度', **options)
    fact.stock_code, fact.company = '000333', '美的集团'
    bound = bind_original_source(fact, path)
    assert bound is not None and checked_source(bound)
    case = sample_case(); case['expect']['goal_expectations'][0]['metrics'] = [bound.metric]
    report = {key: getattr(bound, key) for key in ('stock_code', 'company', 'year', 'period')}
    report.update(source_path=bound.source.source_path, document_version=bound.source.document_version)
    reference = dict(version=bound.data_version, reports=[report], facts={bound.id: bound.model_dump(mode='json')})
    return case, reference, bound, path


@pytest.mark.parametrize('mode', ['bounded_split_decimal', 'crosspage_unit'])
def test_oracle_accepts_revalidated_split_cell_and_crosspage_unit(tmp_path, monkeypatch, mode):
    case, reference, bound, _ = physical_source(tmp_path, monkeypatch, mode)
    oracle = harness.build_oracle([case], reference, root=tmp_path, record_data_gaps=True)
    assert not oracle['data_gaps'], oracle['data_gaps']
    assert oracle['cards'][bound.id]['value_exact'] == '12345000.00'


@pytest.mark.parametrize('raw, value, precision', [
    ('-12,345.00', '-12345000.00', 2),
    ('12,346.00', '12346000.00', 2),
    ('12,345.000', '12345000.000', 3),
    ('12,345.01', '12345010.00', 2),
])
def test_checked_cell_never_rescues_wrong_sign_number_or_precision(tmp_path, monkeypatch, raw, value, precision):
    case, reference, bound, _ = physical_source(tmp_path, monkeypatch, 'bounded_split_decimal')
    fact = reference['facts'][bound.id]
    fact['value'] = value; fact['source'].update(raw_value=raw, raw_precision=precision)
    oracle = harness.build_oracle([case], reference, root=tmp_path, record_data_gaps=True)
    assert bound.id not in oracle['cards']
    assert oracle['data_gaps'][0]['reason'] == 'original_revalidation_failed'


@pytest.mark.parametrize('change', ['wrong_unit', 'wrong_date', 'cached_assertion', 'other_statement_unit'])
def test_crosspage_unit_requires_actual_bound_original_context(tmp_path, monkeypatch, change):
    case, reference, bound, path = physical_source(tmp_path, monkeypatch, 'crosspage_unit')
    fact = reference['facts'][bound.id]
    if change == 'wrong_unit':
        fact['source']['raw_unit'] = '万元'; fact['value'] = '123450000.00'
    elif change == 'wrong_date':
        fact['year'] = 2025; reference['reports'][0]['year'] = 2025
        case['expect']['goal_expectations'][0]['years'] = [2025]
    elif change == 'cached_assertion':
        fact['source']['original_cell']['context'].update(page=2, bbox=[40, 5, 280, 30], text='单位：千元')
    else:
        import pdfplumber
        box = [30, 165, 250, 190]
        with pdfplumber.open(path) as pdf:
            text = pdf.pages[0].crop(tuple(box)).extract_text() or ''
        assert '万元' in text
        fact['source']['original_cell']['context'].update(page=1, bbox=box, text=text)
    oracle = harness.build_oracle([case], reference, root=tmp_path, record_data_gaps=True)
    assert bound.id not in oracle['cards']


@pytest.mark.parametrize('company', ['其他公司', '比亚迪', '', None])
def test_published_fact_cannot_rename_registered_company(company):
    case = sample_case(); reference, oracle = sample_data(); raw = sample_run(case)
    raw['task']['result']['facts'][0]['company'] = company
    verdict = harness.score_case(case, raw, reference, oracle, model_version='private-model-v1')
    assert not verdict['passed']
    assert 'fact_identity_changed' in verdict['errors']


def test_published_company_accepts_equivalent_registered_name_normalization():
    case = sample_case(); reference, oracle = sample_data(); raw = sample_run(case)
    raw['task']['result']['facts'][0]['company'] = ' 美的\n集团\u3000'
    assert harness.score_case(case, raw, reference, oracle, model_version='private-model-v1')['passed']


def test_oracle_uses_rechecked_physical_row_despite_ocr_dash_annotation(tmp_path, monkeypatch):
    case, reference, bound, _ = physical_source(tmp_path, monkeypatch, 'dash_label')
    actual = bound.source.original_cell.row.text
    reference['facts'][bound.id]['source']['row'] = actual.replace('－', '—').replace('-', '—').replace('\n', ' ')
    oracle = harness.build_oracle([case], reference, root=tmp_path, record_data_gaps=True)
    assert not oracle['data_gaps'], oracle['data_gaps']
    card = oracle['cards'][bound.id]
    assert card['value_exact'] == '12345000.00'
    assert card['original_label_text'] == actual
    assert card['source']['row'] == reference['facts'][bound.id]['source']['row']


@pytest.mark.parametrize('change', ['negative_value', 'wrong_unit', 'wrong_date', 'wrong_metric', 'wrong_label', 'wrong_cell'])
def test_normalized_annotation_still_runs_all_original_financial_checks(tmp_path, monkeypatch, change):
    case, reference, bound, _ = physical_source(tmp_path, monkeypatch, 'dash_label')
    fact = reference['facts'][bound.id]; source = fact['source']
    source['row'] = source['row'].replace('－', '—').replace('-', '—')
    if change == 'negative_value':
        source['raw_value'] = '-12,345.00'; fact['value'] = '-12345000.00'
    elif change == 'wrong_unit':
        source['raw_unit'] = '万元'; fact['value'] = '123450000.00'
    elif change == 'wrong_date':
        fact['year'] = 2025; reference['reports'][0]['year'] = 2025
        case['expect']['goal_expectations'][0]['years'] = [2025]
    elif change == 'wrong_metric':
        fact['metric'] = 'operating_revenue'; case['expect']['goal_expectations'][0]['metrics'] = ['operating_revenue']
    elif change == 'wrong_label':
        source['row'] = source['row'].replace('净利润', '归母净利润')
    else:
        source['original_cell']['value']['text'] = '12,345.01'
    oracle = harness.build_oracle([case], reference, root=tmp_path, record_data_gaps=True)
    assert bound.id not in oracle['cards']
