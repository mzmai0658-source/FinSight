"""作品说明：在统一提案入口登记原话中唯一、明确的条件，复杂目标和上下文仍由模型提出。"""
from __future__ import annotations
import re
from pydantic import TypeAdapter
from .contracts import AtomicModification
from .request_bindings import metric_candidates,positive_period_mentions,explicit_output_unit,explicit_presentation_bindings,is_negated_span
MODIFICATION=TypeAdapter(AtomicModification)
NO_QUERY=r'(?:不|别|不要|先别|无需|不需要)(?:重新|再次|再)?(?:查数|查数字|查询数字|查询财务数字|查财务数)'

def explicit_scope_conflicts(question):
    """作品说明：明确要求同一查询只能采用互斥口径时先询问；概念区别不属于查数冲突。"""
    financial_span=re.split(r'(?:再|然后|另外|并)(?:查(?:询)?|看)',question)[-1]
    parent=bool(re.search(r'母公司(?:单体|口径|营业收入|净利润|利润表|报表|的?归母|的?扣非)',financial_span))
    attributed=bool(re.search(r'归母|归属于(?:上市公司|母公司)(?:所有者|股东)',financial_span))
    conflict=[]
    if parent and attributed:
        conflict.append('母公司单体口径与归母净利润口径不同；请确认查母公司净利润，还是合并报表归母净利润。')
    exclusive=bool(re.search(r'仅|只|必须|不(?:要|接受).*拆',financial_span))
    positive_consolidated=any(not is_negated_span(financial_span,m.start(),m.end()) and not re.search(r'不(?:接受|采用|使用)\s*$',financial_span[:m.start()])
                              for m in re.finditer(r'合并(?:口径|报表)',financial_span))
    if parent and positive_consolidated and exclusive:
        conflict.append('同一项查询不能同时仅采用合并口径和仅采用母公司口径；请明确选择，或允许分别查询。')
    return conflict

