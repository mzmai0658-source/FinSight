"""验证云端格式边界、失败恢复及差额要求的保护规则。"""
import asyncio
import copy
import json
import time
from types import SimpleNamespace

import pytest

from src.agent.v3.agent import V3Agent
from src.agent.v3.contracts import Conditions, DialogueState, Goal, Request
from src.agent.v3.model import AsyncModel, Budget, ModelFailure, sampling_schema
from src.agent.v3.model_wire import encode_schema, translate_data
from src.agent.v3.planner import GoalCoverage, SemanticReview
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.state import apply_understanding
from src.agent.v3.turn_transport import turn_schema

COMPANIES={'600085':'同仁堂','600080':'金花股份','002082':'万邦德'}
QUESTION='解释归母净利润，只讲概念。'

def simple_wire():
    return translate_data(dict(continuity='new',goals=[dict(kind='concept',concept_mode='definition')],
        edits={'metrics':[dict(operation='replace',value=['attributable_net_profit'])]},assignments=[],clarification=[]))

def parse_response(wire,question=QUESTION,memory=None):
    class Reply:
        def bind(self,**kwargs):return self
        async def ainvoke(self,messages):
            content=wire if isinstance(wire,str) else json.dumps(wire,ensure_ascii=False)
            return SimpleNamespace(content=content,response_metadata={'model':'transport-fixture'})
    async def run():
        budget=Budget(time.time()+60)
        model=AsyncModel(factory=lambda **unused:Reply())
        plan=await model._structured_once(TurnPlan,'Only JSON',dict(question=question,companies=COMPANIES,state=memory or {}),budget)
        return plan,budget
    return asyncio.run(run())

@pytest.mark.parametrize('shape',['json_marker','example_wrapper','null_sign','wrapper_and_marker'])
def test_unambiguous_transport_metadata_preserves_the_original_semantics(shape):
    wire=simple_wire()
    if shape=='json_marker':wire['type']='json_object'
    elif shape=='null_sign':wire['claimed_sign']=None
    elif shape=='example_wrapper':wire=dict(question=QUESTION,context={},output=wire)
    else:wire=dict(question=QUESTION,context={},output={**wire,'type':'json_object'})
    original=copy.deepcopy(wire)
    if shape=='null_sign':
        with pytest.raises(ModelFailure):parse_response(wire)
        assert wire==original
        return
    plan,budget=parse_response(wire)
    assert plan.goals[0].kind=='concept'
    assert plan.edits[0].value==['attributable_net_profit']
    assert budget.calls==1 and budget.artifacts.get('transport_normalizations')
    assert wire==original

@pytest.mark.parametrize('damage',[
    'different_question','nonempty_context','extra_outer_field','wrong_type','business_field','null_asserted_sign','duplicate_field',
])
def test_transport_normalization_cannot_hide_unknown_or_conflicting_business_data(damage):
    q=QUESTION;memory={};wire=simple_wire()
    if damage=='different_question':wire=dict(question='另一个问题',context={},output=wire)
    elif damage=='nonempty_context':wire=dict(question=q,context={'codes':['600080']},output=wire)
    elif damage=='extra_outer_field':wire=dict(question=q,context={},output=wire,codes=['600080'])
    elif damage=='wrong_type':wire['type']='ignore_validation'
    elif damage=='business_field':wire.update(type='json_object',codes=['600080'])
    elif damage=='duplicate_field':wire='{"goals":[],"goals":[],"edits":{},"assignments":[],"clarification":[],"continuity":"new"}'
    else:
        q='这个数是负的，所以亏损了？';memory={'recent_fact_count':1}
        wire=translate_data(dict(continuity='continue',goals=[dict(kind='sign')],
            context_references=[dict(text='这个数',target='fact',number='singular')],claimed_sign=None,
            edits={},assignments=[],clarification=[]))
    with pytest.raises(ModelFailure):parse_response(wire,q,memory)

