"""作品说明：发布完整工具结果，语言模型只组织有依据的解释文字。"""
from __future__ import annotations
import re
from typing import Literal
from pydantic import Field, create_model
from .chart_policy import CAPABILITIES
from .domain import CODE_TO_NAME_MAP
from .facts import field_specs
from .reply_text import capability_reply, is_greeting, is_identity_question
from .semantic_planner import StrictModel, planner_context
from .query_plan import normalize_model_text
from .turn_runtime import AgentFailure, record_failure, runtime


class Paragraph(StrictModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class AnswerDraft(StrictModel):
    lead: Paragraph
    explanation: list[Paragraph] = Field(default_factory=list, max_length=4)
    covered_tasks: list[int] = Field(default_factory=list, max_length=4, description='Zero-based contract task indexes addressed by this answer')


def answer_schema(allowed_ids, task_count, *, max_explanations=4):
    """作品说明：生成时限制可选引用标识，不能只在生成后检查。"""
    citation_type = list[Literal[tuple(sorted(allowed_ids))]] if allowed_ids else list[str]
    citations = Field(default_factory=list) if allowed_ids else Field(default_factory=list, max_length=0)
    paragraph = create_model('AnswerParagraph', __base__=Paragraph, evidence_ids=(citation_type, citations))
    return create_model('AnswerDraft', __base__=AnswerDraft,
        lead=(paragraph, ...), explanation=(list[paragraph], Field(default_factory=list, max_length=max_explanations)),
        covered_tasks=(list[Literal[tuple(range(task_count))]], Field(min_length=task_count, max_length=task_count)))


PERIODS = {'FY': '全年', 'HY': '上半年', 'Q1': '一季度', 'Q3': '前三季度累计'}
TOKEN = re.compile(r'\{\{([A-Za-z][A-Za-z0-9_]*(?:\.[a-z_]+)?)\}\}')


def fact_tokens(result):
    tokens = {}; records = []
    for i, fact in enumerate([*result.get('facts', []), *result.get('derived_facts', [])], 1):
        key = f'F{i}'
        unit = fact['unit']; value = float(fact['value'])
        target = fact.get('display_unit', (result.get('query_plan') or {}).get('unit'))
        if fact['field'] == 'eps' and unit == '元': unit = '元/股'
        if unit in {'元', '万元', '亿元'} and target in {'元', '万元', '亿元'}:
            scale = {'元': 1, '万元': 10000, '亿元': 100000000}
            value *= scale[unit] / scale[target]; unit = target
        calculation = fact.get('calculation') or (result.get('query_plan') or {}).get('calculation')
        metric = field_specs()[fact['field']]['label']
        if fact.get('source', {}).get('status') == 'derived':
            metric += {'yoy':'同比变化率','difference':'增减额','percentage_points':'百分点变化'}.get(calculation, '计算结果')
        fields = {'value': f'{value:,.4f}'.rstrip('0').rstrip('.') + unit,
                  'company': CODE_TO_NAME_MAP.get(fact['stock_code'], fact['stock_code']),
                  'year': str(fact['report_year']) + '年', 'period': PERIODS[fact['report_period']],
                  'metric': metric}
        for field, text in fields.items(): tokens[f'{key}.{field}'] = text
        tokens[key] = f"{fields['company']}{fields['year']}{fields['period']}{fields['metric']}为{fields['value']}"
        records.append({'id': key, 'fact_id': fact['fact_id'], **fields,
                        'formula': fact.get('formula'), 'inputs': fact.get('source_fact_ids', [])})
    return tokens, records


def render_paragraph(paragraph, tokens, allowed_ids, financial):
    if not set(paragraph.evidence_ids) <= allowed_ids:
        raise ValueError('段落引用了不存在的事实或原文')
    def replace(match):
        key = match.group(1)
        if key not in tokens: raise ValueError('未知数值引用：' + key)
        if key.split('.')[0] not in paragraph.evidence_ids:
            raise ValueError('数值引用未绑定段落证据')
        return tokens[key]
    without_tokens = TOKEN.sub('', paragraph.text)
    if financial and re.search(r'\d', without_tokens):
        raise ValueError('财务正文中的数字、年份须通过事实引用填入')
    text = normalize_model_text(TOKEN.sub(replace, paragraph.text))
    if '{{' in text or '}}' in text: raise ValueError('未解析的事实引用')
    if '|---' in text: raise ValueError('模型不能自行重建财务表格')
    return text


class AnswerComposer:
    def __init__(self, planner):
        self.planner = planner

    def compose(self, question, history, result):
        contract = result.get('request_contract') or {}
        canonical = result['answer']['content']
        if is_identity_question(question):
            result['answer']['content'] = capability_reply(greeting=is_greeting(question))
            result['answer_assessment'] = {'accepted': True, 'mode': 'capability_template', 'semantic_review': 'not_run',
                                            'tasks': result.get('task_results', []), 'attempts': 0}
            return result
        if result['outcome']['status'] == 'no_data':
            result['answer_assessment'] = {'accepted':True,'mode':'validated_tool_answer','semantic_review':'not_run',
                                            'tasks':result.get('task_results',[])}
            return result
        if result['outcome']['status'] in {'query_failed','needs_clarification','unsupported'} and not result.get('facts'):
            result['answer_assessment'] = {'accepted':True,'mode':'validated_status_message','semantic_review':'not_run',
                                            'tasks':result.get('task_results',[])}
            return result
        tasks = contract.get('tasks') or ([contract['request']] if contract.get('request') else [])
        task_results = result.get('task_results', [])
        if len(tasks) == 1 and tasks[0].get('kind') == 'conversation' and tasks[0].get('topic') == 'execution_scope' and canonical.strip():
            result['answer_assessment'] = {'accepted': True, 'mode': 'validated_tool_answer', 'semantic_review': 'not_run',
                                            'tasks': task_results, 'attempts': 0}
            return result
        # 作品说明：数值含义追问由程序组织事实文本，减少额外写作引入无来源数字或遗漏的风险。
        tool_only = bool(tasks) and all(task.get('kind') in {'catalog', 'financial'} for task in tasks)
        if tool_only and canonical.strip() and len(task_results) == len(tasks):
            result['answer_assessment'] = {
                'accepted': True, 'mode': 'validated_tool_answer', 'semantic_review': 'not_run',
                'covered_tasks': list(range(len(tasks))), 'tasks': task_results, 'attempts': 0,
            }
            return result
        tokens, facts = fact_tokens(result)
        references = [{'id': f'R{i}', 'text': r.get('text', '')[:1800],
                       'company': r.get('stock_code'), 'period': r.get('report_period'),
                       'year': r.get('report_year')} for i, r in enumerate(result['answer'].get('references', []), 1)]
        context = planner_context(question, history)
        context_evidence = [{'id': 'C1', 'kind': 'system_capabilities', 'content': CAPABILITIES}]
        if context['state'].get('last_execution'):
            context_evidence.append({'id':'S1', 'kind':'previous_execution_scope',
                                     'content':context['state']['last_execution']})
        allowed = {f['id'] for f in facts} | {r['id'] for r in references} | {r['id'] for r in context_evidence}
        expected_tasks = set(range(len(tasks) or 1))
        brief_help = len(tasks) == 1 and tasks[0].get('kind') == 'conversation' and tasks[0].get('topic') in {
            'complaint',
        }
        schema = answer_schema(allowed, len(expected_tasks), max_explanations=0 if brief_help else 4)
        payload = {**context, 'contract': contract, 'context_evidence': context_evidence,
                   'allowed_evidence_ids': sorted(allowed),
                   'capabilities': CAPABILITIES, 'facts': facts, 'substitutions': tokens,
                   'references': references, 'tool_summary': canonical,
                   'outcome': result.get('outcome'), 'chart_eligibility': result.get('chart_eligibility', []),
                   'catalog': result.get('catalog_result'), 'task_results': result.get('task_results', [])}
        financial = bool(facts) or result.get('response_kind') in {'financial','catalog'}
        feedback = []
        failure_code = 'answer_composition_incomplete'
        for attempt in range(2):
            try:
                draft = self.planner.structured_call(schema,
                    '你是面向财报学习者的助手，按给定JSON schema组织回答。先直接回答原问题，再适量解释。'
                    '简单问题两三句即可，复杂比较给结论和含义。不要只复述表格，不机械问“还有什么需要”。'
                    '长度跟随用户目标：规则确认先直接确认或纠正，再补必要区别；不要分段换词重复同一个意思。'
                    '只解释材料支持的内容，不替系统猜设计动机，不把数据缺失归因于未给出的发布延迟等原因。'
                    '举例优先使用已给定的执行范围，不随意另造年份；问操作条件时给具体可执行的条件。'
                    '单项功能说明集中写在lead，按需要用句子或列表一次说清，不在explanation重复。'
                    '说明工具条件时区分适用模式：趋势图看同公司同指标跨期；对比图可比较多家公司但报告期、指标、单位一致。'
                    'lead放直接结论，explanation放最多四段解释。工具结果随后由程序附上，不重写表格或图点。'
                    '若工具结果已经用一句话直接回答了单值查询，lead可留空，explanation只补有帮助的含义，避免重复。'
                    '金额、比率、公司数值、年份用substitutions中的{{F1.value}}等引用，evidence_ids绑定F1；'
                    '引用原文绑定R1等。公司财务变化、比较方向必须有事实或计算支持；不能把差异当原因。'
                    '功能规则引用C1，上轮实际执行范围引用S1（仅在提供时可用）；这些不是公司数字证据。'
                    'evidence_ids仅从allowed_evidence_ids选择，无需证据的普通解释可用空数组，不填写字段名或目录键。'
                    '引用编号仅写入evidence_ids，正文禁止出现C1/S1/F1/R1等内部编号；不要叙述“系统能力说明”等内部过程，直接回答用户。'
                    '原因证据不足要直接回应“目前不能确认是不是该原因”，不能只说原文在哪。'
                    '概念解释可用普通语言，不编造特定公司事实。功能说明以capabilities为准，别照搬上轮数据量当规则。'
                    '目录覆盖只按catalog或tool_summary声明的范围，不把某次查到的年份说成全库只有这些年份。'
                    '范围外问题友好说明主要帮查财报并给一个相关示例，不回答饮食等内容。'
                    '澄清只问真正缺少条件，不问已知公司。负数可以解释含义，不能凭负数直接断言经营不善。'
                    '所有子任务都要回应，covered_tasks列出已回应任务的零起始序号；已明确解释失败或缺失也算回应。'
                    '失败/无记录/NULL区别说明，不说partial/SQL/字段ID等内部术语。'
                    '财务与目录正文中不要自行写数字或年份；已有事实用引用，其余交给随后的程序表格和范围说明。',
                    {**payload, 'revision_feedback': feedback})
                lead = render_paragraph(draft.lead, tokens, allowed, financial)
                explanation = [render_paragraph(p, tokens, allowed, financial) for p in draft.explanation]
                # 作品说明：规范事实表与明确范围的缺失说明作为结果依据。
                content = '\n\n'.join(x for x in [lead, canonical if result.get('response_kind') != 'conversation' else '', *explanation] if x)
                if set(draft.covered_tasks) != expected_tasks or len(draft.covered_tasks) != len(expected_tasks):
                    raise ValueError('回答没有逐项对应本轮请求')
                if not content.strip(): raise ValueError('回答正文为空')
                result['answer']['content'] = content
                result['answer_assessment'] = {'accepted': True, 'attempts': attempt + 1,
                    'mode':'structural_and_evidence','semantic_review':'not_run',
                    'covered_tasks':draft.covered_tasks,'tasks':result.get('task_results',[])}
                return result
            except Exception as exc:
                # 作品说明：诊断只记录异常类别和校验反馈，不保存模型内部推理。
                feedback = [str(exc)[:700] if isinstance(exc, ValueError) else type(exc).__name__]
                failure_code = exc.code if isinstance(exc,AgentFailure) else 'answer_validation_failed'
                if not isinstance(exc,AgentFailure): record_failure('answer_validation_failed','answer',feedback[0])
                if isinstance(exc,AgentFailure) and not exc.retryable: break
                if runtime() and len(runtime().calls)>=runtime().max_calls: break
        # 作品说明：事实表由程序生成作为正式回答，失败的解释文字不发布。
        result['answer']['content'] = canonical
        if result.get('facts') or result.get('catalog_result'):
            result['answer_assessment'] = {
                'accepted': True, 'attempts': attempt + 1, 'mode': 'validated_tool_answer',
                'semantic_review': 'not_run', 'commentary_dropped': True,
                'issues': feedback, 'tasks': result.get('task_results', []),
            }
            return result
        result['answer_assessment'] = {'accepted': False, 'attempts': attempt+1, 'issues': feedback,
                                       'tasks': result.get('task_results', [])}
        if result.get('outcome', {}).get('status') == 'answered':
            result['outcome'] = {'status': 'query_failed', 'reason_codes': ['answer_composition_failed',failure_code]}
            result['answer']['content'] = '这次没有完整组织好回答，请稍后重试。'
        return result
