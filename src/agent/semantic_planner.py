"""作品说明：类型化请求统一处理来源、对话状态与语义校验。"""
from __future__ import annotations
import json
import time as clock_time
import hashlib
from datetime import datetime
from typing import Annotated, Literal, Union
from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, field_validator
from .turn_runtime import AgentFailure, record_failure, runtime
from .chart_policy import CAPABILITIES
from .domain import CODE_TO_NAME_MAP, COMPANY_CODE_MAP
from .entity_linker import exact_company_codes, link_entities
from .reply_text import (
    asks_for_reading, clarify_cash, clarify_company, clarify_metric, clarify_near_miss, clarify_pronoun,
    clarify_request, clarify_time, is_greeting, is_identity_question,
)
from .facts import MAIN_FINANCIAL_METRICS, YEAR_SPAN_RE, extract_report_periods, extract_report_years
from .query_plan import QueryPlan, is_followup, known_metrics, normalize_model_text, selected_years
import re
# 作品说明：旧版样例不属于下方生产规划器调用路径。
from .semantic_planner_legacy import TurnDecision, validate_decision


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Origin(StrictModel):
    kind: Literal['current', 'history', 'default', 'catalog', 'selection']
    turn_id: str = ''
    text: str = ''


class Origins(StrictModel):
    companies: Origin | None = None
    metrics: Origin | None = None
    time: Origin | None = None
    unit: Origin | None = None

    def get(self, key):
        return getattr(self, key, None)

    def items(self):
        return ((k, getattr(self, k)) for k in type(self).model_fields if getattr(self, k) is not None)


class ReportPeriod(StrictModel):
    year: int = Field(ge=2000, le=2100)
    period: Literal['FY', 'Q1', 'HY', 'Q3']


class TimeRequest(StrictModel):
    mode: Literal['explicit', 'latest', 'calendar', 'inherit', 'default'] = 'default'
    years: list[int] = Field(default_factory=list, max_length=20)
    reports: list[ReportPeriod] = Field(default_factory=list, max_length=20)
    period: Literal['FY', 'Q1', 'HY', 'Q3', 'single_quarter'] | None = None
    count: int = Field(default=1, ge=1, le=10)

    @field_validator('reports', mode='before')
    @classmethod
    def migrate_report_pairs(cls, value):
        if isinstance(value, list):
            return [{'year':r[0], 'period':r[1]} if isinstance(r, (list, tuple)) and len(r)==2 else r for r in value]
        return value


class RequestBase(StrictModel):
    goal: str
    standalone_question: str
    relation: Literal['new', 'followup', 'correction', 'resume'] = 'new'
    origins: Origins = Field(default_factory=Origins)


class ConversationRequest(RequestBase):
    kind: Literal['conversation']
    topic: Literal['help', 'concept', 'complaint', 'greeting', 'out_of_scope', 'refuse_fabrication', 'execution_scope']


class CatalogRequest(RequestBase):
    kind: Literal['catalog']
    dimension: Literal['companies', 'reports', 'metrics']
    companies: list[str] = Field(default_factory=list, max_length=20)
    time: TimeRequest | None = None


class FinancialRequest(RequestBase):
    kind: Literal['financial']
    companies: list[str] = Field(default_factory=list, max_length=20)
    company_scope: Literal['specified', 'inherit', 'catalog', 'all'] = 'specified'
    metrics: list[str] = Field(default_factory=list, max_length=20)
    metric_scope: Literal['specified', 'inherit', 'main', 'overview'] = 'specified'
    time: TimeRequest = Field(default_factory=TimeRequest)
    unit: Literal['', '元', '万元', '亿元'] = ''
    inherit_unit: bool = False
    calculation: Literal['none', 'difference', 'yoy', 'percentage_points', 'ranking'] = 'none'
    comparison: Literal['none', 'time', 'companies', 'metrics'] = 'none'
    presentation: Literal['auto', 'table', 'line', 'bar', 'chart'] = 'auto'
    evidence: Literal['none', 'source', 'cause'] = 'none'


class ClarificationRequest(RequestBase):
    kind: Literal['clarification']
    question: str
    options: list[str] = Field(default_factory=list, max_length=5)
    missing: list[Literal['company','metric','time','period','request']] = Field(default_factory=lambda:['request'], max_length=5)
    partial: FinancialRequest | None = None


Request = Annotated[Union[ConversationRequest, CatalogRequest, FinancialRequest, ClarificationRequest], Field(discriminator='kind')]


class IntentBase(StrictModel):
    goal: str
    standalone_question: str
    relation: Literal['new', 'followup', 'correction', 'resume']


class ConversationIntent(IntentBase):
    kind: Literal['conversation']
    topic: Literal['help', 'concept', 'complaint', 'greeting', 'out_of_scope', 'refuse_fabrication', 'execution_scope']


class CatalogIntent(IntentBase):
    kind: Literal['catalog']
    dimension: Literal['companies', 'reports', 'metrics']
    companies: list[str] = Field(max_length=20)
    time: TimeRequest | None


class FinancialIntent(IntentBase):
    kind: Literal['financial']


class UnclearIntent(IntentBase):
    kind: Literal['unclear']


TaskIntent = Annotated[Union[ConversationIntent, CatalogIntent, FinancialIntent, UnclearIntent], Field(discriminator='kind')]


class TurnIntent(StrictModel):
    tasks: list[TaskIntent] = Field(min_length=1, max_length=4)


class SlotTime(TimeRequest):
    mode: Literal['explicit', 'latest', 'calendar', 'inherit', 'default']
    years: list[int] = Field(max_length=20)
    reports: list[ReportPeriod] = Field(max_length=20)
    period: Literal['FY', 'Q1', 'HY', 'Q3', 'single_quarter'] | None
    count: int = Field(ge=1, le=10)


class FinancialSlots(StrictModel):
    """作品说明：全部条件字段须明确给出，不能以默认值掩盖遗漏。"""
    companies: list[str] = Field(max_length=20)
    company_scope: Literal['specified', 'inherit', 'catalog', 'all']
    metrics: list[str] = Field(max_length=20)
    metric_scope: Literal['specified', 'inherit', 'main', 'overview']
    time: SlotTime
    unit: Literal['', '元', '万元', '亿元']
    inherit_unit: bool
    calculation: Literal['none', 'difference', 'yoy', 'percentage_points', 'ranking']
    comparison: Literal['none', 'time', 'companies', 'metrics']
    presentation: Literal['auto', 'table', 'line', 'bar', 'chart']
    evidence: Literal['none', 'source', 'cause']
    clarify: list[Literal['company', 'metric', 'time', 'period']] = Field(max_length=4)
    options: list[str] = Field(max_length=5)
    origins: Origins = Field(default_factory=Origins)


def slot_schema(count):
    """作品说明：采样阶段限制可选登记标识。"""
    fields = {}
    if CODE_TO_NAME_MAP:
        fields['companies'] = (list[Literal[tuple(CODE_TO_NAME_MAP)]], Field(max_length=20))
    if known_metrics():
        fields['metrics'] = (list[Literal[tuple(known_metrics())]], Field(max_length=20))
    item = create_model('FinancialTaskSlotsItem', __base__=FinancialSlots, **fields)
    return create_model('FinancialTaskSlots', __base__=StrictModel,
                        tasks=(list[item], Field(min_length=count, max_length=count)))


class ResolutionError(ValueError):
    pass


def conversation_state(history: list[dict]) -> dict:
    """作品说明：持久化状态只沿用条件，不把旧文字回答作为事实。"""
    for item in reversed(history):
        meta = item.get('metadata') or {}
        raw = meta.get('dialogue_state') if item.get('role') == 'assistant' and isinstance(meta, dict) else None
        if not isinstance(raw, dict) or raw.get('version') not in {1, 2}:
            continue
        if raw['version'] == 1:
            raw = {**raw, 'version': 2, 'last_execution': None, 'legacy': True}
        state = dict(raw)
        for name in ('active_request', 'pending_request', 'last_catalog_request'):
            request = state.get(name)
            if request:
                try:
                    if not isinstance(request, dict): raise ValueError('Invalid request state')
                    if any(c not in CODE_TO_NAME_MAP for c in request.get('codes', [])): raise ValueError('Unknown company')
                    if any(m not in known_metrics() for m in request.get('metrics', [])): raise ValueError('Unknown metric')
                    pairs = [(int(y), p) for y, p in request.get('pairs', [])]
                    if any(not 2000 <= y <= 2100 or p not in {'FY','Q1','HY','Q3'} for y,p in pairs):
                        raise ValueError('Invalid report scope')
                    if request.get('period', 'FY') not in {'FY','Q1','HY','Q3'}: raise ValueError('Invalid period')
                    if not 0 <= int(request.get('latest_count', 0)) <= 10: raise ValueError('Invalid count')
                    state[name] = {**request, 'pairs': pairs}
                except (ValueError, TypeError):
                    return {}
        return state
    return {'version': 2}


def planner_context(question, history):
    turns = []
    for index, item in enumerate(history):
        if index < len(history) - 16:
            continue
        if item.get('role') == 'user':
            turns.append({'turn_id': f'user-{index}', 'role': 'user', 'content': str(item.get('content', ''))[:1200]})
        elif (item.get('metadata') or {}).get('response_kind') == 'conversation':
            turns.append({'turn_id': f'assistant-{index}', 'role': 'assistant', 'content': str(item.get('content', ''))[:1000]})
    return {'today': datetime.now().date().isoformat(), 'question': question,
            'recent_dialogue': turns, 'state': conversation_state(history)}


