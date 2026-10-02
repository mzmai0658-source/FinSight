from eval.score_v3_regressions import observed_fact_context,score


def test_failed_prior_query_does_not_turn_valid_referral_clarification_into_a_fabrication():
    record=dict(id='context-case',run=dict(task_id='t',task=dict(status='completed',saved=True,result=dict(task_id='t',
        outcome=dict(status='needs_clarification'),answer=dict(content='请明确所指数字。'),
        request_contract=dict(goals=[dict(id='c',kind='concept')]),facts=[],chart_data_list=[]))))
    spec=dict(kind='concept',must_correct_sign=True)
    result=score(record,spec,{},prior_facts=[])
    assert not result['automated_pass']
    assert result['zero_tolerance_violations']==[]
    assert any('original conversation scenario remains unfulfilled' in error for error in result['errors'])
    record['run']['task']['result']['outcome']['status']='answered'
    result=score(record,spec,{},prior_facts=[dict(value='100')])
    assert 'Did not correct the false negative premise' in result['zero_tolerance_violations']


def test_a_blocked_single_quarter_plan_still_fails_without_claiming_published_substitution():
    record=dict(id='quarter',run=dict(task_id='t',task=dict(status='completed',saved=True,result=dict(task_id='t',
        outcome=dict(status='needs_clarification'),answer=dict(content='本轮没有提供财务数据。'),
        request_contract=dict(goals=[],conditions=dict(time={})),facts=[],chart_data_list=[]))))
    result=score(record,dict(kind='unsupported',quarters=[4]),{})
    assert not result['automated_pass'] and 'Single-quarter intent was substituted' in result['errors']
    assert result['zero_tolerance_violations']==[]
    record['run']['task']['result']['outcome']['status']='answered'
    assert 'Single-quarter intent was substituted' in score(record,dict(kind='unsupported',quarters=[4]),{})['zero_tolerance_violations']


def test_observed_context_uses_actual_matching_values_and_clears_after_failed_turn():
    truth=dict(id='f',value='10',stock_code='600085',year=2024,period='FY',metric='net_profit',scope='consolidated',unit='元')
    references={'v':dict(facts={'f':truth})}
    run=dict(task=dict(status='completed',result=dict(data_version='v',facts=[{**truth,'value_exact':'10'}])))
    assert observed_fact_context(run,references,[])==[truth]
    run['task']['result']['facts'][0]['value_exact']='999'
    run['task']['result']['response_kind']='financial'
    assert observed_fact_context(run,references,[truth])==[]
    assert observed_fact_context(dict(task=dict(status='failed',result={})),references,[truth])==[]
    assert observed_fact_context(dict(task=dict(status='completed',result=dict(response_kind='rules'))),references,[truth])==[truth]
