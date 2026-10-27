"""原件段落仅成为候选，不能绕过独立支持审核直接宣布经营原因完成。"""
import asyncio
import time

import pytest

from src.agent.v3.agent import V3Agent
from src.agent.v3.contracts import Conditions, Goal, Request
from src.agent.v3.evidence import EvidenceReview, Explanations
from src.agent.v3.executor import ExecutionResult
from src.agent.v3.model import Budget
from tests.test_v3_cause_context_evidence import PARAGRAPH, physical_context, snippet


@pytest.mark.parametrize('supported', [True, False])
def test_literal_candidate_is_reviewed_with_goal_context_and_false_claim_is_not_published(monkeypatch,tmp_path,supported):
    _,digest,_=physical_context(monkeypatch,tmp_path,['2025年年度报告','经营情况讨论与分析',PARAGRAPH])
    row=snippet(PARAGRAPH);row['document_version']=digest
    class Retriever:
        def retrieve(self,*args):return [row]
    class Repository:
        collection='offline';version='candidate'
    seen=[]
    class Model:
        async def structured(self,schema,system,payload,budget):
            budget.charge();seen.append(schema.__name__)
            if schema is Explanations:
                assert payload['snippets'][0]['support_status']=='unreviewed'
                assert payload['allowed_evidence_ids']['cause']
                assert payload['goal_conditions']['cause']['time']['years']==[2025]
                return Explanations(claims=[dict(goal_id='cause',text='主要原因是产品价格走低。',
                    evidence_ids=payload['allowed_evidence_ids']['cause'])],incomplete_goals=[])
            assert schema is EvidenceReview
            assert payload['goal_conditions']['cause']['time']['years']==[2025]
            assert payload['items'][0]['cited_evidence'][0]['support_status']=='unreviewed'
            return EvidenceReview(judgments=[dict(goal_id='cause',claim_index=0,supported=supported,
                detail='直接支持' if supported else '仅为背景，不足以直接支持归母原因')])
    q='样例股份2025年归母净利润变化的原因，依据不足就说明。'
    request=Request(turn_id='cause',question=q,conditions=Conditions(codes=['123456'],metrics=['attributable_net_profit'],
        time={'mode':'explicit','years':[2025]}),goals=[Goal(id='cause',kind='cause',text=q)])
    async def emit(*args):pass
    result=asyncio.run(V3Agent(None,{'123456':'样例股份'},model=Model(),retriever_factory=lambda _:Retriever())._evidence(
        dict(request=request,executed=ExecutionResult(selections={'123456':[(2025,'FY')]}),previous=None,
            repository=Repository(),budget=Budget(deadline=time.time()+90),emit=emit)))
    assert seen==['Explanations','EvidenceReview']
    goal=result['executed'].goals[0]
    if supported:
        assert goal.status=='completed' and goal.evidence_ids and result['references']
    else:
        assert goal.status!='completed' and not goal.evidence_ids and not result['references']
        assert not any('主要原因是产品价格走低' in text for text in result['prose'])
