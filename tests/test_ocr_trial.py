"""宽松OCR试验可独立运行，不能伪装成原件核验完成。"""
from copy import deepcopy

import pytest
from sqlalchemy import create_engine, select

from src.agent.v3.repository import CanonicalRepository, metadata, releases, pointer, stage_release, publish_release
from src.agent.v3.verification import verify
from src.agent.v3.executor import ExecutionResult
from tests.test_v3_contracts import fact, request


def prepared():
    engine = create_engine('sqlite://')
    metadata.create_all(engine)
    value = fact('600085', 2024, '100', metric='operating_revenue')
    value.status = 'unverified'
    report = dict(stock_code='600085', company=value.company, year=2024, period='FY', document_version='a'*64)
    manifest = dict(dataset_profile=dict(id='ocr-trial-v1', kind='real', reports=1, companies=1,
                                        inventory=[report]), review_mode='relaxed_ocr')
    stage_release(engine, 'v', [value], [report], 'trial-index', manifest)
    return engine, value, manifest


def test_trial_uses_a_separate_pointer_and_keeps_strict_publication_blocked():
    from src.etl.ocr_trial import TrialRepository, publish_trial
    engine, value, manifest = prepared()
    with pytest.raises(ValueError, match='incomplete'):
        publish_release(engine, 'v', dict(facts_verified=False))
    publish_trial(engine, 'v', dict(inventory_checked=True, index_verified=True))
    with engine.connect() as conn:
        assert conn.execute(select(pointer.c.version).where(pointer.c.name=='serving')).scalar_one_or_none() is None
        assert conn.execute(select(releases.c.status)).scalar_one() == 'experimental'
    repository = TrialRepository(engine)
    assert repository.by_ids([value.id]) == [value]
    assert repository.query(['600085'], {'600085':[(2024,'FY')]}, ['operating_revenue'], 'consolidated') == [value]
    with pytest.raises(ValueError):
        CanonicalRepository(engine, version='v')
    with pytest.raises(ValueError):
        stage_release(engine, 'v', [value], manifest['dataset_profile']['inventory'], 'other', manifest)


def test_trial_requires_explicit_policy_and_integrity_acceptance():
    from src.etl.ocr_trial import publish_trial
    engine, _, _ = prepared()
    with pytest.raises(ValueError):
        publish_trial(engine, 'v', dict(index_verified=False, inventory_checked=True))
    with engine.begin() as conn:
        conn.execute(releases.update().values(manifest={}))
    with pytest.raises(ValueError):
        publish_trial(engine, 'v', dict(index_verified=True, inventory_checked=True))


def test_explicit_trial_profile_can_be_selected_outside_the_original_lab_database(monkeypatch):
    from src.etl.ocr_trial import configured_trial_agent
    engine, _, _ = prepared()
    from src.etl.ocr_trial import publish_trial
    publish_trial(engine, 'v', dict(inventory_checked=True, index_verified=True))
    monkeypatch.setenv('FINSIGHT_OCR_TRIAL', 'true')
    monkeypatch.setenv('FINSIGHT_DATA_PROFILE', 'ocr-trial-v1')
    monkeypatch.delenv('FINSIGHT_UPGRADE_PROFILE', raising=False)
    agent = configured_trial_agent(engine)
    assert agent.repository_factory(engine).numeric_source_policy == 'ocr_trial'


@pytest.mark.parametrize('enabled,profile', [('false', 'ocr-trial-v1'), ('true', 'strict'), ('true', '')])
def test_trial_selection_fails_closed_without_the_explicit_data_policy(monkeypatch, enabled, profile):
    from src.etl.ocr_trial import configured_trial_agent
    engine, _, _ = prepared()
    monkeypatch.setenv('FINSIGHT_OCR_TRIAL', enabled)
    monkeypatch.setenv('FINSIGHT_DATA_PROFILE', profile)
    monkeypatch.delenv('FINSIGHT_UPGRADE_PROFILE', raising=False)
    with pytest.raises(ValueError, match='explicit'):
        configured_trial_agent(engine)


