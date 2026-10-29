"""防止新验收把错误口径、不同目标、格式偶合或对话连带问题算成正确。"""
import copy
import json
from collections import Counter
from pathlib import Path

from eval.common300_cases import build_cases,expected,goal
from eval.common300_run import allocation,select_cases,register_run
from eval.common300_scoring import attribution,match_goals,score_case,summarize,validated_visible_context

MODEL='qwen3.5:9b-q4_K_M'


def fixture():
    f=dict(id='f',stock_code='600085',company='同仁堂',year=2024,period='FY',metric='operating_revenue',
        scope='consolidated',unit='元',value='100000000',data_version='v',source=None)
    ref=dict(version='v',facts={'f':f},reports=[dict(stock_code='600085',year=2024,period='FY')])
    oracle=dict(data_version='v',cards={'f':dict(value_exact='100000000')})
    e=expected(goal(codes=['600085'],metrics=['operating_revenue'],years=[2024],unit='万元',decimals=2))
    case=dict(id='F01V1',group=None,category='查数',expect=e)
    conditions=dict(codes=['600085'],metrics=['operating_revenue'],scope='consolidated',time=dict(years=[2024],periods=['FY']),
        presentation=dict(unit='万元',decimals=2,format='auto'),calculation='none')
    result=dict(request_contract=dict(conditions=conditions,goals=[dict(id='different-id',kind='lookup')]),
        answer=dict(content='营业收入10000.00万元。'),facts=[dict(f,value_exact=f['value'])],outcome=dict(status='answered'),
        data_version='v',task_results=[dict(id='different-id',kind='lookup',status='completed')],
        diagnostics=dict(timings=[dict(model=MODEL)]),dialogue_state=dict(conditions=conditions))
    run=dict(task=dict(status='completed',saved=True,result=result))
    return case,run,ref,oracle


def test_semantic_goal_matching_does_not_depend_on_order_or_ids():
    es=[goal(codes=['a'],metrics=['income'],years=[2023]),goal(codes=['b'],metrics=['profit'],years=[2024])]
    request=dict(conditions={},goals=[dict(id='z',kind='lookup',context_conditions=dict(codes=['b'],metrics=['profit'],scope='consolidated',time=dict(years=[2024],periods=['FY']))),
        dict(id='x',kind='lookup',context_conditions=dict(codes=['a'],metrics=['income'],scope='consolidated',time=dict(years=[2023],periods=['FY'])))])
    matched=match_goals(es,request)
    assert [item[0]['id'] for item in matched]==['x','z'] and not any(item[1] for item in matched)


def test_lookup_correctness_uses_independent_value_and_request_contract():
    case,run,ref,oracle=fixture()
    assert score_case(case,run,ref,oracle,MODEL)['passed']
    run['task']['result']['facts'][0]['value_exact']='100000001'
    verdict=score_case(case,run,ref,oracle,MODEL)
    assert not verdict['passed'] and 'wrong_numeric_fact' in verdict['critical']


def test_real_fact_outside_question_oracle_is_wrong_selection_not_fabrication():
    case,run,ref,oracle=fixture()
    other={**ref['facts']['f'],'id':'other','year':2023}
    ref['facts']['other']=other
    run['task']['result']['facts']=[dict(other,value_exact=other['value'])]
    verdict=score_case(case,run,ref,oracle,MODEL)
    assert not verdict['passed'] and 'out_of_request_fact_published' in verdict['critical']
    assert 'published_fact_outside_prepared_original_oracle' in verdict['errors']
    assert 'unverified_fact_published' not in verdict['critical']


def test_unknown_fact_remains_unverified_even_if_its_number_looks_correct():
    case,run,ref,oracle=fixture()
    run['task']['result']['facts'][0]['id']='invented'
    verdict=score_case(case,run,ref,oracle,MODEL)
    assert 'unverified_fact_published' in verdict['critical']


def test_default_unit_coincidence_is_not_a_saved_user_requirement():
    case,run,ref,oracle=fixture();run['task']['result']['request_contract']['conditions']['presentation']['unit']=None
    verdict=score_case(case,run,ref,oracle,MODEL)
    assert not verdict['passed'] and 'goal_1_lookup_unit' in verdict['errors']


def test_fact_context_survives_a_format_failure_if_number_and_source_are_valid():
    case,run,ref,oracle=fixture();case['expect']['goal_expectations'][0]['format']='table'
    assert not score_case(case,run,ref,oracle,MODEL)['passed']
    assert [f['id'] for f in validated_visible_context(run,ref,oracle)]==['f']
    run['task']['result']['facts'][0]['value_exact']='1'
    assert validated_visible_context(run,ref,oracle)==[]


