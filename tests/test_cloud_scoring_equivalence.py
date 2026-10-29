"""Deliverable equivalence stays strict about facts, operations and original evidence."""
from __future__ import annotations

import copy
import math

import pytest

from eval import cloud_upload200 as harness
from tests.test_cloud_upload200 import sample_case, sample_data, sample_run


def verdict(case, raw, reference, oracle):
    return harness.score_case(case, raw, reference, oracle, model_version='private-model-v1')


def mixed_lookup():
    case = sample_case()
    first = dict(case['expect']['goal_expectations'][0], metrics=['eps_basic'], unit='元/股')
    second = dict(first, metrics=['roe_weighted'], unit='%')
    case['expect']['goal_expectations'] = [first, second]
    reference, oracle = sample_data()
    template = reference['facts'].pop('f1'); card = oracle['cards'].pop('f1')
    for key, metric, value, unit in [('eps', 'eps_basic', '2.50', '元/股'), ('roe', 'roe_weighted', '15.00', '%')]:
        reference['facts'][key] = dict(copy.deepcopy(template), id=key, metric=metric, value=value, unit=unit)
        oracle['cards'][key] = dict(copy.deepcopy(card), value_exact=value)
    raw = sample_run(case); result = raw['task']['result']
    result['request_contract']['conditions']['metrics'] = ['eps_basic', 'roe_weighted']
    result['request_contract']['conditions']['presentation']['unit'] = None
    result['facts'] = [dict(copy.deepcopy(row), value_exact=row['value']) for row in reference['facts'].values()]
    result['task_results'][0]['fact_ids'] = ['eps', 'roe']
    result['answer']['content'] = ('| 公司 | 年份 | 指标 | 数值 | 单位 |\n|---|---|---|---:|---|\n'
        '| 美的集团 | 2024 | 基本每股收益 | 2.50 | 元/股 |\n'
        '| 美的集团 | 2024 | 加权平均净资产收益率 | 15.00 | % |')
    return case, raw, reference, oracle


def test_single_lookup_delivers_both_metrics_in_their_native_units():
    assert verdict(*mixed_lookup())['passed']


@pytest.mark.parametrize('change', ['missing_metric', 'wrong_metric', 'wrong_company', 'wrong_year',
    'wrong_value', 'wrong_unit', 'wrong_visible_unit', 'wrong_source', 'empty', 'missing_goal', 'extra_operation'])
def test_combined_lookup_does_not_hide_mutations(change):
    case, raw, reference, oracle = mixed_lookup(); result = raw['task']['result']
    result['verification_v3'] = {'passed': True}
    if change == 'missing_metric':
        result['facts'].pop()
    elif change == 'wrong_metric':
        result['request_contract']['conditions']['metrics'] = ['eps_basic', 'net_profit']
    elif change == 'wrong_company':
        result['facts'][0]['company'] = '比亚迪'
    elif change == 'wrong_year':
        result['facts'][0]['year'] = 2023
    elif change == 'wrong_value':
        result['facts'][0]['value_exact'] = '-2.50'
    elif change == 'wrong_unit':
        result['facts'][0]['unit'] = '%'
    elif change == 'wrong_visible_unit':
        result['answer']['content'] = result['answer']['content'].replace('2.50 | 元/股', '2.50 | %')
    elif change == 'wrong_source':
        result['facts'][0]['source']['page'] = 99
    elif change == 'empty':
        result['facts'] = []; result['answer']['content'] = ''
    elif change == 'missing_goal':
        result['request_contract']['goals'] = []
    else:
        result['request_contract']['goals'].append(dict(id='unrequested', kind='rank'))
        result['task_results'].append(dict(id='unrequested', status='completed'))
    assert not verdict(case, raw, reference, oracle)['passed']


def test_explicit_wrong_metric_unit_is_not_rescued_by_mixed_native_units():
    case, raw, reference, oracle = mixed_lookup()
    raw['task']['result']['request_contract']['conditions']['presentation']['metric_units'] = {'eps_basic': '%'}
    assert not verdict(case, raw, reference, oracle)['passed']


def test_inherited_money_unit_does_not_override_eps_or_roe_native_units():
    case, raw, reference, oracle = mixed_lookup()
    raw['task']['result']['request_contract']['conditions']['presentation']['unit'] = '万元'
    assert verdict(case, raw, reference, oracle)['passed']


def test_explicit_metric_unit_overrides_compatible_global_unit():
    case = sample_case(); reference, oracle = sample_data(); raw = sample_run(case)
    raw['task']['result']['request_contract']['conditions']['presentation'].update(
        unit='亿元', metric_units={'operating_revenue': '万元'})
    assert verdict(case, raw, reference, oracle)['passed']