INTENT_POLICY = '''你负责判断财报助手本轮用户想做什么，按给定schema返回JSON，不回答问题，不输出工具调用。
按完整语义判断，不凭单个字词触发动作。多个明确目标拆成tasks（最多4项）；相同范围的取数、比较、计算、图表、原文和原因合成一项financial。
conversation：不调用财务工具。topic取值：help系统用法、功能、画图或查询条件；concept财务概念或指标含义；complaint质疑、抱怨系统行为；greeting问候；out_of_scope与财报无关的明确需求（如饮食、天气）；refuse_fabrication要求编造、猜测或估个数；execution_scope询问刚才实际查过什么范围。
catalog：查询目录。dimension=companies问库里有哪些公司（companies留空，time为null）；reports问某公司有哪些年份或报告期、最新年报是哪年；metrics问某公司能查哪些指标。reports和metrics需要公司时companies填股票代码，追问沿用上文公司时可留空。
catalog的time：没限定时为null；问半年报等某类报告有哪些年份用mode=default和period；问某年有哪些报告期用mode=explicit、years，period为null；最新年报是哪年用mode=latest、count=1。
financial：查数、比较、计算、排名、画图、找原文位置、问企业变化原因。具体公司、指标、年份在下一步填写，这里不用写。
unclear：无法确定用户要做什么，例如没有待选编号时的孤立数字、无意义输入。
每项写goal（一句话说明本项要做的事，保留用户原话中的公司、指标、年份词语）、standalone_question（结合上文补全后的本项完整问题）和relation（new新话题；followup基于上文追问；correction纠正上一轮；resume回到之前待澄清或被打断的请求）。
只处理最后一条用户消息；历史和state只是背景。上一轮被拒绝或被澄清，不是本轮意图的证据。
只有本轮表达了明确的范围外需求才判out_of_scope；信息不足、无法确定在问什么时用unclear。
孤立数字只有在state.pending_question有编号选项时才是选择：按所选内容判断类型，relation=resume。
回答上一轮澄清（例如补充指标、报告期）时是financial，relation=resume或followup。
用户说不用查、不要查了、只解释时不生成financial。概念解释加查数时拆成conversation和financial两项。
mentions是程序从本轮原话认出的登记公司、指标和年份，只作参考，不决定类型。'''


SLOT_POLICY = '''你负责为本轮已确定的financial任务填写查询条件，按给定schema返回JSON，不回答问题。tasks的数量和顺序与financial_tasks一致。
公司：companies填company_registry中的股票代码。本轮原话提到的公司必须填入并用company_scope=specified；mentions.companies是程序已核对的本轮公司，mentions.excluded_companies是用户说不要的公司。
本轮没说新公司、用“它/这家/这两家/其他不变”沿用上文时company_scope=inherit，companies留空。上一轮公司目录后的“这些公司”=catalog；全库排序或所有公司=all。不可凭空选公司。
指标：metrics只用metric_registry中的ID。用户说的指标别名见mentions.metrics（例如营收、收入对应total_operating_revenue），metric_scope=specified时metrics至少一项。
追问只改其他条件、沿用上文指标时metric_scope=inherit；“主要指标”=main；“最近怎么样/概况”=overview。同比选择基础指标并calculation=yoy，不拼造字段。
时间：明确年份用mode=explicit，填years，或者在年份的报告期不同时填reports（每项year和period）；mentions.years是本轮原话中的年份。
“最近/最新N份、近N年”用mode=latest、count=N，不填年份；“过去N个自然年”用mode=calendar、count=N；只改公司或指标、沿用上文时间用mode=inherit；独立问题没说时间用mode=default（查最近一份已入库年报，不要追问年份）。去年、前年按today解析；本轮和上文都没出现的年份不能用today推测。
报告期：Q1一季度，HY上半年累计，Q3前三季度累计，FY全年；只说年份时period=FY或null；只改年份、说“同一期”时period沿用上文报告期。明确第三季度单季、非累计、Q2、Q4、第四季度用single_quarter，不得用累计代替。
计算：两期比较comparison=time、calculation=difference；多家公司比较comparison=companies；两个指标是否相同comparison=metrics；排名calculation=ranking；百分点变化percentage_points。
展示：presentation只反映本轮明确要求画图（line趋势图、bar柱状对比图、chart不限），问画图条件、为何没画、说别画时为auto，不继承上一轮的画图命令。
证据：要原文位置或出处=source；问企业某项变化的原因=cause；其余none。
单位：用户指定亿元、万元、元时填unit；追问沿用上文单位时inherit_unit=true；否则unit为空字符串、inherit_unit=false。
clarify：只在用户原话确有实质歧义时填写，例如只说“现金流”没说经营、投资或筹资（填metric），公司简称对应多家或明显错字近音（填company）。原话已写明的公司、指标、年份不得再问；没说年份用default，不算缺失。有歧义时其他明确指标照常填入metrics，不得保留未消歧的猜测指标ID。无歧义时clarify和options为空数组。
origins可选：公司简称有错字需要说明时，在origins.companies.text给出原话中的实体片段，kind=current。
不得从历史助手文字取得财务数字或范围。correction_feedback是上次填写的问题，必须据此改正。'''


def _codes(values):
    codes = list(dict.fromkeys(COMPANY_CODE_MAP.get(v, v) for v in values))
    if any(c not in CODE_TO_NAME_MAP for c in codes):
        raise ResolutionError('公司未匹配登记；未知公司应澄清并给合法候选，不能编造代码。')
    return codes


def _company_candidates(anchor):
    """作品说明：精确登记名称可直接绑定，模糊名称保持未解析。"""
    return exact_company_codes(anchor)


def _origin_check(request, slot, values, previous, state, context):
    if not values:
        return
    # 作品说明：公司集合范围不能仅靠所有名称出现在原话中证明。
    if slot == 'companies' and getattr(request, 'company_scope', 'specified') in {'all', 'catalog'}:
        origin = request.origins.get(slot) or Origin(kind='catalog' if request.company_scope == 'catalog' else 'default')
        request.origins.companies = origin
        return
    origin = request.origins.get(slot)
    # 作品说明：模型提出条件后，由程序核对字面实体来源，不再由模型伪造来源元数据。
    mentioned = exact_company_codes(context['question'])
    if slot == 'companies' and set(values) <= mentioned:
        origin = Origin(kind='current', text=context['question'])
        request.origins.companies = origin
    elif (slot == 'companies' and not mentioned
          and request.relation in {'followup', 'correction', 'resume'}
          and previous.get('turn_id') and set(values) == set(previous.get('codes', []))):
        origin = Origin(kind='history', turn_id=previous['turn_id'])
        request.origins.companies = origin
    elif slot == 'metrics':
        origin = Origin(kind='current', text=context['question'])
        request.origins.metrics = origin
    if not origin:
        if slot == 'companies':
            if (not mentioned and request.relation in {'followup', 'correction', 'resume'}
                    and previous.get('turn_id') and set(values) == set(previous.get('codes', []))):
                origin = Origin(kind='history', turn_id=previous['turn_id'])
            else:
                raise ResolutionError('公司无法对应本轮实体或已验证的追问范围；请提供实体原文片段，歧义时澄清。')
        else:
            # 作品说明：指标选择属于语义理解，关键词不是完整证明；登记合法性另行核验。
            origin = Origin(kind='current', text=context['question'])
        setattr(request.origins, slot, origin)
    if origin.kind == 'current':
        if not origin.text or origin.text not in context['question']:
            raise ResolutionError(f'{slot}当前来源片段不在用户原话中。')
        if slot=='companies' and not set(values)<=_company_candidates(origin.text):
            raise ResolutionError('公司与当前实体片段不匹配；指代请用有效历史来源，多个候选请澄清。')
    elif origin.kind == 'history':
        if request.relation not in {'followup', 'correction', 'resume'}:
            raise ResolutionError('新问题不能继承历史条件。')
        valid = {str(previous.get('turn_id', ''))} | {t['turn_id'] for t in context['recent_dialogue']}
        if not origin.turn_id or origin.turn_id not in valid:
            raise ResolutionError('引用的历史轮次不存在。')
        if slot == 'companies' and set(values) != set(previous.get('codes', [])):
            raise ResolutionError('历史公司与引用请求不同，更换公司应标注current。')
    elif origin.kind == 'catalog':
        catalog = state.get('last_catalog') or {}
        if origin.turn_id != catalog.get('turn_id') or not set(values) <= set(catalog.get('codes', [])):
            raise ResolutionError('公司集合不属于引用目录。')
    elif origin.kind == 'selection':
        pending = state.get('pending_question') or {}
        if not pending or origin.turn_id != pending.get('turn_id'):
            raise ResolutionError('当前没有这个待选问题。')
    elif origin.kind == 'default' and slot == 'companies' and getattr(request, 'company_scope', '') == 'all':
        return
    elif slot == 'companies':
        raise ResolutionError('不能默认选择公司。')


