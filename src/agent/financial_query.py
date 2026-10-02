"""作品说明：执行已校验财务计划，发布有证据的类型化结果。"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import math
import re
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.config import get_stream_writer
from loguru import logger

from sqlalchemy import bindparam, text
from sqlalchemy.dialects import mysql

from .domain import ALLOWED_TABLES, CODE_TO_NAME_MAP, field_table, get_table_fields
from .facts import bind_sql_rows, build_facts, document_identity, evidence_id, field_specs, metadata_matches
from .query_plan import QueryPlan
from .semantic_planner import SemanticPlanner, conversation_state
from .chart_policy import MIN_VALID_POINTS
from .answer_composer import AnswerComposer
from .reply_text import (
    capability_reply, catalog_company_intro, catalog_company_outro,
    catalog_metric_note, catalog_report_note, clarify_request, execution_scope_reply,
    is_greeting, is_identity_question, no_data_reply, reading_suffix, source_page_lines, unsupported_reply,
)
from .turn_runtime import AgentFailure, record_failure, runtime, turn_runtime
from .sql_guard import query_lineage
from .verifier import bind_chart_source, build_evidence, verify_charts, verify_references

PERIOD_LABELS={'FY':'全年','HY':'上半年','Q1':'一季度','Q3':'前三季度'}
_query_cache = ContextVar('financial_query_cache', default=None)


@dataclass(frozen=True)
class PreparedQuery:
    table: str
    sql: str
    params: dict[str,Any]
    purpose: str
    fields: tuple[str,...] = ()

    def display_sql(self):
        statement=text(self.sql).bindparams(*[bindparam(k,v) for k,v in self.params.items()])
        return str(statement.compile(dialect=mysql.dialect(),compile_kwargs={'literal_binds':True}))


def compile_query(table, codes, pairs=(), fields=(), period=None, purpose='facts'):
    if table not in ALLOWED_TABLES or any(f not in get_table_fields()[table] for f in fields):
        raise ValueError('Unknown table or field')
    params={}; conditions=[]
    if codes:
        names=[]
        for i,code in enumerate(codes):
            if not re.fullmatch(r'\d{6}',str(code)): raise ValueError('Invalid company')
            name=f'code{i}'; params[name]=str(code); names.append(':'+name)
        conditions.append('stock_code IN ('+', '.join(names)+')')
    if pairs:
        terms=[]
        for i,(year,p) in enumerate(pairs):
            if not isinstance(year,int) or isinstance(year,bool) or not 2000<=year<=2100 or p not in PERIOD_LABELS:
                raise ValueError('Invalid report identity')
            params[f'year{i}']=year; params[f'period{i}']=p
            terms.append(f'(report_year=:year{i} AND report_period=:period{i})')
        conditions.append('('+' OR '.join(terms)+')')
    elif period:
        if period not in PERIOD_LABELS: raise ValueError('Invalid period')
        params['period']=period; conditions.append('report_period=:period')
    columns=['stock_code','stock_abbr','report_year','report_period']+list(fields)
    distinct='DISTINCT ' if purpose=='coverage' else ''
    sql='SELECT '+distinct+', '.join(columns)+' FROM '+table
    if conditions: sql+=' WHERE '+' AND '.join(conditions)
    sql+=' ORDER BY stock_code, report_year, report_period LIMIT 500'
    return PreparedQuery(table,sql,params,purpose,tuple(fields))


class FinancialRepository:
    def __init__(self, sql_tool):
        self.sql_tool=sql_tool

    def execute(self, query: PreparedQuery):
        """作品说明：SQL 来自程序编译模板，执行时绑定参数。"""
        from src.utils.provenance import attach_provenance
        import pandas as pd
        display=query.display_sql()
        try:
            engine=self.sql_tool._get_engine()
            with engine.connect() as conn:
                if engine.dialect.name=='mysql':
                    conn.execute(text('SET SESSION MAX_EXECUTION_TIME = 5000'))
                    conn.execute(text('SET SESSION TRANSACTION READ ONLY'))
                df=pd.read_sql_query(text(query.sql),conn,params=query.params)
                if len(df)>=500:
                    return {'status':'error','reason':'result_limit','rows':[],'sql':display}
                rows=json.loads(df.to_json(orient='records',force_ascii=False))
                lineage=query_lineage(display)
                if query.purpose=='facts':
                    rows=attach_provenance(rows,lineage['column_lineage'],lineage['scope'],connection=conn)
            return dict(status='success' if rows else 'empty',sql=display,rows=rows,
                        columns=list(df.columns),**lineage)
        except Exception as exc:
            # 作品说明：执行失败不泄露驱动或凭据，也不转查无关数据。
            return dict(status='error',reason=type(exc).__name__,sql=display,rows=[])


def number(value):
    return f'{float(value):,.4f}'.rstrip('0').rstrip('.')


def cell_text(value):
    return str(value).replace('|','\\|').replace('\r',' ').replace('\n',' ')


def markdown_table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join('---' for _ in headers)+'|']+
                     ['| '+' | '.join(cell_text(v) for v in row)+' |' for row in rows])


def identity(fact):
    return (str(fact['stock_code']),int(fact['report_year']),str(fact['report_period']),str(fact['field']))


def _picture_company(plan, history) -> str:
    """作品说明：拒答示例优先使用页面当前公司。"""
    codes = [code for code in (plan.codes or []) if code in CODE_TO_NAME_MAP]
    if not codes:
        saved = conversation_state(history).get('active_request') or {}
        codes = [code for code in (saved.get('codes') or []) if code in CODE_TO_NAME_MAP]
    return CODE_TO_NAME_MAP.get(codes[0], '') if codes else ''


def calculate(plan, facts):
    derived=[]; notes=[]
    if plan.calculation not in {'difference','yoy','percentage_points'}: return derived,notes
    if len({p for y,p in plan.pairs})!=1:
        return [],['报告期口径不同，已分别列出原始数值，不计算增长率或差额。']
    index={identity(f):f for f in facts}
    years=sorted({y for y,p in plan.pairs})
    if len(years)<2: return [],['缺少可比较的基期，未计算变化值。']
    pairs=[(years[i], years[i + 1]) for i in range(len(years) - 1)] if plan.calculation=='yoy' else [(years[0],years[-1])]
    period=plan.pairs[0][1]
    for code in plan.codes:
        for field in plan.metrics:
            for first,last in pairs:
                a=index.get((code,first,period,field)); b=index.get((code,last,period,field))
                if not a or not b:
                    notes.append('部分指标缺少同口径基期或本期值，未计算对应变化值。'); continue
                stored_growth = str(field).endswith(('_yoy_growth', '_qoq_growth'))
                if plan.calculation=='yoy' and a['unit']=='%' and stored_growth:
                    notes.append('百分比指标不能再次计算同比增长率；如需比较两期比率，请指定百分点差。'); continue
                if plan.calculation=='percentage_points' and a['unit']!='%':
                    notes.append('该指标不是百分比，不能计算百分点差。'); continue
                start,end=Decimal(str(a['value'])),Decimal(str(b['value']))
                if plan.calculation=='yoy' and a['unit']!='%' and start==0:
                    notes.append('基期为零，不能计算同比增长率。'); continue
                if plan.calculation=='yoy' and a['unit']=='%':
                    result=end-start; unit='个百分点'; formula='current - previous'; step='percentage_points'
                    notes.append('百分比指标的同比按相邻年份的百分点差计算，不再套一次增长率。')
                elif plan.calculation=='yoy':
                    result=(end-start)/abs(start)*100; unit='%'; formula='(current - previous) / abs(previous) * 100'
                    step='yoy'
                    if start<0: notes.append('负基期同比使用基期绝对值作为分母。')
                else:
                    result=end-start; unit='个百分点' if a['unit']=='%' else a['unit']; formula='current - previous'
                    step=plan.calculation
                derived.append({**b,'fact_id':evidence_id('derived',a['fact_id'],b['fact_id'],step),
                                'value':float(result),'unit':unit,'calculation':step,
                                'source_fact_ids':[a['fact_id'],b['fact_id']], 'formula':formula,
                                'base_year':first,'source':{'status':'derived'}})
    return derived,list(dict.fromkeys(notes))


def compare_company_facts(plan, facts):
    """作品说明：跨公司比较要求全部对象具备相同指标及单位的事实。"""
    if len(plan.codes)<2:
        return []
    indexed={identity(f):f for f in facts}
    notes=[]
    for year,period in plan.pairs:
        for field in plan.metrics:
            group=[indexed.get((code,year,period,field)) for code in plan.codes]
            if any(f is None for f in group):
                continue
            label=f'{year}年{PERIOD_LABELS[period]}{field_specs()[field]["label"]}'
            if len({f['unit'] for f in group})!=1:
                notes.append(f'{label}的单位不同，不能直接比较。')
                continue
            try:
                values=[Decimal(str(f['value'])) for f in group]
            except (InvalidOperation,TypeError,ValueError):
                continue
            if not all(value.is_finite() for value in values):
                continue
            high=max(values)
            winners=[CODE_TO_NAME_MAP.get(code,code) for code,value in zip(plan.codes,values) if value==high]
            if len(plan.codes)==2:
                if len(winners)==2:
                    notes.append(f'{label}：两家公司持平。')
                else:
                    loser_code=plan.codes[1-values.index(high)]
                    loser=CODE_TO_NAME_MAP.get(loser_code,loser_code)
                    notes.append(f'{label}：{winners[0]}更高，高于{loser}。')
            else:
                notes.append(f'{label}：'+ '、'.join(winners)+('并列最高。' if len(winners)>1 else '最高。'))
    return notes


class AgentState(TypedDict, total=False):
    question: str
    history: list[dict]
    plan: QueryPlan
    result: dict
    parts: list[dict]


class FinancialQueryService:
    """作品说明：同一计划的工具结果独立记录，通过统一边界发布。"""
    def __init__(self,llm,repository,rag_tool=None):
        self.llm=llm; self.repository=repository; self.rag_tool=rag_tool
        self.planner = None

    def run(self,question,history=()):
        graph = StateGraph(AgentState)
        graph.add_node('understand_and_validate', self._plan_node)
        graph.add_node('respond_without_tools', self._response_node)
        graph.add_node('execute_financial_tools', self._execution_node)
        graph.add_node('compose_and_validate_answer', self._answer_node)
        graph.add_node('publish', self._publish_node)
        graph.add_edge(START, 'understand_and_validate')
        graph.add_conditional_edges('understand_and_validate', self._route,
            {'direct':'respond_without_tools','tools':'execute_financial_tools','failed':'publish'})
        graph.add_edge('respond_without_tools','compose_and_validate_answer')
        graph.add_edge('execute_financial_tools','compose_and_validate_answer')
        graph.add_edge('compose_and_validate_answer','publish')
        graph.add_edge('publish',END)
        token = _query_cache.set({})
        try:
            with turn_runtime():
                for event in graph.compile().stream({'question':question,'history':list(history)}, stream_mode='custom'):
                    yield event
        finally:
            _query_cache.reset(token)

    def _plan_node(self,state):
        get_stream_writer()(('plan',{'label':'理解问题','detail':'正在理解本轮目的和对话上下文'}))
        try:
            self.planner = self.planner or (self.llm if hasattr(self.llm, 'plan_turn') else SemanticPlanner(self.llm))
            return {'plan':self.planner.plan_turn(state['question'], state['history'])}
        except Exception as exc:
            code = exc.code if isinstance(exc,AgentFailure) else 'planning_internal_error'
            if not isinstance(exc,AgentFailure): record_failure(code,'planning',type(exc).__name__)
            return {'result':self._finish(QueryPlan(state['question']),[],[],[],'query_failed', ['intent_service_failed'],
                                    '本轮未能完成问题理解，尚未执行财务查询。请稍后重试；这不代表库中没有数据。',
                                    failure_code=code)}

    @staticmethod
    def _route(state):
        if 'result' in state: return 'failed'
        return 'tools' if state['plan'].intent in {'facts','explanation','coverage_companies','coverage_periods'} or (state['plan'].intent=='coverage_metrics' and state['plan'].codes) else 'direct'

    def _response_node(self,state):
        if self._route(state) != 'direct': raise ValueError('Invalid direct response route')
        return self._execution_node(state)

    def _execution_node(self,state):
        plans = [state['plan'], *[QueryPlan(**p) for p in state['plan'].additional_plans]]
        parts = []
        for plan in plans:
            final = None
            try:
                for event, payload in self.execute_plan(plan, state['history']):
                    if event == 'done': final = payload['result']
                    elif event not in {'answer_delta','chart','references','clarify'}:
                        get_stream_writer()((event,payload))
            except Exception as exc:
                record_failure('task_execution_failed','tools',type(exc).__name__)
                final = self._finish(plan, [], [], [], 'query_failed', ['task_execution_failed'],
                                     '该项查询未完成，不能据此判断资料是否存在。')
            if final is None: raise RuntimeError('Execution produced no result')
            for fact in [*final.get('facts', []), *final.get('derived_facts', [])]:
                fact['display_unit'] = plan.unit
            parts.append(final)
        result = deepcopy(parts[0])
        result['query_plan']['additional_plans'] = [deepcopy(p['query_plan']) for p in parts[1:]]
        result['task_results'] = [{'goal': p.get('request_contract',{}).get('goal', state['question']),
                                   **p['outcome']} for p in parts]
        if len(parts)>1:
            result['answer']['content'] = '\n\n'.join(p['answer']['content'] for p in parts)
            for key in ('facts','derived_facts','evidence','chart_data_list','chart_eligibility','execution_plan'):
                result[key] = [v for p in parts for v in p.get(key,[])]
            result['answer']['references'] = [v for p in parts for v in p['answer'].get('references',[])]
            result['sql'] = ';\n'.join(p['sql'] for p in parts if p['sql']!='-') or '-'
            result['chart_data'] = next(iter(result['chart_data_list']), None)
            result['chart_format'] = '图表' if result['chart_data_list'] else '无'
            result['response_kind'] = 'financial' if any(p['response_kind']=='financial' for p in parts) else 'catalog' if any(p['response_kind']=='catalog' for p in parts) else 'conversation'
            states = {p['outcome']['status'] for p in parts}
            result['outcome'] = {'status': next(iter(states)) if len(states)==1 else 'partial',
                                  'reason_codes': list(dict.fromkeys(v for p in parts for v in p['outcome']['reason_codes']))}
            result['needs_clarification'] = any(p['needs_clarification'] for p in parts)
            result['clarify_options'] = next((p['clarify_options'] for p in parts if p['clarify_options']), [])
            result['validation']['sql_events'] = [v for p in parts for v in p['validation']['sql_events']]
            result['verification']['checks'] = [v for p in parts for v in p['verification']['checks']]
            result['verification']['status'] = 'fail' if any(p['verification']['status']=='fail' for p in parts) else 'warn' if any(p['verification']['status']=='warn' for p in parts) else 'pass'
            catalogs = [p['catalog_result'] for p in parts if p.get('catalog_result')]
            if catalogs: result['catalog_result'] = catalogs[-1]
        return {'result': result, 'parts': parts}

    def _answer_node(self,state):
        result = state['result']
        if hasattr(self.planner, 'structured_call'):
            get_stream_writer()(('plan',{'label':'整理回答','detail':'正在组织结论并检查是否回应了各项问题'}))
            try:
                result = AnswerComposer(self.planner).compose(state['question'], state['history'], result)
            except Exception as exc:
                record_failure('answer_internal_error','answer',type(exc).__name__)
                result['answer_assessment'] = {'accepted':False,'issues':['answer_internal_error']}
                if result['outcome']['status']=='answered':
                    result['outcome']={'status':'partial','reason_codes':['answer_internal_error']}
                result['answer']['content'] += '\n\n已保留本轮工具结果，进一步解释暂未完成。'
        else:
            result['answer_assessment'] = {'accepted': False, 'mode': 'execution_fixture'}
        return {'result':result}

    @staticmethod
    def _publish_node(state):
        result=state['result']; previous=conversation_state(state['history'])
        dialogue = deepcopy(previous)
        turn_id = runtime().id if runtime() else evidence_id('turn', len(state['history']), state['question'])
        dialogue.update(version=2, turn_id=turn_id, outcome=result['outcome'])
        for part in state.get('parts', []):
            p = part['query_plan']; request = part.get('request_contract',{}).get('request',{})
            dialogue['last_intent'] = p['intent']
            if request.get('topic') == 'out_of_scope':
                dialogue['suspended'] = True
                dialogue['pending_question'] = None; dialogue['pending_request'] = None
            if p['intent'] in {'facts','explanation','coverage_periods','coverage_metrics','clarify'} or p.get('reason')=='single_quarter_not_supported':
                if request.get('relation') == 'new':
                    dialogue['pending_request'] = None; dialogue['pending_question'] = None
                    if p['intent'] in {'facts','explanation'}: dialogue['active_request'] = None
                accepted = p.get('request_contract',{}).get('plan_validation',{}).get('accepted', True)
                scope = {k:p[k] for k in ('intent','codes','metrics','pairs','period','time_mode','latest_count','calculation','unit')}
                if p.get('request_contract',{}).get('requested_pairs'):
                    scope['pairs'] = p['request_contract']['requested_pairs']
                scope.update(turn_id=turn_id, origins=p.get('origins',{}))
                if accepted and p['codes']:
                    dialogue['suspended'] = False
                    if p['intent']=='clarify' or p.get('clarification') or p.get('reason')=='single_quarter_not_supported':
                        dialogue['pending_request'] = scope
                    elif p['intent'].startswith('coverage_'):
                        dialogue['last_catalog_request'] = scope
                    else:
                        dialogue['active_request'] = scope; dialogue['pending_request'] = None
                        dialogue['pending_question'] = None
            if part.get('needs_clarification'):
                pending = {'turn_id':turn_id, 'question':p.get('clarification') or part['answer']['content'],
                           'options':[{'id':str(i),'label':label} for i,label in enumerate(part['clarify_options'],1)]}
                contract = part.get('request_contract') or {}
                choice = contract.get('pending_choice') or {}
                confirm = contract.get('company_confirmation') or {}
                if choice.get('original_question') and choice.get('span'):
                    pending['original_question'] = choice['original_question']
                    pending['span'] = choice['span']
                    pending['slot'] = choice.get('slot') or ''
                    if choice.get('slot') == 'company':
                        pending['typo_span'] = choice['span']
                elif confirm.get('original_question') and confirm.get('typo_span'):
                    pending['original_question'] = confirm['original_question']
                    pending['typo_span'] = confirm['typo_span']
                    pending['span'] = confirm['typo_span']
                    pending['slot'] = 'company'
                dialogue['pending_question'] = pending
            sql_events = part.get('validation',{}).get('sql_events',[])
            if sql_events:
                dialogue['last_execution'] = {'turn_id':turn_id, 'outcome':part['outcome'],
                    'requested_scope':{'codes':p['codes'],'pairs':p['pairs'],'metrics':p['metrics']},
                    'queries':[{'query_id':e['query_id'],'status':e['status'],'purpose':e['purpose'],
                                'requested_scope':e.get('requested_scope',{}),'row_count':e['row_count']} for e in sql_events]}
                dialogue['chart_eligibility'] = part.get('chart_eligibility',[])
            if part.get('catalog_result'):
                dialogue['last_catalog'] = {**part['catalog_result'], 'turn_id':turn_id}
        if not state.get('plan'):
            dialogue['last_intent'] = 'planning_failed'
        elif (result.get('query_plan') or {}).get('intent'):
            dialogue['last_intent'] = result['query_plan']['intent']
        if result.get('catalog_result'):
            dialogue['last_catalog'] = {**result['catalog_result'], 'turn_id': turn_id}
        all_queries = [q for part in state.get('parts', []) for q in part.get('validation',{}).get('sql_events',[])]
        if all_queries:
            dialogue['last_execution'] = {'turn_id':turn_id,'outcome':result['outcome'],
                'queries':[{'query_id':q['query_id'],'status':q['status'],'purpose':q['purpose'],
                            'requested_scope':q.get('requested_scope',{}),'row_count':q['row_count']} for q in all_queries]}
            dialogue['chart_eligibility'] = result.get('chart_eligibility',[])
        result['dialogue_state'] = dialogue
        if runtime(): result['diagnostics'] = runtime().summary()
        if result['outcome']['status']=='query_failed': result['verification']['status']='fail'
        elif result['outcome']['status']!='answered' and result['verification']['status']=='pass':
            result['verification']['status']='warn'
        result['validation']['status'] = result['verification']['status']
        result['validation'].update(outcome=result['outcome'], facts=result.get('facts',[]),
                                    evidence=result.get('evidence',[]), verification=result['verification'])
        result['verification']['summary'] = {'passed':sum(c['status']=='pass' for c in result['verification']['checks']),
            'warnings':sum(c['status']=='warn' for c in result['verification']['checks']),
            'failed':sum(c['status']=='fail' for c in result['verification']['checks'])}
        writer = get_stream_writer()
        if result.get('needs_clarification'):
            writer(('clarify',{'question':result['answer']['content'],'options':result['clarify_options']}))
        for chart in result.get('chart_data_list',[]): writer(('chart',{'chart_data':chart,'title':chart['title'],'path':''}))
        if result['answer'].get('references'): writer(('references',{'items':result['answer']['references']}))
        writer(('answer_delta',{'text':result['answer']['content']}))
        get_stream_writer()(('done',{'result':result}))
        return {'result':result}

    def execute_plan(self, plan, history=()):
        question = plan.question
        events=[]; facts=[]; references=[]; charts=[]; reasons=[]; notes=[]
        if plan.intent=='conversation_scope':
            execution=conversation_state(history).get('last_execution')
            content=execution_scope_reply((execution or {}).get('queries'))
            yield 'done',{'result':self._finish(plan,events,[],[],'answered',[],content)}
            return
        if plan.intent in {'clarify','unsupported','greeting','help','capabilities'} or (plan.intent=='coverage_metrics' and not plan.codes):
            if plan.intent=='clarify':
                status='needs_clarification'; content=plan.clarification or clarify_request()
            elif plan.intent=='unsupported':
                status='unsupported'
                content=unsupported_reply(plan.reason, plan.question, _picture_company(plan, history))
            elif plan.intent=='coverage_metrics':
                status='answered'; content=markdown_table(['指标','单位'],[(s['label'],s['unit']) for s in field_specs().values()])
                content+='\n\n'+catalog_metric_note(CODE_TO_NAME_MAP.get(plan.codes[0], '') if plan.codes else '')
            elif is_identity_question(plan.question) or plan.intent in {'greeting','capabilities'}:
                status='answered'; content=capability_reply(greeting=is_greeting(plan.question) or plan.intent=='greeting')
            else:
                status='answered'; content=plan.direct_reply or '可以查询财报数字、比较报告期、生成图表并查找原文证据。你可以直接说公司、年份和指标。'
            result=self._finish(plan,events,facts,references,status,[plan.reason] if plan.reason else [],content)
            if status=='needs_clarification': yield 'clarify',{'question':content,'options':plan.options}
            yield 'answer_delta',{'text':content}
            yield 'done',{'result':result}; return

        coverage=[]
        need_coverage=bool(plan.latest_count) or plan.all_companies or plan.intent.startswith('coverage_')
        if need_coverage:
            for table in sorted(ALLOWED_TABLES):
                query=compile_query(table,plan.codes,plan.pairs if plan.intent=='coverage_periods' else (),
                                    period=plan.period if plan.latest_count else plan.coverage_period_filter,purpose='coverage')
                yield 'tool_call',{'tool':'query_database','label':'核对已入库报告范围','detail':query.display_sql()}
                result=self._execute(query,events)
                yield 'tool_result',self._tool_event(events[-1])
                coverage.extend(result.get('rows') or [])
            if any(e['status'] not in {'success','empty'} for e in events):
                content='本轮报告范围查询未完成，暂不能确认有哪些年份或报告。请稍后重试。'
                yield 'done',{'result':self._finish(plan,events,[],[],'query_failed',['coverage_query_failed'],content)}; return
            unique={(str(r['stock_code']),int(r['report_year']),str(r['report_period'])):r for r in coverage}
            if plan.all_companies:
                plan.codes=sorted({c for c,y,p in unique})
                if not plan.codes:
                    yield 'done',{'result':self._finish(plan,events,[],[],'no_data',[],'当前问答库没有可用公司记录。')}; return
            if plan.intent=='coverage_companies':
                companies=sorted({(str(r['stock_code']),str(r['stock_abbr'])) for r in coverage})
                plan.request_contract['catalog_result']={'dimension':'companies','codes':[c for c,n in companies],
                                                         'companies':[{'code':c,'name':n} for c,n in companies],'complete':True}
                content=(catalog_company_intro()+'\n\n'+
                         markdown_table(['公司','股票代码'],[(name,code) for code,name in companies])+
                         '\n\n'+catalog_company_outro(companies[0][1] if companies else '')) if companies else '当前问答库没有可用公司记录。'
                yield 'done',{'result':self._finish(plan,events,[],[],'answered' if companies else 'no_data',[],content)}; return
            if plan.latest_count:
                years_by_code=[{y for c,y,p in unique if c==code and p==plan.period} for code in plan.codes]
                common=set.intersection(*years_by_code) if years_by_code else set()
                years=sorted(common,reverse=True)[:plan.latest_count]
                plan.pairs=[(y,plan.period) for y in sorted(years)]
                notes.append('按当前问答库实际收录的'+('共同' if len(plan.codes)>1 else '')+'年份查询：'+('、'.join(map(str,sorted(years))) or '没有可用年份')+'。')
                if len(years)<plan.latest_count: reasons.append('insufficient_available_years')
                if not years:
                    yield 'done',{'result':self._finish(plan,events,[],[],'no_data',reasons,'未查到符合要求的共同报告年份。')}; return
            if plan.intent=='coverage_periods':
                rows=[(str(r['stock_abbr']),y,PERIOD_LABELS[p]) for (c,y,p),r in sorted(unique.items()) if not plan.pairs or (y,p) in plan.pairs]
                plan.request_contract['catalog_result']={'dimension':'reports','codes':plan.codes,
                    'reports':[{'code':c,'year':y,'period':p} for c,y,p in sorted(unique) if not plan.pairs or (y,p) in plan.pairs],
                    'scope':plan.scope(),'complete':True}
                content=(catalog_report_note()+'\n\n'+markdown_table(['公司','年份','报告期'],rows)) if rows else '当前查询范围内没有问答库记录。'
                if plan.latest_count: content='\n\n'.join([*notes,content])
                yield 'done',{'result':self._finish(plan,events,[],[],'answered' if rows else 'no_data',[],content)}; return

        if plan.intent=='coverage_metrics':
            available=set(); failed=False
            for table in sorted(ALLOWED_TABLES):
                fields=[f for f in field_specs() if field_table(f)==table]
                query=compile_query(table,plan.codes,plan.pairs,fields,period=plan.coverage_period_filter)
                yield 'tool_call',{'tool':'query_database','label':'查询可用指标范围','detail':query.display_sql()}
                response=self._execute(query,events)
                yield 'tool_result',self._tool_event(events[-1])
                if response['status'] not in {'success','empty'}: failed=True
                available.update(f for row in response.get('rows',[]) for f in fields if row.get(f) is not None)
            plan.request_contract['catalog_result']={'dimension':'metrics','codes':plan.codes,'metrics':sorted(available),
                                                     'scope':plan.scope(),'complete':not failed}
            content=(markdown_table(['可用指标','单位'],[(field_specs()[f]['label'],field_specs()[f]['unit']) for f in sorted(available)])+
                     '\n\n'+catalog_metric_note(CODE_TO_NAME_MAP.get(plan.codes[0], '') if plan.codes else '')) if available else '当前查询范围没有取得可用指标。'
            if notes: content='\n\n'.join([*notes,content])
            if failed: content+='\n\n部分范围查询未完成，以上不是完整指标目录。'
            yield 'done',{'result':self._finish(plan,events,[],[],'partial' if failed and available else 'query_failed' if failed else 'answered' if available else 'no_data', ['coverage_query_failed'] if failed else [],content)}
            return

        if not plan.request_contract.get('requested_pairs'):
            plan.request_contract['requested_pairs'] = list(plan.pairs)
        if plan.default_time:
            notes.insert(0,'你没有指定年份，这里采用最近一份已入库的'+PERIOD_LABELS[plan.period]+'报告。')
        if len(plan.pairs)==1 and (plan.calculation=='yoy' or plan.overview):
            year,period=plan.pairs[0]
            plan.request_contract['comparison_base_added']=[year-1,period]
            plan.pairs=[(year-1,period),(year,period)]
            if plan.overview: plan.comparison='time'
        plan.request_contract['resolved_scope']=plan.scope()
        groups=defaultdict(list)
        for metric in plan.metrics: groups[field_table(metric)].append(metric)
        for table,fields in sorted(groups.items()):
            query=compile_query(table,plan.codes,plan.pairs,fields)
            yield 'tool_call',{'tool':'query_database','label':'查询'+ '、'.join(field_specs()[f]['label'] for f in fields),'detail':query.display_sql()}
            result=self._execute(query,events)
            yield 'tool_result',self._tool_event(events[-1])
            if result['status']=='success':
                facts.extend(build_facts(events[-1]['rows']))
        # 作品说明：发布范围校验独立于 SQL 编译检查。
        if any(not metadata_matches(f,plan.scope()) for f in facts):
            yield 'done',{'result':self._finish(plan,events,[],[],'query_failed',['out_of_scope_result'],'查询返回的公司或报告期不符合请求，已停止发布本轮数据。')}; return
        index={}; conflicts=set()
        for fact in facts:
            key=identity(fact)
            if key in index and (index[key]['value'],index[key]['unit'])!=(fact['value'],fact['unit']): conflicts.add(key)
            index[key]=fact
        if conflicts:
            yield 'done',{'result':self._finish(plan,events,[],[],'query_failed',['conflicting_facts'],'本轮数据存在相同指标数值冲突，暂不发布。')}; return
        facts=list(index.values())
        rows=[]; missing=False
        for code in plan.codes:
            for year,period in plan.pairs:
                for metric in plan.metrics:
                    fact=index.get((code,year,period,metric))
                    label=field_specs()[metric]['label']
                    if fact:
                        value,unit=self._display_value(fact,plan.unit)
                        display=number(value)+unit
                    else:
                        missing=True
                        queries=[e for e in events if metric in e.get('requested_fields',[])]
                        if not queries or any(e['status'] not in {'success','empty'} for e in queries):
                            display='本轮查询失败'; reasons.append('metric_query_failed')
                        elif any(str(r.get('stock_code'))==code and int(r.get('report_year',0))==year and r.get('report_period')==period for e in queries for r in e.get('rows',[])):
                            display='当前库中无可用值'; reasons.append('metric_value_missing')
                        else:
                            display='当前查询范围内无记录'; reasons.append('report_not_found')
                    rows.append((CODE_TO_NAME_MAP.get(code,code),f'{year}年',PERIOD_LABELS[period],label,display))
        if plan.calculation=='ranking' and len(plan.metrics)==1:
            positions={(r[0],r[1],r[2]):i for i,r in enumerate(rows)}
            values={(CODE_TO_NAME_MAP.get(f['stock_code'],f['stock_code']),f"{f['report_year']}年",PERIOD_LABELS[f['report_period']]):f['value'] for f in facts}
            rows.sort(key=lambda r:values.get(r[:3],-math.inf),reverse=True)
        if len(rows)==1 and facts:
            c,y,p,m,v=rows[0]; content=f'{c}{y}{p}{m}为 {v}。'
        elif rows: content=markdown_table(['公司','年份','报告期','指标','数值'],rows)
        else: content=''
        derived,calculation_notes=calculate(plan,facts)
        notes.extend(calculation_notes)
        if calculation_notes: reasons.append('calculation_notice')
        if derived:
            calculation_rows=[]
            for f in derived:
                value,unit=self._display_value(f,plan.unit)
                calculation_rows.append((CODE_TO_NAME_MAP.get(f['stock_code'],f['stock_code']),f"{f['base_year']}→{f['report_year']}",PERIOD_LABELS[f['report_period']],field_specs()[f['field']]['label'],number(value)+unit))
            content+='\n\n'+('相邻年份的百分点差' if derived[0].get('calculation')=='percentage_points' and plan.calculation=='yoy' else '同比变化（以基期绝对值为分母）' if plan.calculation=='yoy' else '本期减基期的变化值')+'：\n\n'+markdown_table(['公司','比较年份','报告期','指标','变化值'],calculation_rows)
            if plan.calculation=='difference' and len(derived)==1:
                item=derived[0]; value,unit=self._display_value(item,plan.unit)
                direction='增加' if value>0 else '减少' if value<0 else '持平'
                company=CODE_TO_NAME_MAP.get(item['stock_code'],item['stock_code'])
                description=(f"{company}{item['report_year']}年{PERIOD_LABELS[item['report_period']]}"
                             f"{field_specs()[item['field']]['label']}较{item['base_year']}年{direction}")
                notes.append(description+('。' if value==0 else number(abs(value))+unit+'。'))
        if plan.compare_companies:
            notes.extend(compare_company_facts(plan,facts))
        if plan.comparison=='time' and plan.calculation=='none':
            years=sorted({y for y,p in plan.pairs})
            for code in plan.codes:
                for metric in plan.metrics:
                    values=sorted([f for f in facts if f['stock_code']==code and f['field']==metric],key=lambda f:f['report_year'])
                    if len(values)>=2 and len({f['report_period'] for f in values})==1 and len({f['unit'] for f in values})==1:
                        a,b=values[0],values[-1]
                        direction='增加' if b['value']>a['value'] else '减少' if b['value']<a['value'] else '持平'
                        notes.append(f"{CODE_TO_NAME_MAP.get(code,code)}{b['report_year']}年{field_specs()[metric]['label']}较{a['report_year']}年{direction}。")
                    elif len(years)>1:
                        notes.append(f"{field_specs()[metric]['label']}缺少同口径比较值，不能确认增减。")
        if plan.comparison=='metrics':
            for code in plan.codes:
                for year,period in plan.pairs:
                    values=[index.get((code,year,period,m)) for m in plan.metrics]
                    if len(values)==2 and all(values) and values[0]['unit']==values[1]['unit']:
                        a,b=values
                        notes.append(f"{field_specs()[a['field']]['label']}与{field_specs()[b['field']]['label']}的数值"+('相同。' if a['value']==b['value'] else '不同。')+'两者的财务含义需要分别理解。')

        if plan.needs_evidence or plan.intent=='explanation':
            if plan.intent=='explanation' and len(plan.pairs)<2:
                notes.append('本轮只有一个报告期的数字，不能单凭它确认变化幅度或方向；因果解释还需要同口径基期或明确原文。')
            yield 'plan',{'label':'检索原文','detail':'正在查找同公司、同报告期的原始证据'}
            locations=self._source_locations(facts)
            references= self._references(plan)
            if self._reference_errors:
                reasons.append('reference_query_failed')
                notes.append('部分原文检索未完成，不能据此判断原文是否包含所需解释。')
            if locations:
                content+='\n\n数字来源位置（已入库的来源登记，页码为范围）：\n\n'+markdown_table(
                    ['公司','年份','报告期','指标','PDF','PDF页码'],locations)
            if references:
                excerpts=[]
                for i,item in enumerate(references,1):
                    excerpts.append(f"原文摘录【{i}】（{item['source_title']}，PDF第{item['page_start']}页）：\n\n> "+item['text'])
                content+='\n\n'+'\n\n'.join(excerpts)
                if plan.intent=='explanation':
                    notes.append('以上为同报告期原文片段；片段未能明确回答的因果关系，暂不作推断。')
                    reasons.append('explanation_limited_to_excerpt')
            else:
                if self._reference_errors:
                    notes.append('已保留可靠数字，本轮暂不能补充原文解释。')
                elif locations and plan.intent!='explanation':
                    notes.append('已给出数字在原始PDF中的来源位置；本轮未取得可逐字引用的段落。')
                else:
                    notes.append('未找到符合公司和报告期的可靠原文片段，无法确认解释性结论。')
                    reasons.append('evidence_not_found')
                if plan.intent == 'explanation' and facts:
                    notes.append('已保留本轮指标结果与来源位置；原因目前不能确认。')
        if plan.chart:
            yield 'tool_call',{'tool':'render_chart','label':'依据事实生成图表','detail':'核对数据点、报告期和单位'}
            charts=self._charts(plan,facts,events)
            yield 'tool_result',{'tool':'render_chart','status':'success' if charts else 'empty','summary':f'生成 {len(charts)} 张图表','chart_eligibility':plan.chart_eligibility}
            if charts: notes.append('图表按本轮实际数据生成；缺失位置留空，不补零。')
            for item in plan.chart_eligibility:
                if item['status'] != 'available': notes.append(item['message'])
            if not charts: reasons.append('chart_unavailable')
        if missing and (plan.calculation!='none' or plan.comparison!='none' or plan.compare_companies):
            notes.append('所需的同口径数据不完整，不能据此判断全部比较结果或计算增减。')
        if plan.clarification:
            notes.append(plan.clarification)
            reasons.append(plan.reason or 'additional_condition_required')
        if not facts and plan.metrics:
            status='query_failed' if any(e['status'] not in {'success','empty'} and e.get('purpose')=='facts' for e in events) else 'no_data'
        elif not facts and not references: status='no_data'
        else:
            # 作品说明：缺少经营原因原文应明确说明，但不单独否定已有可靠数值。
            blocking=[r for r in reasons if not (facts and r=='evidence_not_found')]
            # 作品说明：缺少所需逐字摘录时保留数值，同时标记该轮尚未完整完成。
            status='partial' if missing or blocking or 'evidence_not_found' in reasons else 'answered'
        content='\n\n'.join([content.strip(),*dict.fromkeys(notes)]).strip() or '当前未取得可发布的财报证据。'
        if status=='no_data' and plan.metrics:
            company=CODE_TO_NAME_MAP.get(plan.codes[0],'') if plan.codes else ''
            year=plan.pairs[0][0] if plan.pairs else None
            period=PERIOD_LABELS.get(plan.pairs[0][1],'') if plan.pairs else ''
            label=field_specs().get(plan.metrics[0],{}).get('label','')
            notice=no_data_reply(company, year, period, label)
            content=notice if content=='当前未取得可发布的财报证据。' else notice+'\n\n'+content
        elif facts and plan.metrics and plan.intent in {'facts','explanation'}:
            company=rows[0][0] if rows else (CODE_TO_NAME_MAP.get(plan.codes[0],'') if plan.codes else '')
            year=max((y for y,_ in plan.pairs), default=None)
            label=field_specs().get(plan.metrics[0],{}).get('label','')
            pages=source_page_lines(self._source_locations(facts))
            suffix=reading_suffix(plan.metrics, company, year, label)
            extra='\n\n'.join(part for part in (pages, suffix) if part and part not in content)
            if extra:
                content=content+'\n\n'+extra
        result=self._finish(plan,events,facts,references,status,reasons,content,charts,derived)
        for chart in result['chart_data_list']: yield 'chart',{'chart_data':chart,'title':chart['title'],'path':''}
        if result['answer']['references']: yield 'references',{'items':result['answer']['references']}
        yield 'answer_delta',{'text':result['answer']['content']}
        yield 'done',{'result':result}

    def _execute(self,query,events):
        sql=query.display_sql()
        cache=_query_cache.get(); key=(sql,query.purpose)
        cached=cache is not None and key in cache
        if cached: result=deepcopy(cache[key])
        else:
            try:
                if runtime() and runtime().remaining()<=1:
                    raise AgentFailure('turn_budget_exhausted','sql','本轮执行时限已到')
                result=self.repository.execute(query)
            except Exception as exc: result={'status':'error','reason':type(exc).__name__,'rows':[]}
            if cache is not None: cache[key]=deepcopy(result)
        allowed_codes={str(v) for k,v in query.params.items() if k.startswith('code')}
        allowed_pairs={(v,query.params[k.replace('year','period')]) for k,v in query.params.items() if k.startswith('year')}
        for row in result.get('rows') or []:
            if result.get('status')!='success': break
            try:
                valid=(not allowed_codes or str(row.get('stock_code')) in allowed_codes)
                valid=valid and (not allowed_pairs or (int(row.get('report_year',0)),row.get('report_period')) in allowed_pairs)
                valid=valid and ('period' not in query.params or row.get('report_period')==query.params['period'])
            except (TypeError,ValueError): valid=False
            if not valid:
                result={'status':'rejected','reason':'out_of_scope_result','rows':[]}; break
        query_id,rows=bind_sql_rows(sql,result.get('rows') or [],result.get('scope'),result.get('column_lineage'))
        if result.get('status') not in {'success','empty'}: rows=[]
        events.append({'query_id':query_id,'sql':sql,'status':result.get('status','error'),'rows':rows,
                       'columns':result.get('columns',[]),'row_count':len(rows),'purpose':query.purpose,
                       'requested_fields':list(query.fields),'reason':result.get('reason',''),'reused':cached,
                       'requested_scope':{'codes':sorted(allowed_codes),'pairs':sorted(allowed_pairs),
                                          'period':query.params.get('period'),'fields':list(query.fields)}})
        return {**result,'rows':rows}

    @staticmethod
    def _tool_event(event):
        return {**event,'tool':'query_database','summary':f"返回 {event['row_count']} 行" if event['status']=='success' else '查询范围内无记录' if event['status']=='empty' else '本轮查询失败'}

    @staticmethod
    def _display_value(fact,unit):
        value=Decimal(str(fact['value'])); source= fact['unit']
        scales={'元':Decimal(1),'万元':Decimal(10000),'亿元':Decimal(100000000)}
        if fact['field']!='eps' and source in scales and unit in scales: return value*scales[source]/scales[unit],unit
        return value, '元/股' if fact['field']=='eps' and source=='元' else source

    def _references(self,plan):
        self._reference_errors = []
        if not self.rag_tool: return []
        output=[]
        from .citations import literal_passage
        for code in plan.codes:
            for year,period in plan.pairs:
                if runtime() and runtime().remaining()<=1:
                    self._reference_errors.append('turn_budget_exhausted')
                    return output
                try:
                    result=self.rag_tool.run(plan.request_contract.get('standalone_question') or plan.question,
                        top_k=3,stock_code=code,report_year=year,report_period=period,
                        validated_scope={'stock_codes':[code],'report_years':[year],'report_period':period,'year_periods':[(year,period)]})
                except Exception as exc:
                    self._reference_errors.append(type(exc).__name__)
                    record_failure('reference_query_failed','retrieval',type(exc).__name__)
                    continue
                if result.get('status')=='error':
                    self._reference_errors.append('retrieval_failed')
                    record_failure('reference_query_failed','retrieval','原文检索工具返回失败')
                    continue
                for original in result.get('results') or []:
                    item=document_identity(original)
                    if not metadata_matches(item,{'stock_codes':[code],'year_periods':[(year,period)]}): continue
                    if not all(item.get(k) for k in ('source_sha256','page_start','paper_path','text')): continue
                    quote=literal_passage(item['text'],limit=600)
                    if not quote: continue
                    item={**item,'_source_text':item['text'],'text':quote,'source_title':item.get('source_title') or str(item['paper_path']).replace('\\','/').split('/')[-1]}
                    output.append(item)
                    if len(output)>=3: return output
        return output

    @staticmethod
    def _source_locations(facts):
        rows=[]
        for fact in facts:
            source=fact.get('source') or {}
            location=str(source.get('source_path') or '')
            page=source.get('page_start')
            if source.get('status')!='source_located' or not source.get('source_sha256') or not location or not isinstance(page,int):
                continue
            if not (Path.cwd()/location).is_file():
                continue
            end=source.get('page_end')
            page_text=str(page) if not isinstance(end,int) or end==page else f'{page}–{end}'
            rows.append((CODE_TO_NAME_MAP.get(fact['stock_code'],fact['stock_code']),str(fact['report_year'])+'年',
                         PERIOD_LABELS[fact['report_period']],field_specs()[fact['field']]['label'],
                         Path(location).name,page_text))
        return list(dict.fromkeys(rows))

    def _charts(self,plan,facts,events):
        charts=[]
        plan.chart_eligibility=[]
        if len({p for y,p in plan.pairs})!=1:
            plan.chart_eligibility=[{'status':'unavailable','reason':'incompatible_periods','message':'报告期口径不同，已保留表格，不画连续趋势。'}]
            return []
        all_rows=[r for event in events for r in event.get('rows',[])]
        company_axis=len(plan.codes)>1 and len(plan.pairs)==1
        for field in plan.metrics:
            groups=[plan.codes] if company_axis else [[code] for code in plan.codes]
            for codes in groups:
                selected=sorted([f for f in facts if f['field']==field and f['stock_code'] in codes],key=lambda f:(f['report_year'],f['stock_code']))
                label=field_specs()[field]['label']
                eligibility={'field':field,'codes':codes,'valid_points':len(selected),
                             'requested_reports':[list(p) for p in plan.pairs],
                             'available_reports':sorted({(f['report_year'],f['report_period']) for f in selected})}
                asked = []
                for year, _period in plan.pairs:
                    if str(year) not in asked:
                        asked.append(str(year))
                got = {str(f['report_year']) for f in selected}
                missing_years = [year for year in asked if year not in got]
                if len(selected)<MIN_VALID_POINTS:
                    if len(asked) > 1:
                        message = (f'这次要查{"、".join(asked)}年的{label}。'
                                   f'{"、".join(missing_years) or "这些"}年没有记录，有数的年份不够两个，画不了趋势。已有值保留在表格中。')
                    else:
                        message = f'{label}这一年只有一个数，画不了趋势。已有值保留在表格中。'
                    plan.chart_eligibility.append({**eligibility,'status':'unavailable','reason':'insufficient_points',
                        'message':message})
                    continue
                x_field='stock_abbr' if company_axis else 'report_year'
                values={str(f[x_field]):self._display_value(f,plan.unit) for f in selected}
                years=sorted({y for y,p in plan.pairs})
                axis=[CODE_TO_NAME_MAP.get(c,c) for c in codes] if company_axis else [str(y) for y in range(min(years),max(years)+1)]
                unit=next(iter(values.values()))[1]
                title='、'.join(CODE_TO_NAME_MAP.get(c,c) for c in codes)+' '+ '、'.join(map(str,years))+'年'+PERIOD_LABELS[plan.pairs[0][1]]+label
                query_id=selected[0]['query_id']
                rows=[r for r in all_rows if r.get('query_id')==query_id and str(r.get('stock_code')) in codes]
                requested_chart=plan.request_contract.get('request',{}).get('presentation')
                chart={'title':title,'chart_type':'bar' if company_axis or requested_chart=='bar' else 'line',
                       'x_data':axis,'y_data':[float(values[x][0]) if x in values else None for x in axis],
                       'x_label':'公司' if company_axis else '年份','y_label':label+'（'+unit+'）',
                       'series_name':label,'unit':unit,
                       'data_source':{'kind':'sql_result','query_id':query_id,'x_field':x_field,'y_field':field,'unit':unit}}
                chart['data_source']=bind_chart_source(chart,rows)
                checked=verify_charts([chart],rows)
                if checked['status']=='pass':
                    charts.append(chart)
                    gaps=[x for x in axis if x not in values]
                    plan.chart_eligibility.append({**eligibility,'status':'partial' if gaps else 'available',
                        'reason':'missing_points' if gaps else 'ready',
                        'message':f'{label}里{"、".join(gaps)}年没有记录，图上已留空。' if gaps else '同口径数据可作图。'})
                else:
                    plan.chart_eligibility.append({**eligibility,'status':'unavailable','reason':'chart_validation_failed','message':label+'的图表来源核验失败，已保留数字表格。'})
        return charts

    def _finish(self,plan,events,facts,references,status,reasons,content,charts=(),derived=(),failure_code=None):
        if failure_code: reasons=[*reasons,failure_code]
        all_rows=[r for e in events if e.get('purpose')=='facts' and e['status']=='success' for r in e.get('rows',[])]
        originals=[{**r,'text':r.get('_source_text',r['text'])} for r in references]
        references=[{k:v for k,v in r.items() if k!='_source_text'} for r in references]
        checks=[{'name':'facts_bound','label':'数字核对','status':'pass' if facts else 'warn',
                 'detail':'这个数字对得上刚才查到的记录。' if facts else '这次没有对上可公布的数字。','total':len(facts),'matched':len(facts)},
                verify_charts(charts,all_rows),verify_references(references,originals)]
        if any(c['status']=='fail' for c in checks):
            status='query_failed'; reasons=[*reasons,'publication_check_failed']
            content='本轮证据一致性检查未通过，暂不发布相关结论。'
            facts=[]; derived=[]; charts=[]; references=[]
        verification={'status':'fail' if status=='query_failed' else 'warn' if status in {'partial','no_data','needs_clarification','unsupported'} or not facts else 'pass',
                      'scope':'只核对这次查到的数字，不代表经营好坏。',
                      'checks':checks,'unmatched_numbers':[],
                      'summary':{'passed':sum(c['status']=='pass' for c in checks),'warnings':sum(c['status']=='warn' for c in checks),'failed':sum(c['status']=='fail' for c in checks)}}
        evidence=build_evidence(events,references,charts)
        outcome={'status':status,'reason_codes':list(dict.fromkeys(reasons))}
        context={'companies':plan.codes,'report_pairs':plan.pairs,'metrics':plan.metrics}
        if len(plan.codes)==1: context['company']=CODE_TO_NAME_MAP.get(plan.codes[0],plan.codes[0])
        if len(plan.pairs)==1: context.update(report_year=plan.pairs[0][0],report_period=plan.pairs[0][1])
        return {'question':plan.question,'answer':{'content':content,'image':[],'references':references},
                'request_contract':deepcopy(plan.request_contract),'catalog_result':plan.request_contract.get('catalog_result'),
                'response_kind':plan.response_kind,'chart_eligibility':plan.chart_eligibility,
                'sql':';\n'.join(e['sql'] for e in events) or '-', 'chart_format':'图表' if charts else '无',
                'chart_data':charts[0] if charts else None,'chart_data_list':list(charts),
                'context':context,'query_plan':plan.to_dict(),'outcome':outcome,
                'needs_clarification':status=='needs_clarification' or bool(plan.clarification),
                'clarify_options':plan.options,
                'facts':list(facts),'derived_facts':list(derived),'evidence':evidence,'verification':verification,
                'execution_plan':[{'step':i+1,'label':'报告范围查询' if e['purpose']=='coverage' else '财务数据查询','detail':e['sql'],'status':'done' if e['status'] in {'success','empty'} else 'error'} for i,e in enumerate(events)],
                'validation':{'status':verification['status'],'mode':'structured','sql_events':events,'outcome':outcome,'verification':verification,'facts':list(facts),'evidence':evidence}}