def test_relaxed_numbers_remain_unknown_and_request_checks_still_apply(monkeypatch):
    from src.etl.ocr_trial import trial_source_ok
    monkeypatch.setattr('src.etl.ocr_trial.trial_source_ok', lambda value: True)
    value = fact('600085', 2024, '100', metric='operating_revenue')
    value.status = 'unverified'
    required = request(codes=['600085'], metrics=['operating_revenue'], time={'mode':'explicit','years':[2024]})
    result = ExecutionResult(facts=[value], selections={'600085':[(2024,'FY')]})
    assert verify(required, result, [], []).status == 'fail'
    relaxed = verify(required, result, [], [], numeric_source_policy='ocr_trial')
    assert relaxed.status == 'partial'
    assert next(check for check in relaxed.numeric if check.name==value.id).status == 'unknown'
    value.stock_code = '002082'
    assert verify(required, result, [], [], numeric_source_policy='ocr_trial').status == 'fail'


def test_relaxed_policy_still_blocks_invalid_values_and_conflict_status(monkeypatch):
    monkeypatch.setattr('src.etl.ocr_trial.trial_source_ok', lambda value: True)
    value = fact('600085', 2024, '100', metric='operating_revenue')
    required = request(codes=['600085'], metrics=['operating_revenue'], time={'mode':'explicit','years':[2024]})
    result = ExecutionResult(facts=[value], selections={'600085':[(2024,'FY')]})
    value.status = 'conflict'
    assert verify(required, result, [], [], numeric_source_policy='ocr_trial').status == 'fail'


def test_derived_fact_keeps_incomplete_input_audit(monkeypatch):
    from src.agent.v3.contracts import Fact,Source
    monkeypatch.setattr('src.etl.ocr_trial.trial_source_ok',lambda value:True)
    revenue=fact('600085',2024,'100',metric='operating_revenue');revenue.status='unverified'
    cost=fact('600085',2024,'60',metric='operating_cost');cost.status='unverified'
    for value in (revenue,cost):
        value.source=Source(document_id='a'*64,document_version='a'*64,source_sha256='a'*64,
            source_path='data_root/report.pdf',page=1,table='合并利润表',row=value.metric,column='2024年度',
            row_index=1,column_index=1,raw_value=value.value,raw_unit='元',raw_precision=0,
            literal=value.value,extraction='ocr',verification='unverified')
    gross=Fact(id='gross',data_version='v',stock_code='600085',company='A',year=2024,period='FY',
               metric='gross_profit',scope='consolidated',value='40',unit='元',status='derived',
               formula='operating_revenue - operating_cost',inputs=[revenue.id,cost.id])
    required=request(codes=['600085'],metrics=['gross_profit'],time={'mode':'explicit','years':[2024]})
    result=ExecutionResult(facts=[gross],selections={'600085':[(2024,'FY')]})
    verdict=verify(required,result,[],[],[revenue,cost],numeric_source_policy='ocr_trial')
    assert next(check for check in verdict.numeric if check.name=='gross').status=='unknown'
    gross.value='41'
    assert verify(required,result,[],[],[revenue,cost],numeric_source_policy='ocr_trial').status=='fail'
    value.status = 'unverified'
    value.unit = '%'
    assert verify(required, result, [], [], numeric_source_policy='ocr_trial').status == 'fail'


def test_trial_profile_discloses_incomplete_audit():
    from src.etl.release_profiles import release_profile, check_report_coverage
    _, _, manifest = prepared()
    manifest['reports'] = 1
    profile = check_report_coverage(manifest, manifest['dataset_profile']['inventory'])
    public = profile.public_metadata()
    assert '未完整核验' in public['label']
    assert public['source_audit_complete'] is False
    wrong = deepcopy(manifest)
    wrong['dataset_profile']['inventory'][0]['document_version'] = ''
    with pytest.raises(ValueError):
        release_profile(wrong)