def resolve_request(question, request, history):
    state = conversation_state(history)
    context = planner_context(question, history)
    previous = state.get('pending_request') or state.get('active_request') or {}
    if isinstance(request, CatalogRequest) or str(state.get('last_intent', '')).startswith('coverage_'):
        previous = state.get('last_catalog_request') or previous
    if state.get('suspended') and request.relation != 'resume':
        previous = {}
    plan = QueryPlan(question=question, correction=request.relation == 'correction')
    contract = {'version': 2, 'request': request.model_dump(mode='json'), 'goal': request.goal,
                'standalone_question': request.standalone_question, 'assumptions': [], 'resolved_origins': {}}
    plan.request_contract = contract
    def check_origin(slot, values):
        _origin_check(request, slot, values, previous, state, context)
        origin = request.origins.get(slot)
        if values and origin:
            contract['resolved_origins'][slot] = {
                **origin.model_dump(),
                'validation': 'entity_and_state' if slot == 'companies' else 'model_semantic_mapping',
            }
            plan.origins[slot] = origin.kind
    if isinstance(request, ConversationRequest):
        plan.intent = 'conversation_scope' if request.topic == 'execution_scope' else 'unsupported' if request.topic in {'out_of_scope', 'refuse_fabrication'} else 'help'
        plan.response_kind = 'conversation'
        plan.reason = request.topic if plan.intent == 'unsupported' else ''
        if request.topic == 'execution_scope':
            execution = state.get('last_execution') or {}
            executed_pairs, executed_codes = [], []
            for query in execution.get('queries') or []:
                scope = query.get('requested_scope') or {}
                executed_pairs.extend(scope.get('pairs') or [])
                executed_codes.extend(scope.get('codes') or [])
            plan.codes = list(dict.fromkeys(executed_codes or previous.get('codes') or []))
            plan.metrics = list(previous.get('metrics') or [])
            plan.pairs = [(int(y), p) for y, p in (executed_pairs or previous.get('pairs') or [])]
            plan.period = previous.get('period') or (plan.pairs[-1][1] if plan.pairs else 'FY')
            plan.latest_count = 0 if plan.pairs else int(previous.get('latest_count') or 0)
            plan.time_mode = 'explicit' if plan.pairs else (previous.get('time_mode') or 'explicit')
        return plan
    if isinstance(request, ClarificationRequest):
        if request.partial:
            plan = resolve_request(question, request.partial, history)
            plan.request_contract = contract
        else:
            plan.intent = 'clarify'; plan.response_kind = 'conversation'
        missing = list(dict.fromkeys(request.missing or ['request']))
        # 作品说明：先确定任务类别，再应用财务条件。
        if 'request' in missing:
            missing = ['request']
        labels = {k:s['label'] for k,s in known_metrics().items()}
        allowed = set(COMPANY_CODE_MAP) | set(labels.values()) | {'全年','上半年','一季度','前三季度累计','查询财报数据','了解系统功能'}
        options = [CODE_TO_NAME_MAP.get(v,labels.get(v,v)) for v in request.options]
        plan.options = list(dict.fromkeys(v for v in options if v in allowed))
        company = CODE_TO_NAME_MAP.get(plan.codes[0], '') if plan.codes else ''
        year = plan.pairs[0][0] if plan.pairs else (selected_years(question)[:1] or [None])[0]
        metric_label = labels.get(plan.metrics[0], '') if plan.metrics else ''
        if 'company' in missing and re.search(r'它|这家', question) and not plan.codes:
            plan.clarification = clarify_pronoun()
            plan.reason = 'company_required'
        elif 'company' in missing and plan.options:
            plan.clarification = clarify_near_miss(plan.options)
            plan.reason = 'company_identity_unconfirmed'
        elif 'metric' in missing and '现金流' in question:
            plan.clarification = clarify_cash(company, year)
            plan.reason = 'cash_flow_kind_required'
        elif 'metric' in missing:
            plan.clarification = clarify_metric(company, year)
            plan.reason = 'metric_required' if plan.reason != 'company_required' else plan.reason
        elif 'company' in missing:
            plan.clarification = clarify_company(metric_label, year)
            plan.reason = 'company_required'
        elif 'time' in missing or 'period' in missing:
            plan.clarification = clarify_time()
            plan.reason = 'ambiguous_request'
        else:
            plan.clarification = clarify_request()
            plan.reason = 'ambiguous_request'
        return plan
    if isinstance(request, CatalogRequest):
        plan.intent = {'companies': 'coverage_companies', 'reports': 'coverage_periods', 'metrics': 'coverage_metrics'}[request.dimension]
        plan.response_kind = 'catalog'
        if request.dimension == 'companies':
            return plan
        plan.codes = _codes(request.companies)
        if not plan.codes and request.relation in {'followup', 'correction', 'resume'}:
            plan.codes = previous.get('codes', [])
        if request.companies:
            check_origin('companies', plan.codes)
        time = request.time
        if request.dimension == 'metrics' and time is None:
            return plan
        if not plan.codes:
            plan.intent = 'clarify'; plan.clarification = '你想查看哪家公司的报告范围？'; plan.reason = 'company_required'
            return plan
        if time is None:
            return plan
        if time.mode in {'default', 'explicit'} and not time.years and not time.reports and time.period != 'single_quarter':
            # 作品说明：目录期间来自全部匹配报告，而非只有最新事实。
            plan.period = time.period or 'FY'
            plan.coverage_period_filter = time.period
            return plan
    else:
        plan.intent = 'explanation' if request.evidence == 'cause' else 'facts'
        plan.needs_evidence = request.evidence != 'none'
        plan.chart = request.presentation in {'line', 'bar', 'chart'}
        plan.calculation = request.calculation; plan.comparison = request.comparison
        plan.compare_companies = request.comparison == 'companies'
        plan.overview = request.metric_scope == 'overview'
        plan.unit = request.unit or (previous.get('unit', '') if request.inherit_unit else '')
        if request.company_scope == 'all':
            plan.all_companies = True
        elif request.company_scope == 'catalog':
            catalog = state.get('last_catalog') or {}
            if catalog.get('complete') and catalog.get('codes'):
                plan.codes = _codes(catalog['codes'])
            elif state.get('last_intent') == 'coverage_companies':
                plan.all_companies = True
            else:
                raise ResolutionError('没有有效公司目录，请先查catalog或明确all。')
        elif request.company_scope == 'inherit' and request.relation != 'new':
            plan.codes = previous.get('codes', [])
            named = [code for code in CODE_TO_NAME_MAP if code in exact_company_codes(question)]
            if named and set(named) != set(plan.codes):
                plan.codes = named
                request.companies = list(plan.codes)
                request.company_scope = 'specified'
                check_origin('companies', plan.codes)
        else:
            plan.codes = _codes(request.companies)
            named = [code for code in CODE_TO_NAME_MAP if code in exact_company_codes(question)]
            if named and plan.codes and not set(plan.codes) <= set(named):
                kept = [code for code in plan.codes if code in named]
                plan.codes = kept or named
                request.companies = list(plan.codes)
            if plan.codes:
                check_origin('companies', plan.codes)
        if request.metric_scope in {'main', 'overview'}:
            plan.metrics = list(MAIN_FINANCIAL_METRICS)
        elif request.metric_scope == 'inherit' and request.relation != 'new':
            plan.metrics = previous.get('metrics', [])
        else:
            plan.metrics = list(dict.fromkeys(request.metrics))
            if plan.metrics:
                check_origin('metrics', plan.metrics)
        if any(m not in known_metrics() for m in plan.metrics):
            raise ResolutionError('指标ID不在目录，请用登记基础指标和计算操作，不能拼造字段。')
        if not plan.codes and not plan.all_companies:
            year = plan.pairs[0][0] if plan.pairs else (selected_years(question)[:1] or [None])[0]
            label = known_metrics().get(plan.metrics[0], {}).get('label', '') if plan.metrics else ''
            plan.intent = 'clarify'; plan.clarification = clarify_company(label, year); plan.reason = 'company_required'
            return plan
        if not plan.metrics and not plan.needs_evidence:
            company = CODE_TO_NAME_MAP.get(plan.codes[0], '') if plan.codes else ''
            year = plan.pairs[0][0] if plan.pairs else (selected_years(question)[:1] or [None])[0]
            plan.intent = 'clarify'; plan.clarification = clarify_metric(company, year); plan.reason = 'metric_required'
            # 作品说明：澄清追问保留已有期间条件。
            if request.time.period != 'single_quarter':
                try:
                    _resolve_time(plan, request, request.time, previous, contract)
                except ResolutionError:
                    plan.pairs = []; plan.latest_count = 0
            return plan
        time = request.time
    return _resolve_time(plan, request, time, previous, contract)


def _resolve_time(plan, request, time, previous, contract):
    if time.period == 'single_quarter':
        plan.intent = 'unsupported'; plan.reason = 'single_quarter_not_supported'
        # 作品说明：不支持单季后改查累计时保留明确年度，不能跳到最新年。
        years = list(dict.fromkeys([*time.years, *(r.year for r in time.reports)]))
        plan.pairs = [(y, 'FY') for y in years if 2000 <= y <= 2100]
        return plan
    # 作品说明：新问题不沿用旧年度；追问空区间应明确失败，不能扩大到所有报告。
    mode = 'default' if time.mode == 'inherit' and (request.relation == 'new' or not previous) else time.mode
    plan.period = time.period or (previous.get('period', 'FY') if mode == 'inherit' else 'FY')
    if mode == 'inherit':
        plan.time_mode = previous.get('time_mode', 'explicit'); plan.latest_count = previous.get('latest_count', 0)
        plan.pairs = [(int(y), time.period or p) for y, p in previous.get('pairs', [])]
        if plan.latest_count: plan.pairs = []
        if not plan.pairs and not plan.latest_count:
            raise ResolutionError('历史请求没有有效时间范围；请从本轮或历史用户问题解析年份，不能无边界查询。')
    elif mode in {'latest', 'default'}:
        plan.time_mode = 'latest'; plan.latest_count = time.count if mode == 'latest' else 1
        plan.default_time = mode == 'default'
        if plan.default_time: contract['assumptions'].append('未指定年份，按最近一份已入库的同口径报告查询。')
    elif mode == 'calendar':
        plan.time_mode = 'calendar'
        plan.pairs = [(y, plan.period) for y in range(datetime.now().year - time.count, datetime.now().year)]
    else:
        plan.pairs = list(dict.fromkeys([(r.year,r.period) for r in time.reports] or [(y, plan.period) for y in time.years]))
        if not plan.pairs or any(not 2000 <= y <= 2100 for y, _ in plan.pairs):
            raise ResolutionError('explicit必须有有效年份；最近N份用latest。')
    if isinstance(request, CatalogRequest):
        plan.coverage_period_filter = time.period
        if time.mode == 'explicit' and not time.period and not time.reports:
            plan.pairs = [(y, p) for y in time.years for p in ('FY', 'Q1', 'HY', 'Q3')]
    else:
        contract['requested_pairs'] = list(plan.pairs)
        if plan.calculation == 'yoy' and len(plan.pairs) == 1:
            year, period = plan.pairs[0]; plan.pairs = [(year - 1, period), (year, period)]
    plan.origins = {k: v.kind for k, v in request.origins.items()}
    if plan.intent != 'clarify':
        contract['resolved_scope'] = plan.scope()
    return plan


