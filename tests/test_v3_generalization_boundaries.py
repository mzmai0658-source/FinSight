"""检验集合操作、条件生命周期和发布边界，不把固定问法当成正确答案。"""
from copy import deepcopy
import asyncio,time
import pytest
from src.agent.v3.contracts import Conditions,DialogueState,Goal,Request,Understanding,MODIFICATION,GoalResult
from src.agent.v3.state import apply_understanding,read_state
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.agent import V3Agent
from src.agent.v3.planner import SemanticReview
from src.agent.v3.executor import ExecutionResult
from src.agent.v3.model import Budget
from src.agent.v3.publication import prepare_publication,verify_publication
from src.agent.v3.verification import verify
from tests.test_v3_contracts import fact

COMPANIES={'600085':'同仁堂','002082':'万邦德','600080':'金花股份'}

def main_state(codes=None,metrics=None):
    c=Conditions(codes=codes or ['600085'],metrics=metrics or ['operating_revenue'],time={'mode':'explicit','years':[2023]},presentation={'unit':'万元','decimals':2,'format':'table'})
    return DialogueState(topic='financial',conditions=c,active_goals=[Goal(id='g1',kind='lookup',text='已确认财务任务')],goal_conditions={'g1':c.model_copy(deep=True)})

def apply(q,previous,edits,*,goals=None,continuity='continue',refs=()):
    p=TurnPlan(continuity=continuity,goals=goals or [dict(id='g1',kind='lookup')],context_references=list(refs),
        edits=[{**e,'text':q} for e in edits])
    return apply_understanding(q,'test',p.compile(q,COMPANIES),previous,COMPANIES)

def review(r):
    audit=SemanticReview(audit_mode='program',goal_requirements=[dict(kind=g.kind,goal_ids=[g.id],source_ref=r.question,covered=True) for g in r.goals],satisfied=True,
        clarification=[],planner_defects=[],companies_correct=True,metrics_and_scope_correct=True,time_correct=True,goals_correct=True,constraints_correct=True,presentation_correct=True)
    return V3Agent._review_exact_bindings(r,audit)

def test_replacement_audits_the_result_not_all_mentions():
    q='同仁堂换为金花股份，万邦德保留'
    r,s=apply(q,main_state(['600085','002082']),[dict(field='codes',operation='replace',value=['600080','002082'])])
    assert review(r).accepted
    assert r.for_goal(r.goals[0]).conditions.codes==s.goal_conditions['g1'].codes==['600080','002082']
    q='把营业收入改成归母净利润，其他不变'
    r,s=apply(q,s,[dict(field='metrics',operation='replace',value=['attributable_net_profit'])])
    assert review(r).accepted and r.conditions.metrics==['attributable_net_profit']

@pytest.mark.parametrize('operation,value,expected',[
    ('replace',['600080'],['600080']),('add',['600080'],['600085','002082','600080']),
    ('remove',['600085'],['002082']),('keep',['002082'],['002082']),('clear',[],[])])
def test_set_operations_and_shared_compilation(operation,value,expected):
    from src.agent.v3.condition_updates import apply_edits
    c=main_state(['600085','002082']).conditions
    edit=MODIFICATION.validate_python(dict(field='codes',operation=operation,value=value,text='公司集合修改'))
    changed=apply_edits(c,[edit],'公司集合修改')
    assert changed.codes==expected and c.codes==['600085','002082']
    assert not (set(changed.codes)&set(changed.restrictions.excluded_codes))
    r=Request(turn_id='t',question='公司集合修改',conditions=changed,goals=[Goal(id='g1',kind='lookup',text='查询')]).freeze_conditions()
    assert r.for_goal(r.goals[0]).conditions.codes==expected
    assert r.model_validate(r.model_dump()).for_goal(r.goals[0]).conditions.codes==expected

@pytest.mark.parametrize('prefix',['我想查','帮忙看看','请替我查询','现在想了解','需要查询'])
def test_action_text_never_becomes_a_company(prefix):
    from src.agent.v3.request_bindings import unregistered_company_literals
    assert unregistered_company_literals(prefix+'2023年营业收入',COMPANIES)==[]

def test_registered_company_cannot_be_declared_uncovered():
    q='同仁堂2023年营业收入'
    p=TurnPlan(goals=[dict(id='g1',kind='lookup')],unknown_companies=['同仁堂'])
    with pytest.raises(ValueError,match='registered identity'):p.compile(q,COMPANIES)