def test_trial_notice_is_in_verified_bundle_and_preserves_table_only(monkeypatch):
    import asyncio,time
    from src.agent.v3.agent import V3Agent
    from tests.test_v3_graph import Model,Repository
    monkeypatch.setattr('src.etl.ocr_trial.trial_source_ok',lambda value:True)
    class Trial(Repository):
        numeric_source_policy='ocr_trial'
        manifest=dict(dataset_profile=dict(id='ocr-trial-v1',kind='real',reports=1,companies=1,
            inventory=[dict(stock_code='600085',year=2024,period='FY',document_version='a'*64)]))
        def query(self,*args):
            values=super().query(*args)
            for value in values:value.status='unverified'
            return values
    async def emit(*args):pass
    result=asyncio.run(V3Agent(None,{'600085':'同仁堂'},model=Model(),repository_factory=lambda _:Trial()).run(
        '同仁堂母公司营收，亿元三位，只要表格',[],'trial',time.time()+270,emit))
    content=result['answer']['content']
    assert '未完成原件行列核验' in content
    assert all(not line.strip() or line.startswith('|') for line in content.splitlines())
    assert result['diagnostics']['publication']['bindings']['data_notice']['policy']=='ocr_trial'
    assert result['verification_v3']['status']=='partial'


def test_trial_chart_label_does_not_claim_complete_original_audit():
    from src.agent.v3.rendering import chart_compat
    chart=dict(chart_type='bar',x=['2024'],unit='亿元',series=[dict(name='A',values=[1])])
    converted=chart_compat(chart,source_audit_complete=False)
    assert converted['data_source']['source_audit_complete'] is False
    assert '未完整核验' in converted['data_source']['label']
    assert chart_compat(chart)['data_source']['label']=='已核实的版本化事实'


def audited_fact(year=2024, value='100', metric='operating_revenue', *, status='verified'):
    from src.agent.v3.contracts import Source
    item = fact('600085', year, value, metric=metric)
    item.status = status
    item.source = Source(document_id='a'*64, document_version='a'*64, source_sha256='a'*64,
        source_path='data_root/report.pdf', page=1, table='合并利润表', row=metric, column=f'{year}年度',
        row_index=1, column_index=1, raw_value=value, raw_unit='元', raw_precision=0,
        literal=value, extraction='ocr', verification='unverified' if status=='unverified' else 'literal_checked')
    return item


def published_trial(required, executed, *, support=(), computed=(), prose=(), policy='ocr_trial'):
    """Run the real prepare/verify/seal/publish boundary with an offline repository."""
    import asyncio, time
    from src.agent.v3.agent import V3Agent
    from src.agent.v3.contracts import DialogueState, Understanding
    from src.agent.v3.model import Budget
    from src.agent.v3.planner import SemanticReview

    class Repository:
        version = 'v'
        numeric_source_policy = policy
        def by_ids(self, ids):
            return [value for value in support if value.id in ids]

    async def emit(*args):pass
    state = dict(request=required, executed=executed, references=[], previous=DialogueState(),
        dialogue=DialogueState(), repository=Repository(), emit=emit, prose=list(prose),
        context_computed=list(computed), evidence_checks=[], budget=Budget(time.time()+100),
        understanding=Understanding(topic='financial', continuity='new', goals=required.goals,
            modifications=[], clarification=[], unsupported=[], unknown_companies=[]),
        semantic=SemanticReview(audit_mode='program', goal_requirements=[dict(kind=g.kind,
            goal_ids=[g.id], representation='represented') for g in required.goals],
            satisfied=True, clarification=[], planner_defects=[], companies_correct=True,
            metrics_and_scope_correct=True, time_correct=True, goals_correct=True,
            constraints_correct=True, presentation_correct=True))
    async def run():
        agent=V3Agent(None,{},model=object())
        state.update(await agent._verify(state))
        state['pending_publication'].assert_sealed()
        return (await agent._publish(state))['result'], state['pending_publication']
    return asyncio.run(run())


def completed_result(required, values=()):
    from src.agent.v3.contracts import GoalResult
    return ExecutionResult(facts=list(values), selections={'600085':[(value.year,value.period) for value in values]},
        goals=[GoalResult(id=goal.id,kind=goal.kind,status='completed',fact_ids=[value.id for value in values])
               for goal in required.goals])


