import asyncio,time
from copy import deepcopy
from tests.test_v3_contracts import fact,request
from tests.test_v3_execution_math import Repository
from src.agent.v3.agent import V3Agent
from src.agent.v3.contracts import DialogueState,FactReference,Goal,Understanding
from src.agent.v3.executor import execute
from src.agent.v3.model import Budget
from src.agent.v3.value_references import computed_references,revalidate_computed,visible_fact_references
from src.agent.v3.rendering import render_computed_implication


class ReferenceRepository(Repository):
    version='v'
    def by_ids(self,ids):return [f for f in self.values if f.id in ids]


def computed(repo):
    r=request(codes=['600085'],metrics=['attributable_net_profit'],time={'mode':'explicit','years':[2024]},
        calculation='yoy',comparison_axis='years',presentation={'unit':'%'})
    result=execute(r,repo)
    assert visible_fact_references(r,result)==[]
    return computed_references(r,result,repo.version)[0]


def test_saved_rate_is_recomputed_and_positive_profit_cannot_replace_negative_growth(monkeypatch):
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda _:True)
    repo=ReferenceRepository([fact('600085',2023,'100'),fact('600085',2024,'50')])
    reference=computed(repo)
    assert revalidate_computed(repo,reference)==reference
    text=render_computed_implication(reference)
    assert '负数' in text and '减少' in text and '变化量' in text
    changed=reference.model_copy(update={'value':'50.0'})
    assert revalidate_computed(repo,changed) is None
    repo.values[0]=repo.values[0].model_copy(update={'value':'200'})
    assert revalidate_computed(repo,reference) is None


def test_zero_base_reference_remains_undefined_and_cannot_be_explained_as_loss(monkeypatch):
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda _:True)
    repo=ReferenceRepository([fact('600085',2023,'0'),fact('600085',2024,'50')])
    reference=computed(repo)
    assert revalidate_computed(repo,reference)==reference
    assert '未定义' in render_computed_implication(reference)
    assert '亏损' not in render_computed_implication(reference)


def test_followup_implication_uses_revalidated_calculation_not_underlying_amount(monkeypatch):
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda _:True)
    repo=ReferenceRepository([fact('600085',2023,'100'),fact('600085',2024,'50')])
    class NoModel:
        async def structured(self,*args):raise AssertionError('A verified sign is rendered by the program')
    async def emit(*args):pass
    r=request(restrictions={'no_query':True});r.goals=[Goal(id='meaning',kind='concept',text='这个负数是什么意思',concept_mode='implication')]
    previous=DialogueState(recent_computed=[computed(repo)])
    u=Understanding(topic='concept',continuity='continue',goals=r.goals,modifications=[],clarification=[],unsupported=[],unknown_companies=[],claimed_sign='negative')
    state=dict(request=r,previous=previous,understanding=u,emit=emit,budget=Budget(time.time()+60))
    agent=V3Agent(None,{},model=NoModel(),repository_factory=lambda *a,**kw:repo)
    async def scenario():
        executed=await agent._execute(state)
        assert not executed['context_facts'] and len(executed['context_computed'])==1
        result=await agent._evidence({**state,**executed})
        assert result['executed'].goals[0].status=='completed'
        assert any('减少' in text for text in result['prose'])
        assert not any('该指标口径下亏损' in text for text in result['prose'])
    asyncio.run(scenario())


def test_new_general_concept_with_claimed_sign_does_not_borrow_old_financial_facts():
    class NoModel:
        async def structured(self,*args):raise AssertionError("An isolated definition executor cannot call a model")
    async def emit(*args):pass
    r=request(restrictions={'no_query':True});r.goals=[Goal(id='definition',kind='concept',text='负的增长率是什么',concept_mode='definition')]
    previous=DialogueState(recent_facts=[FactReference(id='old',data_version='v')])
    u=Understanding(topic='concept',continuity='new',goals=r.goals,modifications=[],clarification=[],unsupported=[],unknown_companies=[],claimed_sign='negative')
    def forbidden(*a,**kw):raise AssertionError('A general definition cannot read prior financial facts')
    state=dict(request=r,previous=previous,understanding=u,emit=emit)
    result=asyncio.run(V3Agent(None,{},model=NoModel(),repository_factory=forbidden)._execute(state))
    assert result['repository'] is None and not result.get('context_facts')
