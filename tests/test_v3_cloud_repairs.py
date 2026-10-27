"""验证稀疏编辑、目标条件、失败状态及财务身份保护。"""
import copy
import pytest
from jsonschema import Draft202012Validator
from src.agent.v3.contracts import Conditions,DialogueState,Goal,Request
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.state import apply_understanding
from src.agent.v3.tasks import failure_context
from src.agent.v3.turn_transport import turn_schema
from src.agent.v3.request_bindings import explicit_output_unit,query_goal_correction

COMPANIES={'600085':'同仁堂','002082':'万邦德','002864':'盘龙药业'}

def edit(field,value,operation='replace',**options):
    return dict(field=field,value=value,operation=operation,**options)

def prior_state(codes=('600085',)):
    conditions=Conditions(codes=list(codes),metrics=['operating_revenue'],time={'mode':'explicit','years':[2023]})
    return DialogueState(topic='financial',conditions=conditions,active_goals=[Goal(id='old',kind='lookup',text='已确认财务任务')])

def apply(question,edits=(),previous=None,goals=None,**options):
    goals=goals or [dict(id='g1',kind='lookup')]
    plan=TurnPlan(goals=goals,edits=[{**e,'text':question} for e in edits],**options)
    for assignment in plan.assignments:
        for e in assignment.edits:e.text=question
    return apply_understanding(question,'new-turn',plan.compile(question,COMPANIES),previous or DialogueState(),COMPANIES)

@pytest.mark.parametrize('company',list(COMPANIES.values()))
def test_sparse_year_update_can_inherit_named_company_and_metric(company):
    state=prior_state().model_dump(mode='json')
    schema=turn_schema(TurnPlan.model_json_schema(),dict(question=f'回到{company}营业收入，改成2022年',companies=COMPANIES,state=state))
    value=dict(continuity='continue',goals=[dict(kind='lookup',context_goal_id='old')],
        edits={'time.years':[dict(operation='replace',value=[2022])]},assignments=[],clarification=[])
    Draft202012Validator(schema).validate(value)

def test_goal_local_company_year_pairs_need_no_duplicate_global_conditions():
    q='分别查询同仁堂2023年和万邦德2022年的营业收入'
    schema=turn_schema(TurnPlan.model_json_schema(),dict(question=q,companies=COMPANIES,state={}))
    def local(code,year):return {field:[dict(operation='replace',value=value)] for field,value in [
        ('codes',[code]),('metrics',['operating_revenue']),('time.years',[year])]}
    value=dict(continuity='new',goals=[dict(kind='lookup'),dict(kind='lookup')],edits={},
        assignments=[dict(id='g1',edits=local('600085',2023)),dict(id='g2',edits=local('002082',2022))],clarification=[])
    Draft202012Validator(schema).validate(value)

def test_unsupported_goal_does_not_require_financial_parameters():
    schema=turn_schema(TurnPlan.model_json_schema(),dict(question='同仁堂的实时股价是多少',companies=COMPANIES,state={}))
    Draft202012Validator(schema).validate(dict(continuity='new',goals=[dict(kind='unsupported')],edits={},assignments=[],clarification=[]))

@pytest.mark.parametrize('kind,field,value',[('quote','quote_mode','location'),('concept','concept_mode','definition'),('catalog','catalog_target','capabilities')])
def test_wire_declares_kind_specific_required_mode(kind,field,value):
    schema=turn_schema(TurnPlan.model_json_schema(),dict(question='请介绍',companies=COMPANIES,state={}))
    raw=dict(continuity='new',goals=[dict(kind=kind)],edits={},assignments=[],clarification=[])
    assert not Draft202012Validator(schema).is_valid(raw)
    raw['goals'][0][field]=value
    Draft202012Validator(schema).validate(raw)

@pytest.mark.parametrize('wording',['元每股','元／股','元/股'])
def test_per_share_unit_is_not_short_matched_as_currency(wording):
    assert explicit_output_unit('基本每股收益用'+wording+'，保留两位小数')=='元/股'
    assert explicit_output_unit('营业收入用元，保留两位小数')=='元'

@pytest.mark.parametrize('code,name',list(COMPANIES.items()))
def test_valid_metric_survives_natural_financial_description(code,name):
    q=f'{name}2022年的净利，要扣非后属于母公司股东的那种'
    request,_=apply(q,[edit('codes',[code]),edit('metrics',['deducted_attributable_net_profit']),edit('time.years',[2022])])
    assert request.conditions.metrics==['deducted_attributable_net_profit']
    assert not request.clarification

def test_existing_lookup_is_not_removed_by_a_pure_query_correction():
    assert not query_goal_correction('毛利额是什么意思？另外给我同仁堂2023年营收的万元数，不用图形',['concept','lookup'])
    assert query_goal_correction('给我同仁堂2023年营业收入',['concept'])['required_goal_kinds']==['lookup']

@pytest.mark.parametrize('scope',['收录公司','已覆盖的企业','数据库里的所有公司','全库公司'])
def test_explicit_catalog_collection_has_one_identity_rule(scope):
    q=f'按2022年毛利率列出{scope}最高三名'
    request,_=apply(q,[edit('all_companies',True),edit('metrics',['gross_margin']),edit('time.years',[2022])],
        goals=[dict(id='rank',kind='rank')],collection_text=scope)
    assert request.conditions.all_companies and set(request.conditions.codes)==set(COMPANIES)
    assert not request.clarification

