"""历史规则回归资产：生产 Agent 不导入，不用于模型理解失败后的回退。"""
"""作品说明：核对本轮明确身份和展示条件，对冲突提案保留失败反馈；这些检查不代替模型选择任务或补充缺少的条件。"""
import re
from src.agent.v3.catalog import ALIASES


def validate_literal_source(question, source):
    """只证明逐字依据来自本轮；来源存在不等于模型解释已获得语义认证。"""
    if not isinstance(source,str) or not source.strip() or source not in question:
        raise ValueError('A literal current-input source is required')
    return source


def registered_company_candidates(source, companies):
    """登记姓名及代码的字面候选，不判定它被选择、排除或替换。"""
    from src.agent.v3.company_identity import registered_company_candidates as candidates
    return candidates(source, companies)


NO_QUERY = r'(?:不|别|不要|先别|无需|不需要|暂不|暂停|停止)(?:重新|再次|再)?(?:查数|查数字|查询数字|查询财务数字|查财务数)'


def explicit_restrictions(question):
    """请求编译、模型字段约束和核验共用同一份明确执行限制。"""
    patterns={'no_query':NO_QUERY,'no_chart':r'(?:不|别|不要)(?:再)?(?:画图|生成图|绘图)',
              'no_repeat':r'(?:不|别|不要)(?:再)?重复(?:金额|数字)'}
    return {field:True for field,pattern in patterns.items() if re.search(pattern,question)}


def explicit_explanation_requirements(question):
    """只核对明确解释目标的原话范围，不替模型创建目标或推断经营原因。"""
    requirements=[]
    for clause in re.split(r'[，,。；;]|然后|并(?=用|解释|说明)|再(?=用|解释|说明)',question):
        clause=clause.strip()
        if not clause or re.match(r'没有|缺|若|如果|原因只|后者只',clause):continue
        concept=bool(re.search(r'(?:解释|说明|讲).*(?:区别|差别|关系|不能.*(?:当作|等同|代表))',clause))
        cause=bool(re.search(r'(?:解释|说明).*(?:原因|为什么|变化|变动)|归因于什么|(?:为什么|为何).*(?:增长|下降|变化|变动)',clause))
        if cause and not (metric_candidates(question) or re.search(r'该指标|它|净额|该数',clause)):cause=False
        if concept or cause:requirements.append({'kind':'concept' if concept else 'cause','source_ref':clause})
    return requirements


def explicit_lookup_requirements(question):
    """仅对明确取数分句检查查数目标；比较、图表、出处与解释表达仍由模型选择任务。"""
    requirements=[]
    for clause in re.split(r'[，,。；;？?!！]|然后|并(?=用|解释|说明)|再(?=用|解释|说明)',question):
        clause=clause.strip()
        if not clause or not (explicit_financial_query(clause) or
                re.search(r'是多少|有多少|多少钱',clause) and metric_candidates(clause,positive_only=True)):continue
        if explicit_restrictions(clause).get('no_query') or has_special_query_action(clause) or explicit_single_quarters(clause):continue
        if re.search(r'(?:删除|清空|修改|更新|写入|移除).{0,18}(?:数据库|库中|库内|数据)|(?:DELETE|DROP|UPDATE|INSERT)\s',clause,re.I):continue
        requirements.append(dict(kind='lookup',source_ref=clause))
    return requirements


def has_special_query_action(question):
    pattern=r'原文|原句|原表|摘录|出处|页码|物理页|所在|位置|引用|图|排名|排行|排序|最高|最低|前[一二三四五六七八九十两几\d]+|从低到高|从高到低|从大到小|从小到大|比较|对比|差额|差异|相差|同比|环比|增长|下降|变化|变动|增减|增加|减少|算|相对|百分点|百分之|原因|为什么|为何|解释|说明|概念|定义|区别|含义|意义|盈利|亏损|正数|负数|正负|规则|如何|怎么|是否|能否|单季'
    return any(not is_negated_span(question,m.start(),m.end()) for m in re.finditer(pattern,question))