def previous_task(interrupted_question,uncertain):
    conditions=Conditions(codes=['600085'],metrics=['operating_revenue'],time={'mode':'explicit','years':[2023]},
        presentation={'unit':'万元','decimals':2,'format':'table'})
    return DialogueState(topic='financial',conditions=conditions,active_goals=[Goal(id='old',kind='lookup',text='已确认查询')],
        goal_conditions={'old':conditions.model_copy(deep=True)},
        interrupted_request=dict(turn_id='failed',question=interrupted_question,status='failed',confirmed_fields=[],uncertain_paths=uncertain))

def year_followup(previous,q='再查2022年，沿用之前的长期显示要求。'):
    plan=TurnPlan(continuity='continue',goals=[dict(id='g1',kind='lookup',source_ref=q,context_goal_id='old')],
        edits=[dict(field='time.years',operation='replace',value=[2022],text=q)])
    return apply_understanding(q,'next',plan.compile(q,COMPANIES),previous,COMPANIES)

@pytest.mark.parametrize('question,paths',[
    ('这轮换成亿元三位小数，其他不变。',['presentation.unit','presentation.decimals']),
    ('这一回只给表格。',['presentation.format']),
    ('仅此次用万元，暂时不画图。',['presentation.unit','restrictions.no_chart']),
])
def test_failed_turn_only_display_does_not_require_reconfirming_saved_long_term_values(question,paths):
    request,state=year_followup(previous_task(question,paths))
    assert not request.clarification
    assert request.conditions.time.years==[2022]
    assert request.conditions.presentation.unit=='万元' and request.conditions.presentation.decimals==2
    assert state.interrupted_request is None

@pytest.mark.parametrize('question,paths',[
    ('改成金花股份，其他不变。',['codes']),
    ('改看归母净利润。',['metrics']),
    ('以后都用亿元三位小数。',['presentation.unit','presentation.decimals']),
    ('改成亿元三位小数。',['presentation.unit','presentation.decimals']),
    ('这轮改成金花股份，并用亿元。',['codes','presentation.unit']),
])
def test_unconfirmed_company_metric_or_persistent_changes_still_require_confirmation(question,paths):
    request,state=year_followup(previous_task(question,paths))
    assert request.clarification and state.interrupted_request is not None
    if 'codes' in paths:assert '公司' in ''.join(request.clarification)

def review(request):
    semantic=SemanticReview(audit_mode='program',goal_requirements=[GoalCoverage(source_ref=request.question,
        kind=g.kind,goal_ids=[g.id],representation='represented') for g in request.goals],satisfied=True,
        clarification=[],planner_defects=[],companies_correct=True,metrics_and_scope_correct=True,
        time_correct=True,goals_correct=True,constraints_correct=True,presentation_correct=True,continuity_correct=True)
    return V3Agent._review_exact_bindings(request,semantic)

def split_year_request(q,calculation='none'):
    shared=Conditions(codes=['600085'],metrics=['operating_revenue'],time={'mode':'explicit','years':[2022,2023]},
        presentation={'unit':'万元'})
    if calculation!='none':
        shared.calculation=calculation;shared.comparison_axis='years'
        return Request(turn_id='calc',question=q,conditions=shared,goals=[Goal(id='g1',kind='lookup',text=q,intent_source=q)])
    goals=[Goal(id='g1',kind='lookup',text=q,intent_source=q),Goal(id='g2',kind='lookup',text=q,intent_source=q)]
    c1=shared.model_copy(deep=True);c1.time.years=[2022]
    c2=shared.model_copy(deep=True);c2.time.years=[2023]
    return Request(turn_id='calc',question=q,conditions=shared,goals=goals,resolved_goal_conditions={'g1':c1,'g2':c2})

