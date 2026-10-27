"""计算结果及用户要求的原始输入必须一起交付。"""
import pytest

from src.agent.v3.agent import V3Agent
from src.agent.v3.contracts import Conditions,Goal,Request
from src.agent.v3.planner import SemanticReview,GoalCoverage
from src.agent.v3.request_bindings import explicit_presentation_bindings

def reviewed(q,include_inputs):
    c=Conditions(codes=['600085'],metrics=['operating_revenue'],time={'mode':'explicit','years':[2022,2023]},
        calculation='difference',comparison_axis='years',presentation={'unit':'万元','include_inputs':include_inputs})
    request=Request(turn_id='inputs',question=q,conditions=c,goals=[Goal(id='g1',kind='lookup',text=q,intent_source=q)])
    check=SemanticReview(audit_mode='program',goal_requirements=[GoalCoverage(source_ref=q,kind='lookup',goal_ids=['g1'],representation='represented')],
        satisfied=True,clarification=[],planner_defects=[],companies_correct=True,metrics_and_scope_correct=True,time_correct=True,
        goals_correct=True,constraints_correct=True,presentation_correct=True,continuity_correct=True)
    return V3Agent._review_exact_bindings(request,check)

@pytest.mark.parametrize('q',[
    '同仁堂2022年和2023年营业收入相差多少万元？把两年的数也列出来。',
    '同仁堂2023年营收减2022年，原数和差额都用万元。',
    '按万元给同仁堂2022与2023年营业收入两期数和差额。',
    '查询同仁堂两年营收差额，同时列出基础数字。',
])
def test_explicit_basis_display_requires_the_existing_input_field(q):
    assert explicit_presentation_bindings(q).get('presentation.include_inputs') is True
    from eval.cloud_upload200 import condition_errors
    assert 'include_inputs' in condition_errors({'include_inputs':True},{'presentation':{'include_inputs':False}})
    assert reviewed(q,True).presentation_correct

@pytest.mark.parametrize('q',[
    '同仁堂两年营业收入相差多少万元，只报差额，不要列原数。',
    '查询同仁堂两年营收差额，基础数字不用重复。',
])
def test_explicitly_excluded_basis_is_not_restored(q):
    assert explicit_presentation_bindings(q).get('presentation.include_inputs') is False
    assert reviewed(q,False).presentation_correct
    from eval.cloud_upload200 import condition_errors
    assert 'include_inputs' in condition_errors({'include_inputs':False},{'presentation':{'include_inputs':True}})

def test_plain_difference_query_does_not_require_unrequested_inputs():
    q='同仁堂2022与2023年营业收入差额是多少万元？'
    assert 'presentation.include_inputs' not in explicit_presentation_bindings(q)
    assert reviewed(q,False).presentation_correct

def test_original_table_column_request_is_not_a_basis_display_edit():
    q='同仁堂2023年营业收入原表整行，保留上年比较列和原始单位。'
    assert 'presentation.include_inputs' not in explicit_presentation_bindings(q)
