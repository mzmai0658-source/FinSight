from src.api.main import _internal_done_payload
from src.agent.v3.agent import V3Agent
from src.agent.v3.contracts import Request,Conditions,Goal
from src.agent.v3.planner import SemanticReview
from src.agent.v3.request_bindings import literal_fact_reference


def test_comparison_and_actual_query_trace_survive_api_normalization():
    comparisons=[dict(first='a',second='b',relation='greater',axis='companies')]
    trace=[dict(sql='SELECT payload WHERE metric = :metric',parameters={'metric':'net_profit'},purpose='financial_facts')]
    payload=_internal_done_payload('compare',dict(answer={'content':'A高于B'},comparisons=comparisons,query_trace=trace))
    assert payload['comparisons']==comparisons and payload['query_trace']==trace


def test_waiting_lookup_is_represented_but_never_approved_as_completed():
    request=Request(turn_id='t',question='它2023年是多少？',conditions=Conditions(metrics=['operating_revenue'],time={'mode':'explicit','years':[2023]}),
        goals=[Goal(id='g',kind='lookup',text='查数',clarification=['请明确公司'])],clarification=['请明确公司'])
    audit=SemanticReview(goal_requirements=[dict(kind='lookup',goal_ids=['g'],source_ref='它2023年是多少',representation='missing')],
        satisfied=False,clarification=['请明确公司'],planner_defects=[],companies_correct=False,metrics_and_scope_correct=True,
        time_correct=True,goals_correct=True,constraints_correct=True,presentation_correct=True)
    V3Agent._review_exact_bindings(request,audit)
    assert audit.goal_requirements[0].covered and not audit.planner_defects
    assert not audit.accepted


def test_actual_deictic_value_and_hypothetical_example_have_different_bindings():
    assert literal_fact_reference('这个数为负意味着什么？')
    assert not literal_fact_reference('如果这个数为负意味着什么？')
    assert not literal_fact_reference('净利润为负是什么意思？')