def test_unregistered_local_identity_is_not_published_or_saved():
    q='查询盘龙药业2022年营业收入'
    request,state=apply(q,[edit('metrics',['operating_revenue']),edit('time.years',[2022])],
        assignments=[dict(id='g1',edits=[edit('codes',['600085'])])])
    assert request.clarification and request.goals[0].clarification
    assert '600085' not in request.for_goal(request.goals[0]).conditions.codes
    assert not any('600085' in c.codes for c in state.goal_conditions.values())

def test_failed_concept_does_not_require_confirming_financial_metric_or_turn_restriction():
    record=dict(id='failed-aside',status='failed',context=prior_state().model_dump(),
        question='先解释毛利率的含义，这轮不查数字',request_checkpoint={'approved':False,'uncertain_paths':['metrics','restrictions.no_query']})
    previous=DialogueState.model_validate(failure_context(record))
    assert previous.conditions.metrics==['operating_revenue']
    assert not previous.interrupted_request.uncertain_paths
    q='回到刚才的查询，改成2022年，其他不变'
    request,_=apply(q,[edit('time.years',[2022])],previous=previous,continuity='continue',goals=[dict(id='g1',kind='lookup',context_goal_id='old')])
    assert not request.clarification

def test_failed_reference_turn_does_not_persist_no_query():
    q='这轮只给原页，不查数，不重复金额'
    request=Request(turn_id='failed-ref',question=q,conditions=Conditions(restrictions={'no_query':True,'no_repeat':True}),
        goals=[Goal(id='ref',kind='quote',text=q,quote_mode='location')])
    previous=DialogueState.model_validate(failure_context(dict(id='failed-ref',status='failed',context=prior_state().model_dump(),question=q,
        request_checkpoint={'approved':False,'request':request.model_dump(),'uncertain_paths':['restrictions.no_query','restrictions.no_repeat']})))
    assert not previous.interrupted_request.uncertain_paths

def test_add_operation_cannot_confirm_an_uncertain_company_base():
    state=prior_state();state.interrupted_request={'turn_id':'bad','question':'更换公司','status':'failed','uncertain_paths':['codes']}
    state=DialogueState.model_validate(state.model_dump())
    request,saved=apply('再加上万邦德',[edit('codes',['002082'],'add')],previous=state,continuity='continue')
    assert request.clarification and saved.interrupted_request.uncertain_paths==['codes']
    request,saved=apply('只保留万邦德',[edit('codes',['002082'],'replace')],previous=saved,continuity='continue')
    assert not request.clarification and saved.interrupted_request is None

def test_current_explicit_company_resolves_a_cleared_context_reference():
    q='我指的是万邦德，单位万元'
    request,_=apply(q,[edit('codes',['002082'])],previous=prior_state(codes=()),continuity='continue',
        context_references=[dict(text='我指的是万邦德',target='company',number='singular')])
    assert request.conditions.codes==['002082'] and not request.clarification

def test_old_failed_turn_restrictions_do_not_block_a_new_financial_task():
    state=prior_state();state.interrupted_request={'turn_id':'old-failed','question':'只给位置，不查数','status':'failed',
        'uncertain_paths':['restrictions.no_query','restrictions.no_repeat']}
    state=DialogueState.model_validate(state.model_dump())
    request,saved=apply('接下来查同仁堂2024年归母净利润',[edit('codes',['600085']),edit('metrics',['attributable_net_profit']),edit('time.years',[2024])],
        previous=state,continuity='continue')
    assert not request.clarification and not request.conditions.restrictions.no_query
    assert saved.interrupted_request is None

def test_global_collection_does_not_authorize_an_unrelated_local_company():
    q='先列库内公司2023年营业收入排名，另外查盘龙药业的营业收入'
    request,_=apply(q,[edit('all_companies',True),edit('metrics',['operating_revenue']),edit('time.years',[2023])],
        goals=[dict(id='rank',kind='rank'),dict(id='local',kind='lookup')],
        assignments=[dict(id='local',edits=[edit('all_companies',False),edit('codes',['600085'])])])
    assert set(request.for_goal(request.goals[0]).conditions.codes)==set(COMPANIES)
    assert not request.for_goal(request.goals[1]).conditions.codes
    assert request.goals[1].clarification

def test_failed_calculation_keeps_verified_available_input_without_fabricating_difference():
    from src.agent.v3.executor import ExecutionResult
    from src.agent.v3.rendering import render_financial
    from tests.test_v3_contracts import fact
    request=Request(turn_id='partial',question='比较两家公司营业收入差额，缺的说明',
        conditions=Conditions(codes=['600085','002082'],metrics=['operating_revenue'],time={'mode':'explicit','years':[2023]},
            calculation='difference',comparison_axis='companies'),goals=[Goal(id='compare',kind='compare',text='比较差额')])
    result=ExecutionResult(facts=[fact('600085',2023,'123450000',metric='operating_revenue')],
        missing=[dict(stock_code='002082',year=2023,period='FY',metric='operating_revenue',reason='report_not_in_catalog')])
    body=render_financial(request,result)
    assert '12,345.00' in body and '万元' in body and '没有' in body
    assert '差额为' not in body

def test_schema_feedback_identifies_missing_and_unexpected_fields_without_values():
    from src.agent.v3.model import schema_feedback
    error=next(Draft202012Validator({'type':'object','required':['quote_mode']}).iter_errors({}))
    assert schema_feedback(error)['missing_fields']==['quote_mode']
    error=next(Draft202012Validator({'type':'object','properties':{},'additionalProperties':False}).iter_errors({'type':'private-content'}))
    feedback=schema_feedback(error)
    assert feedback['unexpected_fields']==['type'] and 'private-content' not in str(feedback)