def _is_continuation(question, mentions, previous):
    """作品说明：简短追问修改一个条件时，不视为新公司查询。"""
    if mentions.non_query or mentions.company_codes() or mentions.unknown_organizations or mentions.near_miss_companies:
        return False
    if mentions.company_scope == 'all':
        return False
    if mentions.company_scope == 'catalog':
        return True
    if not previous or not (previous.get('codes') or previous.get('metrics') or previous.get('pairs') or previous.get('latest_count')):
        return False
    return bool(mentions.years or mentions.metric_ids() or mentions.main_metrics or mentions.periods
                or mentions.drawing or is_followup(question) or re.search(r'亿元|万元|换成元', question))


def _keeps_previous_chart(question, history, mentions):
    """作品说明：纠正图表请求后仍按图表任务处理。"""
    if re.search(r'别画|不画|不要画|不用画', question):
        return False
    if mentions.drawing:
        return True
    if '我是说' not in question:
        return False
    for item in reversed(history):
        if item.get('role') == 'user':
            return bool(re.search(r'画|图|可视化|走势|折线|柱状', str(item.get('content') or '')))
    return False


def previous_scope(history, relation):
    state = conversation_state(history)
    if state.get('suspended') and relation != 'resume':
        return {}
    return state.get('pending_request') or state.get('active_request') or {}


def review_slots(slots, intent, mentions, previous, *, sole_task, question='', keep_chart=False):
    """作品说明：将模型条件与本轮框架比对，返回具体修改项；一致时返回空问题列表。"""
    issues = []
    def issue(code, detail, **fix):
        issues.append({'code': code, 'detail': detail, 'fix': fix})
    names = lambda codes: '、'.join(CODE_TO_NAME_MAP.get(c, c) for c in codes)
    labels = lambda ids: '、'.join(f"{known_metrics()[m]['label']}({m})" for m in ids if m in known_metrics())
    codes, metrics = mentions.company_codes(), mentions.metric_ids()
    near_codes = {item['code'] for item in mentions.near_miss_companies}
    continuation = _is_continuation(question, mentions, previous)
    if continuation and mentions.company_scope != 'catalog' and intent.relation == 'new':
        intent.relation = 'followup'
    dangling_reference = bool(re.search(r'它|这家', question)) and not codes and not mentions.unknown_organizations and not previous.get('codes') and mentions.company_scope != 'catalog'
    if mentions.company_scope == 'all' and slots.company_scope != 'all':
        issue('plan_collective_scope', '原话是全库/全部公司范围，company_scope须为all，不必要求每个公司名出现在句子里',
              company_scope='all', companies=[])
    if 'metric' in slots.clarify and metrics and not mentions.ambiguous_metrics:
        issue('plan_unneeded_clarification', f'原话已写明指标{labels(metrics)}，不需要追问指标', clarify_remove='metric')
    if 'company' in slots.clarify and codes:
        issue('plan_unneeded_clarification', f'原话已写明公司{names(codes)}，不需要追问公司', clarify_remove='company')
    if 'time' in slots.clarify and mentions.years:
        issue('plan_unneeded_clarification', f'原话已写明年份{mentions.years}，不需要追问时间', clarify_remove='time')
    if dangling_reference and (slots.companies or 'company' not in slots.clarify):
        issue('plan_missing_reference', '本轮用了“它/这家”，但没有可沿用的公司，应追问公司，不能自行指定或整轮失败',
              companies_clear=True, metrics_clear=True, company_scope='specified', clarify_add='company')
    elif continuation and mentions.company_scope == 'catalog' and slots.company_scope != 'catalog':
        issue('plan_continuation_company', '本轮说的是上文公司目录，company_scope须为catalog，不必重写每个公司名',
              company_scope='catalog', companies=[])
    elif continuation and slots.company_scope != 'inherit':
        issue('plan_continuation_company', '本轮没有点名新公司，沿用上文公司，只替换本句写明的指标、年份或报告期',
              company_scope='inherit', companies=[])
    elif slots.company_scope == 'specified':
        missing = [c for c in codes if c not in slots.companies]
        extras = [c for c in slots.companies if codes and c not in codes]
        named_kept = [c for c in slots.companies if c in codes]
        if codes and extras and (sole_task or not named_kept):
            issue('plan_lock_named_company',
                  f'原话已写明公司{names(codes)}，只保留这些，模型多带的公司去掉且不因此失败',
                  company_scope='specified', companies=list(codes))
        elif codes and extras:
            issue('plan_lock_named_company', f'原话没有点名{names(extras)}，不能留在本轮公司里',
                  companies_remove=extras)
        elif missing and (not slots.companies or sole_task):
            issue('plan_incomplete_company', f'原话提到的公司{names(missing)}没有填入companies', companies_add=missing)
        excluded = [c for c in slots.companies if c in mentions.excluded_companies]
        if excluded:
            issue('plan_uses_excluded_company', f'用户说了不要{names(excluded)}，不能查询', companies_remove=excluded)
        # 作品说明：近似名称不能作为已确定公司。
        guessed_companies = [c for c in slots.companies if c in near_codes and c not in codes]
        if guessed_companies or (not codes and mentions.near_miss_companies and not mentions.unknown_organizations):
            suggestions = [item['name'] for item in mentions.near_miss_companies]
            words = '、'.join(item['text'] for item in mentions.near_miss_companies) or '原话中的公司'
            issue('plan_unconfirmed_company',
                  f'原话“{words}”未精确对应登记公司，不能直接填入companies；请澄清是否指{"、".join(suggestions)}',
                  companies_clear=True, metrics_clear=True, clarify_add='company', options=suggestions[:5])
    elif slots.company_scope == 'inherit' and codes and set(codes) != set(previous.get('codes', [])):
        issue('plan_inherit_conflicts_company', f'原话指定了公司{names(codes)}，不能沿用上文公司',
              company_scope='specified', companies=codes)
    if mentions.ambiguous_metrics:
        ambiguous_ids = {m for item in mentions.ambiguous_metrics for m in item['options']}
        guessed = [m for m in slots.metrics if m in ambiguous_ids and m not in metrics]
        words = '、'.join(item['text'] for item in mentions.ambiguous_metrics)
        options = [m for item in mentions.ambiguous_metrics for m in item['options']]
        if guessed or 'metric' not in slots.clarify:
            issue('plan_skipped_clarification',
                  f'原话只说了“{words}”，没有说明具体种类，不能把{labels(guessed) if guessed else words}留在metrics里；去掉未消歧项并在clarify中填metric；其他已写明的指标保留',
                  metrics_remove=guessed, clarify_add='metric', options=options[:5])
        missing = [m for m in metrics if m not in slots.metrics]
        if missing and (not [m for m in slots.metrics if m not in ambiguous_ids] or (sole_task and metrics)):
            issue('plan_incomplete_metric', f'目标和原话包含指标{labels(missing)}，没有填入metrics', metrics_add=missing,
                  source='current_question')
    elif mentions.main_metrics:
        main_ids = list(MAIN_FINANCIAL_METRICS)
        scope = 'overview' if mentions.overview else 'main'
        if slots.metric_scope != scope or set(slots.metrics) != set(main_ids) or 'metric' in slots.clarify:
            fix = {'metric_scope': scope, 'metrics': main_ids}
            if 'metric' in slots.clarify:
                fix['clarify_remove'] = 'metric'
            issue('plan_main_metrics', '原话是主要指标或近况，使用固定主要财务指标，不另问指标', **fix)
    else:
        company_only = bool(codes and not metrics and not mentions.years and not mentions.periods and not previous.get('metrics'))
        if company_only and slots.metrics:
            issue('plan_company_only', '本轮只点了公司，上文也没有指标，不能自行补指标；记下公司并追问指标',
                  metrics_clear=True, clarify_add='metric')
        elif continuation and metrics and set(slots.metrics) != set(metrics):
            issue('plan_continuation_metric', '本轮写明了指标，只查这些指标，不保留模型另加的指标',
                  metric_scope='specified', metrics=list(metrics))
        elif metrics and sole_task and any(m not in metrics for m in slots.metrics):
            issue('plan_lock_named_metric',
                  f'原话写明了指标{labels(metrics)}，只查这些，不因“如何”扩成其他指标',
                  metric_scope='specified', metrics=list(metrics))
        elif continuation and not metrics and previous.get('metrics') and slots.metric_scope == 'specified':
            issue('plan_continuation_metric', '本轮没有改指标，沿用上文指标', metric_scope='inherit', metrics=[])
        stated_metrics = metrics or [m for m in link_entities(f'{intent.goal} {intent.standalone_question}').metric_ids()
                                     if m in known_metrics()]
        if company_only:
            stated_metrics = []
        if slots.metric_scope == 'specified' and not (company_only and slots.metrics):
            missing = [m for m in stated_metrics if m not in slots.metrics]
            if missing and (not slots.metrics or (sole_task and metrics)):
                issue('plan_incomplete_metric', f'目标和原话包含指标{labels(missing)}，没有填入metrics', metrics_add=missing,
                      source='current_question' if metrics else 'model_goal')
        elif slots.metric_scope == 'inherit' and metrics and set(metrics) != set(previous.get('metrics', [])):
            issue('plan_inherit_conflicts_metric', f'原话指定了指标{labels(metrics)}，不能沿用上文指标',
                  metric_scope='specified', metrics=metrics)
    if mentions.evidence != 'none' and slots.evidence != mentions.evidence:
        issue('plan_evidence_mismatch', f'原话要求{mentions.evidence}证据，evidence须与之一致',
              evidence=mentions.evidence)
    if mentions.single_quarter and slots.time.period != 'single_quarter':
        issue('plan_period_mismatch', '原话明确要求单季/Q2/Q4，period须为single_quarter', time_period='single_quarter')
    elif not mentions.single_quarter and slots.time.period == 'single_quarter':
        period = mentions.periods[0] if mentions.periods else 'FY'
        issue('plan_period_mismatch', f'原话没有单季口径，不能用single_quarter；应按{period}', time_period=period)
    elif mentions.periods and len(mentions.periods) == 1 and slots.time.period not in {None, mentions.periods[0], 'single_quarter'}:
        if not (slots.time.mode == 'inherit' and intent.relation != 'new'):
            issue('plan_period_mismatch', f'原话报告期为{mentions.periods[0]}，time.period须与之一致',
                  time_period=mentions.periods[0])
    years = mentions.years
    if getattr(mentions, 'year_span', 0) and not years and slots.time.mode != 'latest':
        issue('plan_year_span', f'原话要的是{mentions.year_span}年，不是单独某一年', time_latest=mentions.year_span)
    planned = set(slots.time.years) | {r.year for r in slots.time.reports}
    prior_years = sorted({int(y) for y, _ in previous.get('pairs') or []})
    if continuation and not years and mentions.periods and prior_years and (slots.time.mode != 'explicit' or set(slots.time.years) != set(prior_years)):
        issue('plan_continuation_time', '本轮只改了报告期，年份沿用上文，不改查最新一年', time_years=prior_years)
    elif continuation and not years and not mentions.periods and slots.time.mode == 'explicit':
        issue('plan_continuation_time', '本轮没有改年份或报告期，沿用上文时间', time_inherit=True)
    elif years and slots.time.period != 'single_quarter' and (slots.time.mode != 'explicit' or not set(years) <= planned):
        issue('plan_time_mismatch', f'原话写明年份{years}，time须为explicit并包含这些年份', time_years=years)
    elif slots.time.mode == 'explicit' and not getattr(mentions, 'year_span', 0):
        followup = intent.relation != 'new'
        inherited = {int(y) for y, _ in previous.get('pairs', [])} if followup else set()
        # 作品说明：同比基期由目标年减一得到，其他年份必须来自用户明确要求。
        compare = slots.calculation == 'yoy' or bool(re.search(r'同比|增减|少了多少|多了多少|变化了多少|差了多少', question))
        base = {y - 1 for y in years} if compare else set()
        unstated = sorted(planned - set(years) - base - inherited)
        if unstated:
            can_inherit = followup and bool(previous.get('pairs') or previous.get('latest_count'))
            fix = {'time_years': years} if years else {'time_inherit': True} if can_inherit else {'time_default': True}
            issue('plan_unstated_year', f'原话没有说{unstated}年，不能用today或自行指定年份；沿用上文时间用inherit，没说时间用default', **fix)
    if keep_chart and not mentions.drawing and slots.presentation == 'auto':
        issue('plan_continuation_chart', '本轮是在纠正上一轮的画图请求，presentation须保留图表', presentation='line')
    elif slots.presentation in {'line', 'bar', 'chart'} and not keep_chart:
        issue('plan_unrequested_chart', '本轮没有要求画图，presentation须为auto，不继承上一轮的画图', presentation='auto')
    return issues


