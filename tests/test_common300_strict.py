"""元数据正确不能替代回答正文和比较结论正确。"""
import copy

from eval.common300_cases import expected,goal
from eval.common300_strict import strict_score
from eval.common300_known120 import resolved_contract_adapter
from tests.test_common300_evaluation import fixture,MODEL


def compare_fixture():
    case,run,ref,oracle=fixture();r=run['task']['result']
    b={**ref['facts']['f'],'id':'b','stock_code':'002082','company':'万邦德','value':'200000000'}
    ref['facts']['b']=b;oracle['cards']['b']=dict(value_exact=b['value'])
    r['facts'].append({**b,'value_exact':b['value']})
    r['request_contract']['conditions']['codes']=['600085','002082']
    r['request_contract']['conditions']['comparison_axis']='companies'
    r['request_contract']['goals'][0]['kind']='compare';r['task_results'][0]['kind']='compare'
    case['expect']=expected(goal('compare',['600085','002082'],['operating_revenue'],[2024],unit='万元',decimals=2,axis='companies'))
    r['comparisons']=[dict(first='f',second='b',axis='companies',relation='less')]
    r['answer']['content']='同仁堂10000.00万元低于万邦德20000.00万元。'
    return case,run,ref,oracle


def test_compare_checks_relation_not_only_source_values():
    case,run,ref,oracle=compare_fixture()
    assert strict_score(case,run,ref,oracle,MODEL)['passed']
    run['task']['result']['comparisons'][0]['relation']='greater'
    verdict=strict_score(case,run,ref,oracle,MODEL)
    assert not verdict['passed'] and 'wrong_comparison_conclusion' in verdict['critical']


def test_explicit_comparison_unit_is_delivered_not_only_stored():
    case,run,ref,oracle=compare_fixture();run['task']['result']['answer']['content']='同仁堂低于万邦德。'
    verdict=strict_score(case,run,ref,oracle,MODEL)
    assert 'comparison_requested_number_missing' in verdict['errors'] and 'comparison_requested_unit_missing' in verdict['errors']


def test_new_resolved_contract_adapter_does_not_change_original_results():
    _,run,_,_=fixture();original=copy.deepcopy(run);r=run['task']['result']['request_contract']
    resolved={**r['conditions'],'scope':'parent'};r['resolved_goal_conditions']={'different-id':resolved}
    current=copy.deepcopy(run);adapted=resolved_contract_adapter(run)
    assert adapted['task']['result']['request_contract']['goals'][0]['context_conditions']['scope']=='parent'
    assert run==current
    assert adapted['task']['result']['facts']==original['task']['result']['facts']
