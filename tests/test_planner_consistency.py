"""作品说明：通过录制的模型输出重放两步规划，离线测试无需启动 Ollama。"""
import pytest
from src.agent.domain import CODE_TO_NAME_MAP, COMPANY_CODE_MAP
from src.agent.entity_linker import link_entities
from src.agent.semantic_planner import SemanticPlanner, TurnIntent


@pytest.fixture(autouse=True)
def companies(monkeypatch):
    for code, name in [('002082', '万邦德'), ('600085', '同仁堂'), ('600222', '太龙药业'),
                       ('600129', '太极集团'), ('600080', '金花股份'), ('002864', '盘龙药业')]:
        monkeypatch.setitem(CODE_TO_NAME_MAP, code, name)
        monkeypatch.setitem(COMPANY_CODE_MAP, name, code)


def financial_task(goal, question, relation='new'):
    return {'tasks': [{'kind': 'financial', 'goal': goal, 'standalone_question': question, 'relation': relation}]}


def slots(**changes):
    value = dict(companies=[], company_scope='specified', metrics=[], metric_scope='specified',
                 time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1),
                 unit='', inherit_unit=False, calculation='none', comparison='none',
                 presentation='auto', evidence='none', clarify=[], options=[])
    value.update(changes)
    return {'tasks': [value]}


def scripted(*outputs):
    """作品说明：每次输出都由规划器实际要求的模式校验。"""
    planner = object.__new__(SemanticPlanner)
    calls = []
    def structured_call(schema, system, payload):
        calls.append({'schema': schema.__name__, 'feedback': payload.get('correction_feedback')})
        return schema.model_validate(outputs[len(calls) - 1])
    planner.structured_call = structured_call
    return planner, calls


def history(**scope):
    catalog = scope.pop('catalog', None)
    pending = scope.pop('pending', False)
    last_intent = scope.pop('last_intent', None)
    outcome = scope.pop('outcome', None)
    state = {'turn_id': 'saved-turn', 'codes': ['002082'], 'metrics': ['net_profit'],
             'pairs': [[2023, 'FY']], 'period': 'FY', 'time_mode': 'explicit', 'latest_count': 0, **scope}
    dialogue = {'version': 2, 'active_request': None if pending else state, 'pending_request': state if pending else None}
    if catalog is not None:
        dialogue['last_catalog'] = catalog
    if last_intent:
        dialogue['last_intent'] = last_intent
    if outcome:
        dialogue['outcome'] = outcome if isinstance(outcome, dict) else {'status': outcome}
    return [{'role': 'user', 'content': '万邦德23年净利润'},
            {'role': 'assistant', 'content': '旧答案', 'metadata': {
                'response_kind': 'financial', 'dialogue_state': dialogue}}]


def test_linker_reads_aliases_negation_and_ambiguous_cash_flow():
    assert link_entities('太龙药业跟太极集团24年营收都给我看看').company_codes() == ['600222', '600129']
    assert link_entities('太极24年营收').company_codes() == ['600129']
    negated = link_entities('不看万邦德了，看同仁堂')
    assert negated.company_codes() == ['600085'] and negated.excluded_companies == ['002082']
    cash = link_entities('金花股份23年现金流多少')
    assert cash.metric_ids() == [] and cash.ambiguous_metrics and cash.years == [2023]
    assert link_entities('同仁堂最近三年净利润').years == []


def test_recorded_empty_metrics_are_repaired_instead_of_asking_the_user():
    # 作品说明：防止模型只填写营业总收入目标，却遗漏指标列表。
    recorded = slots(companies=['600222', '600129'])
    planner, calls = scripted(financial_task('查询太龙药业和太极集团2024年的营业总收入', '太龙药业和太极集团2024年营收'),
                              recorded, recorded)
    plan = planner.plan_turn('太龙药业跟太极集团24年营收都给我看看', [])
    assert plan.intent == 'facts' and not plan.clarification
    assert plan.codes == ['600222', '600129'] and plan.metrics == ['total_operating_revenue']
    assert plan.pairs == [(2024, 'FY')]
    review = plan.request_contract['plan_validation']
    assert review['slot_attempts'] == 2 and 'plan_repaired' in review['reason_codes']
    assert review['repairs'][0] == {'task': 0, 'issue': 'plan_incomplete_metric',
                                    'repaired_by': 'entity_linker', 'source': 'current_question'}
    assert [c['schema'] for c in calls] == ['TurnIntent', 'FinancialTaskSlots', 'FinancialTaskSlots']
    assert '营业总收入' in calls[2]['feedback'][0]


def test_model_correction_on_retry_is_not_counted_as_program_repair():
    planner, _ = scripted(financial_task('查询万邦德2024年营收', '万邦德2024年营收'),
                          slots(companies=['002082']),
                          slots(companies=['002082'], metrics=['total_operating_revenue']))
    plan = planner.plan_turn('万邦德24年营收同比多少，如何说明', [])
    review = plan.request_contract['plan_validation']
    assert plan.metrics == ['total_operating_revenue'] and not review['repairs']
    assert review['reason_codes'] == ['plan_retried']


def test_consistent_plan_needs_exactly_one_slot_call():
    planner, calls = scripted(financial_task('查询万邦德2024年营收同比', '万邦德2024年营收同比'),
                              slots(companies=['002082'], metrics=['total_operating_revenue'], calculation='yoy'))
    plan = planner.plan_turn('万邦德24年营收同比多少', [])
    assert plan.intent == 'facts' and plan.calculation == 'yoy' and plan.pairs == [(2023, 'FY'), (2024, 'FY')]
    assert calls == []
    assert plan.request_contract['plan_validation']['reason_codes'] == ['short_path']


def test_truly_missing_metric_is_a_user_clarification_without_retry():
    planner, calls = scripted(financial_task('查询万邦德2024年财报', '万邦德2024年财报'),
                              slots(companies=['002082']))
    plan = planner.plan_turn('万邦德24年的呢', [])
    assert plan.intent == 'clarify' and plan.reason == 'metric_required'
    assert plan.request_contract['plan_validation']['reason_codes'] == ['user_missing_metric']
    assert len(calls) == 2


def test_unneeded_metric_clarification_is_removed():
    asks = slots(companies=['002082'], metrics=['total_operating_revenue'], clarify=['metric'], options=['营业收入'])
    planner, _ = scripted(financial_task('查询万邦德2024年营收', '万邦德2024年营收'), asks, asks)
    plan = planner.plan_turn('万邦德24年营收有多少啊', [])
    assert plan.intent == 'facts' and not plan.clarification and not plan.options


def test_ambiguous_cash_flow_clarification_keeps_company_and_year_for_the_answer():
    planner, _ = scripted(financial_task('查询金花股份2023年现金流', '金花股份2023年现金流'),
                          slots(companies=['600080'], time=dict(mode='explicit', years=[2023], reports=[], period=None, count=1),
                                clarify=['metric']))
    plan = planner.plan_turn('金花股份23年现金流多少', [])
    assert plan.intent == 'clarify' and plan.codes == ['600080'] and plan.pairs == [(2023, 'FY')]
    assert '现金流' in plan.clarification and len(plan.options) >= 3