def repair_slots(slots, issues, intent, previous, question):
    """作品说明：模型重试后再进行程序约束修正。"""
    data = slots.model_dump()
    for item in issues:
        fix = item['fix']
        if 'clarify_remove' in fix:
            data['clarify'] = [c for c in data['clarify'] if c != fix['clarify_remove']]
            if not data['clarify']: data['options'] = []
        if 'clarify_add' in fix and fix['clarify_add'] not in data['clarify']:
            data['clarify'].append(fix['clarify_add'])
        if 'options' in fix:
            data['options'] = list(fix['options'])[:5]
        if 'metrics_remove' in fix:
            data['metrics'] = [m for m in data['metrics'] if m not in fix['metrics_remove']]
        if fix.get('metrics_clear'):
            data['metrics'] = []
        if 'presentation' in fix:
            data['presentation'] = fix['presentation']
        if 'evidence' in fix:
            data['evidence'] = fix['evidence']
        if 'companies_add' in fix:
            data['companies'] += [c for c in fix['companies_add'] if c not in data['companies']]
        if 'companies_remove' in fix:
            data['companies'] = [c for c in data['companies'] if c not in fix['companies_remove']]
        if fix.get('companies_clear'):
            data['companies'] = []
        if 'company_scope' in fix:
            data['company_scope'] = fix['company_scope']; data['companies'] = list(fix.get('companies', data['companies']))
        if 'metrics_add' in fix:
            data['metrics'] += [m for m in fix['metrics_add'] if m not in data['metrics']]
        if 'metric_scope' in fix:
            data['metric_scope'] = fix['metric_scope']; data['metrics'] = list(fix['metrics'])
        if 'time_period' in fix:
            data['time']['period'] = fix['time_period']
        if 'time_years' in fix:
            periods = extract_report_periods(question)
            period = (periods[0] if len(periods) == 1 else data['time']['period']
                      or (previous.get('period') if intent.relation != 'new' else None))
            data['time'].update(mode='explicit', years=list(fix['time_years']), reports=[], period=period)
        if fix.get('time_inherit') or fix.get('time_default'):
            data['time'].update(mode='inherit' if fix.get('time_inherit') else 'default', years=[], reports=[])
        if 'time_latest' in fix:
            data['time'].update(mode='latest', years=[], reports=[], count=int(fix['time_latest']))
    return FinancialSlots.model_validate(data)


def merge_request(intent, slots, mentions):
    """作品说明：将意图与条件合并为统一解析契约。"""
    common = dict(goal=intent.goal, standalone_question=intent.standalone_question, relation=intent.relation)
    if isinstance(intent, ConversationIntent):
        return ConversationRequest(kind='conversation', topic=intent.topic, **common)
    if isinstance(intent, CatalogIntent):
        return CatalogRequest(kind='catalog', dimension=intent.dimension, companies=intent.companies,
                              time=intent.time, **common)
    if isinstance(intent, UnclearIntent):
        return ClarificationRequest(kind='clarification', question='', missing=['request'], **common)
    payload = slots.model_dump(exclude={'clarify', 'options'})
    # 作品说明：有歧义的指标不自动猜测，同时保留其他已明确指标。
    if mentions.ambiguous_metrics:
        ambiguous_ids = {m for item in mentions.ambiguous_metrics for m in item['options']}
        payload['metrics'] = [m for m in payload.get('metrics', []) if m not in ambiguous_ids]
        for metric_id in mentions.metric_ids():
            if metric_id not in payload['metrics']:
                payload['metrics'].append(metric_id)
    elif mentions.main_metrics and payload.get('metric_scope') in {'main', 'overview', 'specified'}:
        payload['metrics'] = list(MAIN_FINANCIAL_METRICS)
        payload['metric_scope'] = 'main' if payload.get('metric_scope') != 'overview' else payload['metric_scope']
    if mentions.evidence != 'none':
        payload['evidence'] = mentions.evidence
    if mentions.company_scope == 'catalog':
        payload['company_scope'] = 'catalog'
        payload['companies'] = []
    elif mentions.company_scope == 'all':
        payload['company_scope'] = 'all'
        payload['companies'] = []
    if 'company' in slots.clarify and mentions.near_miss_companies and not mentions.company_codes():
        payload['companies'] = []
        payload['metrics'] = []
    request = FinancialRequest(kind='financial', **common, **payload)
    if not slots.clarify:
        return request
    options = list(slots.options)
    if 'metric' in slots.clarify and mentions.ambiguous_metrics:
        options = [m for item in mentions.ambiguous_metrics for m in item['options']]
    if 'company' in slots.clarify and mentions.near_miss_companies:
        options = [item['name'] for item in mentions.near_miss_companies]
    return ClarificationRequest(kind='clarification', question='', options=options[:5],
                                missing=list(slots.clarify), partial=request, **common)


# 作品说明：同比是增长率，不是「和某一年比」这种差额。同和比之间必须有字，避免把同比算进去。
_TIME_COMPARE = re.compile(r'相比|比较|对比|比一比|(?:跟|和|与).{0,16}比|同.{1,16}比|比一下')
_CALCULATED_CHANGE = re.compile(r'同比|增减|增长率|变动率')
_METRIC_CHOICE = {
    'operating_cf_net_amount': '经营活动现金流量净额',
    'investing_cf_net_amount': '投资活动现金流量净额',
    'financing_cf_net_amount': '筹资活动现金流量净额',
    'net_cash_flow': '净现金流',
}


def _short_contract(kind, **extra):
    return {
        'version': 2,
        'plan_validation': {
            'accepted': True, 'mode': 'short_path', 'attempts': 0, 'slot_attempts': 0,
            'issues': [], 'repairs': [], 'reason_codes': ['short_path'],
        },
        'tasks': [{'kind': kind, **extra}],
    }


def _unresolved(mentions):
    """作品说明：实体识别已发现歧义时，不按单值查询处理。"""
    return bool(
        mentions.non_query or mentions.ambiguous_metrics or mentions.near_miss_companies
        or mentions.unknown_organizations or mentions.drawing or mentions.evidence != 'none'
        or mentions.main_metrics or mentions.overview or mentions.single_quarter
        or mentions.company_scope in {'all', 'catalog'}
    )


def _facts_short(question, codes, metrics, pairs, *, calculation='none', comparison='none', relation='followup'):
    period = pairs[0][1] if len({p for _, p in pairs}) == 1 else pairs[-1][1]
    plan = QueryPlan(
        question=question, intent='facts', codes=list(codes), metrics=list(metrics),
        pairs=list(pairs), period=period, time_mode='explicit', response_kind='financial',
        calculation=calculation, comparison=comparison,
    )
    plan.request_contract = _short_contract(
        'financial', goal=question, standalone_question=question, relation=relation,
        evidence='none', metric_scope='specified', companies=list(codes), metrics=list(metrics),
    )
    plan.request_contract['requested_pairs'] = list(pairs)
    plan.request_contract['resolved_scope'] = plan.scope()
    return plan


def _saved_pairs(saved):
    pairs = []
    for item in saved.get('pairs') or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        year, period = int(item[0]), item[1]
        if 2000 <= year <= 2100 and period in {'FY', 'Q1', 'HY', 'Q3'}:
            pairs.append((year, period))
    return pairs