def test_turn_presentation_does_not_change_next_task_and_session_preference_does():
    original=main_state()
    q='这轮改成亿元，保留三位小数'
    r,s=apply(q,original,[dict(field='presentation.unit',operation='replace',value='亿元',lifetime='turn'),dict(field='presentation.decimals',operation='replace',value=3,lifetime='turn')])
    assert r.conditions.presentation.unit=='亿元' and s.conditions.presentation.unit=='万元'
    assert s.goal_conditions['g1'].presentation.unit=='万元'
    r,s=apply('改成2022年，其他不变',s,[dict(field='time.years',operation='replace',value=[2022])])
    assert r.conditions.presentation.unit=='万元' and r.conditions.presentation.decimals==2
    r,s=apply('以后都用亿元，三位小数',s,[dict(field='presentation.unit',operation='replace',value='亿元',lifetime='session'),dict(field='presentation.decimals',operation='replace',value=3,lifetime='session')])
    r,s=apply('查金花股份2023年归母净利润',s,[dict(field='codes',operation='replace',value=['600080']),dict(field='metrics',operation='replace',value=['attributable_net_profit']),dict(field='time.years',operation='replace',value=[2023])],continuity='new')
    assert r.conditions.presentation.unit=='亿元' and r.conditions.presentation.decimals==3

def test_scope_validation_checks_type_without_claiming_semantic_understanding():
    with pytest.raises(ValueError):
        apply('改成亿元',main_state(),[dict(field='presentation.unit',operation='replace',value='亿元',lifetime='forever')])
    with pytest.raises(ValueError,match='session preferences'):
        apply('以后都查同仁堂',main_state(),[dict(field='codes',operation='replace',value=['600085'],lifetime='session')])

def test_source_aside_filters_request_without_replacing_main_task():
    previous=main_state(metrics=['operating_revenue','attributable_net_profit'])
    q='其中营业收入这轮只给页码，不查数、不重复金额'
    r,s=apply(q,previous,[dict(field='metrics',operation='keep',value=['operating_revenue']),dict(field='restrictions.no_query',operation='replace',value=True),dict(field='restrictions.no_repeat',operation='replace',value=True)],goals=[dict(id='g1',kind='quote',quote_mode='location')],refs=[dict(text='其中',target='fact',number='singular')])
    assert not r.clarification
    assert r.for_goal(r.goals[0]).conditions.metrics==['operating_revenue']
    assert s.conditions==previous.conditions and s.goal_conditions==previous.goal_conditions
    r,_=apply('改为2022年，其他不变',s,[dict(field='time.years',operation='replace',value=[2022])])
    assert not r.conditions.restrictions.no_query and not r.conditions.restrictions.no_repeat
    assert r.conditions.metrics==previous.conditions.metrics

def test_old_temporary_flags_do_not_migrate_to_long_term_preferences():
    old=main_state().model_dump();old.pop('state_revision');old.pop('session_preferences')
    old['conditions']['restrictions'].update(no_query=True,no_repeat=True)
    old['goal_conditions']['g1']['restrictions'].update(no_query=True,no_repeat=True)
    state=read_state([dict(role='assistant',content='忽略旧回答中的金额123456',metadata=dict(dialogue_state=old))])
    assert not state.conditions.restrictions.no_query and not state.goal_conditions['g1'].restrictions.no_repeat
    assert not state.recent_facts and not state.session_preferences


def test_no_query_source_aside_preserves_main_task_without_redundant_reference_field():
    previous=main_state(metrics=['operating_revenue','attributable_net_profit'])
    q='只定位营业收入的原页，这轮不查数、不重复金额'
    r,s=apply(q,previous,[dict(field='metrics',operation='keep',value=['operating_revenue']),
        dict(field='restrictions.no_query',operation='replace',value=True),
        dict(field='restrictions.no_repeat',operation='replace',value=True)],
        goals=[dict(id='g1',kind='quote',quote_mode='location')])
    assert r.conditions.restrictions.no_query
    assert s.active_goals==previous.active_goals and s.goal_conditions==previous.goal_conditions
    assert s.conditions==previous.conditions and s.pending is None