def test_named_company_overrides_a_wrong_inherit():
    wrong = slots(company_scope='inherit', metric_scope='inherit', time=dict(mode='inherit', years=[], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('换成盘龙药业查询', '盘龙药业2023年净利润', 'followup'), wrong, wrong)
    plan = planner.plan_turn('换盘龙药业，其他不变', history())
    assert plan.codes == ['002864'] and plan.metrics == ['net_profit'] and plan.pairs == [(2023, 'FY')]


def test_stated_year_overrides_default_time():
    wrong = slots(companies=['002082'], metrics=['total_operating_revenue'],
                  time=dict(mode='default', years=[], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询万邦德2024年营收', '万邦德2024年营收'), wrong, wrong)
    plan = planner.plan_turn('万邦德24年营收有多少啊', [])
    assert plan.pairs == [(2024, 'FY')] and not plan.default_time


def test_year_change_keeps_previous_period_when_repaired():
    wrong = slots(company_scope='inherit', metric_scope='inherit', time=dict(mode='inherit', years=[], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('换成2023年同一期', '同仁堂2023年前三季度净利润', 'followup'), wrong, wrong)
    plan = planner.plan_turn('换23年同一期', history(codes=['600085'], pairs=[[2024, 'Q3']], period='Q3'))
    assert plan.codes == ['600085'] and plan.pairs == [(2023, 'Q3')]


def test_unclear_input_after_refusal_asks_for_the_task_only():
    planner, calls = scripted({'tasks': [{'kind': 'unclear', 'goal': '无法确定', 'standalone_question': '1', 'relation': 'new'}]})
    plan = planner.plan_turn('1', [{'role': 'user', 'content': '今天吃什么'}])
    assert plan.intent == 'clarify' and '我还没确定你的意思。你想查询财报数据，还是了解系统功能？' in plan.clarification
    assert '例如' in plan.clarification
    assert len(calls) == 1


def test_conversation_turn_uses_a_single_planning_call():
    planner, calls = scripted({'tasks': [{'kind': 'conversation', 'topic': 'help', 'goal': '说明画图条件',
                                          'standalone_question': '画图需要什么条件', 'relation': 'new'}]})
    plan = planner.plan_turn('你需要满足什么条件才能画图', [])
    assert plan.intent == 'help' and not plan.codes and len(calls) == 1


def test_catalog_without_model_company_uses_the_stated_company():
    planner, _ = scripted({'tasks': [{'kind': 'catalog', 'dimension': 'reports', 'companies': [], 'time': None,
                                      'goal': '万邦德有哪些报告', 'standalone_question': '万邦德有哪些报告', 'relation': 'new'}]})
    plan = planner.plan_turn('万邦德有哪些报告', [])
    assert plan.intent == 'coverage_periods' and plan.codes == ['002082']


def test_intent_schema_rejects_financial_fields_in_first_step():
    with pytest.raises(ValueError):
        TurnIntent.model_validate({'tasks': [{'kind': 'financial', 'goal': 'g', 'standalone_question': 'q',
                                              'relation': 'new', 'metrics': []}]})


def test_recorded_self_chosen_cash_flow_becomes_a_clarification():
    # 作品说明：泛称现金流不能默默映射成净现金流。
    guessed = slots(companies=['600080'], metrics=['net_cash_flow'],
                    time=dict(mode='explicit', years=[2023], reports=[], period=None, count=1))
    planner, calls = scripted(financial_task('查询金花股份2023年的现金流数据', '金花股份2023年现金流'), guessed, guessed)
    plan = planner.plan_turn('金花股份23年现金流多少', [])
    assert plan.intent == 'clarify' and plan.codes == ['600080'] and plan.pairs == [(2023, 'FY')]
    assert '经营' in plan.clarification and not plan.metrics
    assert calls == [] and plan.request_contract['pending_choice']['slot'] == 'metric'


def test_retry_that_adds_clarification_but_keeps_guessed_cash_flow_is_still_repaired():
    # 作品说明：真实样例曾同时返回指标澄清和净现金流，错误继续查询数值；本用例防止重现。
    guessed = slots(companies=['600080'], metrics=['net_cash_flow'],
                    time=dict(mode='explicit', years=[2023], reports=[], period=None, count=1))
    kept = slots(companies=['600080'], metrics=['net_cash_flow'], clarify=['metric'],
                 time=dict(mode='explicit', years=[2023], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询金花股份2023年的现金流数据', '金花股份2023年现金流'), guessed, kept)
    plan = planner.plan_turn('金花股份23年现金流多少', [])
    assert plan.intent == 'clarify' and not plan.metrics
    assert plan.codes == ['600080'] and plan.pairs == [(2023, 'FY')]
    assert plan.request_contract['plan_validation']['mode'] == 'short_path'


def test_recorded_today_year_in_followup_is_replaced_by_inherited_time():
    # 作品说明：追问样例曾把 2023 年替换为当前自然年；本用例固定正确上下文。
    invented = slots(companies=['600080'], metrics=['operating_cf_net_amount'],
                     time=dict(mode='explicit', years=[2026], reports=[], period='HY', count=1))
    planner, _ = scripted(financial_task('查询金花股份2026年半年报经营活动现金流', '金花股份半年报经营现金流', 'followup'),
                          invented, invented)
    plan = planner.plan_turn('经营活动那个，半年报', history(codes=['600080'], pairs=[[2023, 'FY']]))
    assert plan.pairs == [(2023, 'HY')]
    issues = [item['issue'] for item in plan.request_contract['plan_validation']['repairs']]
    assert 'plan_unstated_year' in issues or 'plan_continuation_time' in issues


def test_invented_year_in_new_question_falls_back_to_default_latest():
    invented = slots(companies=['002082'], metrics=['total_operating_revenue'],
                     time=dict(mode='explicit', years=[2026], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询万邦德营收', '万邦德营收'), invented, invented)
    plan = planner.plan_turn('万邦德营收多少', [])
    assert plan.default_time and plan.latest_count == 1 and not plan.pairs


def test_year_on_year_base_year_is_not_treated_as_invented():
    yoy = slots(companies=['002082'], metrics=['total_operating_revenue'], calculation='yoy',
                time=dict(mode='explicit', years=[2023, 2024], reports=[], period=None, count=1))
    planner, calls = scripted(financial_task('查询万邦德2024年营收同比', '万邦德2024年营收同比'), yoy)
    plan = planner.plan_turn('万邦德24年营收同比多少', [])
    assert plan.pairs == [(2023, 'FY'), (2024, 'FY')] and plan.calculation == 'yoy' and calls == []


def test_recorded_inherited_chart_is_dropped_when_turn_does_not_ask_for_one():
    # 作品说明：趋势图后的单独查毛利率请求，不能因旧图表上下文自动再画图。
    drawn = slots(companies=['600085'], metrics=['gross_profit_margin'], presentation='line',
                  time=dict(mode='inherit', years=[], reports=[], period='FY', count=3))
    planner, _ = scripted(financial_task('查询同仁堂毛利率', '同仁堂最新三份年报毛利率', 'followup'), drawn, drawn)
    plan = planner.plan_turn('对，再单独看毛利率', history(codes=['600085'], pairs=[], latest_count=3, time_mode='latest'))
    assert not plan.chart and plan.latest_count == 3


def test_requested_chart_is_kept():
    chart = slots(companies=['600085'], metrics=['total_operating_revenue'], presentation='line',
                  time=dict(mode='latest', years=[], reports=[], period='FY', count=3))
    planner, calls = scripted(financial_task('画同仁堂营收趋势图', '同仁堂最新三份年报营收趋势图'), chart)
    plan = planner.plan_turn('同仁堂最新三份年报营收画个趋势图', [])
    assert plan.chart and len(calls) == 2


def test_typo_company_near_miss_clarifies_instead_of_resolving():
    guessed = slots(companies=['600085'], metrics=['total_operating_revenue'],
                    time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询同仁堂2024年收入', '同仁堂2024年收入'), guessed, guessed)
    plan = planner.plan_turn('同仁唐2024年收入有多少', [])
    assert plan.intent == 'clarify' and not plan.codes and not plan.metrics
    assert '同仁堂' in plan.clarification and '同仁堂' in plan.options
    assert plan.request_contract['company_confirmation'] == {
        'original_question': '同仁唐2024年收入有多少', 'typo_span': '同仁唐',
    }


def test_unknown_organization_is_unsupported_not_company_required():
    guessed = slots(companies=['002082'], metrics=['total_operating_revenue'],
                    time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询苹果公司2024年营收', '苹果公司2024年营收'), guessed, guessed)
    plan = planner.plan_turn('苹果公司24年的营收呢', [])
    assert plan.intent == 'unsupported' and plan.reason == 'unknown_company'


def test_main_metric_wording_forces_fixed_main_set():
    from src.agent.facts import MAIN_FINANCIAL_METRICS
    invented = slots(companies=['002082'], metrics=['net_profit'],
                     time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询万邦德2024年主要数字', '万邦德2024年主要数字'), invented, invented)
    plan = planner.plan_turn('万邦德2024年主要数字给我列一下', [])
    assert plan.intent == 'facts' and plan.metrics == list(MAIN_FINANCIAL_METRICS)


def test_q3_wording_stays_cumulative_not_single_quarter():
    wrong = slots(companies=['002864'], metrics=['net_profit'],
                  time=dict(mode='explicit', years=[2025], reports=[], period='single_quarter', count=1))
    planner, _ = scripted(financial_task('查询盘龙药业前三季度净利润', '盘龙药业2025年前三季度净利润'), wrong, wrong)
    plan = planner.plan_turn('盘龙药业去年前三季度挣了多少', [])
    assert plan.intent == 'facts' and plan.pairs == [(2025, 'Q3')]
    assert plan.reason != 'single_quarter_not_supported'


def test_collective_ten_companies_resolves_as_all(monkeypatch):
    # 作品说明：“这 N 家”只有在数量与实际登记目录一致时才表示全库。
    registry = {f'{i:06d}': f'公司{i}' for i in range(10)}
    monkeypatch.setattr('src.agent.entity_linker.CODE_TO_NAME_MAP', registry)
    monkeypatch.setattr('src.agent.semantic_planner.CODE_TO_NAME_MAP', registry)
    monkeypatch.setattr('src.agent.semantic_planner.COMPANY_CODE_MAP', {name: code for code, name in registry.items()})
    wrong = slots(companies=['000000'], metrics=['total_operating_revenue'],
                  time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('全库营收排名', '这十家公司2024年营收排名'), wrong, wrong)
    plan = planner.plan_turn('这十家公司24年营收从大到小排一下', [])
    assert plan.intent == 'facts' and plan.all_companies and not plan.codes


def test_income_plus_ambiguous_cash_flow_publishes_specific_metric_and_clarifies():
    partial = slots(companies=['600080'], metrics=['total_operating_revenue', 'net_cash_flow'],
                    time=dict(mode='explicit', years=[2022], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询金花股份收入和现金流', '金花股份2022年收入和现金流'), partial, partial)
    plan = planner.plan_turn('金花股份22年收入和现金流分别多少', [])
    assert plan.metrics == ['total_operating_revenue']
    assert 'net_cash_flow' not in plan.metrics
    assert plan.codes == ['600080'] and plan.pairs == [(2022, 'FY')]
    assert plan.clarification and ('现金流' in plan.clarification or any('经营' in o for o in plan.options))


def test_cause_performance_question_keeps_main_metrics_not_only_net_profit():
    from src.agent.facts import MAIN_FINANCIAL_METRICS
    guessed = slots(companies=['002082'], metrics=['net_profit'], evidence='cause',
                    time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('解释万邦德业绩变化', '万邦德2024年业绩变化原因'), guessed, guessed)
    plan = planner.plan_turn('万邦德24年业绩为啥变了，引用同一年财报', [])
    assert plan.intent == 'explanation' and plan.metrics == list(MAIN_FINANCIAL_METRICS)
    assert plan.needs_evidence and plan.pairs == [(2024, 'FY')]


def test_unique_short_alias_inside_a_sentence_is_an_exact_company():
    planned = slots(companies=['600129'], metrics=['total_operating_revenue'],
                    time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1))
    planner, calls = scripted(financial_task('查询太极2024年收入', '太极2024年收入'), planned)
    plan = planner.plan_turn('太极2024年收入多少', [])
    assert plan.intent == 'facts' and plan.codes == ['600129'] and plan.pairs == [(2024, 'FY')]
    assert len(calls) == 0 and plan.request_contract['plan_validation']['mode'] == 'short_path'


def test_year_only_followup_keeps_previous_company_and_metric():
    invented = slots(companies=['600085'], metrics=['gross_profit_margin'],
                     time=dict(mode='explicit', years=[2026], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询2023年', '再看看23年', 'new'), invented, invented)
    plan = planner.plan_turn('再看看23年', history(codes=['002864'], metrics=['net_profit'], pairs=[[2024, 'FY']]))
    assert plan.codes == ['002864'] and plan.metrics == ['net_profit'] and plan.pairs == [(2023, 'FY')]


def test_metric_only_followup_keeps_previous_company_and_years():
    invented = slots(companies=['002082'], metrics=['net_profit'],
                     time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('单独看毛利率', '那毛利率单独列一下', 'new'), invented, invented)
    plan = planner.plan_turn('那毛利率单独列一下', history(codes=['600085'], metrics=['total_operating_revenue'],
                                                         pairs=[[2022, 'FY'], [2023, 'FY'], [2024, 'FY']]))
    assert plan.codes == ['600085'] and plan.metrics == ['gross_profit_margin']
    assert plan.pairs == [(2022, 'FY'), (2023, 'FY'), (2024, 'FY')]


def test_bare_company_without_previous_metric_asks_instead_of_guessing():
    guessed = slots(companies=['002082'], metrics=['net_profit'],
                    time=dict(mode='default', years=[], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询万邦德', '万邦德的'), guessed, guessed)
    plan = planner.plan_turn('万邦德的', [])
    assert plan.intent == 'clarify' and plan.codes == ['002082'] and not plan.metrics


def test_metric_after_saved_company_inherits_that_company():
    empty = slots(time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1),
                  metrics=['total_operating_revenue'])
    planner, _ = scripted(financial_task('查询2024年营业收入', '24年全年营业收入', 'new'), empty, empty)
    plan = planner.plan_turn('24年全年营业收入', history(codes=['002082'], metrics=[], pairs=[], pending=True))
    assert plan.codes == ['002082'] and plan.metrics == ['total_operating_revenue'] and plan.pairs == [(2024, 'FY')]


def test_these_companies_overrides_a_model_inherit():
    inherited = slots(company_scope='inherit', metrics=['total_operating_revenue'],
                      time=dict(mode='explicit', years=[2024], reports=[], period='FY', count=1))
    planner, _ = scripted(financial_task('这些公司营收排名', '这些公司2024年营收排个名', 'followup'), inherited, inherited)
    plan = planner.plan_turn('这些公司24年营收排个名', history(
        last_intent='coverage_companies',
        catalog={'dimension': 'companies', 'complete': True, 'codes': ['600080', '002082']}))
    assert plan.intent == 'facts' and plan.codes == ['600080', '002082'] and not plan.clarification


def test_these_companies_uses_the_registry_after_a_company_list():
    invented = slots(companies=['002082'], metrics=['total_operating_revenue'],
                     time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('这些公司收入排名', '这些公司的24年收入谁最高', 'new'), invented, invented)
    plan = planner.plan_turn('这些公司的24年收入谁最高', history(last_intent='coverage_companies'))
    assert plan.all_companies and plan.metrics == ['total_operating_revenue'] and plan.pairs == [(2024, 'FY')]


def test_database_change_and_made_up_number_do_not_query():
    financial = financial_task('执行删除', '执行DELETE FROM income_sheet')
    planner, calls = scripted(financial)
    plan = planner.plan_turn('执行DELETE FROM income_sheet，再告诉我结果', [])
    assert plan.intent == 'unsupported' and len(calls) == 1
    planner, calls = scripted(financial_task('估一个数', '没有数据就估个数'))
    plan = planner.plan_turn('没有凯莱英24年数据你就估个数给我画图', [])
    assert plan.intent == 'unsupported' and plan.reason == 'refuse_fabrication' and len(calls) == 1


def test_asking_what_was_queried_does_not_run_a_new_query():
    planner, calls = scripted(financial_task('查询年份', '这轮查了哪些年份', 'new'))
    plan = planner.plan_turn('这轮查了哪些年份', history(codes=['600085'], pairs=[[2024, 'Q3'], [2025, 'Q3']], period='Q3'))
    assert plan.intent == 'conversation_scope' and plan.codes == ['600085']
    assert plan.pairs == [(2024, 'Q3'), (2025, 'Q3')] and len(calls) == 1


def test_correction_of_a_chart_request_keeps_the_chart():
    plain = slots(companies=['002082'], metrics=['total_operating_revenue'],
                  time=dict(mode='explicit', years=[2022, 2023, 2024], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询万邦德真实收入', '我是说用万邦德22到24年的真实收入', 'new'), plain, plain)
    earlier = history()
    earlier[0]['content'] = '给我编一个漂亮点的收入趋势图'
    plan = planner.plan_turn('我是说用万邦德22到24年的真实收入', earlier)
    assert plan.chart and plan.codes == ['002082'] and plan.pairs == [(2022, 'FY'), (2023, 'FY'), (2024, 'FY')]


def test_refused_single_quarter_keeps_the_year_for_the_next_turn():
    quarter = slots(companies=['600085'], metrics=['net_profit'],
                    time=dict(mode='explicit', years=[2024], reports=[], period='single_quarter', count=1))
    planner, _ = scripted(financial_task('查询单季利润', '同仁堂2024年第三季度单季利润'), quarter)
    plan = planner.plan_turn('同仁堂24年第三季度单季利润，不是累计', [])
    assert plan.intent == 'unsupported' and plan.codes == ['600085'] and plan.pairs == [(2024, 'FY')]


def test_how_is_it_going_uses_overview_metrics_without_asking():
    from src.agent.facts import MAIN_FINANCIAL_METRICS
    asked = slots(companies=['002082'], clarify=['metric'],
                  time=dict(mode='latest', years=[], reports=[], period='FY', count=1))
    planner, _ = scripted(financial_task('查询万邦德近况', '万邦德最近怎么样'), asked, asked)
    plan = planner.plan_turn('万邦德最近怎么样', [])
    assert plan.intent == 'facts' and plan.overview and plan.metrics == list(MAIN_FINANCIAL_METRICS)
    assert not plan.clarification and plan.latest_count == 1


def test_named_metric_is_not_replaced_by_overview_wording():
    asked = slots(companies=['002082'], metrics=['total_operating_revenue'],
                  time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1))
    planner, calls = scripted(financial_task('查询万邦德2024年营收', '万邦德2024年营收最近怎么样'), asked)
    plan = planner.plan_turn('万邦德24年营收最近怎么样', [])
    assert plan.metrics == ['total_operating_revenue'] and not plan.overview and len(calls) == 2


def test_cause_question_drops_an_unrequested_prior_year():
    both = slots(companies=['600085'], metrics=['total_operating_revenue'], evidence='cause',
                 time=dict(mode='explicit', years=[2023, 2024], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('解释同仁堂经营情况', '同仁堂2024年管理层怎么解释经营情况'), both, both)
    plan = planner.plan_turn('同仁堂24年管理层怎么解释经营情况，摘一段原文', [])
    assert plan.pairs == [(2024, 'FY')] and plan.needs_evidence


def test_explain_do_not_invent_is_help_and_delete_is_refused_even_if_unclear():
    planner, calls = scripted({'tasks': [{'kind': 'unclear', 'goal': '无法确定', 'standalone_question': 'DELETE', 'relation': 'new'}]})
    plan = planner.plan_turn('执行DELETE FROM income_sheet，再告诉我结果', [])
    assert plan.intent == 'unsupported' and len(calls) == 1
    planner, calls = scripted({'tasks': [{'kind': 'conversation', 'topic': 'refuse_fabrication', 'goal': '不要编',
                                          'standalone_question': '没有原因的证据就说明，不要编', 'relation': 'new'}]})
    plan = planner.plan_turn('没有原因的证据就说明，不要编', [])
    assert plan.intent == 'help' and len(calls) == 1


def test_pronoun_without_saved_company_asks_which_company():
    guessed = slots(companies=['002082'], metrics=['net_profit'],
                    time=dict(mode='explicit', years=[2025], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询它去年净利润', '它去年赚多少'), guessed, guessed)
    plan = planner.plan_turn('它去年赚多少', [])
    assert plan.intent == 'clarify' and not plan.codes and not plan.metrics


def test_period_change_after_refusal_keeps_the_previous_year():
    latest = slots(companies=['600085'], metrics=['net_profit'],
                   time=dict(mode='latest', years=[], reports=[], period='Q3', count=1))
    planner, _ = scripted(financial_task('改查前三季度', '前三季度累计也行，查那个', 'new'), latest, latest)
    plan = planner.plan_turn('前三季度累计也行，查那个', history(codes=['600085'], metrics=['net_profit'], pairs=[[2024, 'FY']]))
    assert plan.codes == ['600085'] and plan.metrics == ['net_profit'] and plan.pairs == [(2024, 'Q3')]


def test_named_company_drops_a_neighbor_copied_from_the_previous_table():
    mixed = slots(companies=['600129', '600222'], metrics=['total_operating_revenue'],
                  time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询太龙营收', '太龙的营收如何'), mixed, mixed)
    plan = planner.plan_turn('太龙的营收如何', history(codes=['600129'], metrics=['total_operating_revenue'],
                                                    pairs=[[2024, 'FY']]))
    assert plan.intent == 'facts' and plan.codes == ['600222']
    assert plan.metrics == ['total_operating_revenue'] and not plan.overview


def test_new_short_name_question_does_not_fail_when_the_model_inherits_time():
    inherited = slots(companies=['600222'], metrics=['total_operating_revenue'],
                      time=dict(mode='inherit', years=[], reports=[], period=None, count=1))
    planner, calls = scripted(financial_task('查询太龙营收', '太龙的营收如何'), inherited)
    plan = planner.plan_turn('太龙的营收如何', [])
    assert plan.intent == 'facts' and plan.codes == ['600222']
    assert plan.metrics == ['total_operating_revenue'] and plan.default_time and plan.latest_count == 1
    assert len(calls) == 2


def test_wrong_neighbor_is_replaced_by_the_company_in_the_sentence():
    wrong = slots(companies=['600129'], metrics=['total_operating_revenue'],
                  time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询太龙营收', '太龙的营收如何'), wrong, wrong)
    plan = planner.plan_turn('太龙的营收如何', [])
    assert plan.codes == ['600222'] and plan.metrics == ['total_operating_revenue']


def test_jinhua_short_name_stays_jinhua_and_revenue_is_not_expanded():
    from src.agent.facts import MAIN_FINANCIAL_METRICS
    expanded = slots(companies=['600080', '600129'], metrics=list(MAIN_FINANCIAL_METRICS),
                     metric_scope='main',
                     time=dict(mode='explicit', years=[2024], reports=[], period=None, count=1))
    planner, _ = scripted(financial_task('查询金花营收', '金花的营收如何'), expanded, expanded)
    plan = planner.plan_turn('金花的营收如何', [])
    assert plan.codes == ['600080'] and plan.metrics == ['total_operating_revenue'] and not plan.overview


def test_source_page_sentence_names_the_file_and_page():
    from src.agent.reply_text import source_page_lines
    text = source_page_lines([('太龙药业', '2024年', '全年', '营业总收入', '600222_20250409_R8ON.pdf', '90–92')])
    assert text == '太龙药业2024年全年营业总收入的原文在该报告 PDF 第90–92页。'


def test_confirming_the_suggested_company_continues_the_original_question():
    dialogue = {'version': 2, 'pending_question': {
        'turn_id': 'saved-turn', 'question': '你是说同仁堂吗',
        'options': [{'id': '1', 'label': '同仁堂'}],
        'original_question': '同仁唐2024年收入有多少', 'typo_span': '同仁唐',
    }}
    history = [
        {'role': 'user', 'content': '同仁唐2024年收入有多少'},
        {'role': 'assistant', 'content': '你是说同仁堂吗', 'metadata': {'dialogue_state': dialogue}},
    ]
    planner, calls = scripted(financial_task('不应列出公司', '同仁堂'))
    plan = planner.plan_turn('同仁堂', history)
    assert plan.intent == 'facts' and plan.codes == ['600085']
    assert plan.metrics == ['total_operating_revenue'] and plan.pairs == [(2024, 'FY')]
    assert calls == []


def test_confirmation_and_followup_use_the_registry_not_a_fixed_company_list(monkeypatch):
    """作品说明：不在演示名称集合中的已登记公司，也应支持确认及后续指标或年份替换。"""
    monkeypatch.setitem(CODE_TO_NAME_MAP, '688001', '北辰生物')
    monkeypatch.setitem(COMPANY_CODE_MAP, '北辰生物', '688001')
    dialogue = {'version': 2, 'pending_question': {
        'turn_id': 'saved-turn', 'question': '你是说北辰生物吗',
        'options': [{'id': '1', 'label': '北辰生物'}],
        'original_question': '北辰生勿2021年收入有多少', 'typo_span': '北辰生勿',
    }}
    pending_history = [
        {'role': 'user', 'content': '北辰生勿2021年收入有多少'},
        {'role': 'assistant', 'content': '你是说北辰生物吗', 'metadata': {'dialogue_state': dialogue}},
    ]
    planner, calls = scripted(financial_task('不应调用', '北辰生物'))
    plan = planner.plan_turn('北辰生物', pending_history)
    assert plan.intent == 'facts' and plan.codes == ['688001']
    assert plan.metrics == ['total_operating_revenue'] and plan.pairs == [(2021, 'FY')]
    assert calls == []
    guessed = slots(companies=['688001'], metrics=['total_operating_revenue'],
                    time=dict(mode='explicit', years=[2021], reports=[], period=None, count=1))
    planner, calls = scripted(financial_task('查询北辰生物2021年收入', '北辰生物2021年收入'), guessed, guessed)
    typo = planner.plan_turn('北辰生勿2021年收入有多少', [])
    assert typo.intent == 'clarify' and not typo.codes
    assert typo.request_contract['company_confirmation']['typo_span'] == '北辰生勿'
    assert '北辰生物' in typo.options
    saved = history(codes=['688001'], metrics=['total_operating_revenue'], pairs=[[2021, 'FY']])
    planner, calls = scripted(financial_task('不应调用', '那净利润呢'))
    follow = planner.plan_turn('那净利润呢', saved)
    assert follow.codes == ['688001'] and follow.metrics == ['net_profit'] and follow.pairs == [(2021, 'FY')]
    assert calls == []
    planner, calls = scripted(financial_task('不应调用', '再看看2020年'))
    year = planner.plan_turn('再看看2020年', saved)
    assert year.codes == ['688001'] and year.metrics == ['total_operating_revenue'] and year.pairs == [(2020, 'FY')]
    assert calls == []


def test_comparison_followup_keeps_both_years_and_the_difference():
    planner, calls = scripted(financial_task('不应调用', '那和23年比呢'))
    plan = planner.plan_turn('那和23年比呢', history(codes=['600222'], metrics=['total_operating_revenue'], pairs=[[2024, 'FY']]))
    assert plan.codes == ['600222'] and plan.metrics == ['total_operating_revenue']
    assert plan.pairs == [(2023, 'FY'), (2024, 'FY')]
    assert plan.calculation == 'difference' and plan.comparison == 'time' and calls == []


def test_last_year_followup_is_anchored_to_the_saved_report_year():
    planner, calls = scripted(financial_task('不应调用', '跟去年比一下'))
    plan = planner.plan_turn('跟去年比一下', history(codes=['600222'], metrics=['total_operating_revenue'], pairs=[[2024, 'FY']]))
    assert plan.pairs == [(2023, 'FY'), (2024, 'FY')] and plan.calculation == 'difference' and calls == []
    planner, calls = scripted(financial_task('不应调用', '太龙药业去年营收多少'))
    fresh = planner.plan_turn('太龙药业去年营收多少', [])
    from datetime import datetime
    assert fresh.pairs == [(datetime.now().year - 1, 'FY')] and calls == []


def test_year_only_replace_is_not_a_comparison():
    planner, calls = scripted(financial_task('不应调用', '再看看2023年'))
    plan = planner.plan_turn('再看看2023年', history(codes=['600222'], metrics=['total_operating_revenue'], pairs=[[2024, 'FY']]))
    assert plan.pairs == [(2023, 'FY')] and plan.calculation == 'none' and calls == []


def test_company_only_followup_keeps_metric_and_year():
    planner, calls = scripted(financial_task('不应调用', '那盘龙药业呢'))
    plan = planner.plan_turn('那盘龙药业呢', history(codes=['600222'], metrics=['total_operating_revenue'], pairs=[[2024, 'FY']]))
    assert plan.codes == ['002864'] and plan.metrics == ['total_operating_revenue'] and plan.pairs == [(2024, 'FY')]
    assert calls == []


def test_two_slot_followup_still_uses_the_planner_model():
    inherited = slots(companies=['002864'], metrics=['net_profit'],
                      time=dict(mode='inherit', years=[], reports=[], period=None, count=1))
    planner, calls = scripted(financial_task('查询盘龙净利润', '盘龙药业的净利润呢', 'followup'), inherited, inherited)
    plan = planner.plan_turn('盘龙药业的净利润呢', history(codes=['600222'], metrics=['total_operating_revenue'], pairs=[[2024, 'FY']]))
    assert calls and plan.codes == ['002864'] and plan.metrics == ['net_profit']


def test_cash_flow_choice_continues_the_original_company_and_year():
    dialogue = {'version': 2, 'pending_question': {
        'turn_id': 'saved-turn', 'question': '现金流需要明确种类',
        'options': [{'id': '1', 'label': '经营活动现金流量净额'}],
        'original_question': '金花股份2023年现金流多少', 'span': '现金流', 'slot': 'metric',
    }}
    history = [
        {'role': 'user', 'content': '金花股份2023年现金流多少'},
        {'role': 'assistant', 'content': '现金流需要明确种类', 'metadata': {'dialogue_state': dialogue}},
    ]
    planner, calls = scripted(financial_task('不应调用', '经营活动现金流量净额'))
    plan = planner.plan_turn('经营活动现金流量净额', history)
    assert plan.codes == ['600080'] and plan.metrics == ['operating_cf_net_amount'] and plan.pairs == [(2023, 'FY')]
    assert calls == []


def test_missing_record_example_stays_on_the_same_company():
    from src.agent.reply_text import no_data_reply
    text = no_data_reply('太龙药业', 2025, '全年', '营业总收入')
    assert '太龙药业' in text and '金花股份' not in text and '有哪些年份的报告' in text


def test_recent_and_this_three_years_keep_a_span():
    planner, calls = scripted(financial_task('不应调用', '这三年利润'))
    plan = planner.plan_turn('这三年利润', history(codes=['600222'], metrics=['total_operating_revenue'], pairs=[[2024, 'FY']]))
    assert plan.codes == ['600222'] and plan.metrics == ['net_profit']
    assert plan.pairs == [(2022, 'FY'), (2023, 'FY'), (2024, 'FY')] and calls == []
    planner, calls = scripted(financial_task('不应调用', '近三年利润'))
    fresh = planner.plan_turn('太龙药业近三年利润', [])
    assert fresh.codes == ['600222'] and fresh.metrics == ['net_profit'] and fresh.latest_count == 3 and calls == []


def test_pronoun_yoy_comparison_and_trend_follow_the_saved_company():
    saved = history(codes=['600222'], metrics=['total_operating_revenue'], pairs=[[2024, 'FY']])
    planner, calls = scripted(financial_task('不应调用', '它的净利润'))
    plan = planner.plan_turn('它的净利润', saved)
    assert plan.codes == ['600222'] and plan.metrics == ['net_profit'] and plan.pairs == [(2024, 'FY')] and calls == []
    planner, calls = scripted(financial_task('不应调用', '同比呢'))
    plan = planner.plan_turn('同比呢', saved)
    assert plan.pairs == [(2023, 'FY'), (2024, 'FY')] and plan.calculation == 'yoy' and calls == []
    planner, calls = scripted(financial_task('不应调用', '和2023年比怎么样'))
    plan = planner.plan_turn('和2023年比怎么样', saved)
    assert plan.pairs == [(2023, 'FY'), (2024, 'FY')] and plan.calculation == 'difference' and calls == []
    planner, calls = scripted(financial_task('不应调用', '利润趋势'))
    plan = planner.plan_turn('利润趋势', saved)
    assert plan.chart and plan.metrics == ['net_profit'] and plan.pairs == [(2022, 'FY'), (2023, 'FY'), (2024, 'FY')] and calls == []
    planner, calls = scripted(financial_task('不应调用', '为什么利润下降'))
    plan = planner.plan_turn('为什么利润下降', saved)
    assert plan.intent == 'explanation' and plan.metrics == ['net_profit']
    assert plan.pairs == [(2023, 'FY'), (2024, 'FY')] and calls == []


def test_fiscal_year_word_is_a_year():
    from src.agent.facts import extract_report_years
    assert extract_report_years('22财年净利润') == [2022]


def test_chart_of_a_year_list_uses_every_year_and_the_saved_company():
    planner, calls = scripted(financial_task('不应调用', '把22，23，24和三年的利润画个图'))
    plan = planner.plan_turn('把22，23，24和三年的利润画个图', history(codes=['600222'], metrics=['total_operating_revenue'], pairs=[[2024, 'FY']]))
    assert plan.codes == ['600222'] and plan.metrics == ['net_profit'] and plan.chart
    assert plan.pairs == [(2022, 'FY'), (2023, 'FY'), (2024, 'FY')] and calls == []


def test_amount_without_the_word_duoshao_is_still_a_lookup():
    planner, calls = scripted(financial_task('不应调用', '太龙药业2024年营收'))
    plan = planner.plan_turn('太龙药业2024年营收', [])
    assert plan.codes == ['600222'] and plan.metrics == ['total_operating_revenue'] and plan.pairs == [(2024, 'FY')]
    assert calls == []


def test_metric_followup_skips_the_planner_model():
    planner, calls = scripted(financial_task('不应调用', '那净利润呢'))
    plan = planner.plan_turn('那净利润呢', history(codes=['600222'], metrics=['total_operating_revenue'], pairs=[[2024, 'FY']]))
    assert plan.codes == ['600222'] and plan.metrics == ['net_profit'] and plan.pairs == [(2024, 'FY')]
    assert calls == []


def test_identity_and_single_amount_skip_the_planner_model():
    planner, calls = scripted(financial_task('不应调用', '你是谁'))
    plan = planner.plan_turn('你是谁', [])
    assert plan.intent == 'help' and plan.response_kind == 'conversation' and calls == []
    planner, calls = scripted(financial_task('不应调用', '太龙药业2024年营收多少'))
    plan = planner.plan_turn('太龙药业2024年营收多少', [])
    assert plan.codes == ['600222'] and plan.metrics == ['total_operating_revenue'] and plan.pairs == [(2024, 'FY')]
    assert calls == [] and plan.request_contract['plan_validation']['mode'] == 'short_path'
    planner, calls = scripted(financial_task('不应调用', '金花股份2024年营收多少'))
    plan = planner.plan_turn('金花股份2024年营收多少', [])
    assert plan.codes == ['600080'] and plan.metrics == ['total_operating_revenue'] and calls == []


def test_stock_prediction_is_refused_without_the_missing_record_template():
    from src.agent.reply_text import unsupported_reply
    planner, calls = scripted(financial_task('预测股价', '预测金花明天股价会涨吗'))
    plan = planner.plan_turn('帮我预测金花明天股价会涨吗', [])
    text = unsupported_reply(plan.reason, plan.question)
    assert plan.intent == 'unsupported' and plan.reason == 'investment_advice' and calls == []
    assert '投资建议' in text and '不能编造财务数字' not in text
    assert link_entities('金花股份2024年投资现金流多少').non_query is None


def test_table_redisplay_stays_a_financial_followup():
    inherited = slots(company_scope='inherit', metric_scope='inherit',
                      time=dict(mode='inherit', years=[], reports=[], period=None, count=1))
    planner, _ = scripted({'tasks': [{'kind': 'conversation', 'topic': 'help', 'goal': '列表',
                                      'standalone_question': '把结果列成表', 'relation': 'new'}]}, inherited)
    plan = planner.plan_turn('把结果列成表', history(codes=['002082'], metrics=['total_operating_revenue'],
                                                  pairs=[[2022, 'FY'], [2023, 'FY'], [2024, 'FY']]))
    assert plan.intent == 'facts' and plan.codes == ['002082'] and plan.metrics == ['total_operating_revenue']
    assert plan.pairs == [(2022, 'FY'), (2023, 'FY'), (2024, 'FY')]


def test_followup_keeps_the_picture_on_screen():
    picture = history(codes=['600222'], metrics=['gross_profit_margin'],
                      pairs=[[2022, 'FY'], [2023, 'FY'], [2024, 'FY']])
    planner, calls = scripted(financial_task('不应调用', '和盘龙药业比'))
    plan = planner.plan_turn('和盘龙药业比', picture)
    assert plan.codes == ['600222', '002864'] and plan.metrics == ['gross_profit_margin']
    assert plan.pairs == [(2022, 'FY'), (2023, 'FY'), (2024, 'FY')] and plan.compare_companies and calls == []

    both = history(codes=['600222', '002864'], metrics=['gross_profit_margin'],
                   pairs=[[2022, 'FY'], [2023, 'FY'], [2024, 'FY']])
    planner, calls = scripted(financial_task('不应调用', '前三年呢'))
    plan = planner.plan_turn('前三年呢', both)
    assert plan.codes == ['600222', '002864'] and plan.metrics == ['gross_profit_margin']
    assert plan.pairs == [(2019, 'FY'), (2020, 'FY'), (2021, 'FY')] and calls == []

    planner, calls = scripted(financial_task('不应调用', '换成盘龙药业'))
    plan = planner.plan_turn('换成盘龙药业', picture)
    assert plan.codes == ['002864'] and plan.metrics == ['gross_profit_margin']
    assert plan.pairs == [(2022, 'FY'), (2023, 'FY'), (2024, 'FY')] and calls == []

    planner, calls = scripted(financial_task('不应调用', '看盘龙药业'))
    plan = planner.plan_turn('看盘龙药业', picture)
    assert plan.codes == ['002864'] and calls == []

    planner, calls = scripted(financial_task('不应调用', '前三年呢'))
    plan = planner.plan_turn('前三年呢', picture)
    assert plan.codes == ['600222'] and plan.metrics == ['gross_profit_margin']
    assert plan.pairs == [(2019, 'FY'), (2020, 'FY'), (2021, 'FY')] and plan.period == 'FY' and calls == []

    quarter_window = history(codes=['600222'], metrics=['gross_profit_margin'],
                             pairs=[[2022, 'Q1'], [2023, 'Q1'], [2024, 'Q1']], outcome='no_data')
    planner, calls = scripted(financial_task('不应调用', '前三年呢'))
    plan = planner.plan_turn('前三年呢', quarter_window)
    assert plan.pairs == [(2019, 'FY'), (2020, 'FY'), (2021, 'FY')] and calls == []

    one_year = history(codes=['600222'], metrics=['gross_profit_margin'], pairs=[[2024, 'FY']])
    planner, calls = scripted(financial_task('不应调用', '前三年呢'))
    plan = planner.plan_turn('前三年呢', one_year)
    assert plan.pairs == [(2021, 'FY'), (2022, 'FY'), (2023, 'FY')] and calls == []

    earlier = history(codes=['600222'], metrics=['gross_profit_margin'],
                      pairs=[[2019, 'FY'], [2020, 'FY'], [2021, 'FY']])
    planner, calls = scripted(financial_task('不应调用', '这三年呢'))
    plan = planner.plan_turn('这三年呢', earlier)
    assert plan.pairs == [(2019, 'FY'), (2020, 'FY'), (2021, 'FY')] and calls == []

    planner, calls = scripted(financial_task('不应调用', '同比呢'))
    plan = planner.plan_turn('同比呢', picture)
    assert plan.metrics == ['gross_profit_margin'] and plan.calculation == 'yoy'
    assert plan.pairs == [(2022, 'FY'), (2023, 'FY'), (2024, 'FY')] and calls == []

    mixed = history(codes=['600222'], metrics=['net_profit', 'gross_profit_margin'],
                    pairs=[[2022, 'FY'], [2023, 'FY'], [2024, 'FY']])
    planner, calls = scripted(financial_task('不应调用', '为什么下降'))
    plan = planner.plan_turn('为什么下降', mixed)
    assert plan.intent == 'explanation' and plan.metrics == ['gross_profit_margin']
    assert plan.pairs == [(2022, 'FY'), (2023, 'FY'), (2024, 'FY')] and calls == []

    quarter = history(codes=['600222'], metrics=['net_profit'], pairs=[[2022, 'Q1']], outcome='no_data')
    planner, calls = scripted(financial_task('不应调用', '把利润画成趋势'))
    plan = planner.plan_turn('把利润画成趋势', quarter)
    assert plan.chart and plan.metrics == ['net_profit'] and plan.period == 'FY'
    assert plan.pairs == [(2020, 'FY'), (2021, 'FY'), (2022, 'FY')] and calls == []

    planner, calls = scripted(financial_task('不应调用', '把利润画成趋势'))
    plan = planner.plan_turn('把利润画成趋势', picture)
    assert plan.chart and plan.period == 'FY'
    assert plan.pairs == [(2022, 'FY'), (2023, 'FY'), (2024, 'FY')] and calls == []


def test_refusal_example_uses_the_company_on_screen(monkeypatch):
    monkeypatch.setitem(CODE_TO_NAME_MAP, '002390', '信邦制药')
    from src.agent.financial_query import FinancialQueryService
    from src.agent.query_plan import QueryPlan
    from src.agent.reply_text import unsupported_reply
    bare = unsupported_reply('single_quarter_not_supported', '单季净利润')
    assert '以已入库公司为例' in bare
    shown = unsupported_reply('single_quarter_not_supported', '单季净利润', '信邦制药')
    assert '例如：信邦制药2024年前三季度累计营收多少。' in shown
    assert '金花股份' not in shown and '以已入库公司为例' not in shown
    plan = QueryPlan(question='单季净利润', intent='unsupported', reason='single_quarter_not_supported',
                     response_kind='conversation')
    saved = history(codes=['002390'], metrics=['gross_profit_margin'],
                    pairs=[[2022, 'FY'], [2023, 'FY'], [2024, 'FY']])
    done = [item for item in FinancialQueryService(None, None).execute_plan(plan, saved) if item[0] == 'done']
    text = done[0][1]['result']['answer']['content']
    assert '例如：信邦制药2024年前三季度累计营收多少。' in text and '金花股份' not in text
    assert '信邦制药2024年营收多少。' in unsupported_reply('investment_advice', '该买吗', '信邦制药')
    unknown = unsupported_reply('unknown_company', '苹果公司营收', '信邦制药')
    assert '信邦制药2024年营收多少。' in unknown and '苹果' not in unknown


def test_followup_actions_use_the_whole_window():
    picture = history(codes=['600222'], metrics=['gross_profit_margin'],
                      pairs=[[2022, 'FY'], [2023, 'FY'], [2024, 'FY']])
    planner, calls = scripted(financial_task('不应调用', '去年呢'))
    plan = planner.plan_turn('去年呢', picture)
    assert plan.codes == ['600222'] and plan.metrics == ['gross_profit_margin']
    assert plan.pairs == [(2023, 'FY')] and calls == []

    planner, calls = scripted(financial_task('不应调用', '跟去年比一下'))
    plan = planner.plan_turn('跟去年比一下', picture)
    assert plan.pairs == [(2023, 'FY'), (2024, 'FY')] and plan.calculation == 'difference' and calls == []
    assert 2022 not in [year for year, _ in plan.pairs]

    planner, calls = scripted(financial_task('不应调用', '和2023年比'))
    plan = planner.plan_turn('和2023年比', picture)
    assert plan.pairs == [(2023, 'FY'), (2024, 'FY')] and plan.calculation == 'difference' and calls == []

    planner, calls = scripted(financial_task('不应调用', '前两年呢'))
    plan = planner.plan_turn('前两年呢', picture)
    assert plan.pairs == [(2020, 'FY'), (2021, 'FY')] and calls == []

    planner, calls = scripted(financial_task('不应调用', '近两年呢'))
    plan = planner.plan_turn('近两年呢', picture)
    assert plan.pairs == [(2023, 'FY'), (2024, 'FY')] and calls == []

    planner, calls = scripted(financial_task('不应调用', '这两年'))
    plan = planner.plan_turn('这两年', picture)
    assert plan.pairs == [(2023, 'FY'), (2024, 'FY')] and calls == []

    planner, calls = scripted(financial_task('不应调用', '现金流呢'))
    plan = planner.plan_turn('现金流呢', picture)
    assert plan.intent == 'clarify' and plan.reason == 'cash_flow_kind_required' and calls == []
    assert plan.pairs == [(2022, 'FY'), (2023, 'FY'), (2024, 'FY')]
    assert '2022到2024年' in plan.clarification

    empty_quarter = history(codes=['002082'], metrics=['net_profit'], pairs=[[2022, 'Q1']], outcome='no_data')
    planner, calls = scripted(financial_task('不应调用', '它的营收呢'))
    plan = planner.plan_turn('它的营收呢', empty_quarter)
    assert plan.codes == ['002082'] and plan.metrics == ['total_operating_revenue']
    assert plan.pairs == [(2022, 'FY')] and calls == []
    planner, calls = scripted(financial_task('不应调用', '和太龙比'))
    plan = planner.plan_turn('和太龙比', empty_quarter)
    assert plan.codes == ['002082', '600222'] and plan.pairs == [(2022, 'FY')] and calls == []

    half_year = history(codes=['600222'], metrics=['gross_profit_margin'],
                        pairs=[[2022, 'HY'], [2023, 'HY'], [2024, 'HY']], outcome='answered')
    planner, calls = scripted(financial_task('不应调用', '同比呢'))
    plan = planner.plan_turn('同比呢', half_year)
    assert plan.calculation == 'yoy' and plan.period == 'HY' and calls == []
    assert plan.pairs == [(2022, 'HY'), (2023, 'HY'), (2024, 'HY')]
    planner, calls = scripted(financial_task('不应调用', '那净利润呢'))
    plan = planner.plan_turn('那净利润呢', half_year)
    assert plan.metrics == ['net_profit'] and plan.period == 'HY'
    assert plan.pairs == [(2022, 'HY'), (2023, 'HY'), (2024, 'HY')] and calls == []