def test_correct_raw_proposal_overridden_by_compiler_is_code_not_model():
    case,run,ref,oracle=fixture();result=run['task']['result']
    raw=dict(continuity='new',goals=[dict(id='g1',kind='lookup')],edits=[dict(field=k,operation='replace',value=v) for k,v in [
        ('codes',['600085']),('metrics',['operating_revenue']),('time.years',[2024]),('presentation.unit','万元'),('presentation.decimals',2)]])
    result['diagnostics']['structured_trace']=[dict(step='TurnPlan',proposal=raw)]
    result['request_contract']['conditions']['metrics']=['net_profit']
    verdict=score_case(case,run,ref,oracle,MODEL)
    assert attribution(case,run,verdict)['primary']=='code'


def test_missing_raw_proposal_cannot_prove_model_capacity_failure():
    case,run,ref,oracle=fixture();run['task']['result']['request_contract']['conditions']['metrics']=[]
    verdict=score_case(case,run,ref,oracle,MODEL)
    assert attribution(case,run,verdict)['primary']=='unresolved'


def test_account_assignment_caps_sessions_and_keeps_groups_on_one_account():
    cases=[dict(id='S'+str(i),group=None) for i in range(180)]
    cases.extend(dict(id=f'D{g}T{t}',group='D'+str(g)) for g in range(30) for t in range(4))
    cohorts,counts=allocation(cases)
    assert counts=={0:80,1:80,2:50}
    for g in range(30):assert len({cohorts[f'D{g}T{t}'] for t in range(4)})==1


def test_adjustment_expands_whole_families_and_dialogues_within_limit():
    cases=[dict(id=f'F{i}V{v}',family=f'F{i}',group=None,partition='development') for i in range(48) for v in range(3)]
    cases.extend(dict(id=f'D{i}T{t}',family=None,group=f'D{i}',partition='development') for i in range(24) for t in range(4))
    selected=select_cases(dict(cases=cases),'adjustment','F0V0,D0T0')
    assert len(selected)==7
    assert {c['group'] for c in selected if c['group']}=={'D0'}


def test_report_has_no_score_gate_or_fabricated_acceptance():
    row=dict(case=dict(category='classic',group=None,family=None),score=dict(passed=False,automated_pass=False,
        result_class='full_answer',review_status='not_required',critical=[]),attribution=dict(primary='model'))
    result=summarize([row],1)
    assert result['complete_run'] and result['accuracy']==0 and result['no_accuracy_threshold']
    assert 'acceptance_passed' not in result and 'target' not in result


def test_fixed_round_ledger_prevents_unbounded_adjustments_or_final_replacement(tmp_path):
    import pytest
    path=tmp_path/'ledger.json';output=tmp_path/'baseline'
    register_run(path,'baseline',None,output,{},['a'])
    with pytest.raises(AssertionError):register_run(path,'adjustment',1,tmp_path/'r1',{},['a'])
    ledger=json.loads(path.read_text());ledger['runs']['baseline']['terminal']=True;path.write_text(json.dumps(ledger))
    register_run(path,'adjustment',1,tmp_path/'r1',{},['a'])
    with pytest.raises(AssertionError):register_run(path,'adjustment',1,tmp_path/'rerun',{},['a'])
    with pytest.raises(AssertionError):register_run(path,'adjustment',2,tmp_path/'r2',{},['a'])
    with pytest.raises(AssertionError):register_run(path,'adjustment',3,tmp_path/'r3',{},['a'])
    with pytest.raises(AssertionError):register_run(path,'final',None,tmp_path/'final',{},['a'])


def test_resolved_goal_conditions_take_precedence_over_legacy_projection():
    request=dict(conditions=dict(codes=['wrong']),goals=[dict(id='g',kind='lookup')],
        resolved_goal_conditions={'g':dict(codes=['a'],metrics=['income'],scope='consolidated',time=dict(years=[2024],periods=['FY']))})
    matched=match_goals([goal(codes=['a'],metrics=['income'],years=[2024])],request)
    assert not matched[0][1]


def test_real_case_builder_has_fixed_counts_and_whole_partitions():
    from eval.real200_acceptance import reference
    root=Path(__file__).resolve().parents[1];audit=root/'.local_runtime/Real Rebuild Room/FinSight/data/runtime/real-rebuild-ocr'
    if not audit.exists():
        import pytest
        pytest.skip('Private real-PDF acceptance is absent in a public source checkout')
    cases=build_cases(reference(audit))
    assert len(cases)==300 and Counter(c['partition'] for c in cases)==Counter(development=240,sealed=60)
    for group in {c['group'] for c in cases if c['group']}:
        selected=[c for c in cases if c['group']==group]
        assert len(selected)==4 and len({c['partition'] for c in selected})==1
    for family in {c['family'] for c in cases if c['family']}:
        selected=[c for c in cases if c['family']==family]
        assert len(selected)==3 and len({c['partition'] for c in selected})==1