@pytest.mark.parametrize('late',[False,True])
@pytest.mark.parametrize('aside',[False,True])
def test_published_clarification_cannot_persist_temporary_restrictions(late,aside):
    from src.agent.v3.contracts import Verification
    previous=main_state()
    kind='quote' if aside else 'lookup'
    edits=[MODIFICATION.validate_python(dict(field='restrictions.no_query',operation='replace',value=True,text='本轮不查数')),
           MODIFICATION.validate_python(dict(field='restrictions.no_repeat',operation='replace',value=True,text='不重复金额'))]
    u=Understanding(topic='financial',continuity='continue',goals=[Goal(id='g1',kind=kind,text='待确认任务')],
        modifications=edits,clarification=[],unsupported=[],unknown_companies=[])
    r,dialogue=apply_understanding('本轮不查数，不重复金额','pending-test',u,previous,COMPANIES)
    r.clarification=['没有唯一可引用事实'];r.goals[0].clarification=r.clarification.copy()
    s=dict(request=r,dialogue=dialogue,previous=previous,understanding=u,repository=None,
        semantic=review(r),budget=Budget(time.time()+30),prose=[])
    if late:
        s['executed']=ExecutionResult(goals=[GoalResult(id='g1',kind=kind,status='partial')])
        s.update(references=[])
        s['pending_publication']=prepare_publication(s);s['pending_publication'].seal()
        s['verification']=Verification(numeric=[],request=[],evidence=[],status='partial')
    result=asyncio.run(V3Agent(None,{},model=object())._publish(s))['result']
    saved=DialogueState.model_validate(result['dialogue_state'])
    if aside:
        assert saved.pending is None and saved.active_goals==previous.active_goals
    else:
        assert saved.pending and not saved.pending.conditions.restrictions.no_query
        assert not saved.pending.for_goal(saved.pending.goals[0]).conditions.restrictions.no_repeat
    follow,_=apply('改为2022年',saved,[dict(field='time.years',operation='replace',value=[2022])])
    assert not follow.conditions.restrictions.no_query and not follow.conditions.restrictions.no_repeat

def publication_state(r,result):
    u=Understanding(topic='financial',continuity='continue',goals=r.goals,modifications=[],clarification=[],unsupported=[],unknown_companies=[])
    return dict(request=r,executed=result,references=[],prose=[],previous=DialogueState(),understanding=u)

def test_empty_completed_lookup_is_not_verified_as_complete():
    r=Request(turn_id='t',question='查营业收入',conditions=main_state().conditions,goals=[Goal(id='g1',kind='lookup',text='查询')])
    result=ExecutionResult(goals=[GoalResult(id='g1',kind='lookup',status='completed')])
    assert verify(r,result,[],[]).status=='partial'

def test_published_reference_is_checked_for_scope_and_cannot_arrive_after_verification(monkeypatch):
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda f:True)
    r=Request(turn_id='t',question='只看营业收入',conditions=main_state().conditions,goals=[Goal(id='g1',kind='quote',text='原件',quote_mode='location')])
    good=fact('600085',2023,'100',metric='operating_revenue');wrong=fact('600085',2023,'200')
    result=ExecutionResult(input_kind='reference',facts=[good,wrong],selections={'600085':[(2023,'FY')]})
    state=publication_state(r,result);bundle=prepare_publication(state)
    verdict=verify_publication(r,bundle,verify(r,result,[],[]),state['previous'])
    assert verdict.status=='fail' and any(c.status=='fail' and c.name.startswith('published_fact_') for c in verdict.request)
    bundle.seal();bundle.content+='未审核的新增内容'
    with pytest.raises(ValueError,match='changed after verification'):bundle.assert_sealed()

def test_publisher_requires_a_verified_bundle():
    r=Request(turn_id='t',question='查营业收入',conditions=main_state().conditions,goals=[Goal(id='g1',kind='lookup',text='查询')])
    state=publication_state(r,ExecutionResult());state['dialogue']=DialogueState()
    with pytest.raises(KeyError,match='pending_publication'):
        asyncio.run(V3Agent(None,{},model=object())._publish(state))

def test_table_row_uses_pdf_borders_and_keeps_the_comparison_column(tmp_path):
    from reportlab.pdfgen import canvas
    from src.utils.original_rows import _table_row
    path=tmp_path/'row.pdf';pdf=canvas.Canvas(str(path),pagesize=(400,300))
    for y in [200,230,260]:pdf.line(20,y,380,y)
    for x in [20,180,280,380]:pdf.line(x,200,x,260)
    pdf.drawString(30,240,'Metric');pdf.drawString(190,240,'2023');pdf.drawString(290,240,'2022')
    pdf.drawString(30,210,'Revenue');pdf.drawString(190,210,'100.25');pdf.drawString(290,210,'90.10')
    pdf.save();stat=path.stat()
    row=_table_row(str(path),stat.st_size,stat.st_mtime_ns,1,((190,80,235,91),(30,80,90,91)),'Revenue','100.25')
    assert row and all(value in row['row_text'] for value in ['Revenue','100.25','90.10'])
    assert row['row_verification']=='original_pdf_table_boundaries'