def explicit_goal_requirements(question,*,uncovered=()):
    return [*explicit_explanation_requirements(question),
        *(r for r in explicit_lookup_requirements(question) if not any(name in r['source_ref'] for name in uncovered))]


def query_goal_correction(question,kinds):
    """仅在已证明漏掉查数目标时提供纠错合同，不在正常入口按关键词切换执行路径。"""
    if re.search(r'(?:删除|清空|修改|更新|写入|移除).{0,18}(?:数据库|库中|库内|数据)|(?:DELETE|DROP|UPDATE|INSERT)\s',question,re.I):return {}
    if not explicit_lookup_requirements(question):return {}
    pure=not has_special_query_action(question)
    if 'lookup' in kinds and (not pure or set(kinds)=={'lookup'}):return {}
    correction=dict(required_goal_kinds=['lookup'],minimum_goal_count=1)
    if pure:
        from typing import get_args
        from src.agent.v3.contracts import GoalKind
        correction['goal_kind_exclusions']=[kind for kind in get_args(GoalKind) if kind!='lookup']
    return correction


def metric_candidates(text, positive_only=False, *, concept=False):
    matches=[]
    for alias,metric in ALIASES.items():
        for match in re.finditer(re.escape(alias),text,re.IGNORECASE):
            if not positive_only or not is_negated_span(text,match.start(),match.end()):
                matches.append((match.start(),match.end(),metric))
    for match in re.finditer(r'母公司(?:单体)?净利润|合并净利润',text):
        if not positive_only or not is_negated_span(text,match.start(),match.end()):
            matches.append((match.start(),match.end(),'net_profit'))
    longest=[item for item in matches if not any(other[0]<=item[0] and other[1]>=item[1] and
        other[1]-other[0]>item[1]-item[0] for other in matches)]
    candidates={metric for _,_,metric in longest}
    for match in re.finditer('净利润',text):
        if positive_only and is_negated_span(text,match.start(),match.end()):continue
        if any(start<=match.start() and end>=match.end() for start,end,_ in longest):continue
        # 报表范围可以与指标名分开表述；只在同一分句的明确范围中识别报表净利润，独立概念插话不改变查询默认。
        boundaries=list(re.finditer(r'[，,。；;？?!！]|然后|再(?=解释|说明)|并(?=解释|说明)',text))
        left=max([0,*[m.end() for m in boundaries if m.end()<=match.start()]])
        right=min([len(text),*[m.start() for m in boundaries if m.start()>=match.end()]])
        scoped=any(not is_negated_span(text,left+m.start(),left+m.end()) for m in re.finditer(r'(?:合并|母公司(?:单体)?)(?:报表|利润表|口径)',text[left:right]))
        candidates.add('net_profit' if concept or scoped else 'attributable_net_profit')
    # 增长查询可读取披露增长率或基础金额，具体计算规则由执行条件检查确定。
    if any(word in text for word in ('同比','增长率','变动率')):
        candidates.update(metric.removesuffix('_reported_yoy') for metric in list(candidates) if metric.endswith('_reported_yoy'))
        candidates.update(metric+'_reported_yoy' for metric in list(candidates) if metric+'_reported_yoy' in ALIASES)
    return candidates


PERIOD_ALIASES={'年度报告':'FY','年报':'FY','全年':'FY','半年度报告':'HY','半年报':'HY',
    '上半年':'HY','半年':'HY','第一季度报告':'Q1','第一季度累计':'Q1','一季报':'Q1','一季度累计':'Q1',
    '第三季度报告':'Q3','前三季度':'Q3','三季报':'Q3'}


