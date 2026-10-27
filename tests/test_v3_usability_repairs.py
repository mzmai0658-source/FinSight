"""保护口语范围、局部否定、临时展示、原页定位和礼貌引导。"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
import pytest

from src.agent.v3.agent import V3Agent
from src.agent.v3.contracts import Conditions,DialogueState,Goal,Request,MODIFICATION
from src.agent.v3.planner import SemanticReview,GoalCoverage
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.state import apply_understanding

COMPANIES={'600085':'同仁堂','600080':'金花股份','002082':'万邦德'}


def edit(field,value,q,**kw):
    return dict(field=field,value=value,operation='replace',text=q,**kw)


def make_request(q,kind='lookup',codes=None,no_query=False):
    edits=[edit('metrics',['operating_revenue'],q)]
    if '2022' in q:edits.append(edit('time.years',[2022],q))
    if codes is not None:edits.append(edit('codes',codes,q))
    if no_query:edits.append(edit('restrictions.no_query',True,q,lifetime='turn'))
    plan=TurnPlan(goals=[dict(id='g1',kind=kind,source_ref=q)],edits=edits)
    return apply_understanding(q,'oral',plan.compile(q,COMPANIES),DialogueState(),COMPANIES)[0]


def audit(request):
    review=SemanticReview(audit_mode='program',goal_requirements=[GoalCoverage(source_ref=request.question,
        kind=g.kind,goal_ids=[g.id],representation='represented') for g in request.goals],
        satisfied=True,clarification=[],planner_defects=[],companies_correct=True,metrics_and_scope_correct=True,
        time_correct=True,goals_correct=True,constraints_correct=True,presentation_correct=True,continuity_correct=True)
    return V3Agent._review_exact_bindings(request,review)


@pytest.mark.parametrize('span',['库里','数据库里','知识库中','库内全部公司','全库公司'])
def test_universe_omission_cannot_hide_behind_company_clarification(span):
    q=f'按2022年营业收入给{span}从小到大排名'
    request=make_request(q,kind='rank')
    assert request.clarification
    result=audit(request)
    from tests.request_expectations import mismatches
    assert mismatches(request,all_companies=True)


@pytest.mark.parametrize('q',[
    '查询库里同仁堂2022年营业收入',
    '只查数据库里的金花股份2022年营业收入',
    '不查全库公司，只查同仁堂2022年营业收入',
])
def test_named_or_negated_library_mention_is_not_forced_to_universe(q):
    code='600080' if '金花' in q else '600085'
    assert audit(make_request(q,codes=[code])).companies_correct


@pytest.mark.parametrize('q',[
    '查同仁堂2022年营业收入，解释归母净利润，利润金额不用查。',
    '同仁堂2022年营收是多少？另外解释扣非归母，不查利润金额。',
    '把同仁堂2022年营收告诉我；净利润金额别查，只讲概念。',
    '同仁堂2022年营收用万元回答，净利润金额不用查。',
])
def test_local_negative_cannot_disable_positive_numeric_goal(q):
    result=audit(make_request(q,codes=['600085'],no_query=True))
    assert not result.constraints_correct


@pytest.mark.parametrize('q',[
    '不要查数，只解释营业收入的定义。',
    '本轮不查询数字，解释归母净利润。',
])
def test_explicit_global_no_query_is_still_allowed(q):
    request=make_request(q,codes=['600085'],no_query=True)
    assert audit(request).constraints_correct


@pytest.mark.parametrize('q,field,value,required',[
    ('这轮换成亿元三位小数，其他不变。','presentation.unit','亿元','turn'),
    ('这一轮先用万元，其他要求保留。','presentation.unit','万元','turn'),
    ('仅此次保留四位小数。','presentation.decimals',4,'turn'),
    ('这一回先只给表格。','presentation.format','table','turn'),
    ('以后都按亿元回答。','presentation.unit','亿元','session'),
    ('今后都保留三位小数。','presentation.decimals',3,'session'),
])
def test_explicit_lifetime_is_checked_without_requiring_a_restore_phrase(q,field,value,required):
    from src.agent.v3.condition_lifetimes import validate_lifetime,persist_conditions
    from src.agent.v3.condition_updates import apply_edits
    correct=MODIFICATION.validate_python(edit(field,value,q,lifetime=required))
    validate_lifetime(correct,q)
    base=Conditions()
    current=apply_edits(base,[correct],q)
    saved=persist_conditions(current,base,[correct],[])
    group,key=field.split('.')
    if required=='turn':assert getattr(getattr(saved,group),key)==getattr(getattr(base,group),key)
    else:assert getattr(getattr(saved,group),key)==value


def test_each_presentation_field_uses_its_own_scope_clause():
    from src.agent.v3.condition_lifetimes import validate_lifetime
    q='本轮按亿元回答，今后都保留三位小数。'
    validate_lifetime(MODIFICATION.validate_python(edit('presentation.unit','亿元',q,lifetime='turn')),q)
    validate_lifetime(MODIFICATION.validate_python(edit('presentation.decimals',3,q,lifetime='session')),q)


def test_unmarked_display_keeps_existing_task_policy():
    from src.agent.v3.condition_lifetimes import validate_lifetime
    q='查询同仁堂2022年营业收入，用万元保留两位小数。'
    validate_lifetime(MODIFICATION.validate_python(edit('presentation.unit','万元',q,lifetime='task')),q)


@pytest.mark.parametrize('mode,granularity,expected',[('location','row','completed'),('location','location','completed'),('literal','row','partial')])
def test_location_completion_does_not_require_a_verbatim_table_row(monkeypatch,mode,granularity,expected):
    from src.agent.v3 import agent as module
    from src.agent.v3.executor import ExecutionResult
    q='这次只给原页和行名，不重复金额。'
    goal=Goal(id='g1',kind='quote',text=q,quote_mode=mode,quote_granularity=granularity)
    request=Request(turn_id='quote',question=q,conditions=Conditions(codes=['600085'],metrics=['operating_revenue'],
        restrictions={'no_query':True,'no_repeat':True}),goals=[goal])
    fact=SimpleNamespace(id='f1',decimal=Decimal('10'),inputs=[],status='verified',
        source=SimpleNamespace(page=7,table='合并利润表',row='营业收入',raw_unit='元'))
    seen=[]
    def quote(f,precision):
        seen.append(precision)
        return dict(id='quote-f1',fact_id='f1',granularity='cell',text='营业收入单元格')
    monkeypatch.setattr(module,'numeric_quote',quote)
    async def run():
        state=dict(request=request,executed=ExecutionResult(),context_facts=[fact],
            understanding=SimpleNamespace(claimed_sign='none'),repository=None,previous=DialogueState())
        return await V3Agent(None,COMPANIES,model=object())._evidence(state)
    result=asyncio.run(run())
    assert result['executed'].goals[-1].status==expected
    assert seen==['location' if mode=='location' else granularity]
    assert '第 7 页' in '\n'.join(result['prose'])


@pytest.mark.parametrize('target,terms',[('greeting',['你好','财报']),('closing',['不客气'])])
def test_social_catalog_has_a_short_reply_without_report_dependency(target,terms):
    from src.agent.v3.executor import ExecutionResult
    from src.agent.v3.rendering import render_catalog
    text=render_catalog([],target,Conditions())
    assert all(term in text for term in terms)
    assert len(text)<100 and '不在' not in text
    goal=Goal(id='g1',kind='catalog',text='谢谢',catalog_target=target)
    request=Request(turn_id='social',question='谢谢',conditions=Conditions(),goals=[goal])
    async def run():
        return await V3Agent(None,COMPANIES,model=object())._evidence(dict(request=request,executed=ExecutionResult(),
            repository=None,understanding=SimpleNamespace(claimed_sign='none'),previous=DialogueState()))
    result=asyncio.run(run())
    assert result['prose']==[text] and result['executed'].goals[0].status=='completed'


@pytest.mark.parametrize('target',['greeting','closing'])
def test_social_mode_survives_wire_schema_and_condition_compilation(target):
    from src.agent.v3.turn_transport import turn_schema,decode_turn
    from src.agent.v3.model import sampling_schema
    from jsonschema import Draft202012Validator
    q='谢谢，先这样' if target=='closing' else '你好'
    wire=dict(continuity='new',goals=[dict(kind='catalog',catalog_target=target)],edits={},assignments=[],clarification=[])
    schema=turn_schema(sampling_schema(TurnPlan.model_json_schema()),dict(question=q,companies=COMPANIES,state={}))
    Draft202012Validator(schema).validate(wire)
    plan=TurnPlan.model_validate(decode_turn(wire,q))
    request,_=apply_understanding(q,'social',plan.compile(q,COMPANIES),DialogueState(),COMPANIES)
    assert request.goals[0].catalog_target==target


def test_turn_schema_blocks_explicit_temporary_unit_being_saved_as_task():
    from src.agent.v3.turn_transport import turn_schema
    from src.agent.v3.model import sampling_schema
    from jsonschema import Draft202012Validator
    from src.agent.v3.condition_lifetimes import persist_conditions
    from src.agent.v3.condition_updates import apply_edits
    q='这轮只用亿元，其他不变。'
    schema=turn_schema(sampling_schema(TurnPlan.model_json_schema()),dict(question=q,companies=COMPANIES,state={'active_goals':[{'kind':'lookup'}]}))
    wire=dict(continuity='continue',goals=[dict(kind='lookup',context_goal_id='old')],
        edits={'presentation.unit':[dict(operation='replace',value='亿元',lifetime='turn')]})
    Draft202012Validator(schema).validate(wire)
    change=MODIFICATION.validate_python(edit('presentation.unit','亿元',q,lifetime='turn'))
    base=Conditions(presentation={'unit':'万元'})
    current=apply_edits(base,[change],q)
    assert current.presentation.unit=='亿元'
    assert persist_conditions(current,base,[change],[]).presentation.unit=='万元'


def test_prompt_examples_match_the_actual_wire_contract():
    import copy
    import json
    from jsonschema import Draft202012Validator
    from src.agent.v3.prompts import TURN
    from src.agent.v3.turn_transport import turn_schema,decode_turn
    from src.agent.v3.model import sampling_schema
    from src.agent.v3.model_wire import encode_schema,translate_data
    examples=[json.loads(line) for line in TURN.splitlines() if line.startswith('{"question"')]
    assert 1<=len(examples)<=6
    companies={'990101':'甲公司','990102':'乙公司','990103':'丙公司'}
    for example in examples:
        payload=dict(question=example['question'],companies=companies,state=example['context'])
        schema=encode_schema(turn_schema(sampling_schema(TurnPlan.model_json_schema()),payload))
        try:Draft202012Validator(schema).validate(example['output'])
        except Exception as error:raise AssertionError(example['question']) from error
        raw=translate_data(copy.deepcopy(example['output']),decode=True)
        TurnPlan.model_validate(decode_turn(raw,example['question']))


@pytest.mark.parametrize('problem',['universe','local_negative'])
def test_real_review_repair_keeps_sparse_feedback_and_accepts_correct_conditions(problem):
    import time
    q='库里2022年营业收入从低到高排名' if problem=='universe' else '查询同仁堂2022年营业收入，利润金额不用查。'
    kind='rank' if problem=='universe' else 'lookup'
    class Capture:
        calls=0
        async def structured(self,schema,system,payload,budget):
            self.calls+=1
            edits=[edit('metrics',['operating_revenue'],q),edit('time.years',[2022],q)]
            if problem=='universe':
                edits.append(edit('presentation.order','asc',q,lifetime='task'))
                if self.calls==2:edits.append(edit('all_companies',True,q))
            else:
                edits.append(edit('codes',['600085'],q))
                if self.calls==1:edits.append(edit('restrictions.no_query',True,q,lifetime='turn'))
            if self.calls==2:
                feedback=payload['correction_feedback'][0]
                assert feedback['defects'] and 'invalid_request' not in feedback
                assert 'invalid_proposal' in feedback
                assert not {'id','text'}&set(feedback['invalid_proposal']['goals'][0])
                assert 'assignments' not in feedback['invalid_proposal']
            return TurnPlan(goals=[dict(id='g1',kind=kind,source_ref=q)],edits=edits)
    async def run():
        from src.agent.v3.model import Budget
        capture=Capture();agent=V3Agent(None,COMPANIES,model=capture)
        async def emit(*args):pass
        state=dict(question=q,history=[],turn_id='correction',budget=Budget(time.time()+60),emit=emit)
        state.update(await agent._understand(state))
        result=await agent._review(state)
        if problem=='universe':
            from tests.request_expectations import mismatches
            assert result['request'].clarification and capture.calls==1
            assert mismatches(result['request'],all_companies=True)
            return
        assert result['semantic'].accepted and not result['request'].clarification
        assert capture.calls==2
        assert not result['request'].conditions.restrictions.no_query
        if problem=='universe':assert set(result['request'].conditions.codes)==set(COMPANIES)
    asyncio.run(run())


@pytest.mark.parametrize('target,q',[('greeting','你好'),('closing','谢谢，我先走了')])
def test_social_full_graph_preserves_financial_task_without_loading_repository(target,q):
    import time
    from src.agent.v3.state import read_state
    conditions=Conditions(codes=['600085'],metrics=['operating_revenue'],time={'mode':'explicit','years':[2023]},
        presentation={'unit':'万元','format':'table','decimals':2})
    financial=Goal(id='old',kind='lookup',text='已确认财务查询')
    previous=DialogueState(topic='financial',conditions=conditions,active_goals=[financial],goal_conditions={'old':conditions})
    history=[dict(role='assistant',content='旧任务',metadata={'dialogue_state':previous.model_dump(mode='json')})]
    class Capture:
        calls=0
        async def structured(self,schema,system,payload,budget):
            self.calls+=1
            return TurnPlan(goals=[dict(id='g1',kind='catalog',catalog_target=target,source_ref=q)])
    async def run():
        capture=Capture();agent=V3Agent(None,COMPANIES,model=capture)
        def forbidden(*args,**kw):raise AssertionError('Social turn cannot load financial repository')
        agent.repository_factory=forbidden
        async def emit(*args):pass
        result=await agent.run(q,history,'social',time.time()+60,emit)
        assert capture.calls==1 and not result['facts'] and not result['evidence']
        assert '不支持' not in result['answer']['content']
        saved=DialogueState.model_validate(result['dialogue_state'])
        assert saved.conditions==read_state(history).conditions
        assert saved.active_goals==previous.active_goals
        assert saved.goal_conditions==previous.goal_conditions
    asyncio.run(run())