def _saved_scope(history):
    saved = conversation_state(history).get('active_request') or {}
    codes = [code for code in saved.get('codes') or [] if code in CODE_TO_NAME_MAP]
    metrics = [metric for metric in saved.get('metrics') or [] if metric in known_metrics()]
    return codes, metrics, _saved_pairs(saved)


def _window_years(saved_pairs):
    return sorted({year for year, _ in saved_pairs})


def _action_period(mentions, saved_pairs, history):
    """作品说明：明确期间优先；否则在有数据时保留既定图表要求。"""
    if len(mentions.periods) == 1:
        return mentions.periods[0]
    outcome = (conversation_state(history).get('outcome') or {}).get('status')
    if outcome == 'no_data':
        return 'FY'
    periods = {period for _, period in saved_pairs}
    if len(periods) == 1:
        return next(iter(periods))
    return 'FY'


def _window_pairs(saved_pairs, period):
    return [(year, period) for year in _window_years(saved_pairs)]


def _when_label(years):
    years = sorted({int(year) for year in years})
    if len(years) == 1:
        return f'{years[0]}年'
    if len(years) >= 2:
        return f'{years[0]}到{years[-1]}年'
    return ''


def _years_for_followup(question, pairs):
    """作品说明：去年、前年锚到窗口最后一年；今年按今天。明确年份和相对年份叠在一起就不猜。"""
    text = str(question or '')
    bare = re.sub(r'今年|去年|前年', '', text)
    explicit = extract_report_years(bare)
    relative = bool(re.search(r'今年|去年|前年', text))
    if explicit and relative:
        return None
    if re.search(r'去年|前年', text):
        anchors = _window_years(pairs)
        if not anchors:
            return extract_report_years(text)
        return extract_report_years(text, report_anchor=anchors[-1])
    return extract_report_years(text)


def _unparsed_words(question, mentions):
    """作品说明：提取去除公司、指标、年度与期间后的剩余表达。"""
    text = str(question or '')
    for item in list(mentions.companies) + list(mentions.metrics):
        span = str(item.get('text') or '')
        if span:
            text = text.replace(span, ' ')
    text = re.sub(r'今年|去年|前年', ' ', text)
    text = YEAR_SPAN_RE.sub(' ', text)
    text = re.sub(
        r'一季度|第一季度|一季报|上半年|半年度|半年报|中报|中期|半年|前三季度|三季度|第三季度|三季报|全年|年报|年度',
        ' ', text, flags=re.I)
    text = re.sub(r'(?<![A-Za-z])(?:Q1|HY|Q3|FY|H1)(?![A-Za-z0-9])', ' ', text, flags=re.I)
    text = re.sub(r'\d+', ' ', text)
    text = re.sub(r'它|这家', '', text)
    text = re.sub(r'[那再换改的了呢啊吧吗和与及把个一下，,、。！？\s年月报图画看比跟]', '', text)
    return text.strip()


def _changed(question, mentions, history):
    """作品说明：区分单条件替换与新增年度比较。"""
    if _unresolved(mentions) or len(mentions.periods) > 1:
        return None
    codes, metrics, pairs = _saved_scope(history)
    if not codes or not metrics or not pairs:
        return None
    years = _years_for_followup(question, pairs)
    if years is None or len(years) > 1 or len(mentions.company_codes()) > 1 or len(mentions.metric_ids()) > 1:
        return None
    named = mentions.company_codes()
    new_metrics = mentions.metric_ids()
    periods = mentions.periods
    comparing = bool(_TIME_COMPARE.search(str(question or '')))
    switching = bool(re.search(r'换成|改为|改成', str(question or '')))
    if comparing and named and set(named) - set(codes):
        focus = new_metrics if len(new_metrics) == 1 else ([metrics[-1]] if metrics else [])
        if len(focus) != 1:
            return None
        period = _action_period(mentions, pairs, history)
        scope = _window_pairs(pairs, period)
        if switching:
            return _facts_short(question, named, focus, scope)
        combined = list(dict.fromkeys([*codes, *named]))
        plan = _facts_short(question, combined, focus, scope, comparison='companies')
        plan.compare_companies = True
        return plan
    if switching and len(named) == 1 and set(named) != set(codes):
        focus = new_metrics if len(new_metrics) == 1 else ([metrics[-1]] if metrics else [])
        if len(focus) == 1:
            period = _action_period(mentions, pairs, history)
            return _facts_short(question, named, focus, _window_pairs(pairs, period))
    if comparing:
        if named and set(named) != set(codes):
            return None
        if new_metrics and set(new_metrics) != set(metrics):
            return None
        focus = new_metrics if len(new_metrics) == 1 else ([metrics[-1]] if metrics else [])
        window = _window_years(pairs)
        if len(focus) != 1 or len(years) != 1 or not window:
            return None
        period = _action_period(mentions, pairs, history)
        other = years[0]
        focus_year = window[-1]
        if other == focus_year:
            return None
        sides = [(year, period) for year in sorted({other, focus_year})]
        use_codes = named or codes
        return _facts_short(question, use_codes, focus, sides, calculation='difference', comparison='time')
    if asks_for_reading(question) or _CALCULATED_CHANGE.search(str(question or '')):
        return None
    if _unparsed_words(question, mentions):
        return None
    company_change = bool(named) and set(named) != set(codes)
    metric_change = bool(new_metrics) and set(new_metrics) != set(metrics)
    if year_and_period := (bool(years) and bool(periods) and not company_change and not metric_change):
        return _facts_short(question, codes, metrics, [(years[0], periods[0])])
    changes = sum([company_change, metric_change, bool(years), bool(periods)])
    if changes != 1:
        return None
    if company_change:
        period = _action_period(mentions, pairs, history)
        return _facts_short(question, named, metrics, _window_pairs(pairs, period))
    if metric_change:
        period = _action_period(mentions, pairs, history)
        return _facts_short(question, codes, new_metrics, _window_pairs(pairs, period))
    if years:
        period = _action_period(mentions, pairs, history)
        return _facts_short(question, codes, metrics, [(years[0], period)])
    return _facts_short(question, codes, metrics, [(year, periods[0]) for year, _ in pairs])


def _choice_short(question, mentions, history):
    """作品说明：近似名称、候选菜单及未命名现金流澄清可直接处理，无需再调用规划模型。"""
    if (mentions.non_query or mentions.drawing or mentions.evidence != 'none' or mentions.single_quarter
            or mentions.main_metrics or mentions.overview or mentions.unknown_organizations
            or mentions.company_scope in {'all', 'catalog'}):
        return None
    if mentions.near_miss_companies and not mentions.company_codes():
        if mentions.ambiguous_metrics or len(mentions.metric_ids()) != 1 or len(mentions.years) != 1:
            return None
        if len(mentions.periods) > 1:
            return None
        spans = list(dict.fromkeys(item['text'] for item in mentions.near_miss_companies))
        if len(spans) != 1:
            return None
        names = list(dict.fromkeys(item['name'] for item in mentions.near_miss_companies))
        period = mentions.periods[0] if len(mentions.periods) == 1 else 'FY'
        return _clarify_short(
            question, clarification=clarify_near_miss(names), options=names,
            reason='company_identity_unconfirmed', codes=[], metrics=[],
            pairs=[(mentions.years[0], period)],
            choice={'original_question': question, 'slot': 'company', 'span': spans[0]},
        )
    if not mentions.ambiguous_metrics or mentions.metric_ids() or mentions.near_miss_companies:
        return None
    if len(mentions.ambiguous_metrics) != 1:
        return None
    saved_codes, _, saved_pairs = _saved_scope(history)
    codes = mentions.company_codes() or saved_codes
    years = list(mentions.years)
    if not years and saved_pairs:
        years = _window_years(saved_pairs)
    if not codes or not years or len(mentions.periods) > 1:
        return None
    period = _action_period(mentions, saved_pairs, history)
    item = mentions.ambiguous_metrics[0]
    options = [_METRIC_CHOICE.get(metric, metric) for metric in item['options']]
    company = '、'.join(CODE_TO_NAME_MAP.get(code, code) for code in codes)
    return _clarify_short(
        question, clarification=clarify_cash(company, _when_label(years)), options=options,
        reason='cash_flow_kind_required', codes=codes, metrics=[],
        pairs=[(year, period) for year in years],
        choice={'original_question': question, 'slot': 'metric', 'span': item['text']},
    )


def _clarify_short(question, *, clarification, options, reason, codes, metrics, pairs, choice):
    period = pairs[0][1] if pairs else 'FY'
    plan = QueryPlan(
        question=question, intent='clarify', codes=list(codes), metrics=list(metrics),
        pairs=list(pairs), period=period, clarification=clarification, options=list(options),
        reason=reason, response_kind='conversation',
    )
    plan.request_contract = _short_contract(
        'financial', goal=question, standalone_question=question, relation='followup',
        evidence='none', metric_scope='specified',
    )
    plan.request_contract['pending_choice'] = choice
    plan.request_contract['requested_pairs'] = list(pairs)
    if choice.get('slot') == 'company':
        plan.request_contract['company_confirmation'] = {
            'original_question': choice['original_question'],
            'typo_span': choice['span'],
        }
    return plan


def _resume_pending_choice(question, history):
    """作品说明：待澄清选择填入对应未完成请求。"""
    pending = conversation_state(history).get('pending_question') or {}
    labels = [str(option.get('label') if isinstance(option, dict) else option) for option in pending.get('options') or []]
    chosen = ''.join(str(question or '').split())
    replacement = next((label for label in labels if ''.join(label.split()) == chosen), '')
    original = str(pending.get('original_question') or '')
    span = str(pending.get('span') or pending.get('typo_span') or '')
    if not replacement or not original or not span or span not in original:
        return None
    return original.replace(span, replacement, 1)


