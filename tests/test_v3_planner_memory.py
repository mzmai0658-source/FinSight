import asyncio,time
from src.agent.v3.contracts import DialogueState
from src.agent.v3.model import Budget
from src.agent.v3.planner import Planner
from src.agent.v3.proposals import TurnPlan


def test_cancelled_or_failed_user_text_does_not_supply_new_goals():
    class Capture:
        payload=None
        async def structured(self,schema,system,payload,budget):
            self.payload=payload
            assert schema is TurnPlan
            return TurnPlan(goals=[dict(id='g',kind='lookup',source_ref=payload['question'])],edits=[dict(field='codes',operation='replace',value=['600085'],text='同仁堂'),dict(field='metrics',operation='replace',value=['attributable_net_profit'],text='归母净利润')])
    async def scenario():
        model=Capture();planner=Planner(model,{'600085':'同仁堂'})
        question='同仁堂归母净利润是多少？'
        interpretation=await planner.understand(question,DialogueState(),
            [dict(role='user',content='此前取消的任务要求分析三年的经营原因')],Budget(time.time()+60))
        assert [g.kind for g in interpretation.goals]==['lookup']
        assert 'recent_user_messages' not in model.payload
        assert '此前取消的任务' not in str(model.payload)
        assert model.payload['state']['active_goals']==[]
    asyncio.run(scenario())


def test_year_change_must_have_current_evidence():
    import pytest
    from src.agent.v3.state import InvalidUnderstanding,validate_year_change
    old={'years':[2023]}
    for change in ({'years':[2026],'periods':['HY']},{'years':[],'periods':['FY']}):
        with pytest.raises(InvalidUnderstanding):validate_year_change('改成半年报，其他不变',old,change)
    validate_year_change('改成2024年',old,{'years':[2024]})
    validate_year_change('改成2022年至2024年',old,{'years':[2022,2023,2024]})
    validate_year_change('改成最新年报',old,{'years':[],'mode':'latest_each'})
    validate_year_change('改成半年报',old,{'years':[2023],'periods':['HY']})


def test_each_explicit_correction_rechecks_the_whole_single_turn_proposal():
    class Capture:
        calls=[]
        async def structured(self,schema,system,payload,budget):
            budget.charge();self.calls.append(schema)
            assert schema is TurnPlan
            return TurnPlan(goals=[dict(id='value',kind='lookup',source_ref=payload['question'])],edits=[dict(field='codes',operation='replace',value=['600085'],text='同仁堂'),dict(field='metrics',operation='replace',value=['operating_revenue'],text='营业收入')])
    async def scenario():
        model=Capture();planner=Planner(model,{'600085':'同仁堂'});budget=Budget(time.time()+90)
        for feedback in ([],[dict(defects=['单位不符'],repair_goals=False)],[dict(defects=['目标类型不符'],repair_goals=True)]):
            result=await planner.understand('同仁堂营业收入',DialogueState(),[],budget,feedback)
            assert result.goals[0].kind=='lookup'
        assert model.calls==[TurnPlan,TurnPlan,TurnPlan]
        assert budget.calls==3
    asyncio.run(scenario())


def test_pure_capability_goal_has_no_parameter_model_or_financial_edit():
    class Capture:
        async def structured(self,schema,system,payload,budget):
            assert schema is TurnPlan
            return TurnPlan(goals=[dict(id='coverage',kind='catalog',text='支持范围',catalog_target='capabilities')])
    result=asyncio.run(Planner(Capture(),{'600085':'同仁堂'}).understand('你能做什么',DialogueState(),[],Budget(time.time()+60)))
    assert result.modifications==[] and result.goals[0].catalog_target=='capabilities'
