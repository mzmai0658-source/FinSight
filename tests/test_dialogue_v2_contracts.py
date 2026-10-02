"""作品说明：离线验证对话契约，测试通过与真实模型验收分开记录。"""
import pytest
from src.agent.domain import CODE_TO_NAME_MAP, COMPANY_CODE_MAP
from src.agent.semantic_planner import (
    CatalogRequest, ConversationRequest, FinancialRequest, Origin, ResolutionError,
    SemanticPlanner, TimeRequest, conversation_state, resolve_request,
)
from src.agent.answer_composer import AnswerComposer, Paragraph, render_paragraph


@pytest.fixture(autouse=True)
def companies(monkeypatch):
    monkeypatch.setitem(CODE_TO_NAME_MAP, '002082', '万邦德')
    monkeypatch.setitem(COMPANY_CODE_MAP, '万邦德', '002082')


def history(**changes):
    scope = {'turn_id':'saved-turn', 'codes':['002082'], 'metrics':['net_profit'],
             'pairs':[[2024,'FY']], 'period':'FY', 'time_mode':'explicit', 'latest_count':0}
    scope.update(changes)
    return [{'role':'assistant','content':'不可靠旧答案：2025年有数据', 'metadata':{
        'dialogue_state':{'version':2,'active_request':scope}, 'response_kind':'financial'}}]


def financial(**changes):
    values = dict(kind='financial', goal='查营收', standalone_question='万邦德营收',
                  companies=['002082'], metrics=['total_operating_revenue'],
                  origins={'companies':Origin(kind='current',text='万邦德'),
                           'metrics':Origin(kind='current',text='营收')})
    values.update(changes)
    return FinancialRequest(**values)


def test_company_catalog_does_not_validate_an_irrelevant_company_slot():
    request = CatalogRequest(kind='catalog',dimension='companies',companies=['无关残留'],
                             goal='公司目录',standalone_question='库里有哪些公司')
    plan = resolve_request('库里有哪些公司',request,history())
    assert plan.intent == 'coverage_companies' and not plan.codes and not plan.metrics


def test_default_year_is_latest_available_not_old_answer_year():
    plan = resolve_request('万邦德营收',financial(),history())
    assert plan.latest_count == 1 and plan.default_time and not plan.pairs


def test_history_origin_can_supply_a_validated_company_without_repeating_its_name():
    request = financial(relation='followup', origins={
        'companies':Origin(kind='history',turn_id='saved-turn'),
        'metrics':Origin(kind='current',text='营收')},time=TimeRequest(mode='inherit'))
    plan = resolve_request('那营收呢',request,history())
    assert plan.codes == ['002082'] and plan.pairs == [(2024,'FY')]


def test_unbounded_inherited_time_is_rejected():
    request = financial(relation='followup',company_scope='inherit',time=TimeRequest(mode='inherit'))
    with pytest.raises(ResolutionError):
        resolve_request('营收呢',request,history(pairs=[]))


def test_period_catalog_does_not_default_to_latest_report():
    request = CatalogRequest(kind='catalog',dimension='reports',relation='followup',
        goal='半年报年份',standalone_question='万邦德有哪些半年报',time=TimeRequest(period='HY'))
    plan = resolve_request('半年报有哪些年份',request,history())
    assert plan.codes == ['002082'] and plan.coverage_period_filter == 'HY'
    assert not plan.latest_count and not plan.pairs


def test_system_question_does_not_inherit_financial_actions():
    request = ConversationRequest(kind='conversation',topic='help',goal='解释作图条件',
                                   standalone_question='画图需要什么条件')
    plan = resolve_request('画图需要什么条件',request,history())
    assert plan.intent == 'help' and not plan.codes and not plan.metrics and not plan.chart


def test_invalid_persisted_period_is_not_inherited():
    assert conversation_state(history(pairs=[[2024,'Q4']])) == {}


def test_internal_model_error_is_not_a_fake_user_clarification():
    planner = object.__new__(SemanticPlanner)
    calls = []
    def invalid(*args):
        calls.append(args)
        raise ValueError('invalid structured response')
    planner.structured_call = invalid
    with pytest.raises(ValueError): planner.plan_turn('你数据库里有哪些公司',[])
    assert len(calls) == 2