def explicit_conditions(question):
    """作品说明：只登记无歧义条件，不用关键词选择任务，不推断未说出的条件。"""
    output={}
    unit=explicit_output_unit(question)
    if unit:output['presentation.unit']=unit
    output.update(explicit_presentation_bindings(question))
    periods={period for _,_,period in positive_period_mentions(question)}
    if len(periods)==1:output['time.periods']=sorted(periods)
    metrics=metric_candidates(question,positive_only=True)
    computed=bool(re.search(r'计算|相对|相差|差额|减去|增加多少|变化多少',question))
    if computed and not re.search(r'披露|原文.*增长率',question):metrics={m for m in metrics if not m.endswith('_reported_yoy')}
    if len(metrics)==1:output['metrics']=sorted(metrics)
    parent=bool(re.search(r'母公司(?:单体|口径|营业收入|净利润|利润表|报表|的?归母|的?扣非)',question))
    consolidated=bool(re.search(r'合并(?:口径|报表|营业收入|净利润|营业成本|总资产)|归母(?:净利润)?',question))
    if parent!=consolidated:output['scope']='parent' if parent else 'consolidated'
    for m in re.finditer(r'保留\s*(\d{1,2}|[零一二两三四五六七八九十])\s*位(?:小数)?',question):
        if is_negated_span(question,m.start(),m.end()):continue
        word=m[1];digits=int(word) if word.isdigit() else {'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}[word]
        if digits<=12:output['presentation.decimals']=digits
    if re.search(r'只(?:要|给)表格|列(?:成|个)表(?:格)?|用表格',question) and not re.search(r'不(?:要|用).*表格',question):output['presentation.format']='table'
    for pattern,field in ((NO_QUERY,'no_query'),(r'(?:不|别|不要)(?:再)?(?:画图|生成图|绘图)','no_chart'),(r'(?:不|别|不要)(?:再)?重复(?:金额|数字)','no_repeat')):
        if re.search(pattern,question):output['restrictions.'+field]=True
    # 作品说明：计算类型仅登记明确名称；不能以金额计算覆盖报告已经披露的增长率。
    if computed and not re.search(r'披露.*(?:同比|增长率)|原文.*增长率',question):
        if '同比' in question:output['calculation']='yoy'
        elif '百分点' in question and not re.search(r'不要(?:用)?百分点|不(?:用|要)百分点',question):output['calculation']='percentage_points'
        elif re.search(r'相对.*(?:百分比|变化)|百分比变化',question):output['calculation']='relative_percent'
        elif re.search(r'差额|相差|减去',question):output['calculation']='difference'
    return output

def bind_explicit(plan,question,companies=None):
    """作品说明：字段直接依据原话登记，仍需合同、能力、冲突及执行核验，不构成放行捷径。"""
    values=explicit_conditions(question)
    goal_ids={g.id for g in plan.goals}
    # 作品说明：没有对应本轮目标的重复公共条件没有独立含义；不同内容仍保留为结构错误。
    original_edits=[e.model_dump(mode='json') for e in plan.edits]
    assignments=[a for a in plan.assignments if a.id in goal_ids or a.clarification or
                 any(e.model_dump(mode='json') not in original_edits for e in a.edits)]
    plan=plan.model_copy(update={'assignments':assignments})
    # 作品说明：有分目标例外时不扩大指标、口径或期间到所有目标；各个例外仍须显式编译。
    if plan.assignments and len(plan.goals)>1:
        values={k:v for k,v in values.items() if k not in {'metrics','scope','time.periods','calculation'}}
    replacements={};edits=[];applied={}
    for edit in plan.edits:replacements.setdefault(edit.field,[]).append(edit)
    for edit in plan.edits:
        value=values.get(edit.field)
        # 作品说明：不修改集合操作含义；添加仍为添加，删除及清空由原提案及状态检查负责。
        if edit.field in values and len(replacements[edit.field])==1 and edit.operation in {'replace','add','keep'}:
            edits.append(edit.model_copy(update={'value':value,'text':question}));applied[edit.field]=value
        else:edits.append(edit)
    for field,value in values.items():
        if field in replacements:continue
        if field=='metrics' and plan.continuity!='new':continue
        edits.append(MODIFICATION.validate_python({'field':field,'operation':'replace','value':value,'text':question}));applied[field]=value
    if len(plan.goals)==1:
        assignments=[]
        for assignment in plan.assignments:
            local=[];counts={field:sum(e.field==field for e in assignment.edits) for field in values}
            for edit in assignment.edits:
                if edit.field in values and counts[edit.field]==1 and edit.operation in {'replace','add','keep'}:
                    local.append(edit.model_copy(update={'value':values[edit.field],'text':question}))
                else:local.append(edit)
            assignments.append(assignment.model_copy(update={'edits':local}))
        plan=plan.model_copy(update={'assignments':assignments})
    # 作品说明：单独的单位否定不能变成不查数；只在明确新查询且无财务查询否定时撤去这项误提案。
    financial=any(g.kind in {'lookup','quote','compare','chart','rank','cause'} for g in plan.goals)
    explicit_query=bool(re.search(r'查|是多少|多少钱|原始表格|原页|出处|引用',question))
    unit_negation=bool(re.search(r'(?:原文)?单位(?:不|不要)(?:改写|换算|转换)',question))
    if plan.continuity=='new' and financial and explicit_query and unit_negation and not re.search(NO_QUERY,question):
        edits=[edit for edit in edits if not (edit.field=='restrictions.no_query' and edit.operation=='replace' and edit.value is True)]
    if companies:
        from .request_bindings import explicit_company_set_constraints,company_set_is_global
        binding=explicit_company_set_constraints(question,companies)
        if binding['keep'] and company_set_is_global(question,companies,binding):
            codes=sorted(binding['keep'])
            edits=[e for e in edits if e.field!='codes']
            edits.append(MODIFICATION.validate_python({'field':'codes','operation':'keep','value':codes,'text':question}))
            plan=plan.model_copy(update={'assignments':[a.model_copy(update={'edits':[e for e in a.edits if e.field!='codes']}) for a in plan.assignments]})
            applied['codes']={'operation':'keep','value':codes}
    clarification=list(plan.clarification)
    if 'codes' in applied:
        clarification=[text for text in clarification if not re.fullmatch(r'请(?:明确|提供)(?:要查询的)?(?:公司名称|公司|股票代码)(?:或(?:股票代码|公司名称))?[。？?]?',text)]
    if financial:
        # 作品说明：报告有无由查询结果确定，不能把模型对发布日期的猜测转换成用户需澄清的条件。
        clarification=[text for text in clarification if not re.match(r'[^。？?]{0,25}20\d{2}年(?:年报|报告)?(?:尚未发生|尚未发布|未发布)',text)]
    if financial:clarification=list(dict.fromkeys([*clarification,*explicit_scope_conflicts(question)]))
    return plan.model_copy(update={'edits':edits,'clarification':clarification}),applied