def test_lookup_cannot_add_an_unrequested_calculation_operation():
    case, raw, reference, oracle = mixed_lookup()
    raw['task']['result']['request_contract']['conditions']['calculation'] = 'yoy'
    assert not verdict(case, raw, reference, oracle)['passed']


def test_published_calculation_without_a_requested_operation_is_rejected():
    case, raw, reference, oracle = mixed_lookup()
    raw['task']['result']['derived_facts'] = [dict(inputs=['eps', 'roe'], formula='current-base', value='12.50', unit='%')]
    assert not verdict(case, raw, reference, oracle)['passed']


def comparison_with_input_lookup():
    case = sample_case(); spec = case['expect']['goal_expectations'][0]
    spec.update(kind='compare', years=[2023, 2024], calculation='yoy', axis='years')
    reference, oracle = sample_data()
    base = dict(copy.deepcopy(reference['facts']['f1']), id='base', year=2023, value='50000000.00')
    reference['facts']['base'] = base
    oracle['cards']['base'] = dict(copy.deepcopy(oracle['cards']['f1']), value_exact=base['value'])
    raw = sample_run(case); result = raw['task']['result']; request = result['request_contract']
    request['goals'] = [dict(id='comparison', kind='compare'), dict(id='inputs', kind='lookup')]
    request['conditions']['time']['years'] = [2023, 2024]
    request['conditions'].update(calculation='yoy', comparison_axis='years')
    request['goals'][1]['selection'] = dict(calculation='none')
    result['task_results'] = [dict(id=key, status='completed', fact_ids=['base', 'f1']) for key in ['comparison', 'inputs']]
    result['facts'].append(dict(copy.deepcopy(base), value_exact=base['value']))
    result['derived_facts'] = [dict(inputs=['base', 'f1'], formula='(current-base)/abs(base)*100', value='100', unit='%')]
    result['answer']['content'] = '美的集团2023年营业收入5,000.00万元；2024年10,000.00万元；同比增长100.00%。'
    return case, raw, reference, oracle


def test_compare_may_use_a_separate_lookup_for_the_requested_inputs():
    assert verdict(*comparison_with_input_lookup())['passed']


@pytest.mark.parametrize('global_unit,metric_unit,passed', [('亿元', '万元', True), ('万元', '亿元', False)])
def test_compare_input_lookup_respects_explicit_metric_unit_override(global_unit, metric_unit, passed):
    case, raw, reference, oracle = comparison_with_input_lookup()
    raw['task']['result']['request_contract']['goals'][1]['selection']['presentation'] = dict(
        unit=global_unit, metric_units={'operating_revenue': metric_unit})
    assert verdict(case, raw, reference, oracle)['passed'] is passed


@pytest.mark.parametrize('change', ['missing_calculation', 'extra_2022', 'partial_comparison', 'partial_inputs', 'wrong_input_value', 'extra_derived_2022'])
def test_input_lookup_does_not_excuse_missing_or_extra_calculation(change):
    case, raw, reference, oracle = comparison_with_input_lookup(); result = raw['task']['result']
    if change == 'missing_calculation':
        result['derived_facts'] = []
    elif change == 'extra_2022':
        result['request_contract']['goals'][1]['selection']['time'] = dict(years=[2022, 2023, 2024])
    elif change == 'partial_comparison':
        result['task_results'][0]['status'] = 'partial'
    elif change == 'partial_inputs':
        result['task_results'][1]['status'] = 'partial'
    elif change == 'extra_derived_2022':
        result['derived_facts'].append(dict(inputs=['unrequested-2022', 'base'], formula='(current-base)/abs(base)*100', value='1', unit='%'))
    else:
        result['facts'][1]['value_exact'] = '1'
    assert not verdict(case, raw, reference, oracle)['passed']


def test_definition_inherits_half_year_context_but_lookup_period_remains_exact():
    case = sample_case(); spec = case['expect']['goal_expectations'][0]; spec['periods'] = ['HY']
    definition = dict(kind='concept', codes=['000333'], metrics=['operating_revenue'], years=[], periods=['FY'], scope='consolidated')
    case['expect']['goal_expectations'].insert(0, definition)
    reference, oracle = sample_data(); reference['facts']['f1']['period'] = 'HY'
    raw = sample_run(case); result = raw['task']['result']
    result['facts'][0]['period'] = 'HY'; result['request_contract']['conditions']['time']['periods'] = ['HY']
    result['request_contract']['goals'].append(dict(id='definition', kind='concept', concept_mode='definition'))
    result['task_results'].append(dict(id='definition', status='completed'))
    score = verdict(case, raw, reference, oracle)
    assert score['automated_pass'], score['errors']
    assert score['review_status'] == 'pending_review'
    result['request_contract']['goals'][0]['selection'] = dict(time=dict(periods=['FY']))
    assert not verdict(case, raw, reference, oracle)['automated_pass']


