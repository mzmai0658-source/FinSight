"""作品说明：验证明确条件不被模型替换，经营证据不能跨指标或伪装完成。"""
from src.agent.v3.explicit_conditions import bind_explicit,explicit_scope_conflicts
from src.agent.v3.request_bindings import explicit_output_unit,explicit_period_bindings,positive_years
from src.agent.v3.cause_evidence import bound_cause_snippets,is_evidence_limit
from src.agent.v3.proposals import TurnPlan

def test_negation_and_report_periods_are_preserved():
    assert explicit_output_unit('不用亿元，按万元列出')=='万元'
    assert explicit_output_unit('按亿元并按万元') is None
    assert explicit_period_bindings('2024年第一季度累计')=={(2024,'Q1')}
    assert positive_years('查询2030年的数，不要拿2023年的数回答')=={2030}
    assert positive_years('查2022至2024年')=={2022,2023,2024}

def test_metric_add_operation_survives_literal_binding():
    q='再加归母净利润，其余不变'
    plan=TurnPlan(goals=[dict(id='g1',kind='lookup')],continuity='continue',edits=[dict(field='metrics',operation='add',value=['net_profit'],text=q)])
    fixed,_=bind_explicit(plan,q)
    edit=next(e for e in fixed.edits if e.field=='metrics')
    assert edit.operation=='add' and edit.value==['attributable_net_profit']

def test_global_keep_cannot_be_overridden_by_local_company_edit():
    q='去掉其他公司，只留甲药业'
    plan=TurnPlan(goals=[dict(id='g1',kind='lookup')],continuity='continue',edits=[dict(field='codes',operation='remove',value=['000001'],text=q)],assignments=[dict(id='g1',edits=[dict(field='codes',operation='add',value=['000002'],text=q)])])
    fixed,_=bind_explicit(plan,q,{'000001':'甲药业','000002':'乙药业'})
    assert next(e for e in fixed.edits if e.field=='codes').value==['000001']
    assert all(e.field!='codes' for a in fixed.assignments for e in a.edits)

def test_orphan_nonduplicate_edits_are_not_silently_discarded():
    q='查归母净利润'
    plan=TurnPlan(goals=[dict(id='g1',kind='lookup')],edits=[],assignments=[dict(id='g2',edits=[dict(field='codes',operation='replace',value=['000001'],text=q)])])
    fixed,_=bind_explicit(plan,q)
    assert fixed.assignments[0].id=='g2'

def test_conflicting_scope_requires_confirmation():
    assert explicit_scope_conflicts('只查母公司归母净利润，不接受合并口径')
    assert explicit_scope_conflicts('净利润必须仅用合并口径又必须仅用母公司口径')
    assert not explicit_scope_conflicts('先解释母公司净利润，再查合并净利润')

def test_profit_cause_is_not_a_revenue_cause():
    s={'id':'chunk','text':'扣非归母净利润增长，主要原因为营业收入增加和成本下降所致。\n营业收入变动原因说明：主要由于开拓市场所致。','page':1}
    found=bound_cause_snippets([s],['operating_revenue'])
    assert len(found)==1 and found[0]['text']=='营业收入变动原因说明：主要由于开拓市场所致。'
    assert found[0]['text']==s['text'][found[0]['original_start']:found[0]['original_end']]
    assert not bound_cause_snippets([{**s,'text':s['text'].split('\n')[0]}],['operating_revenue'])
    assert is_evidence_limit('原因尚未确认') and not is_evidence_limit('主要由于开拓市场所致')
