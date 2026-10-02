"""作品说明：使用已校验意图统一约束 SQL、检索与展示。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import difflib
import json
import re
from typing import Any

from .domain import CODE_TO_NAME_MAP, COMPANY_CODE_MAP, field_table, get_table_fields
from .facts import MAIN_FINANCIAL_METRICS, extract_report_years, extract_report_periods, metric_mentions
from .reply_text import clarify_cash, clarify_company, clarify_metric, clarify_time

INTENTS = {'facts','coverage_companies','coverage_periods','coverage_metrics','conversation_scope','explanation','greeting','capabilities','clarify','unsupported'}
CALCULATIONS = {'none','difference','yoy','percentage_points','ranking'}
PERIODS = {'FY','Q1','HY','Q3'}


@dataclass
class QueryPlan:
    question: str
    intent: str = 'facts'
    codes: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    pairs: list[tuple[int,str]] = field(default_factory=list)
    time_mode: str = 'explicit'
    latest_count: int = 0
    all_companies: bool = False
    period: str = 'FY'
    calculation: str = 'none'
    chart: bool = False
    unit: str = ''
    needs_evidence: bool = False
    clarification: str = ''
    options: list[str] = field(default_factory=list)
    origins: dict[str,str] = field(default_factory=dict)
    reason: str = ''
    response_kind: str = 'financial'
    direct_reply: str = ''
    correction: bool = False
    compare_companies: bool = False
    chart_eligibility: list[dict[str, Any]] = field(default_factory=list)
    coverage_period_filter: str | None = None
    request_contract: dict[str, Any] = field(default_factory=dict)
    additional_plans: list[dict[str, Any]] = field(default_factory=list)
    comparison: str = 'none'
    overview: bool = False
    default_time: bool = False

    def to_dict(self):
        return asdict(self)

    def scope(self):
        return {'stock_codes':self.codes, 'report_years':sorted({y for y,p in self.pairs}),
                'report_period':self.period, 'report_periods':sorted({p for y,p in self.pairs}) or [self.period],
                'year_periods':self.pairs}


def normalize_model_text(value: Any) -> str:
    """作品说明：只恢复文字列表的换行转义，不任意修改路径或代码转义。"""
    text=str(value or '').replace('\r\n','\n')
    pieces=re.split(r'(```[\s\S]*?```|`[^`]*`)',text)
    for i in range(0,len(pieces),2):
        pieces[i]=re.sub(r'\\n(?=\s*(?:\d+[.、]|[-*] |[•]))','\n',pieces[i])
    return ''.join(pieces).strip()


def known_metrics():
    from .facts import field_specs
    return {name:{**spec, 'table':field_table(name),
                  'semantics':'比率，不得重复计算增长率' if spec['unit']=='%' else
                              '每股金额，不能按总金额换算' if name=='eps' else
                              '按报告期口径查询，累计值不能直接当作单季度'}
            for name,spec in field_specs().items() if field_table(name)}


def explicit_codes(question):
    aliases=dict(COMPANY_CODE_MAP)
    for name,code in COMPANY_CODE_MAP.items():
        for suffix in ('集团','药业','股份','制药'):
            short=name.removesuffix(suffix)
            if short!=name and len(short)>=2 and sum(other.startswith(short) for other in COMPANY_CODE_MAP)==1:
                aliases.setdefault(short,code)
    matches=[(name,code) for name,code in aliases.items() if name and name in question
             and not re.search(r'(?:不看|别看|不要查|不是)\s*'+re.escape(name),question)]
    names=[(name,code) for name,code in matches if not any(name!=other and name in other for other,_ in matches)]
    codes={m.group() for m in re.finditer(r'(?<!\d)\d{6}(?!\d)',question)
           if not re.search(r'(?:不看|别看|不要查|不是)\s*$',question[:m.start()])}
    return sorted({str(code) for _,code in names} | codes)


def selected_years(question):
    source=re.sub(r'(?:不是|不要|别查|不看)\s*(?:20)?\d{2}\s*年','',question)
    years=set(extract_report_years(source))
    chinese={'零':0,'〇':0,'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    for text_year in re.findall(r'(?<![零〇一二三四五六七八九])([零〇一二三四五六七八九]{2})年',source):
        years.add(2000+10*chinese[text_year[0]]+chinese[text_year[1]])
    return sorted(years)


def is_followup(question):
    return bool(re.search(r'^(?:那|再|换|不对|不是|只看|只要|仅|用|顺便|把结果|原文在哪|同比|环比|哪个|这轮|毛利率也|没有就)|它|这家公司|别看|不看.{1,12}了',question))


def main_metrics(question):
    return bool(re.search(r'主要|核心|关键|概况|业绩|怎么样|怎样|(?:最新|最近).{0,10}指标|指标都|主要数字',question))


def hints(question, history):
    """作品说明：历史仅提供用户选择的条件，不将旧回答当作事实。"""
    previous={'codes':[], 'metrics':[], 'pairs':[], 'period':'FY','latest_count':0,'unit':''}
    for message in history[-16:]:
        if message.get('role')!='user': continue
        q=str(message.get('content') or '')
        codes=explicit_codes(q)
        fields=list(dict.fromkeys(x[2] for x in metric_mentions(q)))
        periods=extract_report_periods(q)
        years=selected_years(q)
        old_pairs=list(previous['pairs'])
        if codes: previous['codes']=codes
        if fields: previous['metrics']=fields
        elif main_metrics(q): previous['metrics']=list(MAIN_FINANCIAL_METRICS)
        if periods: previous['period']=periods[-1]
        if years:
            selected_period=periods[-1] if periods else previous['period'] if is_followup(q) else 'FY'
            previous['period']=selected_period
            previous['pairs']=[(y,selected_period) for y in years]
            if is_followup(q) and re.search(r'跟|相比|比较|对比',q):
                previous['pairs']=sorted(set(previous['pairs']+old_pairs))
            previous['latest_count']=0
        latest=_latest(q)
        if latest:
            previous['latest_count']=latest
            previous['pairs']=[]
            previous['period']=periods[-1] if periods else 'FY'
        elif periods and previous['pairs']:
            previous['pairs']=[(y,periods[-1]) for y,p in previous['pairs']]
        if re.search(r'同比',q) and len(previous['pairs'])==1:
            y,p=previous['pairs'][0]; previous['pairs']=[(y-1,p),(y,p)]
        if '亿元' in q: previous['unit']='亿元'
        elif '万元' in q: previous['unit']='万元'
        elif re.search(r'换成元|单位.?元(?:[，。 ]|$)',q): previous['unit']='元'
    return previous


def _latest(question):
    match=re.search(r'最新\s*(?:的\s*)?([一二两三四五六七八九十\d]+)?\s*(?:个|份)?\s*(?:年|年度|年报)|最近\s*([一二两三四五六七八九十\d]+)\s*(?:个|份)?(?:有年报|年报)',question)
    if not match: return 0
    word=match.group(1) or match.group(2) or '1'
    return int(word) if word.isdigit() else {'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}.get(word,0)


def propose_rule_plan(question: str) -> dict[str, Any]:
    """作品说明：思考预算耗尽时只采用保守的结构恢复。"""
    q=question.strip()
    intent='facts'
    if re.fullmatch(r'(你好|您好|嗨|hi|hello)[！!。\s]*',q,re.I): intent='greeting'
    elif re.search(r'财报.{0,4}研报.{0,8}区别|你能做什么|资料库有PDF',q): intent='capabilities'
    elif re.search(r'有哪些公司|(?:能|可|要)?查哪些公司|哪些公司.{0,6}(?:能查|可查)|都有谁|公司名单|公司全称和代码',q): intent='coverage_companies'
    elif re.search(r'哪些指标|能查哪些财务指标',q): intent='coverage_metrics'
    elif re.search(r'这轮查了哪些年份|刚才查了哪些年份|上一轮查了哪些年份',q): intent='conversation_scope'
    elif re.search(r'哪些.{0,6}报告期|什么报告期|哪几年的年报|有哪些年份|最新年报是哪年',q): intent='coverage_periods'
    elif re.search(r'(?:年报|季报|半年报|中报|报告).{0,12}(?:有没有|有吗|没查到|没找到|没有|收录|查到)',q) and not metric_mentions(q): intent='coverage_periods'
    elif re.search(r'为什么|为啥|原因|怎么解释|怎么说明|因为|导致',q): intent='explanation'
    metrics=list(dict.fromkeys(x[2] for x in metric_mentions(q)))
    if re.search(r'挣了多少|赚了多少|赚的钱|每股挣了多少',q):
        metrics.append('eps' if '每股' in q else 'net_profit')
    if re.search(r'卖了多少钱',q): metrics.append('total_operating_revenue')
    if main_metrics(q) and not metrics: metrics=list(MAIN_FINANCIAL_METRICS)
    periods=extract_report_periods(q)
    years=selected_years(q)
    calc='yoy' if '同比' in q else 'percentage_points' if '百分点' in q else 'ranking' if re.search(r'排名|排序|从大到小',q) else 'difference' if re.search(r'增加多少|减少多少|少了多少|多了多少|差了多少|变化了多少',q) else 'none'
    return {'intent':intent,'codes':explicit_codes(q),'metrics':list(dict.fromkeys(metrics)),
            'pairs':[[y,periods[-1] if periods else 'FY'] for y in years],
            'latest_count':_latest(q),'period':periods[-1] if periods else 'FY',
            'calculation':calc,'chart':bool(re.search(r'画|绘|柱状|折线|趋势图',q)),
            'unit':'亿元' if '亿元' in q else '万元' if '万元' in q else '',
            'needs_evidence':bool(re.search(r'原文|原始(?:PDF|文件)|PDF.{0,8}页|哪页|引用|出处|为什么|为啥|原因|摘一段|证据|因为|导致|怎么解释|怎么说明',q))}


def planning_messages(question,history):
    # 作品说明：减少小模型决策字段，避免在显式字段生成前耗尽预算。
    prompt=('你是财报问答意图分类器。思考后立即只输出一个JSON对象，唯一的键为intent。'
            'intent只能为facts,coverage_companies,coverage_periods,coverage_metrics,explanation,'
            'conversation_scope,greeting,capabilities,clarify,unsupported。不要回答财务数字，也不要解释。')
    return [{'role':'system','content':prompt},{'role':'user','content':json.dumps(
        {'历史用户提问':[m['content'] for m in history[-6:] if m.get('role')=='user'],
         '本轮问题':question},ensure_ascii=False,separators=(',',':'))}]


def validate_plan(question: str, raw: Any, history=()) -> QueryPlan:
    plan=QueryPlan(question=question)
    if not isinstance(raw,dict):
        plan.intent='clarify'; plan.reason='intent_unresolved'; plan.clarification='这次未能可靠理解问题，请明确公司、年份、报告期和指标。'
        return plan
    plan.intent=raw.get('intent') if raw.get('intent') in INTENTS else 'clarify'
    if not isinstance(raw.get('codes',[]),list) or not isinstance(raw.get('metrics',[]),list):
        plan.intent='clarify'; plan.clarification='请明确要查询的公司和财务指标。'; return plan
    if re.search(r'密码|别人的聊天|其他用户|(?<![A-Za-z0-9_])(?:delete|drop|update|insert)(?![A-Za-z0-9_])|随便造|编造|编个(?:数|值)|估个数|估计个|猜一下|猜个|随便猜|去猜|瞎猜|全部积蓄|忽略证据',question,re.I):
        plan.intent='unsupported'; plan.reason='unsafe_or_ungrounded_request'; return plan
    direct_codes=explicit_codes(question)
    previous=hints(question,history)
    if plan.intent=='conversation_scope':
        plan.codes=list(previous['codes']); plan.metrics=list(previous['metrics'])
        plan.pairs=list(previous['pairs']); plan.period=previous['period']
        plan.origins={'codes':'history','metrics':'history','time':'history'}
        if not plan.pairs:
            plan.intent='clarify'; plan.reason='conversation_scope_unavailable'
            plan.clarification='当前对话中没有明确的上轮查询年份；请说明要查看哪一轮。'
        return plan
    followup=is_followup(question) or (not direct_codes and bool(history) and len(question)<28)
    plan.codes=direct_codes or (previous['codes'] if followup else [])
    proposed=[str(c) for c in raw.get('codes',[]) if str(c) in CODE_TO_NAME_MAP]
    # 作品说明：已登记短名称必须实际出现在原话中。
    if not plan.codes:
        plan.codes=[c for c in proposed if any(len(n)>=2 and n in question for n in [CODE_TO_NAME_MAP[c],CODE_TO_NAME_MAP[c].replace('集团','').replace('股份','')])]
    if any(c not in CODE_TO_NAME_MAP for c in direct_codes):
        plan.intent='unsupported'; plan.reason='unknown_company'; return plan
    plan.origins['codes']='current' if direct_codes else 'history' if followup and plan.codes else 'model'
    fields=list(dict.fromkeys(x[2] for x in metric_mentions(question)))
    if '别拿扣非' in question or '不要扣非' in question:
        fields=[f for f in fields if f!='roe_weighted_excl_non_recurring']
    elif '扣非' in question and 'roe' in fields:
        fields=['roe_weighted_excl_non_recurring' if f=='roe' else f for f in fields]
    if main_metrics(question) and not fields: fields=list(MAIN_FINANCIAL_METRICS)
    candidate=[str(f) for f in raw.get('metrics',[])]
    if any(f not in known_metrics() for f in candidate):
        plan.intent='clarify'; plan.reason='unknown_metric'; plan.clarification='请明确要查询的财务指标。'; return plan
    plan.metrics=fields or candidate or (previous['metrics'] if followup else [])
    plan.origins['metrics']='current' if fields else 'model'
    periods=extract_report_periods(question)
    period=periods[-1] if periods else previous['period'] if followup else 'FY'
    plan.period=period if period in PERIODS else 'FY'
    years=selected_years(question)
    latest=_latest(question)
    if latest or (followup and not years and previous['latest_count']):
        plan.time_mode='latest'; plan.latest_count=latest or previous['latest_count']
        plan.period=periods[-1] if periods else 'FY'
        plan.origins['time']='current' if latest else 'history'
    else:
        plan.origins['time']='current' if years else 'history' if followup else 'model'
        pairs=raw.get('pairs',[])
        if not isinstance(pairs,list): pairs=[]
        valid=[]
        for item in pairs:
            if isinstance(item,(list,tuple)) and len(item)==2 and isinstance(item[0],int) and not isinstance(item[0],bool) and 2000<=item[0]<=2100 and item[1] in PERIODS:
                if not years or item[0] in years: valid.append((item[0],item[1]))
        if years:
            if len(periods)>1:
                from .facts import extract_year_period_pairs
                plan.pairs=extract_year_period_pairs(question)
            else: plan.pairs=[(y,periods[0] if periods else (previous['period'] if followup else 'FY')) for y in years]
        elif followup: plan.pairs=[(y,periods[0] if periods else p) for y,p in previous['pairs']]
        else:
            # 作品说明：中文年份依据可见表达解析，不猜年份。
            plan.pairs=valid if re.search(r'[〇零一二三四五六七八九]{2,4}年',question) else []
    plan.calculation='none'
    rate_fields={field for field in plan.metrics if field.endswith(('_yoy_growth','_qoq_growth'))}
    # 作品说明：已存增长率是百分比指标，不能再次计算其增长率。
    if ('同比' in question or re.search(r'增长率|变动率',question)) and not rate_fields:
        plan.calculation='yoy'
    if '百分点' in question: plan.calculation='percentage_points'
    if re.search(r'从大到小|排名|排一下|排序',question): plan.calculation='ranking'
    if re.search(r'增加多少|减少多少|少了多少|多了多少|差了多少|变化了多少',question): plan.calculation='difference'
    if followup and plan.calculation=='none' and re.search(r'画|绘|用.{0,4}单位|用(?:亿元|万元|元)|换成',question):
        prior_text=' '.join(str(m.get('content') or '') for m in history[-4:] if m.get('role')=='user')
        if '同比' in prior_text: plan.calculation='yoy'
        elif len(plan.pairs)>1 and re.search(r'对比|比较|相比|比一比|跟.{0,12}比',prior_text):
            plan.calculation='difference'
    if plan.calculation=='yoy' and len(plan.pairs)==1:
        y,p=plan.pairs[0]; plan.pairs=[(y-1,p),(y,p)]
    elif followup and re.search(r'跟|相比|比较|对比',question) and years and previous['pairs']:
        plan.pairs=sorted(set(plan.pairs+previous['pairs']))
    if plan.calculation=='none' and len(plan.pairs)>1 and re.search(r'对比|比较|相比|比一比|跟.{0,12}比|比.{0,8}(?:多|少|高|低)',question):
        plan.calculation='difference'
    plan.chart=bool(re.search(r'画|绘|柱状|折线|趋势图',question))
    plan.unit='亿元' if '亿元' in question else '元' if re.search(r'换成元|单位.?元(?:[，。 ]|$)',question) else '万元' if '万元' in question else previous['unit'] if followup else ''
    plan.needs_evidence=bool(re.search(r'原文|原始(?:PDF|文件)|PDF.{0,8}页|哪页|引用|出处|为什么|为啥|原因|摘一段|证据|因为|导致|怎么解释|怎么说明',question))
    if re.search(r'单季|第四季度|第二季度|Q2|Q4',question,re.I):
        plan.intent='unsupported'; plan.reason='single_quarter_not_supported'; return plan
    if plan.intent in {'coverage_companies','coverage_metrics'}:
        plan.codes=[]; plan.pairs=[]; return plan
    if plan.intent in {'greeting','capabilities','unsupported'}: return plan
    if re.search(r'所有公司|这些公司|这十家|十家公司|全部公司',question):
        plan.all_companies=True
    if not plan.codes and not plan.all_companies:
        named_companies=re.findall(r'[\u4e00-\u9fff]{2,10}(?:公司|集团|药业|股份|制药)',question)
        if any(not re.search(r'这家|那家|哪家|哪个公司|什么公司|一家|几家',name)
               for name in named_companies):
            plan.intent='unsupported'; plan.reason='unknown_company'; return plan
        plan.intent='clarify'; plan.reason='company_required'; plan.clarification=clarify_company('', years[0] if years else None)
        for name in CODE_TO_NAME_MAP.values():
            if any(difflib.SequenceMatcher(None,question[i:i+len(name)],name).ratio()>=.75 for i in range(max(1,len(question)-len(name)+1))):
                plan.options.append(name)
        return plan
    if '现金流' in question and not re.search(r'经营|投资|筹资|净现金流|现金及现金等价物|现金净增加',question):
        plan.metrics=[metric for metric in plan.metrics if metric!='net_cash_flow']
        company_name=CODE_TO_NAME_MAP.get(plan.codes[0], '') if plan.codes else ''
        plan.reason='cash_flow_kind_required'; plan.clarification=clarify_cash(company_name, plan.pairs[0][0] if plan.pairs else (years[0] if years else None))
        plan.options=['经营活动现金流量净额','投资活动现金流量净额','筹资活动现金流量净额']
        if not plan.metrics:
            plan.intent='clarify'; return plan
    if plan.intent=='coverage_periods':
        if years and not periods and not re.search(r'年报|年度|全年',question):
            plan.pairs=[(y,p) for y in years for p in sorted(PERIODS)]
        return plan
    if not plan.pairs and not plan.latest_count:
        plan.intent='clarify'; plan.reason='period_required'; plan.clarification=clarify_time(); return plan
    if not plan.metrics and plan.intent!='explanation':
        company_name=CODE_TO_NAME_MAP.get(plan.codes[0], '') if plan.codes else ''
        plan.intent='clarify'; plan.reason='metric_required'; plan.clarification=clarify_metric(company_name, years[0] if years else None); plan.options=['营业收入','净利润','主要财务指标']; return plan
    if plan.intent=='clarify':
        plan.intent='explanation' if plan.needs_evidence and not plan.metrics else 'facts'
    return plan