def _span_years(mentions, saved_pairs):
    """作品说明：移动年度窗口；未提供窗口时允许数据库选择最新若干期。"""
    count = int(getattr(mentions, 'year_span', 0) or 0)
    if not count or mentions.years:
        return None
    anchors = _window_years(saved_pairs)
    if not anchors:
        return None
    if getattr(mentions, 'year_span_before', False):
        start = anchors[0]
        return list(range(start - count, start))
    end = anchors[-1]
    return list(range(end - count + 1, end + 1))


def _latest_short(question, codes, metrics, count, period):
    plan = QueryPlan(
        question=question, intent='facts', codes=list(codes), metrics=list(metrics),
        pairs=[], period=period, time_mode='latest', latest_count=count, response_kind='financial',
    )
    plan.request_contract = _short_contract(
        'financial', goal=question, standalone_question=question, relation='followup',
        evidence='none', metric_scope='specified', companies=list(codes), metrics=list(metrics),
    )
    plan.request_contract['resolved_scope'] = plan.scope()
    return plan


def _span_turn(question, mentions, history):
    """作品说明：近三年、这三年或前三年保持多年范围，不能缩成单一年份。"""
    if not getattr(mentions, 'year_span', 0) or mentions.years:
        return None
    if (mentions.non_query or mentions.ambiguous_metrics or mentions.near_miss_companies
            or mentions.unknown_organizations or mentions.single_quarter
            or mentions.company_scope in {'all', 'catalog'} or len(mentions.periods) > 1
            or len(mentions.metric_ids()) > 1 or len(mentions.company_codes()) > 1):
        return None
    saved_codes, saved_metrics, saved_pairs = _saved_scope(history)
    codes = mentions.company_codes() or saved_codes
    metrics = mentions.metric_ids() or ([saved_metrics[-1]] if saved_metrics else [])
    if not codes or len(metrics) != 1:
        return None
    period = _action_period(mentions, saved_pairs, history)
    years = _span_years(mentions, saved_pairs)
    if years:
        plan = _facts_short(question, codes, metrics, [(year, period) for year in years])
    else:
        plan = _latest_short(question, codes, metrics, mentions.year_span, period)
    if mentions.drawing:
        plan.chart = True
    return plan


def _yoy_turn(question, mentions, history):
    """作品说明：同比沿用已保存公司，将目标年与前一年同期间比较。"""
    if '同比' not in str(question or '') or asks_for_reading(question):
        return None
    if (mentions.non_query or mentions.ambiguous_metrics or mentions.near_miss_companies
            or mentions.unknown_organizations or mentions.drawing or mentions.single_quarter
            or len(mentions.metric_ids()) > 1 or len(mentions.company_codes()) > 1):
        return None
    codes, metrics, pairs = _saved_scope(history)
    if mentions.company_codes():
        codes = mentions.company_codes()
    focus = mentions.metric_ids() or ([metrics[-1]] if metrics else [])
    if not codes or len(focus) != 1:
        return None
    sentence_years = list(mentions.years)
    window = _window_years(pairs)
    period = _action_period(mentions, pairs, history)
    if not sentence_years and len(window) >= 2:
        return _facts_short(question, codes, focus, [(year, period) for year in window], calculation='yoy')
    years = _years_for_followup(question, pairs) if pairs else sentence_years
    if years is None:
        return None
    if not years and len(window) == 1:
        years = window
    if len(years) != 1:
        return None
    year = years[0]
    return _facts_short(question, codes, focus, [(year - 1, period), (year, period)], calculation='yoy')


def _cause_turn(question, mentions, history):
    """作品说明：问原因时只解释画面里正在看的那一个指标，年份用已经摆开的窗口。"""
    if mentions.evidence != 'cause':
        return None
    if (mentions.non_query or mentions.ambiguous_metrics or mentions.near_miss_companies
            or mentions.unknown_organizations or mentions.single_quarter
            or len(mentions.metric_ids()) > 1 or len(mentions.company_codes()) > 1):
        return None
    codes, metrics, pairs = _saved_scope(history)
    if mentions.company_codes():
        codes = mentions.company_codes()
    focus = mentions.metric_ids() or ([metrics[-1]] if metrics else [])
    if not codes or len(focus) != 1:
        return None
    period = _action_period(mentions, pairs, history)
    window = _window_years(pairs)
    years = list(mentions.years) or window
    if len(years) >= 2:
        scope = [(year, period) for year in years]
    elif len(years) == 1:
        scope = [(years[0] - 1, period), (years[0], period)]
    else:
        return None
    plan = _facts_short(question, codes, focus, scope)
    plan.intent = 'explanation'
    plan.needs_evidence = True
    return plan


def _chart_turn(question, mentions, history):
    """作品说明：单指标图表按用户明确列出的年度选择数据。"""
    if not mentions.drawing:
        return None
    if (mentions.non_query or mentions.ambiguous_metrics or mentions.near_miss_companies
            or mentions.unknown_organizations or mentions.single_quarter or mentions.overview
            or mentions.evidence != 'none' or mentions.company_scope in {'all', 'catalog'}
            or len(mentions.periods) > 1):
        return None
    if len(mentions.metric_ids()) > 1 or (not mentions.metric_ids() and not mentions.years):
        return None
    codes = mentions.company_codes()
    saved_codes, saved_metrics, saved_pairs = _saved_scope(history)
    if not codes:
        codes = saved_codes
    period = _action_period(mentions, saved_pairs, history)
    metrics = mentions.metric_ids() or ([saved_metrics[-1]] if saved_metrics else [])
    if not codes or len(metrics) != 1:
        return None
    if len(mentions.years) >= 2:
        years = list(mentions.years)
    else:
        years = _span_years(mentions, saved_pairs)
        window = _window_years(saved_pairs)
        if not years and not mentions.years and len(window) >= 2:
            years = window
        elif not years and not mentions.years and len(window) == 1:
            years = list(range(window[-1] - 2, window[-1] + 1))
        if not years or len(years) < 2:
            return None
    plan = _facts_short(question, codes, metrics, [(year, period) for year in years])
    plan.chart = True
    return plan


def _short_path_plan(question, mentions):
    """作品说明：身份明确的完整单值问题可跳过额外模型识别。"""
    if is_identity_question(question):
        intent = 'greeting' if is_greeting(question) else 'help'
        plan = QueryPlan(question=question, intent=intent, response_kind='conversation')
        plan.request_contract = _short_contract('conversation', topic=intent, goal='功能介绍', standalone_question=question, relation='new')
        return plan
    if mentions.non_query == 'investment_advice':
        plan = QueryPlan(question=question, intent='unsupported', reason='investment_advice', response_kind='conversation')
        plan.request_contract = _short_contract('conversation', topic='out_of_scope', goal='投资建议拒答', standalone_question=question, relation='new')
        return plan
    if (_unresolved(mentions) or asks_for_reading(question) or _TIME_COMPARE.search(str(question or ''))
            or _CALCULATED_CHANGE.search(str(question or ''))):
        return None
    if len(mentions.company_codes()) != 1 or len(mentions.metric_ids()) != 1 or len(mentions.years) != 1:
        return None
    if len(mentions.periods) > 1:
        return None
    code = mentions.company_codes()[0]
    metric = mentions.metric_ids()[0]
    year = mentions.years[0]
    period = mentions.periods[0] if len(mentions.periods) == 1 else 'FY'
    plan = QueryPlan(
        question=question, intent='facts', codes=[code], metrics=[metric],
        pairs=[(year, period)], period=period, time_mode='explicit', response_kind='financial',
    )
    plan.request_contract = _short_contract(
        'financial', goal=question, standalone_question=question, relation='new',
        evidence='none', metric_scope='specified', companies=[code], metrics=[metric],
    )
    plan.request_contract['requested_pairs'] = [(year, period)]
    plan.request_contract['resolved_scope'] = plan.scope()
    return plan


