"""系统说明按事实身份生成一次，保持原文、数字和不同澄清条件。"""
import asyncio
from types import SimpleNamespace

import pytest

from src.agent.v3.agent import V3Agent
from src.agent.v3.contracts import ComputedReference,Conditions,DialogueState,FactReference,Goal,GoalSelection
from src.agent.v3.executor import ExecutionResult
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.publication import prepare_publication
from src.agent.v3.rendering import render_numeric_quote
from src.agent.v3.state import apply_understanding
from tests.test_v3_contracts import fact,request


class NoModel:
    async def structured(self,*args):
        raise AssertionError('确定性程序说明不得调用模型')


async def emit(*args):pass


def publication(values,claimed_sign,*,computed=None,kinds=('concept',)):
    r=request(codes=['600085'],metrics=['attributable_net_profit'],time={'mode':'explicit','years':[2024]},
              restrictions={'no_query':True,'no_repeat':True})
    r.goals=[Goal(id=f'g{i}',kind=kind,text='检查事实',concept_mode='implication' if kind=='concept' else None)
             for i,kind in enumerate(kinds,1)]
    previous=DialogueState(recent_facts=[FactReference(id=f.id,data_version=f.data_version) for f in values])
    blocks=[(r.for_goal(g),ExecutionResult(input_kind='reference',facts=list(values))) for g in r.goals]
    executed=ExecutionResult(facts=list(values),blocks=blocks)
    state=dict(request=r,executed=executed,previous=previous,references=[],prose=[],emit=emit,
        understanding=SimpleNamespace(claimed_sign=claimed_sign),
        context_facts=list(values),context_computed=[computed] if computed else [],
        context_by_goal={g.id:list(values) for g in r.goals},
        context_computed_by_goal={g.id:[computed] if computed else [] for g in r.goals})
    extra=asyncio.run(V3Agent(None,{},model=NoModel())._evidence(state))
    return prepare_publication({**state,**extra})


@pytest.mark.parametrize('value,claimed,actual',[
    ('12345','negative','正数'),('-12345','positive','负数'),('0','positive','零'),
])
def test_one_verified_fact_has_one_premise_correction(value,claimed,actual):
    f=fact('600085',2024,value)
    bundle=publication([f],claimed)
    assert bundle.content.count('先核对前提')==1
    assert actual in bundle.content
    assert bundle.execution.facts==[f]
    bundle.seal().assert_sealed()


def test_two_goals_sharing_a_fact_do_not_repeat_the_same_correction():
    f=fact('600085',2024,'12345')
    bundle=publication([f],'negative',kinds=('sign','concept'))
    assert bundle.content.count('先核对前提')==1
    notices=bundle.bindings['notices']
    assert len(notices)==1 and set(notices[0]['goal_ids'])=={'g1','g2'}
    assert notices[0]['value_id']==f.id


def test_a_correct_premise_does_not_add_a_correction():
    bundle=publication([fact('600085',2024,'-12345')],'negative')
    assert '先核对前提' not in bundle.content and '亏损' in bundle.content


def test_a_multi_company_correction_identifies_the_affected_fact():
    values=[fact('600085',2024,'12345'),fact('002082',2024,'-54321')]
    r=request(codes=['600085','002082'],metrics=['attributable_net_profit'],time={'mode':'explicit','years':[2024]})
    r.goals=[Goal(id=f'g{i}',kind='sign',text='盈亏',selection=GoalSelection(codes=[f.stock_code]))
             for i,f in enumerate(values,1)]
    blocks=[(r.for_goal(g),ExecutionResult(facts=[f])) for g,f in zip(r.goals,values)]
    bundle=prepare_publication(dict(request=r,executed=ExecutionResult(facts=values,blocks=blocks),references=[],
        previous=DialogueState(),understanding=SimpleNamespace(claimed_sign='negative'),prose=[]))
    assert len(bundle.bindings['notices'])==1
    notice=bundle.bindings['notices'][0]
    assert values[0].company in notice['text'] and '2024' in notice['text']
    assert notice['goal_ids']==['g1'] and notice['value_id']==values[0].id


def test_an_undefined_calculation_is_not_corrected_to_a_profit_or_a_zero():
    value=ComputedReference(id='computed',data_version='v',inputs=('base','current'),calculation='yoy',
        comparison_axis='years',metric='attributable_net_profit',scope='consolidated',company='A',year=2024,period='FY',
        value=None,unit='%',formula='(current-base)/abs(base)*100',label='同比增长率',detail='零基期，增长率未定义')
    bundle=publication([],'negative',computed=value)
    assert bundle.content.count('先核对前提')==1 and '未定义' in bundle.content
    assert '实际为零' not in bundle.content and '该指标口径下亏损' not in bundle.content


@pytest.mark.parametrize('metric,scope',[
    ('operating_revenue','consolidated'),('attributable_net_profit','consolidated'),
    ('net_profit','parent'),('eps_basic','consolidated'),('gross_margin','consolidated'),
])
def test_definition_assembly_has_one_sentence_ending_and_does_not_rewrite_original_quotes(metric,scope):
    r=request(metrics=[metric],scope=scope)
    r.goals=[Goal(id='concept',kind='concept',text='定义',concept_mode='definition')]
    state=dict(request=r,executed=ExecutionResult(),understanding=SimpleNamespace(claimed_sign='none'),emit=emit)
    result=asyncio.run(V3Agent(None,{},model=NoModel())._evidence(state))
    assert all('。。' not in text for text in result['prose'])
    assert result['prose'][-1].endswith('。')
    original='原表文字。。\n比较列 1.48 元/股'
    assert render_numeric_quote({'page_start':12,'text':original}).endswith(original)


@pytest.mark.parametrize('pronoun',['它','这家','前面那家'])
def test_one_missing_company_produces_one_question_for_any_registered_reference(pronoun):
    q=f'{pronoun}2024年的营业收入是多少？'
    c=Conditions(metrics=['operating_revenue'],time={'mode':'explicit','years':[2023]})
    previous=DialogueState(topic='financial',conditions=c,active_goals=[Goal(id='old',kind='lookup',text='任务')])
    plan=TurnPlan(continuity='continue',goals=[dict(id='g1',kind='lookup',context_goal_id='old')],
        edits=[dict(field='time.years',operation='replace',value=[2024],text=q)],
        context_references=[dict(text=pronoun,target='company',number='singular')])
    r,s=apply_understanding(q,'new',plan.compile(q,{'600085':'同仁堂'}),previous,{'600085':'同仁堂'})
    assert len(r.clarification)==1 and len(r.goals[0].clarification)==1
    assert pronoun in r.clarification[0]
    assert r.conditions.time.years==[2024] and s.pending


def test_separate_missing_conditions_are_not_removed_as_duplicate_questions():
    q='它2024年的数呢？'
    previous=DialogueState(topic='financial',active_goals=[Goal(id='old',kind='lookup',text='任务')])
    plan=TurnPlan(continuity='continue',goals=[dict(id='g1',kind='lookup',context_goal_id='old')],
        edits=[dict(field='time.years',operation='replace',value=[2024],text=q)],
        context_references=[dict(text='它',target='company',number='singular')])
    r,_=apply_understanding(q,'new',plan.compile(q,{'600085':'同仁堂'}),previous,{'600085':'同仁堂'})
    assert len(r.clarification)==2
    assert any('公司' in line for line in r.clarification) and any('指标' in line for line in r.clarification)
