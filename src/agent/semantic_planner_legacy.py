"""作品说明：历史类型化对话状态与 LangGraph 校验实现。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, TypedDict
from urllib.parse import urlparse

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from .domain import CODE_TO_NAME_MAP, COMPANY_CODE_MAP
from .facts import MAIN_FINANCIAL_METRICS
from .chart_policy import CAPABILITIES
from .query_plan import QueryPlan, known_metrics, normalize_model_text, selected_years, explicit_codes


class ReportIdentity(BaseModel):
    model_config = ConfigDict(extra='forbid')
    year: int = Field(ge=2000, le=2100)
    period: Literal['FY', 'Q1', 'HY', 'Q3'] = 'FY'


class TurnDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent: Literal['facts', 'coverage_companies', 'coverage_periods', 'coverage_metrics',
                    'conversation_scope', 'explanation', 'help', 'greeting', 'unsupported']
    followup: bool = False
    correction: bool = False
    companies: list[str] = Field(default_factory=list, max_length=20)
    company_text: str = ''
    metrics: list[str] = Field(default_factory=list, max_length=20)
    metric_text: str = ''
    main_metrics: bool = False
    time_mode: Literal['explicit', 'latest', 'calendar', 'missing'] = 'missing'
    time_text: str = ''
    reports: list[ReportIdentity] = Field(default_factory=list, max_length=20)
    count: int = Field(default=3, ge=1, le=10)
    period: Literal['FY', 'Q1', 'HY', 'Q3', 'single_quarter'] | None = None
    all_companies: bool = False
    calculation: Literal['none', 'difference', 'yoy', 'percentage_points', 'ranking'] = 'none'
    compare_companies: bool = False
    chart: bool = False
    unit: Literal['', '元', '万元', '亿元'] = ''
    needs_evidence: bool = False
    inherit_fields: list[Literal['companies', 'metrics', 'time', 'period', 'unit', 'calculation']] = Field(default_factory=list)
    clarification: str = ''
    options: list[str] = Field(default_factory=list, max_length=5)
    reply: str = ''


class RequestState(BaseModel):
    model_config = ConfigDict(extra='ignore')
    codes: list[str] = Field(default_factory=list, max_length=20)
    metrics: list[str] = Field(default_factory=list, max_length=50)
    pairs: list[tuple[int, Literal['FY', 'Q1', 'HY', 'Q3']]] = Field(default_factory=list, max_length=30)
    period: Literal['FY', 'Q1', 'HY', 'Q3'] = 'FY'
    time_mode: str = 'explicit'
    latest_count: int = Field(default=0, ge=0, le=10)
    calculation: str = 'none'
    unit: Literal['', '元', '万元', '亿元'] = ''
    intent: Literal['facts', 'explanation', 'coverage_periods', 'clarify'] = 'facts'


def decision_schema():
    """作品说明：单一语义工具契约避免重复声明原话条件。"""
    schema=TurnDecision.model_json_schema()
    for key in ('company_text','metric_text','time_text'):
        schema['properties'].pop(key)
    return schema


def conversation_state(history: list[dict]) -> dict:
    """作品说明：版本化持久状态保存当前财务查询范围。"""
    for message in reversed(history):
        if message.get('role') != 'assistant':
            continue
        metadata = message.get('metadata') or {}
        state = metadata.get('dialogue_state') if isinstance(metadata, dict) else None
        if isinstance(state, dict) and state.get('version') == 1:
            try:
                cleaned = {k: state[k] for k in ('version', 'last_intent', 'outcome', 'chart_eligibility') if k in state}
                for key in ('active_request', 'pending_request'):
                    if state.get(key):
                        request = RequestState.model_validate(state[key])
                        if any(c not in CODE_TO_NAME_MAP for c in request.codes): raise ValueError('Unknown company')
                        if any(m not in known_metrics() for m in request.metrics): raise ValueError('Unknown metric')
                        if any(not 2000 <= y <= 2100 for y, _ in request.pairs): raise ValueError('Invalid year')
                        cleaned[key] = request.model_dump()
                return cleaned
            except (ValueError, TypeError):
                return {}
    return {}


def _messages(question: str, history: list[dict]) -> list[dict]:
    metrics = {key: spec['label'] for key, spec in known_metrics().items()}
    companies = dict(CODE_TO_NAME_MAP)
    state = conversation_state(history)
    turns = []
    for item in history[-12:]:
        if item.get('role') == 'user':
            turns.append({'role': 'user', 'content': str(item.get('content', ''))[:1000]})
        elif (item.get('metadata') or {}).get('response_kind') == 'conversation':
            turns.append({'role': 'assistant', 'content': str(item.get('content', ''))[:700]})
    prompt = f'''你是财报助手的对话理解器。今天是{datetime.now():%Y-%m-%d}。你的任务是调用TurnDecision工具提交本轮语义决定。这个工具仅标注意图，不是查数据库或画图。每一轮包括help和greeting都必须调用TurnDecision，不能直接输出普通文字。只做意图与参数标注，不查询、不计算、不编造答案。简短检查一次后提交，不反复推演。只填写本轮适用的条件，不适用的字段可以省略。
用户可能问数字、要求画图、询问系统用法、解释财务概念、质疑或纠正。出现某个词不代表要求执行该动作。
intent: facts查具体数字；coverage_companies公司目录；coverage_periods报告年份/报告期目录；coverage_metrics指标目录；conversation_scope问上轮实际查了什么；explanation解释企业业绩且需要原文；help系统用法/图表条件/金融概念/质疑行为；greeting问候；unsupported无关任务或要求编造/越权。
help/greeting必须在reply中直接、简短回答用户；不要要求公司年份，不执行财务查询。help可以解释上一轮chart_eligibility和outcome，但不能臆测数据库覆盖或生成具体财务数字。纠正行为不等于数据追问。
correction单独表示纠正：如果同时有新查数/改图要求，仍用facts；如果只是质疑行为，用help并承认误解。例如“不是让你画图，我问画图条件”是help；“改成2023年半年报画图”是facts。
companies/metrics/reports只填写本轮明确提出的条件，不抄历史；公司用下面登记代码，指标用下面字段。拿不准字段或公司就clarification并给候选，不能自行选相似公司。
followup和inherit_fields仅当本轮确实追问前一数据请求时设置。可继承来源为pending_request（正在补充条件的请求）或active_request，不是历史回答。独立新问题不继承旧年份指标；换公司式追问继承仍适用条件。“那净利润呢”继承公司时间，“再看去年”继承公司指标报告期。问过概念后“这个指标”可从概念问答明确解析字段。若不知道所指字段则澄清。
最近/最新/近N年默认time_mode=latest、count=N，取库中最近N份相同报告期；不得自行填写年份。明确过去N个自然年用calendar，由程序按今天计算。明确年份及去年/前年等用explicit+reports。没给年份也不是latest追问用missing。只给年份默认FY。Q1一季度，HY上半年，Q3前三季度累计；明确单季度/Q2/Q4用single_quarter。
主要/核心指标用main_metrics=true。现金流泛称不等于经营现金流，需澄清。calculation仅表示用户要求的计算；已存的同比率字段不用再次同比。单位换算不改指标。查原文用needs_evidence=true；企业原因用explanation。概念“为什么需要现金流”用help。
chart表示本轮真的要求画图/可视化，不继承上轮画图指令。询问“能画什么图”“为什么没画”“别画了”不算。多值可以表格呈现。compare_companies表示询问多公司高低关系。
金额和图表只能取工具事实，禁止编造。缺记录只能由查询证明，不能在clarification/reply里预判。
系统实际图表能力：{json.dumps(CAPABILITIES,ensure_ascii=False)}。使用说明以这些能力和本轮状态为准；不能把上次恰好有几个数据点当作最低数量要求。
公司登记：{json.dumps(companies, ensure_ascii=False)}
指标登记：{json.dumps(metrics, ensure_ascii=False)}
JSON Schema（明确公司填companies，明确指标填metrics或main_metrics；明确年份填time_mode=explicit及reports，最近几份报告用latest和count；未在本轮出现的公司/指标/时间通过inherit_fields明确继承）：{json.dumps(decision_schema(),ensure_ascii=False,separators=(',',':'))}'''
    return [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps(
        {'recent_dialogue': turns, 'state': state, 'current_question': question}, ensure_ascii=False)}]


def _clarify(plan: QueryPlan, reason: str, text: str) -> QueryPlan:
    plan.intent = 'clarify'
    plan.reason = reason
    plan.clarification = text
    return plan


def validate_decision(question: str, decision: TurnDecision, history: list[dict]) -> QueryPlan:
    d = decision
    plan = QueryPlan(question=question, intent=d.intent, chart=d.chart, unit=d.unit,
                     calculation=d.calculation, needs_evidence=d.needs_evidence or d.intent == 'explanation')
    plan.correction = d.correction
    plan.compare_companies = d.compare_companies
    plan.direct_reply = normalize_model_text(d.reply)
    plan.response_kind = 'conversation' if d.intent in {'help', 'greeting'} else 'financial'
    state = conversation_state(history)
    previous = state.get('pending_request') or state.get('active_request') or {}
    if d.intent in {'help', 'greeting', 'unsupported'}:
        plan.chart = False
        plan.needs_evidence = False
        if d.intent == 'unsupported':
            plan.reason = 'outside_supported_task'
        return plan
    if d.intent == 'conversation_scope':
        if not previous:
            return _clarify(plan, 'previous_scope_unavailable', '这段旧对话没有保存可核对的查询条件，请说明要查看哪一轮。')
        plan.codes = previous.get('codes', [])
        plan.metrics = previous.get('metrics', [])
        plan.pairs = [tuple(pair) for pair in previous.get('pairs', [])]
        plan.period = previous.get('period', 'FY')
        return plan
    for slot, text in [('company', d.company_text), ('metric', d.metric_text), ('time', d.time_text)]:
        if text and text not in question:
            return _clarify(plan, 'unanchored_condition', '本轮条件与问题原话不一致，请重新明确公司、指标及报告期。')
    inherited = set(d.inherit_fields) if d.followup else set()
    if inherited and not previous:
        return _clarify(plan, 'previous_scope_unavailable', '这段对话还没有可继承的可靠查询条件，请说明公司、年份和指标。')
    plan.codes = list(dict.fromkeys(COMPANY_CODE_MAP.get(c, c) for c in d.companies))
    if plan.codes and any(c not in CODE_TO_NAME_MAP for c in plan.codes):
        return _clarify(plan, 'unknown_company', '请确认要查询的公司名称或股票代码。')
    if plan.codes and set(plan.codes) != set(explicit_codes(d.company_text or question)):
        return _clarify(plan, 'company_identity_unconfirmed', '公司名称与登记信息尚未对应，请确认公司全名或股票代码。')
    if not plan.codes and 'companies' in inherited:
        plan.codes = list(previous.get('codes', []))
    plan.metrics = list(MAIN_FINANCIAL_METRICS) if d.main_metrics else list(dict.fromkeys(d.metrics))
    if any(m not in known_metrics() for m in plan.metrics):
        return _clarify(plan, 'unknown_metric', '这个指标尚未对应到明确的财务口径，请说明具体指标。')
    if not plan.metrics and 'metrics' in inherited:
        plan.metrics = list(previous.get('metrics', []))
    if d.period == 'single_quarter':
        plan.intent = 'unsupported'; plan.reason = 'single_quarter_not_supported'
        return plan
    plan.period = d.period or (previous.get('period', 'FY') if 'period' in inherited else 'FY')
    if d.time_mode == 'latest':
        plan.time_mode = 'latest'; plan.latest_count = d.count
    elif d.time_mode == 'calendar':
        plan.time_mode = 'calendar'
        plan.pairs = [(y, plan.period) for y in range(datetime.now().year-d.count, datetime.now().year)]
    elif d.time_mode == 'explicit':
        plan.pairs = [(r.year, d.period or (plan.period if 'period' in inherited else r.period)) for r in d.reports]
        explicit = selected_years(d.time_text or question)
        if explicit and {y for y, _ in plan.pairs} != set(explicit):
            return _clarify(plan, 'year_mismatch', '解析到的年份与本轮提问不一致，请确认年份。')
    elif 'time' in inherited:
        plan.pairs = [(int(y), d.period or p) for y, p in previous.get('pairs', [])]
        plan.time_mode = previous.get('time_mode', 'explicit')
        plan.latest_count = int(previous.get('latest_count', 0)) if plan.time_mode == 'latest' else 0
        if plan.latest_count: plan.pairs = []
    if not plan.unit and 'unit' in inherited: plan.unit = previous.get('unit', '')
    if d.calculation == 'none' and 'calculation' in inherited: plan.calculation = previous.get('calculation', 'none')
    if plan.calculation == 'yoy' and len(plan.pairs) == 1:
        year, period = plan.pairs[0]; plan.pairs = [(year-1, period), (year, period)]
    if plan.calculation == 'yoy' and any(m.endswith(('_yoy_growth', '_qoq_growth')) for m in plan.metrics):
        plan.calculation = 'none'
    plan.all_companies = d.all_companies
    plan.origins = {'intent': 'model', 'companies': 'current' if d.companies else 'history',
                    'metrics': 'current' if d.metrics or d.main_metrics else 'history',
                    'time': 'history' if d.time_mode == 'missing' else 'current'}
    if d.clarification:
        plan.options = [normalize_model_text(x) for x in d.options]
        plan.clarification = normalize_model_text(d.clarification)
        plan.reason = 'ambiguous_request'
        if not (plan.codes and plan.metrics and (plan.pairs or plan.latest_count)):
            return _clarify(plan, 'ambiguous_request', plan.clarification)
    if d.intent in {'coverage_companies', 'coverage_metrics'}:
        plan.codes = []; plan.metrics = []; plan.pairs = []; plan.latest_count = 0
        return plan
    if not plan.codes and not plan.all_companies:
        return _clarify(plan, 'company_required', '你想查看哪家公司的财报？可以提供公司名或股票代码。')
    if d.intent == 'coverage_periods':
        plan.coverage_period_filter = d.period
        if plan.pairs and d.period is None:
            plan.pairs = [(y,p) for y in sorted({y for y,_ in plan.pairs}) for p in ('FY','Q1','HY','Q3')]
        return plan
    if not plan.metrics and d.intent != 'explanation' and not plan.needs_evidence:
        return _clarify(plan, 'metric_required', '你想看哪些指标，例如营业收入、净利润或经营活动现金流量净额？')
    if not plan.pairs and not plan.latest_count:
        return _clarify(plan, 'time_required', '你想看哪一年或哪些报告期？也可以说最近三份年报。')
    return plan


class PlanningState(TypedDict, total=False):
    question: str
    history: list[dict]
    decision: TurnDecision
    plan: QueryPlan


class SemanticPlanner:
    def __init__(self, llm):
        from langchain_ollama import ChatOllama
        config = llm.config
        parsed = urlparse(llm.api_url)
        if not config.is_local or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('Conversational agent requires the configured local model')
        self.model = ChatOllama(model=llm.model, base_url=f'{parsed.scheme}://{parsed.netloc}',
                                reasoning=True, temperature=0, num_ctx=config.num_ctx,
                                num_predict=4096, client_kwargs={'timeout': llm.timeout})
        self.structured = self.model.with_structured_output(decision_schema(), method='function_calling', include_raw=True)
        graph = StateGraph(PlanningState)
        graph.add_node('understand', self._understand)
        graph.add_node('validate', self._validate)
        graph.add_edge(START, 'understand')
        graph.add_edge('understand', 'validate')
        graph.add_edge('validate', END)
        self.graph = graph.compile()

    def _understand(self, state):
        messages=_messages(state['question'], state['history'])
        for attempt in range(2):
            response = self.structured.invoke(messages)
            try:
                raw=response.get('parsed')
                if response.get('parsing_error') or not isinstance(raw,dict):
                    raise ValueError('请调用TurnDecision工具返回结构化结果，不要仅输出普通文本。')
                missing=set(decision_schema()['required'])-set(raw)
                if missing: raise ValueError('缺少字段：'+','.join(sorted(missing)))
                return {'decision':TurnDecision.model_validate(raw)}
            except ValueError as exc:
                if attempt: raise ValueError('Invalid model decision after one correction') from exc
                messages.append({'role':'user','content':'上次输出未满足结构约定。请调用TurnDecision提交本轮意图，不要调用其他工具或直接回答。'+str(exc)[:400]})

    @staticmethod
    def _validate(state):
        return {'plan': validate_decision(state['question'], state['decision'], state['history'])}

    def plan_turn(self, question, history):
        return self.graph.invoke({'question': question, 'history': list(history)})['plan']