def test_unbound_financial_number_cannot_enter_composed_prose():
    with pytest.raises(ValueError):
        render_paragraph(Paragraph(text='收入123万元'),{},set(),True)


def test_bound_reference_and_literal_path_are_preserved():
    paragraph = Paragraph(text=r'{{F1.value}}；路径 `C:\new\report.pdf`',evidence_ids=['F1'])
    assert render_paragraph(paragraph,{'F1.value':'10万元'},{'F1'},True) == '10万元；路径 `C:\\new\\report.pdf`'


def test_explicit_company_and_semantic_metric_do_not_require_origin_metadata():
    request = financial(origins={}, time=TimeRequest(mode='explicit', years=[2024]))
    plan = resolve_request('万邦德24年营收有多少啊', request, [])
    assert plan.codes == ['002082'] and plan.pairs == [(2024, 'FY')]
    assert plan.request_contract['resolved_origins']['companies']['kind'] == 'current'
    assert plan.request_contract['resolved_origins']['metrics']['validation'] == 'model_semantic_mapping'


def test_multi_company_query_does_not_require_model_authored_provenance(monkeypatch):
    monkeypatch.setitem(CODE_TO_NAME_MAP, '600222', '太龙药业')
    monkeypatch.setitem(CODE_TO_NAME_MAP, '600129', '太极集团')
    plan = resolve_request('太龙药业跟太极集团24年营收都给我看看', financial(
        companies=['600222','600129'], origins={}, time=TimeRequest(mode='explicit',years=[2024])), [])
    assert plan.codes == ['600222','600129']


def test_missing_origin_cannot_authorize_an_unmentioned_company():
    with pytest.raises(ResolutionError):
        resolve_request('24年营收多少', financial(origins={}), [])


def test_missing_origin_can_resolve_company_from_validated_followup():
    plan = resolve_request('那营收呢', financial(origins={}, relation='followup',
        time=TimeRequest(mode='inherit')), history())
    assert plan.codes == ['002082'] and plan.origins['companies'] == 'history'


def test_proven_followup_scope_does_not_retry_for_a_mislabelled_origin():
    request = financial(relation='followup',time=TimeRequest(mode='inherit'))
    plan = resolve_request('做个趋势图吧', request, history())
    assert plan.codes == ['002082'] and plan.origins['companies'] == 'history'


def test_answer_schema_constrains_reference_ids_and_task_numbers():
    from src.agent.answer_composer import answer_schema
    from pydantic import ValidationError
    schema = answer_schema({'C1','S1'}, 1)
    valid = {'lead':{'text':'按已入库年份查询','evidence_ids':['C1']},'covered_tasks':[0]}
    assert schema.model_validate(valid).lead.evidence_ids == ['C1']
    with pytest.raises(ValidationError):
        schema.model_validate({**valid,'lead':{'text':'说明','evidence_ids':['latest_reports']}})
    with pytest.raises(ValidationError):
        schema.model_validate({**valid,'covered_tasks':[1]})


def test_new_topic_does_not_borrow_missing_company_from_history():
    with pytest.raises(ResolutionError):
        resolve_request('24年营收多少', financial(origins={}), history())


@pytest.mark.parametrize('kind', ['financial','catalog'])
def test_complete_tool_answer_does_not_call_model_again(kind):
    class MustNotCallModel:
        def structured_call(self, *args):
            raise AssertionError('A complete tool answer needs no prose pass')
    result = {'request_contract':{'tasks':[{'kind':kind,'evidence':'none'}]},
              'outcome':{'status':'answered','reason_codes':[]},
              'task_results':[{'status':'answered'}], 'answer':{'content':'可靠结果'}}
    actual = AnswerComposer(MustNotCallModel()).compose('查数据', [], result)
    assert actual['outcome']['status'] == 'answered'
    assert actual['answer_assessment']['attempts'] == 0
    assert actual['answer']['content'] == '可靠结果'


