"""作品说明：覆盖双模型诊断发现的真实缺陷，验证紧凑请求契约。"""
import pytest
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.turn_transport import turn_schema,decode_turn
from src.agent.v3.state import apply_understanding
from src.agent.v3.contracts import DialogueState,Conditions
from src.agent.v3.agent import V3Agent
from src.agent.v3.planner import SemanticReview,GoalCoverage
from src.agent.v3.model import sampling_schema

COMPANIES={'600085':'同仁堂','002082':'万邦德'}


def test_one_turn_contract_keeps_quote_and_concept_available_without_keyword_whitelist():
    schema=turn_schema(sampling_schema(TurnPlan.model_json_schema()),dict(question='先岔开一下，毛利额和毛利率差在哪',companies=COMPANIES))
    assert 'IntentQuoteGoal' in schema['$defs'] and 'IntentConceptGoal' in schema['$defs']
    assert schema['$defs']['IntentQuoteGoal']['properties']['kind']['const']=='quote'


def test_company_deletion_is_not_missing_company_and_preserves_other_conditions():
    q='先删掉万邦德，保留同仁堂，年份不变。'
    previous=DialogueState(conditions=Conditions(codes=['600085','002082'],metrics=['operating_revenue'],
        time={'mode':'explicit','years':[2024]},presentation={'unit':'亿元','format':'table'}),topic='financial')
    plan=TurnPlan.model_validate(decode_turn(dict(continuity='continue',goals=[dict(id='g1',kind='lookup',source_ref=q)],
        edits={'codes':[dict(field='codes',operation='remove',value=['002082'],text=q)]})))
    request,_=apply_understanding(q,'test',plan.compile(q,COMPANIES),previous,COMPANIES)
    review=SemanticReview(audit_mode='program',goal_requirements=[GoalCoverage(kind='lookup',goal_ids=['g1'],representation='represented',source_ref=q)],
        satisfied=True,clarification=[],planner_defects=[],companies_correct=True,metrics_and_scope_correct=True,
        time_correct=True,goals_correct=True,constraints_correct=True,presentation_correct=True)
    assert V3Agent._review_exact_bindings(request,review).accepted
    assert request.conditions.codes==['600085']
    assert request.conditions.time.years==[2024] and request.conditions.presentation.unit=='亿元'


def test_wrong_parent_scope_and_only_table_are_rejected_without_model_approval():
    q='同仁堂2024年母公司营业收入，用亿元，只给表格。'
    plan=TurnPlan.model_validate(decode_turn(dict(goals=[dict(id='g1',kind='lookup',source_ref=q)],edits={
        'codes':[dict(field='codes',operation='replace',value=['600085'],text=q)],
        'metrics':[dict(field='metrics',operation='replace',value=['operating_revenue'],text=q)],
        'time.years':[dict(field='time.years',operation='replace',value=[2024],text=q)]})))
    request,_=apply_understanding(q,'test',plan.compile(q,COMPANIES),DialogueState(),COMPANIES)
    review=SemanticReview(audit_mode='program',goal_requirements=[GoalCoverage(kind='lookup',goal_ids=['g1'],representation='represented',source_ref=q)],
        satisfied=True,clarification=[],planner_defects=[],companies_correct=True,metrics_and_scope_correct=True,
        time_correct=True,goals_correct=True,constraints_correct=True,presentation_correct=True)
    checked=V3Agent._review_exact_bindings(request,review)
    assert not checked.accepted
    assert not checked.metrics_and_scope_correct and not checked.presentation_correct


def test_field_map_cannot_hide_a_different_edit_or_internal_span_id():
    with pytest.raises(ValueError):decode_turn({'edits':{'codes':[{'field':'metrics'}]}})
    q='解释归母净利润'
    with pytest.raises(ValueError):
        TurnPlan(goals=[dict(id='g1',kind='concept',concept_mode='definition',source_ref='s0')]).compile(q,COMPANIES)