def test_verified_only_trial_answer_passes_without_an_answer_warning(monkeypatch):
    monkeypatch.setattr('src.agent.v3.verification.checked_source', lambda value:True)
    required=request(codes=['600085'], metrics=['operating_revenue'], time={'mode':'explicit','years':[2024]})
    value=audited_fact()
    answer,bundle=published_trial(required,completed_result(required,[value]),support=[
        audited_fact(2023,status='unverified')])
    assert answer['verification_v3']['status']=='pass'
    assert answer['outcome']['status']=='answered'
    assert '未完成原件行列核验' not in answer['answer']['content']
    assert 'data_notice' not in bundle.bindings
    assert all(check['status']=='pass' for check in answer['verification_v3']['numeric'])


@pytest.mark.parametrize('candidate', [False,True])
def test_trial_calculation_status_follows_its_actual_inputs(monkeypatch,candidate):
    from src.agent.v3.executor import calculate
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda value:True)
    monkeypatch.setattr('src.etl.ocr_trial.trial_source_ok',lambda value:True)
    required=request(codes=['600085'],metrics=['operating_revenue'],time={'mode':'explicit','years':[2024]},
        calculation='yoy',comparison_axis='years',presentation={'unit':'%'})
    current=audited_fact(value='120')
    base=audited_fact(2023,status='unverified' if candidate else 'verified')
    executed=completed_result(required,[current]);executed.support_facts=[base]
    calculate(required,executed)
    answer,bundle=published_trial(required,executed)
    calculation=answer['derived_facts'][0]
    assert answer['verification_v3']['status']==('partial' if candidate else 'pass')
    assert calculation['status']==('unverified' if candidate else 'verified')
    assert calculation['source_audit_complete'] is (not candidate)
    assert ('data_notice' in bundle.bindings) is candidate
    assert next(check for check in answer['verification_v3']['numeric']
                if check['name']==calculation['id'])['status']==('unknown' if candidate else 'pass')


@pytest.mark.parametrize('candidate',[False,True])
def test_nested_derived_trial_fact_follows_verified_or_unverified_leaves(monkeypatch,candidate):
    from src.agent.v3.contracts import Fact
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda value:True)
    monkeypatch.setattr('src.etl.ocr_trial.trial_source_ok',lambda value:True)
    revenue=audited_fact()
    cost=audited_fact(value='60',metric='operating_cost',status='unverified' if candidate else 'verified')
    gross=Fact(id='gross',data_version='v',stock_code='600085',company='A',year=2024,period='FY',
        metric='gross_profit',scope='consolidated',value='40',unit='元',status='derived',
        formula='operating_revenue - operating_cost',inputs=[revenue.id,cost.id])
    margin=gross.model_copy(update=dict(id='margin',metric='gross_margin',unit='%',
        formula='gross_profit / operating_revenue * 100',inputs=[gross.id,revenue.id]))
    required=request(codes=['600085'],metrics=['gross_margin'],time={'mode':'explicit','years':[2024]})
    answer,bundle=published_trial(required,completed_result(required,[margin]),support=[gross,revenue,cost])
    assert answer['verification_v3']['status']==('partial' if candidate else 'pass')
    assert ('data_notice' in bundle.bindings) is candidate
    assert next(check for check in answer['verification_v3']['numeric']
                if check['name']=='margin')['status']==('unknown' if candidate else 'pass')


@pytest.mark.parametrize('broken',['missing','cycle'])
def test_trial_incomplete_ancestry_never_claims_a_complete_source_audit(monkeypatch,broken):
    from src.agent.v3.contracts import Fact
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda value:True)
    revenue=audited_fact()
    gross=Fact(id='gross',data_version='v',stock_code='600085',company='A',year=2024,period='FY',
        metric='gross_profit',scope='consolidated',value='40',unit='元',status='derived',
        formula='operating_revenue - operating_cost',inputs=[revenue.id,'missing'])
    support=[revenue]
    if broken=='cycle':
        cost=audited_fact(value='60',metric='operating_cost')
        gross.inputs=['gross',cost.id]
        support.append(cost)
    required=request(codes=['600085'],metrics=['gross_profit'],time={'mode':'explicit','years':[2024]})
    answer,bundle=published_trial(required,completed_result(required,[gross]),support=support)
    assert answer['verification_v3']['status']=='fail'
    assert bundle.bindings['data_notice']['source_audit_complete'] is False
    assert '未完成原件行列核验' in bundle.content
    assert 'gross' in bundle.bindings['data_notice']['fact_ids']
    assert next(check for check in answer['verification_v3']['numeric']
                if check['name']=='original_source_audit')['status']=='unknown'