def is_negated_span(question,start,end):
    # 直接排除条件在此绑定；复杂语义继续交由理解与审核步骤处理。
    before=re.split(r'[，,。；;？?！!]',question[:start])[-1]
    after=question[end:]
    direct=re.search(r'(?:不是|不要|不查|别查|别看|不看|排除|不用|并非|不改(?:成|为)?|而非)\s*$',before)
    grouped=list(re.finditer(r'不是|并非|排除|(?:不要|别|不用|无需|不)(?:再|重新|另外|另)?(?:查(?:询)?|列(?:出)?|展示|输出|显示|保留|看|比较|计算)',before))
    in_group=bool(grouped and not re.search(r'而是|而(?:查|看|用)|但是|但(?:查|看|用)|改(?:查|看|用|成|为)|换(?:成|为)|(?:只|继续)(?:查|看|显示)|(?:用|按)',before[grouped[-1].end():]))
    return bool(direct or in_group or re.match(r'\s*(?:不要|不查|排除)',after))


def positive_period_mentions(question):
    pattern='|'.join(re.escape(label) for label in sorted(PERIOD_ALIASES,key=len,reverse=True))
    return [(match.start(),match.end(),PERIOD_ALIASES[match.group()])
        for match in re.finditer(pattern,question) if not is_negated_span(question,match.start(),match.end())]


def positive_latest_mentions(question):
    return any(not is_negated_span(question,m.start(),m.end()) for m in re.finditer(r'最新|最近',question))


def positive_calendar_mentions(question):
    return any(not is_negated_span(question,m.start(),m.end()) for m in re.finditer('自然年',question))


def explicit_company_set_constraints(question,companies):
    """完整公司集合指令按登记身份校验；否定某公司的某个指标属于目标条件，不等于排除整家公司。"""
    names={span:code for code,name in companies.items()
        for span in (code,*(name if isinstance(name,list) else [name]))}
    if not names:return {'keep':set(),'exclude':set()}
    entity='(?:'+'|'.join(re.escape(name) for name in sorted(names,key=len,reverse=True))+')'
    group=entity+r'(?:\s*(?:和|与|及|、|,|，)\s*'+entity+')*'
    tail=r'\s*(?:这家(?:公司)?|公司)?\s*(?:吧|了)?\s*(?=[，,。；;！？?!]|$)'
    keep=set();exclude=set()
    for clause in re.finditer(r'(?:只留|仅保留|只保留)\s*('+group+')'+tail,question):
        keep.update(names[m.group()] for m in re.finditer(entity,clause.group(1)))
    for pattern in (r'(?:去掉|删掉|删除|排除|剔除)\s*('+group+')'+tail,
                    r'把\s*('+group+r')\s*(?:去掉|删掉|删除|排除|剔除)'+tail):
        for clause in re.finditer(pattern,question):exclude.update(names[m.group()] for m in re.finditer(entity,clause.group(1)))
    return {'keep':keep,'exclude':exclude}


def company_set_is_global(question,companies,binding):
    names={span:code for code,name in companies.items()
        for span in (code,*(name if isinstance(name,list) else [name]))}
    mentioned={code for name,code in names.items() if name in question}
    return mentioned<=binding['keep']|binding['exclude']


def explicit_period_bindings(question):
    bindings=set();boundary=0
    for start,end,period in positive_period_mentions(question):
        prefix=question[boundary:start]
        match=re.search(r'(?<!\d)((?:20\d{2}|\d{2})\s*年?(?:\s*(?:和|及|、|与|到|至|[-—~～])\s*(?:20\d{2}|\d{2})\s*年?)*)\s*$',prefix)
        if match and not is_negated_span(question,boundary+match.start(),end):
            years={2000+int(value) if len(value)==2 else int(value) for value in re.findall(r'20\d{2}|\d{2}',match.group(1))}
            if len(years)>=2 and re.search(r'到|至|[-—~～]',match.group(1)):years.update(range(min(years),max(years)+1))
            bindings.update((year,period) for year in years)
        boundary=end
    return bindings