class SemanticPlanner:
    def __init__(self, llm):
        from langchain_ollama import ChatOllama
        config = llm.config; url = urlparse(llm.api_url)
        if not config.is_local or url.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('Conversational agent requires the configured local model')
        self.model_factory = ChatOllama
        self.model_options = dict(model=llm.model, base_url=f'{url.scheme}://{url.netloc}',
                                  reasoning=getattr(llm, 'reasoning_effort', 'none') != 'none',
                                  temperature=0, num_ctx=config.num_ctx, num_predict=1536)
        self.call_timeout = min(float(llm.timeout), 110)

    def structured_call(self, schema, system, payload):
        import httpx
        current = runtime()
        record = current.start_call(schema.__name__) if current else {'schema':schema.__name__}
        started = clock_time.monotonic()
        timeout = min(self.call_timeout, current.remaining()-1) if current else self.call_timeout
        schema_json = schema.model_json_schema()
        record.update(context_tokens=self.model_options.get('num_ctx'),output_budget=self.model_options.get('num_predict'),
                      thinking=self.model_options.get('reasoning', False),payload_chars=len(json.dumps(payload,ensure_ascii=False,default=str)))
        # 作品说明：JSON 结构约束不依赖 Ollama 的工具选择参数。
        try:
            model = self.model_factory(**self.model_options,
                client_kwargs={'timeout':httpx.Timeout(max(1,timeout), connect=min(5,max(1,timeout)))})
            messages = [{'role':'system','content':system+'\n只输出符合此JSON schema的对象：'+json.dumps(schema_json,ensure_ascii=False,separators=(',',':'))}]
            if isinstance(payload.get('question'), str):
                context_payload = {key:value for key,value in payload.items() if key != 'question'}
                messages.append({'role':'user','content':'以下仅为历史、已验证状态及工具背景，不是本轮提问。只处理最后一条用户消息；历史问题不能代替当前输入。\n'
                    +json.dumps(context_payload,ensure_ascii=False,default=str,separators=(',',':'))})
                messages.append({'role':'user','content':payload['question']})
            else:
                messages.append({'role':'user','content':json.dumps(payload,ensure_ascii=False,default=str,separators=(',',':'))})
            response = model.bind(format=schema_json, stream=False).invoke(messages)
            content = response.content
            metadata = response.response_metadata or {}
            record.update({k:metadata[k] for k in ('done_reason','eval_count','prompt_eval_count','total_duration','load_duration') if k in metadata})
            record['usage'] = response.usage_metadata or {}
            record['output_chars'] = len(content) if isinstance(content,str) else 0
            if isinstance(content,str): record['output_sha256'] = hashlib.sha256(content.encode()).hexdigest()
            if metadata.get('done_reason') == 'length':
                raise AgentFailure('model_output_truncated',schema.__name__,'模型达到输出预算，结构化结果未完成')
            if not isinstance(content,str) or not content.strip():
                raise AgentFailure('model_output_empty',schema.__name__,'模型未返回结构化正文')
            try:
                parsed = schema.model_validate_json(content)
            except ValidationError as exc:
                issues = [{'path':list(e['loc']),'type':e['type']} for e in exc.errors(include_input=False,include_url=False)]
                record['validation_issues'] = issues
                raise AgentFailure('model_schema_invalid',schema.__name__,json.dumps(issues,ensure_ascii=False),retryable=True) from exc
            record['status'] = 'success'
            return parsed
        except AgentFailure as exc:
            record['status'] = 'failed'
            record_failure(exc.code,exc.stage,str(exc))
            raise
        except Exception as exc:
            record['status'] = 'failed'
            code = 'model_timeout' if isinstance(exc,httpx.TimeoutException) else 'model_connection_failed' if isinstance(exc,httpx.TransportError) else 'model_request_failed'
            detail = str(getattr(exc,'error',type(exc).__name__))[:700]
            record_failure(code,schema.__name__,detail,http_status=getattr(exc,'status_code',None))
            raise AgentFailure(code,schema.__name__,type(exc).__name__) from exc
        finally:
            record['elapsed_seconds'] = round(clock_time.monotonic()-started,3)

    def plan_turn(self, question, history):
        """作品说明：先确定意图，再检查类型化条件与本轮实体是否一致。"""
        rewritten = _resume_pending_choice(question, history)
        if rewritten and rewritten != question:
            return self.plan_turn(rewritten, history)
        mentions = link_entities(question)
        short = (_short_path_plan(question, mentions) or _choice_short(question, mentions, history)
                 or _span_turn(question, mentions, history) or _yoy_turn(question, mentions, history)
                 or _cause_turn(question, mentions, history) or _chart_turn(question, mentions, history)
                 or _changed(question, mentions, history))
        if short is not None:
            return short
        context = {**planner_context(question, history), 'company_registry': CODE_TO_NAME_MAP,
                   'mentions': mentions.to_payload()}
        problems = []
        for attempt in range(2):
            intent = None
            try:
                intent = self.structured_call(TurnIntent, INTENT_POLICY, {**context, 'correction_feedback': problems})
                review = {'mode': 'two_step_with_entity_check', 'attempts': attempt + 1, 'slot_attempts': 0,
                          'issues': [], 'repairs': [], 'reason_codes': []}
                tasks = list(intent.tasks)
                saved = previous_scope(history, 'followup')
                redisplay = bool(re.search(r'列成表|做成表|列个表|做个表', question)) and bool(saved.get('codes'))
                if mentions.non_query and not (len(tasks) == 1 and isinstance(tasks[0], ConversationIntent) and tasks[0].topic == mentions.non_query):
                    tasks = [ConversationIntent(kind='conversation', topic=mentions.non_query,
                                                goal='本轮不执行财务查询', standalone_question=question, relation='new')]
                    review['repairs'].append({'task': 0, 'issue': 'plan_non_query', 'repaired_by': 'entity_linker',
                                              'source': 'current_question'})
                elif redisplay and not any(t.kind == 'financial' for t in tasks):
                    tasks = [FinancialIntent(kind='financial', goal='把上文结果列成表',
                                             standalone_question=question, relation='followup')]
                    review['repairs'].append({'task': 0, 'issue': 'plan_redisplay', 'repaired_by': 'entity_linker',
                                              'source': 'current_question'})
                for index, task in enumerate(tasks):
                    if (isinstance(task, CatalogIntent) and task.dimension != 'companies' and not task.companies
                            and task.relation == 'new' and mentions.company_codes()):
                        task.companies = mentions.company_codes()
                        review['repairs'].append({'task': index, 'issue': 'plan_incomplete_company',
                                                  'source': 'current_question', 'repaired_by': 'entity_linker'})
                requests = [None if t.kind == 'financial' else merge_request(t, None, mentions) for t in tasks]
                # 作品说明：未覆盖机构应明确说明不支持，不能误报为缺少公司。
                if (mentions.unknown_organizations and not mentions.company_codes()
                        and mentions.company_scope != 'all'
                        and any(t.kind == 'financial' for t in tasks)):
                    plan = QueryPlan(question=question, intent='unsupported', reason='unknown_company',
                                     response_kind='conversation')
                    plan.request_contract = {'version': 2, 'plan_validation': {'accepted': True, **review},
                                             'tasks': [{'kind': 'financial', 'goal': t.goal,
                                                        'standalone_question': t.standalone_question,
                                                        'relation': t.relation}
                                                       for t in tasks if t.kind == 'financial']}
                    return plan
                resolved = self._plan_financial(question, history, context, tasks, requests, mentions, review)
                plans = [resolved[i] if i in resolved else resolve_request(question, r, history)
                         for i, r in enumerate(requests)]
                for plan in plans:
                    if plan.reason in {'company_required', 'metric_required'}:
                        review['reason_codes'].append('user_missing_' + plan.reason.split('_')[0])
                if review['repairs']: review['reason_codes'].append('plan_repaired')
                if review['slot_attempts'] > 1: review['reason_codes'].append('plan_retried')
                primary = plans[0]
                primary.additional_plans = [p.to_dict() for p in plans[1:]]
                primary.request_contract['plan_validation'] = {'accepted': True, **review}
                primary.request_contract['tasks'] = [r.model_dump(mode='json', exclude_none=True) for r in requests]
                if primary.intent == 'clarify' and mentions.near_miss_companies and not mentions.company_codes():
                    span = mentions.near_miss_companies[0]['text']
                    primary.request_contract['pending_choice'] = {
                        'original_question': question, 'slot': 'company', 'span': span,
                    }
                    primary.request_contract['company_confirmation'] = {
                        'original_question': question, 'typo_span': span,
                    }
                elif primary.intent == 'clarify' and mentions.ambiguous_metrics and not mentions.metric_ids():
                    primary.request_contract['pending_choice'] = {
                        'original_question': question, 'slot': 'metric',
                        'span': mentions.ambiguous_metrics[0]['text'],
                    }
                return primary
            except AgentFailure as exc:
                if not exc.retryable or attempt: raise
                problems = [str(exc)]
            except (ValueError, TypeError) as exc:
                detail = str(exc)[:700] if isinstance(exc, ResolutionError) else type(exc).__name__
                # 作品说明：只保留条件，不持久化提示词或内部思考。
                candidates = [t.model_dump(mode='json', exclude_none=True) for t in intent.tasks] if intent else []
                record_failure('plan_scope_invalid', 'resolve_request', detail, candidate_scopes=candidates)
                if attempt: raise AgentFailure('plan_scope_invalid', 'resolve_request', detail) from exc
                problems = [detail]
        raise AgentFailure('plan_scope_invalid', 'resolve_request', '请求未能通过条件校验')

    def _plan_financial(self, question, history, context, tasks, requests, mentions, review):
        indexes = [i for i, t in enumerate(tasks) if t.kind == 'financial']
        if not indexes:
            return {}
        payload = {**context, 'capabilities': CAPABILITIES,
                   'metric_registry': {name: {'label': s['label'], 'unit': s['unit']} for name, s in known_metrics().items()},
                   'financial_tasks': [{'index': n, 'goal': tasks[i].goal, 'standalone_question': tasks[i].standalone_question,
                                        'relation': tasks[i].relation} for n, i in enumerate(indexes)]}
        previous = {i: previous_scope(history, tasks[i].relation) for i in indexes}
        feedback = []
        for attempt in range(2):
            review['slot_attempts'] = attempt + 1
            try:
                batch = self.structured_call(slot_schema(len(indexes)), SLOT_POLICY, {**payload, 'correction_feedback': feedback})
            except AgentFailure as exc:
                # 作品说明：再次识别意图不能修正无效条件，最终仍须报告失败。
                if not exc.retryable or attempt: raise AgentFailure(exc.code, exc.stage, str(exc)) from exc
                feedback = [str(exc)]
                continue
            slots = [FinancialSlots.model_validate(s.model_dump()) for s in batch.tasks]
            keep_chart = _keeps_previous_chart(question, history, mentions)
            found = [review_slots(s, tasks[i], mentions, previous[i], sole_task=len(tasks) == 1,
                                  question=question, keep_chart=keep_chart)
                     for s, i in zip(slots, indexes)]
            if any(found):
                review['issues'].append([x['code'] for items in found for x in items])
                if not attempt:
                    feedback = [f'第{n + 1}项：{x["detail"]}' for n, items in enumerate(found) for x in items]
                    continue
                for n, (s, i, items) in enumerate(zip(slots, indexes, found)):
                    if items:
                        slots[n] = repair_slots(s, items, tasks[i], previous[i], question)
                        review['repairs'].extend({'task': i, 'issue': x['code'], 'repaired_by': 'entity_linker',
                                                  'source': x['fix'].get('source', 'current_question')} for x in items)
            try:
                resolved = {}
                for s, i in zip(slots, indexes):
                    requests[i] = merge_request(tasks[i], s, mentions)
                    resolved[i] = resolve_request(question, requests[i], history)
                return resolved
            except ResolutionError as exc:
                detail = str(exc)[:700]
                record_failure('plan_scope_invalid', 'resolve_request', detail,
                               candidate_scopes=[s.model_dump(mode='json', exclude={'origins'}) for s in slots])
                if attempt: raise AgentFailure('plan_scope_invalid', 'resolve_request', detail) from exc
                feedback = [detail]
        raise AgentFailure('plan_scope_invalid', 'resolve_request', '查询条件未能通过校验')