def test_trial_chart_provenance_is_decided_for_each_chart(monkeypatch):
    from src.agent.v3.contracts import Goal
    from src.agent.v3.executor import build_charts
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda value:True)
    monkeypatch.setattr('src.etl.ocr_trial.trial_source_ok',lambda value:True)
    values=[audited_fact(),audited_fact(value='60',metric='operating_cost',status='unverified')]
    required=request(codes=['600085'],metrics=['operating_revenue','operating_cost'],
        time={'mode':'explicit','years':[2024]},presentation={'chart_type':'bar'})
    required.goals=[Goal(id='charts',kind='chart',text='图表')]
    executed=completed_result(required,values);build_charts(required,executed)
    answer,bundle=published_trial(required,executed)
    charts={chart['metric']:chart for chart in answer['chart_data_list']}
    assert charts['operating_revenue']['data_source']['source_audit_complete'] is True
    assert charts['operating_revenue']['data_source']['label']=='已核实的版本化事实'
    assert charts['operating_cost']['data_source']['source_audit_complete'] is False
    assert '未完整核验' in charts['operating_cost']['data_source']['label']
    assert bundle.bindings['charts']['0']['source_audit_complete'] is True
    assert bundle.bindings['charts']['1']['source_audit_complete'] is False
    assert bundle.bindings['data_notice']['fact_ids']==[values[1].id]


@pytest.mark.parametrize('kind',['catalog','concept'])
def test_no_financial_facts_get_no_trial_answer_warning(monkeypatch,kind):
    from src.agent.v3.contracts import Goal
    required=request()
    required.goals=[Goal(id='intro',kind=kind,text='介绍',
        catalog_target='greeting' if kind=='catalog' else None,
        concept_mode='definition' if kind=='concept' else None)]
    answer,bundle=published_trial(required,completed_result(required),prose=['可以查询已登记报告的财务指标。'])
    assert answer['verification_v3']['status']=='pass'
    assert not answer['verification_v3']['numeric']
    assert 'data_notice' not in bundle.bindings
    assert 'OCR宽松入库试验' not in answer['answer']['content']


@pytest.mark.parametrize('policy',['strict','ocr_trial'])
def test_bad_verified_source_is_still_rejected_in_each_policy(monkeypatch,policy):
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda value:False)
    monkeypatch.setattr('src.etl.ocr_trial.trial_source_ok',lambda value:True)
    required=request(codes=['600085'],metrics=['operating_revenue'],time={'mode':'explicit','years':[2024]})
    answer,_=published_trial(required,completed_result(required,[audited_fact()]),policy=policy)
    assert answer['verification_v3']['status']=='fail'
    assert answer['facts']==[]


def test_trial_history_computation_audit_includes_recursive_inputs(monkeypatch):
    from src.agent.v3.contracts import ComputedReference,Goal
    monkeypatch.setattr('src.agent.v3.verification.checked_source',lambda value:True)
    monkeypatch.setattr('src.etl.ocr_trial.trial_source_ok',lambda value:True)
    base=audited_fact(2023,status='unverified');current=audited_fact(value='120')
    computed=ComputedReference(id='history-growth',data_version='v',inputs=(base.id,current.id),
        calculation='yoy',comparison_axis='years',metric='operating_revenue',scope='consolidated',
        company='A',year=2024,period='FY',value='20',unit='%',formula='(current-base)/abs(base)*100',
        label='同比增长率')
    required=request(codes=['600085'],metrics=['operating_revenue'],time={'mode':'explicit','years':[2024]})
    required.goals=[Goal(id='concept',kind='concept',text='含义',concept_mode='implication')]
    answer,bundle=published_trial(required,completed_result(required),support=[base,current],computed=[computed],
        prose=['所引用的增长率为正，表示相对基期增加。'])
    assert answer['verification_v3']['status']=='partial'
    assert bundle.bindings['data_notice']['calculation_ids']==['history-growth']
    assert next(check for check in answer['verification_v3']['numeric']
                if check['name']=='history-growth')['status']=='unknown'
