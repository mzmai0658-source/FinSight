"""评分按冻结全集和实际交付核对，不依赖题号或唯一内部任务名称。"""
import copy

import pytest

from eval.common300_scoring import match_goals,score_case
from eval.score_v3_regressions import selectors
from tests.test_common300_evaluation import fixture,MODEL


def rank_fixture():
    case,run,ref,oracle=fixture()
    facts=[dict(ref['facts']['f'],id=f'f{i}',stock_code=f'99000{i}',company=f'企业{i}',value=str(i*100000000)) for i in range(1,4)]
    ref['facts']={f['id']:f for f in facts}
    ref['reports']=[dict(stock_code=f['stock_code'],year=2024,period='FY') for f in facts]
    oracle['cards']={f['id']:dict(value_exact=f['value']) for f in facts}
    spec=case['expect']['goal_expectations'][0]
    spec.update(kind='rank',codes=[],all_companies=True,order='desc',limit=2,format='table')
    result=run['task']['result'];cond=result['request_contract']['conditions']
    cond.update(codes=[f['stock_code'] for f in facts],all_companies=True)
    cond['presentation'].update(order='desc',limit=2,format='table')
    result['request_contract']['goals'][0]['kind']='rank'
    result['task_results'][0]['kind']='rank'
    result['facts']=[dict(f,value_exact=f['value']) for f in reversed(facts[1:])]
    result['answer']['content']='| 公司 | 数值 | 单位 |\n|---|---:|---|\n| 企业3 | 30000.00 | 万元 |\n| 企业2 | 20000.00 | 万元 |'
    return case,run,ref,oracle


def test_all_company_selection_is_expanded_from_the_frozen_catalog():
    case,run,ref,oracle=rank_fixture()
    spec=case['expect']['goal_expectations'][0]
    assert set(selectors(spec,ref))=={'990001','990002','990003'}
    assert score_case(case,run,ref,oracle,MODEL)['automated_pass']
    assert spec['codes']==[]
    explicit={**spec,'all_companies':False,'codes':[]}
    assert selectors(explicit,ref)=={}


@pytest.mark.parametrize('damage',['wrong_value','wrong_order','wrong_universe','not_all_companies','wrong_year','wrong_unit'])
def test_scope_expansion_does_not_weaken_ranking_checks(damage):
    case,run,ref,oracle=rank_fixture()
    result=run['task']['result'];c=result['request_contract']['conditions']
    if damage=='wrong_value':result['facts'][0]['value_exact']='1'
    elif damage=='wrong_order':result['facts'].reverse()
    elif damage=='wrong_universe':c['codes']=['990002','990003']
    elif damage=='not_all_companies':c['all_companies']=False
    elif damage=='wrong_year':c['time']['years']=[2023]
    else:c['presentation']['unit']='亿元'
    assert not score_case(case,run,ref,oracle,MODEL)['automated_pass']


def sign_fixture():
    case,run,ref,oracle=fixture()
    spec=case['expect']['goal_expectations'][0]
    spec.update(kind='sign',metrics=['attributable_net_profit'])
    case['expect'].update(kind='reference',no_query=True,no_repeat=True)
    ref['facts']['f']['metric']='attributable_net_profit'
    result=run['task']['result'];result['facts'][0]['metric']='attributable_net_profit'
    c=result['request_contract']['conditions']
    c.update(metrics=['attributable_net_profit'],restrictions={'no_query':True,'no_repeat':True})
    result['request_contract']['goals'][0].update(kind='concept',concept_mode='implication')
    result['task_results'][0].update(kind='concept',fact_ids=['f'])
    result['answer']['content']='这个已核实的数为正数，表示该口径下盈利。'
    return case,run,ref,oracle,[dict(ref['facts']['f'],value_exact=oracle['cards']['f']['value_exact'])]


def test_equivalent_sign_delivery_is_matched_but_the_conclusion_still_needs_review():
    case,run,ref,oracle,context=sign_fixture()
    verdict=score_case(case,run,ref,oracle,MODEL,context,context)
    assert verdict['automated_pass'] and not verdict['passed']
    assert 'sign_conclusion' in verdict['review_required']


@pytest.mark.parametrize('damage',['definition_only','different_metric','missing_fact','different_year','wrong_value'])
def test_generic_concept_or_wrong_evidence_cannot_replace_a_sign_answer(damage):
    case,run,ref,oracle,context=sign_fixture()
    result=run['task']['result']
    if damage=='definition_only':result['request_contract']['goals'][0]['concept_mode']='definition'
    elif damage=='different_metric':result['request_contract']['conditions']['metrics']=['operating_revenue']
    elif damage=='missing_fact':result['facts']=[];result['task_results'][0]['fact_ids']=[]
    elif damage=='different_year':result['facts'][0]['year']=2023
    else:result['facts'][0]['value_exact']='-100000000'
    assert not score_case(case,run,ref,oracle,MODEL,context,context)['automated_pass']