def quote_only():
    case = sample_case(); case['question'] = '不要重新查数，只给上一项原件PDF页码和未改写的原文整行。'
    case['expect'].update(kind='reference', no_query=True)
    spec = case['expect']['goal_expectations'][0]; spec.update(kind='quote', quote_mode='literal')
    reference, oracle = sample_data(); raw = sample_run(case); result = raw['task']['result']
    literal = '营业收入 100,000,000.00 90,000,000.00'
    oracle['cards']['f1']['original_table_row'] = dict(row_text=literal, row_bbox=[1, 2, 3, 4])
    result['request_contract']['goals'][0].update(kind='quote', quote_mode='literal', quote_granularity='row')
    result['task_results'][0]['kind'] = 'quote'; result['query_trace'] = []
    result['answer']['content'] = '原件PDF第3页：\n原表整行：\n'+literal+'\n原表单位：元。'
    result['evidence'] = [dict(fact_id='f1', document_version='a'*64, page_start=3,
        granularity='row', value_literal='100,000,000.00', row_literal=literal, text=literal,
        row_bbox=[1, 2, 3, 4])]
    return case, raw, reference, oracle


def test_quote_only_delivers_native_literal_without_repeating_conversion_or_identity():
    assert verdict(*quote_only())['passed']


def test_quote_row_rectangle_accepts_only_one_binary_float_roundoff():
    case, raw, reference, oracle = quote_only()
    bbox = raw['task']['result']['evidence'][0]['row_bbox']
    bbox[1] = math.nextafter(2.0, math.inf)
    assert verdict(case, raw, reference, oracle)['passed']
    bbox[1] = math.nextafter(bbox[1], math.inf)
    assert not verdict(case, raw, reference, oracle)['passed']


@pytest.mark.parametrize('requested', ['原文行', '原表所在行', '完整原表行'])
def test_model_cannot_downgrade_requested_original_row_to_single_cell(requested):
    case, raw, reference, oracle = quote_only()
    case['question'] = '不要重新查数，只给上一项原件PDF页码和未改写的'+requested+'。'
    result = raw['task']['result']; result['request_contract']['goals'][0]['quote_granularity'] = 'cell'
    result['evidence'][0].update(granularity='cell', text='营业收入 100,000,000.00')
    result['evidence'][0].pop('row_literal'); result['evidence'][0].pop('row_bbox')
    result['answer']['content'] = '原件PDF第3页：营业收入100,000,000.00；原表单位：元。'
    assert not verdict(case, raw, reference, oracle)['passed']


@pytest.mark.parametrize('change', ['wrong_page', 'wrong_value', 'wrong_sign', 'wrong_unit', 'wrong_source',
    'wrong_row', 'cell_only', 'truncated_body', 'query', 'empty_data'])
def test_quote_only_keeps_source_literal_unit_and_granularity_checks(change):
    case, raw, reference, oracle = quote_only(); result = raw['task']['result']; evidence = result['evidence'][0]
    if change == 'wrong_page':
        evidence['page_start'] = 4
    elif change in {'wrong_value', 'wrong_sign'}:
        evidence['value_literal'] = '100,000,000.01' if change == 'wrong_value' else '-100,000,000.00'
    elif change == 'wrong_unit':
        result['answer']['content'] = result['answer']['content'].replace('单位：元', '单位：万元')
    elif change == 'wrong_source':
        evidence['source'] = dict(path='other.pdf')
    elif change == 'wrong_row':
        evidence['row_literal'] = '营业收入 100,000,000.00 1.00'
    elif change == 'cell_only':
        evidence.update(granularity='cell', row_literal='营业收入')
    elif change == 'truncated_body':
        result['answer']['content'] = '原件PDF第3页：营业收入100,000,000.00；原表单位：元。'
    elif change == 'query':
        result['query_trace'] = [dict(purpose='financial_facts', sql='SELECT * FROM canonical_facts')]
    else:
        result['facts'] = []
    assert not verdict(case, raw, reference, oracle)['passed']


def test_separately_requested_conversion_still_requires_visible_converted_value():
    case, raw, reference, oracle = quote_only()
    conversion = dict(case['expect']['goal_expectations'][0], kind='lookup'); conversion.pop('quote_mode')
    case['expect']['goal_expectations'].append(conversion)
    raw['task']['result']['request_contract']['goals'].append(dict(id='conversion', kind='lookup'))
    raw['task']['result']['task_results'].append(dict(id='conversion', status='completed'))
    assert not verdict(case, raw, reference, oracle)['passed']
    raw['task']['result']['answer']['content'] += '\n美的集团2024年营业收入10,000.00万元。'
    assert verdict(case, raw, reference, oracle)['passed']