@pytest.mark.parametrize('q',[
    '同仁堂2022年和2023年营业收入相差多少万元？两年数也列出来。',
    '算一下同仁堂2022年与2023年营收的差额，用万元。',
    '同仁堂2022、2023年营业收入的差值是多少？',
])
def test_difference_requirement_cannot_be_satisfied_by_two_independent_lookups(q):
    from eval.cloud_upload200 import match_goals
    expected=dict(kind='compare',codes=['600085'],metrics=['operating_revenue'],years=[2022,2023],
                  periods=['FY'],scope='consolidated',calculation='difference',axis='years')
    request=split_year_request(q)
    _,errors=match_goals([expected],request.model_dump(mode='json'))
    assert errors
    correct=split_year_request(q,calculation='difference')
    assert not match_goals([expected],correct.model_dump(mode='json'))[1]

@pytest.mark.parametrize('q',[
    '同仁堂2022年和2023年营业收入相差多少万元？两年数也列出来。',
    '算一下同仁堂2022年与2023年营收的差额，用万元。',
])
def test_explicit_difference_is_accepted_when_typed_calculation_covers_both_years(q):
    assert review(split_year_request(q,'difference')).goals_correct

@pytest.mark.parametrize('q',[
    '查同仁堂2022年和2023年营业收入，单位万元。',
    '查同仁堂2022年和2023年营业收入，不要计算差额。',
    '查同仁堂2022年和2023年营业收入，不需要相差计算。',
])
def test_independent_or_negated_difference_queries_are_not_forced_to_calculate(q):
    assert review(split_year_request(q)).goals_correct

def test_original_comparison_column_is_not_a_new_calculation_requirement():
    q='同仁堂2023年营业收入的原表整行，保留去年比较列，不计算差额。'
    request=Request(turn_id='row',question=q,conditions=Conditions(codes=['600085'],metrics=['operating_revenue'],
        time={'mode':'explicit','years':[2023]}),goals=[Goal(id='g1',kind='quote',text=q,intent_source=q,quote_mode='literal',quote_granularity='row')])
    assert review(request).goals_correct

def test_cleared_context_does_not_advertise_invented_goal_ids():
    from jsonschema import Draft202012Validator
    q='它2024年的营业收入呢？'
    memory={'active_goals':[],'goal_conditions':{},'pending':None,'conditions':{}}
    schema=encode_schema(turn_schema(sampling_schema(TurnPlan.model_json_schema()),dict(question=q,companies=COMPANIES,state=memory)))
    wire=translate_data(dict(continuity='new',goals=[dict(kind='lookup',context_goal_id='g1')],
        edits={},assignments=[],clarification=[]))
    assert not Draft202012Validator(schema).is_valid(wire)
    wire['goals'][0].pop('context_goal_id')
    assert Draft202012Validator(schema).is_valid(wire)

def test_real_confirmed_goal_id_remains_available_to_followups():
    from jsonschema import Draft202012Validator
    q='再查2022年，其他不变。'
    memory={'active_goals':[{'id':'old','kind':'lookup'}],'goal_conditions':{'old':{'codes':['600085']}},'conditions':{'codes':['600085']}}
    schema=encode_schema(turn_schema(sampling_schema(TurnPlan.model_json_schema()),dict(question=q,companies=COMPANIES,state=memory)))
    wire=translate_data(dict(continuity='continue',goals=[dict(kind='lookup',context_goal_id='old')],
        edits={},assignments=[],clarification=[]))
    assert Draft202012Validator(schema).is_valid(wire)


def test_legacy_context_without_goal_identifiers_preserves_wire_compatibility():
    from jsonschema import Draft202012Validator
    q='再查2022年，其他不变。'
    memory={'active_goals':[{'kind':'lookup'}]}
    schema=encode_schema(turn_schema(sampling_schema(TurnPlan.model_json_schema()),dict(question=q,companies=COMPANIES,state=memory)))
    wire=translate_data(dict(continuity='continue',goals=[dict(kind='lookup',context_goal_id='old')],
        edits={},assignments=[],clarification=[]))
    assert Draft202012Validator(schema).is_valid(wire)