def explicit_single_quarters(question):
    """在财务目标确定后登记明确单季身份，由能力检查说明不支持。"""
    if any(word in question for word in ('改累计','转累计','改为累计','不要单季','不查单季')):return set()
    values={int(value) if value.isdigit() else '一二三四'.index(value)+1
            for value in re.findall(r'第?([一二三四1234])季度',question)}
    return values if any(word in question for word in ('单季','单季度')) else values & {2,4}


def explicit_report_count(question):
    numerals={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
    values=set()
    pattern=r'(?:最新|最近|过去|近|前)\s*(三十|二十[一二三四五六七八九]?|十[一二三四五六七八九]?|[一二三四五六七八九两]|\d{1,2})\s*(?:个)?(?:完整)?(?:自然年|年|份)'
    for word in re.findall(pattern,question):
        if word.isdigit():value=int(word)
        elif '十' in word:
            tens,ones=word.split('十');value=numerals.get(tens,1)*10+numerals.get(ones,0)
        else:value=numerals[word]
        if 1<=value<=30:values.add(value)
    return next(iter(values)) if len(values)==1 else None


def explicit_output_unit(question):
    units=[]
    pattern=r'(?:单位(?:为|是|用|[:：])?|(?:纵轴|横轴|坐标轴|金额|数值)(?:单位)?(?:用|以|按|为|是|[:：])?|按|用|以|换成|改成|改用|多少|换算(?:为|成))\s*(亿元|万元|元/股|元|%|百分比)'
    for match in re.finditer(pattern,question):
        start,end=match.span(1)
        if is_negated_span(question,start,end) or re.search(r'(?:不|别|不要|不再|无需)(?:用|按|以|换算为|换成)\s*$',question[:start]):continue
        units.append(match[1])
    for match in re.finditer(r'(?:^|[，,。；;？?])\s*(亿元|万元|元/股|元|%|百分比)\s*(?=[，,。；;！？?!]|$)',question):
        if not is_negated_span(question,match.start(1),match.end(1)):units.append(match[1])
    distinct=set('%' if unit=='百分比' else unit for unit in units)
    return next(iter(distinct)) if len(distinct)==1 else None


def explicit_presentation_bindings(question):
    """提取无歧义的展示值；冲突的图形、排序或数量保持待确定状态。"""
    values={}
    patterns={
        'presentation.chart_type':{'bar':r'柱状图|条形图','line':r'折线图','pie':r'饼图|饼状图','scatter':r'散点图'},
        'presentation.order':{'asc':r'最低|最少|从低到高|升序','desc':r'最高|最多|从高到低|降序'},
        'presentation.format':{'table':r'只(?:要|给|用)表(?:格)?|列(?:个|成)表|用表格|表格(?:即可|就好|就行)'},
    }
    for field,choices in patterns.items():
        found={value for value,pattern in choices.items() for m in re.finditer(pattern,question)
            if not is_negated_span(question,m.start(),m.end()) and not re.search(r'(?:不|别|不要)(?:画|用|选)?\s*$',question[:m.start()])}
        if len(found)==1:values[field]=found.pop()
    counts=[]
    for m in re.finditer(r'(?:前|后|最(?:高|低|多|少)(?:的)?|(?:取|列出|显示|给出)(?:其中)?)\s*(\d{1,3}|[一二两三四五六七八九十])\s*(?:名|家)',question):
        if is_negated_span(question,m.start(),m.end()):continue
        word=m[1];n=int(word) if word.isdigit() else {'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}[word]
        if 1<=n<=100:counts.append(n)
    if len(set(counts))==1:values['presentation.limit']=counts[0]
    precision=explicit_precision(question)
    if precision is not None:values['presentation.decimals']=precision
    return values


def explicit_precision(question):
    """同一小数位身份供理解、编译和交付核验共用；否定与冲突不自动选值。"""
    numerals={'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
    number=r'(\d{1,2}|[零一二两三四五六七八九十])'
    patterns=(r'(?:保留|精确到|显示|展示|给出)\s*'+number+r'\s*位(?:小数)?',
              r'小数点后\s*(?:保留|显示|取)?\s*'+number+r'\s*位',number+r'\s*位小数')
    values=set()
    for pattern in patterns:
        for match in re.finditer(pattern,question):
            if is_negated_span(question,match.start(),match.end()) or re.search(r'(?:不|别|不要|无需)\s*$',question[:match.start()]):continue
            word=match[1];value=int(word) if word.isdigit() else numerals[word]
            if value<=12:values.add(value)
    return next(iter(values)) if len(values)==1 else None


def presentation_goal_applies(field, kind):
    if field=='presentation.chart_type':return kind=='chart'
    if field in {'presentation.order','presentation.limit'}:return kind=='rank'
    return kind in {'lookup','compare','rank','chart','quote','cause','sign'}


def explicit_quote_mode(question):
    """原句摘录与出处定位分别登记；模式冲突时不自行选择。"""
    def positive(match):
        before=re.split(r'[，,。；;]',question[:match.start()])[-1]
        return not is_negated_span(question,match.start(),match.end()) and not re.search(
            r'(?:不|别|不要|无需|不用)(?:再|逐字|完整|直接)?(?:摘录|摘出|引用)?\s*$',before)
    literal=any(positive(m) for m in re.finditer(
        r'逐字|(?:摘(?:出|录)|引用)(?:原文|原句|原表)?|原句|原始(?:表格|行)',question))
    location=any(not is_negated_span(question,m.start(),m.end()) for m in re.finditer(
        r'只(?:定位|给|要|说明)(?:出处|位置|页码)|只(?:定位)?出处',question))
    return None if literal and location else 'literal' if literal else 'location' if location else None


def clarification_question(text):
    """仅保留面向用户的条件询问，模型字段标签不进入正文。"""
    text=re.sub(r'^\s*text\s*[:：]\s*','',text,flags=re.I).strip().strip('"')
    if re.search(r'(?:text|target|number|context_references)\s*[:：]|->',text,re.I):return None
    asks=bool(re.search(r'[？?]|请(?:明确|确认|选择|提供|指定).{0,24}(?:公司|代码|指标|年份|期间|报告期|口径|单位|数量|图形|排序|形式|哪个|哪种|对象|含义|意思|指代)',text))
    return text if asks else None


def pending_company_answer(question,pending,companies):
    """待澄清任务已确定指标和时间时，单独补登记公司仍是条件回复。"""
    if not pending:return False
    raw=pending.model_dump(mode='json') if hasattr(pending,'model_dump') else pending
    conditions=raw.get('conditions') or {}
    if re.search(r'换个话题|新(?:的)?话题|另(?:一个|一件|起)|不(?:再)?延续|清空|清除|重置|解释|概念|区别|定义|什么意思',question):return False
    return bool(conditions.get('metrics') and not conditions.get('codes') and not conditions.get('all_companies')
        and any(not is_negated_span(question,m.start(),m.end()) for code,name in companies.items()
            for span in (code,name) for m in re.finditer(re.escape(span),question))
        and not metric_candidates(question,positive_only=True)
        and not re.search(r'(?<!\d)(?:19|20)\d{2}(?!\d)',question))


def explicit_financial_query(question):
    """明确取数动作与登记指标的同句组合用于检查矛盾提案，不生成执行目标。"""
    for clause in re.split(r'[，,。；;]',question):
        if re.search(r'不要|不查|别查|不查询|不取数|不用查|如何|怎么|规则|是否支持|能否支持|定义|概念|什么意思',clause):continue
        if re.search(r'(?:查(?:询|一下)?|取(?:出|数)|列出|给出|画|我要|想看|给我|提供).{0,40}',clause) and metric_candidates(clause,positive_only=True):return True
    return False


def explicit_collection_span(question):
    for m in re.finditer(r'库内公司|库里(?:的)?公司|库中(?:的)?公司|全库(?:公司)?|所有(?:已覆盖|库内)?公司|库内(?:的)?(?=\s*(?:20\d{2}|所有|全部|各家|$))',question):
        if not is_negated_span(question,m.start(),m.end()):return m.group()
    return None


def explicit_company_replacements(question,companies):
    names={span:code for code,name in companies.items() for span in (code,*(name if isinstance(name,list) else [name]))}
    if not names:return []
    entity='(?:'+'|'.join(re.escape(n) for n in sorted(names,key=len,reverse=True))+')'
    pattern=r'(?:不是|并非)\s*('+entity+r')\s*[，,]?\s*(?:而是|是|改成|换成)\s*('+entity+r')(?=[，,。；;！？?!\s]|$)'
    return [(names[m[1]],names[m[2]]) for m in re.finditer(pattern,question)]


def unregistered_company_literals(question,companies):
    """只核对明确代码或紧接年份和指标的完整名称分句；未匹配名称表示输入身份未登记，复杂歧义仍由模型理解。"""
    found=[]
    for m in re.finditer(r'(?:股票代码|股票|代码)\s*(\d{6})(?!\d)|(?:^|[，,。；;])\s*(\d{6})(?=\s*20\d{2}\s*年?)',question):
        code=m[1] or m[2]
        if code not in companies and not is_negated_span(question,m.start(),m.end()):found.append(code)
    metrics='|'.join(re.escape(a) for a in sorted(ALIASES,key=len,reverse=True))
    # 以完整分句开头定位名称，两个年份之间的连接词不能成为公司身份。
    pattern=r'(?:^|[，,。；;])\s*([\u4e00-\u9fffA-Za-z·]{2,20})\s*20\d{2}\s*(?:年(?:度)?(?:年报|年度报告)?|年度报告)?\s*(?:的)?(?:'+metrics+')'
    for m in re.finditer(pattern,question):
        name=re.sub(r'^(?:请问|请告诉我|请帮我|帮我|请)?(?:改(?:成|为)?|换(?:成|为)?|重新|继续|接着|再)?(?:我要|我想看|想看|给我|提供|列出|给出|查询|查一下|查|看|解释|分析|说明)?','',m[1])
        if len(name)<2 or any(n in name for n in companies.values()) or any(a in name for a in ALIASES):continue
        if re.search(r'母公司|合并|股东|库内|库里|全库|所有|最近|最新|过去|自然年|它|这家|那家|只查|不要|不查|别查|不是|再看|再查|然后|另外|顺便',name):continue
        if is_negated_span(question,m.start(),m.end()):continue
        found.append(name)
    return list(dict.fromkeys(found))


def memory_clear_bindings(question,companies=()):
    """要求删除记忆的本轮逐字依据，区别于普通条件修改。"""
    bindings={'company_context':set(),'financial_context':set()}
    names=[name for name in (companies.values() if isinstance(companies,dict) else companies)]
    verb=r'忘(?:记|掉)?|清(?:空|除)|不(?:用|要)?(?:再)?(?:沿用|延续|继承)|别(?:再)?(?:沿用|延续|继承)|不(?:再)?记(?:住|着)?|别记(?:住|着)?'
    for clause in [part.strip() for part in re.split(r'[，,。；;？?!！\n]',question) if part.strip()]:
        if not re.search(verb,clause) or re.search(r'(?:不要|不用|别|不能|不必|无需)(?:再)?(?:忘|清空|清除)',clause):continue
        company='公司' in clause.replace('母公司','') or any(name in clause for name in names)
        financial=bool(re.search(r'任务|查询|上下文|话题|所有条件|全部条件|财务条件|刚才的条件|之前的条件|前面(?:的)?内容|上一轮(?:的)?内容|此前(?:的)?内容',clause))
        if company:bindings['company_context'].add(clause)
        if financial:bindings['financial_context'].add(clause)
    return bindings


def literal_memory_edits(question,companies):
    from src.agent.v3.contracts import MemoryEdit
    return [MemoryEdit(field=field,operation='clear',text=sorted(spans)[0])
        for field,spans in memory_clear_bindings(question,companies).items() if spans]


def explicit_condition_amendment(question):
    """检查连续关系与本轮明确修改是否矛盾，不在此重新选择或改写目标。"""
    return bool(re.search(r'改(?:成|为|看|查)|换(?:成|为)|再(?:看|查|用)|加上|添加|只留|只保留|仅保留|去掉|删掉|其他不变|其它不变',question)) and not bool(re.search(r'换个话题|新问题|重新开始|独立(?:问题|查询)',question))


def independent_financial_allowed(question,memory,companies=()):
    """明确修改已确认条件时检查连续关系，防止把局部编辑当作独立重置；不代替任务、指标或公司的选择。"""
    financial={'lookup','compare','rank','chart','quote','cause','sign'}
    retained=[*memory.get('active_goals',[]),*(memory.get('pending') or {}).get('goals',[])]
    if not any(g['kind'] in financial for g in retained):return True
    if re.search(r'换个话题|新问题|重新开始|独立(?:问题|查询)|清空|忘掉',question):return True
    if explicit_condition_amendment(question):return False
    paths=literal_condition_paths(question,companies)
    if paths and all(path.startswith(('time.','presentation.','restrictions.')) for path in paths):return False
    if paths and paths<={'codes','scope','all_companies'}:return False
    if re.search(r'它(?:们)?|这家(?:公司)?|那家(?:公司)?|刚才|之前|这个数|那个数|这次',question) and not (
        isinstance(companies,dict) and any(code in question or name in question for code,name in companies.items())):
        return False
    return True


def literal_condition_paths(question, companies=()):
    """记录需保守处理的条件字段身份，不猜取值、默认规则或执行路径。"""
    from src.agent.v3.condition_updates import mentioned_years
    paths=set()
    if isinstance(companies,dict) and any(code in question or name in question for code,name in companies.items()):paths.add('codes')
    if metric_candidates(question):paths.add('metrics')
    if mentioned_years(question):paths.add('time.years')
    if positive_period_mentions(question):paths.add('time.periods')
    if explicit_report_count(question) is not None:paths.update({'time.mode','time.span'})
    if explicit_single_quarters(question):paths.update({'time.single_quarter','time.quarters'})
    if explicit_output_unit(question):paths.add('presentation.unit')
    if re.search(r'小数|保留.*位',question):paths.add('presentation.decimals')
    if re.search(r'表格|只要表|只要图',question):paths.add('presentation.format')
    if re.search(r'母公司|合并(?:口径|报表|净利润)',question):paths.add('scope')
    if re.search(r'不查(?:数|数字)|不查询',question):paths.add('restrictions.no_query')
    if re.search(r'不画图|不要图|不需要图',question):paths.add('restrictions.no_chart')
    if re.search(r'不重复|不用重复',question):paths.add('restrictions.no_repeat')
    if re.search(r'差额|相差|同比|相对变化|百分点',question):paths.add('calculation')
    return paths


def literal_fact_reference(question):
    """真实指代的数值需绑定核实事实，不能当作独立假设例子。"""
    for clause in re.split(r'[，,。；;？?!！\n]',question):
        if re.search(r'这个数|那个数|该数值|这个值|那个值|该值',clause) and not re.search(r'如果|假如|假设|例如|举例',clause):
            return True
    return False


def goal_source_supported(goal,understanding,previous):
    """拒绝能精确判断的目标矛盾，保留理解和澄清的职责边界。"""
    span=goal.intent_source
    comparison=[match for match in re.finditer(r'比较|对比|比一比|谁更(?:高|低|多|少)|哪家更(?:高|低|多|少)',span)
        if not is_negated_span(span,match.start(),match.end())]
    if goal.kind=='lookup' and comparison and not re.search(r'查(?:询)?|分别.*多少|各.*多少|数值|数字',span):return False
    if goal.kind=='concept' and goal.concept_mode=='difference' and comparison and len(metric_candidates(span))<2 and not re.search(r'概念|定义|区别|差别|区分|异同',span):return False
    patterns={'chart':r'图|折线|柱状|条形|散点|饼状|气泡',
        'quote':r'出处|来源|原文|原句|原表|页码|哪页|哪里来|哪儿来|哪段|摘录|引用|佐证',
        'cause':r'原因|为什么|为何|为啥|何以|怎么会',
        'rules':r'为什么|为何|为啥|怎么|如何|解释|说明|说说|告诉|请问|什么|哪些|哪|有没有|是否|吗|呢|？|\?',
        'catalog':r'哪些|有什么|有哪些|覆盖|支持|目录|收录|范围|库内|库里|全库|能做|能查|你能|可以|你好|您好|hello|hi'}
    if goal.kind=='concept':
        patterns['concept']={'definition':r'定义|概念|意思|含义|理解|解释|说明|是什么|是啥|什么叫|何为|讲讲|科普|介绍|了解|表示什么|表示啥|代表什么|代表啥|explain|definition|what.*mean',
            'difference':r'区别|差别|区分|有何不同|有什么不同|异同|之间.*不同|一样吗|相同吗|对比|比较|difference',
            'implication':r'(?:数|负|正|零|增长|下降|上升|下跌|扭亏|盈利|亏损).*(?:含义|意思|说明|意味|代表|理解|怎么|什么)|(?:含义|意思|说明|理解).*(?:数|负|正|增长|下降|亏损)'}[goal.concept_mode]
    if goal.kind=='rules' and not re.search(r'系统|默认|规则|选(?:择|取|的|了|出|定)|选.*(?:年|报告期|公司|指标)|查的是|按什么|依据|当前(?:公司|指标|条件|期间|年份)|'
        r'(?:这次|刚才|之前|刚刚).*查.*(?:哪|什么|指标|年|公司)|你(?:这次|刚才)?(?:用|查|选|算|画)的|'
        r'现在.*(?:指|说|条件|公司|指标)|为什么.*(?:用|查).*(?:年份|年报|报告期)|(?:拿|用).*年份.*(?:补|缺)',span):
        return False
    pattern=patterns.get(goal.kind)
    if not pattern or re.search(pattern,span,re.IGNORECASE):return True
    prior=[*previous.active_goals,*(previous.pending.goals if previous.pending else [])]
    return understanding.continuity in {'continue','resume'} and bool(understanding.modifications) and any(old.kind==goal.kind for old in prior)


def requirement_source_supported(requirement,request):
    from src.agent.v3.contracts import Goal,DialogueState
    source=requirement.source_ref or request.question
    if not source or source not in request.question:return False
    if requirement.kind=='greeting':return bool(re.search(r'你好|您好|hello|\bhi\b|早上好|晚上好',source,re.IGNORECASE))
    if requirement.kind not in {'rules','quote','chart','cause'}:return True
    return goal_source_supported(Goal(id='requirement',kind=requirement.kind,text=source,intent_source=source),request,DialogueState())


def positive_years(question):
    """直接排除的年份不属于正向查询要求；范围中的中间年仍按原规则展开。"""
    from src.agent.v3.condition_updates import mentioned_years
    excluded=set();positive=set()
    for match in re.finditer(r'(?<!\d)(20\d{2})(?!\d)',question):
        before=re.split(r'[，,。；;]',question[:match.start()])[-1]
        negated=is_negated_span(question,match.start(),match.end()) or re.search(r'(?:不|不要|别|禁止|不再)(?:拿|用|取|查|看|查询|采用)\s*$',before)
        (excluded if negated else positive).add(int(match[1]))
    return mentioned_years(question)-(excluded-positive)