def test_explanatory_request_still_uses_composition():
    from src.agent.answer_composer import AnswerDraft
    class Composer:
        calls = 0
        def structured_call(self, *args):
            self.calls += 1
            return AnswerDraft(lead=Paragraph(text='我可以解释指标、查询财报和按数据绘图。'), covered_tasks=[0])
    model = Composer()
    result = {'request_contract':{'tasks':[{'kind':'conversation','topic':'help'}]},
              'response_kind':'conversation', 'outcome':{'status':'answered','reason_codes':[]},
              'task_results':[{'status':'answered'}], 'answer':{'content':'功能说明'}}
    actual = AnswerComposer(model).compose('画图需要什么条件', [], result)
    assert model.calls == 1 and actual['answer_assessment']['accepted']


def test_identity_question_uses_the_capability_template():
    class MustNotCallModel:
        def structured_call(self, *args):
            raise AssertionError('身份说明使用固定模板')
    result = {'request_contract':{'tasks':[{'kind':'conversation','topic':'help'}]},
              'response_kind':'conversation', 'outcome':{'status':'answered','reason_codes':[]},
              'task_results':[{'status':'answered'}], 'answer':{'content':'我是财报学习助手。'}}
    actual = AnswerComposer(MustNotCallModel()).compose('你是谁', [], result)
    text = actual['answer']['content']
    assert actual['answer_assessment']['mode'] == 'capability_template'
    assert text.count('比如') >= 4
    assert '不构成投资建议' in text and '不编数字' in text


def test_how_question_asks_for_prose_and_a_plain_number_does_not():
    class Recorder:
        calls = 0
        def structured_call(self, schema, system, payload):
            self.calls += 1
            from src.agent.answer_composer import AnswerDraft
            return AnswerDraft(lead=Paragraph(text='这是全年口径。', evidence_ids=['F1']), covered_tasks=[0])
    result = {
        'request_contract': {'tasks': [{'kind': 'financial', 'evidence': 'none', 'metric_scope': 'specified'}]},
        'response_kind': 'financial',
        'outcome': {'status': 'answered', 'reason_codes': []},
        'task_results': [{'status': 'answered'}],
        'facts': [{'fact_id': 'f1', 'stock_code': '600222', 'report_year': 2024, 'report_period': 'FY',
                   'field': 'total_operating_revenue', 'value': 10, 'unit': '万元', 'source': {}}],
        'answer': {'content': '太龙药业2024年全年营业收入为10万元。', 'references': []},
    }
    plain = AnswerComposer(Recorder()).compose('太龙2024年营收多少', [], dict(result))
    assert plain['answer_assessment']['attempts'] == 0
    asked = Recorder()
    reading = AnswerComposer(asked).compose('太龙的营收如何', [], dict(result))
    assert asked.calls == 0 and reading['answer_assessment']['attempts'] == 0
    assert reading['answer']['content'] == '太龙药业2024年全年营业收入为10万元。'


def test_fact_backed_answer_stays_accepted_when_commentary_has_raw_digit():
    from src.agent.answer_composer import AnswerDraft
    class BadCommentary:
        def structured_call(self, *args):
            return AnswerDraft(lead=Paragraph(text='收入大约增长了12个百分点。'), covered_tasks=[0])
    result = {
        'request_contract': {'tasks': [{'kind': 'financial', 'evidence': 'cause', 'metric_scope': 'main'}]},
        'response_kind': 'financial',
        'outcome': {'status': 'answered', 'reason_codes': []},
        'task_results': [{'status': 'answered'}],
        'facts': [{'fact_id': 'f1', 'stock_code': '002082', 'report_year': 2024, 'report_period': 'FY',
                   'field': 'net_profit', 'value': 100, 'unit': '万元', 'source': {}}],
        'answer': {'content': '万邦德2024年全年净利润为100万元', 'references': []},
    }
    actual = AnswerComposer(BadCommentary()).compose('万邦德24年业绩为啥变了', [], result)
    assert actual['answer_assessment']['accepted']
    assert actual['outcome']['status'] == 'answered'
    assert 'answer_composition_incomplete' not in actual['outcome']['reason_codes']
    assert actual['answer']['content'] == '万邦德2024年全年净利润为100万元'
