"""作品说明：将本轮编辑约束为有限类型，同时向两模型开放全部任务类别。"""
from copy import deepcopy
from .catalog import METRICS
import re


def turn_schema(schema, payload):
    schema=deepcopy(schema)
    definitions=schema['$defs']
    question=payload['question']
    companies=payload.get('companies',{})
    from .request_bindings import metric_candidates
    candidates=metric_candidates(question)
    memory=payload.get('state') or {}
    has_context=bool(memory.get('active_goals') or memory.get('pending') or memory.get('suspended') or memory.get('recent_fact_count'))
    refs={}
    for name,definition in list(definitions.items()):
        props=definition.get('properties',{})
        if 'source_ref' in props:
            props.pop('text',None)
            props['source_ref']={'type':'string','enum':[question]}
            definition['required']=[key for key in definition.get('required',[]) if key!='text']
        field=props.get('field',{}).get('const')
        if not field:continue
        props['text']={'type':'string','enum':[question]}
        if field=='codes':props['value']['items']['enum']=list(companies)
        if field=='metrics':
            props['value']['items']['enum']=list(METRICS)
            if candidates:props['value']['minItems']=1
        operations=props.get('operation',{}).get('enum',[])
        if operations:
            # 作品说明：稀疏字段缺省表示沿用；显式inherit不能附带随后被丢弃的新值。
            operations=[op for op in operations if op!='inherit']
            if not re.search(r'清空|清除|重置|不指定|删除全部|去掉全部',question):operations=[op for op in operations if op!='clear']
            props['operation']={'type':'string','enum':operations}
        if not has_context:props['operation']={'type':'string','enum':['replace']}
        refs[field]={'type':'array','items':{'$ref':'#/$defs/'+name},'minItems':1,'maxItems':1}
        props.pop('field',None);props.pop('text',None)
        definition['required']=[key for key in definition.get('required',[]) if key not in {'field','text'}]
        from .contracts import SET_PATHS
        if field in SET_PATHS and {'add','remove'}<=set(props['operation'].get('enum',[])):
            operation_name=name+'SetOperations'
            operation_definition=deepcopy(definition)
            operation_definition['properties']['operation']={'type':'string','enum':['add','remove']}
            definitions[operation_name]=operation_definition
            refs[field]={'anyOf':[refs[field],{'type':'array','items':{'$ref':'#/$defs/'+operation_name},'minItems':2,'maxItems':4}]}
    # 作品说明：字段映射避免重复替换覆盖条件，使局部修改区别于整份默认对象。
    from .request_bindings import explicit_output_unit,explicit_single_quarters,explicit_report_count
    from .catalog import ALIASES
    required=[]
    from .request_bindings import explicit_presentation_bindings,explicit_collection_span,memory_clear_bindings
    for field,value in explicit_presentation_bindings(question).items():
        required.append(field)
        definition=definitions[refs[field]['items']['$ref'].rsplit('/',1)[-1]]
        definition['properties']['value']['enum']=[value]
    if explicit_collection_span(question):
        required.append('all_companies')
        definition=definitions[refs['all_companies']['items']['$ref'].rsplit('/',1)[-1]]
        definition['properties']['value']['enum']=[True]
    # 作品说明：代码须有本轮身份或确认记忆依据；缺公司时允许空集合，避免模型猜登记公司。
    clears=memory_clear_bindings(question,companies)
    current_codes={code for code,name in companies.items() if code in question or name in question}
    retained=set()
    if not any(clears.values()):
        for c in [memory.get('conditions') or {},memory.get('suspended') or {},
                  (memory.get('pending') or {}).get('conditions') or {},*(memory.get('goal_conditions') or {}).values()]:
            retained.update(c.get('codes') or [])
    if (not has_context or any(clears.values())) and not current_codes and not retained and not explicit_collection_span(question):
        for rule in refs['codes'].get('anyOf',[refs['codes']]):
            definitions[rule['items']['$ref'].rsplit('/',1)[-1]]['properties']['value']['maxItems']=0
    from .request_bindings import explicit_company_replacements
    replacements=explicit_company_replacements(question,companies)
    code_bindings=[set(c.get('codes') or []) for c in (memory.get('goal_conditions') or {}).values()]
    if replacements and (not code_bindings or all(c==code_bindings[0] for c in code_bindings)):
        expected=set(retained)
        for removed,added in replacements:expected.discard(removed);expected.add(added)
        rule=refs['codes'].get('anyOf',[refs['codes']])[0]
        definition=definitions[rule['items']['$ref'].rsplit('/',1)[-1]]
        definition['properties']['operation']={'type':'string','enum':['replace']}
        definition['properties']['value']={'type':'array','items':{'type':'string','enum':sorted(expected)},'minItems':len(expected),'maxItems':len(expected),'uniqueItems':True}
        refs['codes']=rule
        required.append('codes')
        if has_context:schema['properties']['continuity']['enum']=['continue','resume']
    if any(name in question or code in question for code,name in companies.items()):required.append('codes')
    if any(alias in question for alias in ALIASES):required.append('metrics')
    if re.search(r'(?<!\d)(?:20\d{2}|\d{2})(?=\s*年)|(?<!\d)20\d{2}(?!\d)',question):required.append('time.years')
    if re.search(r'母公司(?:单体|口径|营业收入|净利润|利润表|报表)|合并(?:口径|营业收入|净利润)',question):required.append('scope')
    if explicit_output_unit(question):required.append('presentation.unit')
    if re.search(r'只(?:要|给)表(?:格)?|列(?:个|成)表|用表格',question):required.append('presentation.format')
    if explicit_single_quarters(question):required.extend(['time.single_quarter','time.quarters'])
    if explicit_report_count(question) is not None:required.append('time.span')
    precision=re.search(r'(?:保留|精确到)\s*([0-9]+|[零一二两三四五六七八九十])\s*位(?:小数)?',question)
    if precision:
        word=precision[1];digits=int(word) if word.isdigit() else {'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}[word]
        if digits<=12:
            required.append('presentation.decimals')
            definition=definitions[refs['presentation.decimals']['items']['$ref'].rsplit('/',1)[-1]]
            definition['properties']['value']={'type':'integer','enum':[digits]}
    if 'scope' in required:
        parent=bool(re.search(r'母公司(?:单体|口径|营业收入|净利润|利润表|报表)',question))
        definitions['ScopeEdit']['properties']['value']={'type':'string','enum':['parent' if parent else 'consolidated']}
    if 'presentation.unit' in required:
        definition=definitions[refs['presentation.unit']['items']['$ref'].rsplit('/',1)[-1]]
        definition['properties']['value']={'type':'string','enum':[explicit_output_unit(question)]}
    if 'presentation.format' in required:
        definition=definitions[refs['presentation.format']['items']['$ref'].rsplit('/',1)[-1]]
        definition['properties']['value']={'type':'string','enum':['table']}
    for pattern,field in ((r'(?:不|别|不要|先别)(?:重新|再次|再)?(?:查数|查数字|查询数字)','no_query'),
                          (r'(?:不|别|不要)(?:再)?(?:画图|生成图|绘图)','no_chart'),
                          (r'(?:不|别|不要)(?:再)?重复(?:金额|数字)','no_repeat')):
        if re.search(pattern,question):
            required.append('restrictions.'+field)
            rule=refs['restrictions.'+field]
            definition=definitions[rule['items']['$ref'].rsplit('/',1)[-1]]
            definition['properties']['value']={'type':'boolean','enum':[True]}
    # 作品说明：明确单数事实代词要求建立引用，不直接选择事实或替用户生成答案。
    from .request_bindings import literal_fact_reference
    if has_context and literal_fact_reference(question) and not any(clears.values()):
        spans=[m.group() for m in re.finditer(r'这个数|那个数|该数值|这个值|那个值|该值',question)]
        references=schema['properties']['context_references']
        references['minItems']=1
        props=definitions['ContextReference']['properties']
        props['target']={'type':'string','enum':['fact']}
        props['number']={'type':'string','enum':['singular']}
        props['text']={'type':'string','enum':list(dict.fromkeys(spans))}
        schema['properties']['continuity']['enum']=['continue','resume']
        sign=re.search(r'(?:这个数|那个数|该数值|这个值|那个值|该值)\s*是\s*(负|正|零)',question)
        if sign:
            schema['properties']['claimed_sign']={'type':'string','enum':[{'负':'negative','正':'positive','零':'zero'}[sign[1]]]}
            schema['required'].append('claimed_sign')
    mapping={'type':'object','properties':refs,'required':required,'additionalProperties':False}
    schema['properties']['edits']=mapping
    definitions['GoalAssignment']['properties']['edits']={**mapping,'required':[]}
    from .contracts import GoalKind
    from typing import get_args
    goal=dict(type='object',properties={
        'kind':dict(type='string',enum=list(get_args(GoalKind))),
        'concept_mode':dict(type='string',enum=['definition','difference','implication']),
        'quote_mode':dict(type='string',enum=['literal','location']),
        'catalog_target':dict(type='string',enum=['companies','periods','metrics','capabilities']),
        'context_goal_id':{'anyOf':[{'type':'string'},{'type':'null'}]},
        'execution_ref':{'anyOf':[{'type':'string'},{'type':'null'}]},
    },required=['kind'],additionalProperties=False)
    schema['properties']['goals']={'type':'array','items':goal,'minItems':1,'maxItems':6}
    if not has_context:
        schema['properties']['continuity']['enum']=['new','clear']
    schema['required']=list(dict.fromkeys([*schema.get('required',[]),'edits','assignments','clarification','continuity']))
    return schema


def turn_guide(schema):
    """作品说明：以同源紧凑说明解释有限字段，减少重复完整合同带来的上下文负担。"""
    definitions=schema['$defs']
    fields={}
    for field,rule in schema['properties']['edits']['properties'].items():
        rule=rule.get('anyOf',[rule])[0]
        props=definitions[rule['items']['$ref'].rsplit('/',1)[-1]]['properties']
        fields[field]={'operation':props['operation'],'value':props['value']}
    return dict(top_level=list(schema['properties']),goals=schema['properties']['goals']['items']['properties'],
        edits=dict(format='field -> [{operation, value}]; program supplies field and current-input provenance',required=schema['properties']['edits']['required'],fields=fields),
        continuity=schema['properties']['continuity'],
        context_references=definitions['ContextReference']['properties'],
        memory_edits=definitions['MemoryEdit']['properties'],
        defaults='assignments=[], clarification=[], memory_edits=[], context_references=[], unknown_companies=[], uncertain_companies=[]; collection_text=null, unresolved_reference=null, claimed_sign=none')


def decode_turn(raw,question=None):
    if not isinstance(raw.get('goals'),list) or any(not isinstance(g,dict) for g in raw['goals']):raise ValueError('Goals must be typed objects')
    if question is not None:
        for index,goal in enumerate(raw.get('goals',[]),1):
            goal['id']='g'+str(index);goal['source_ref']=question
            # 作品说明：紧凑传输共用一个目标形状，kind决定附带模式是否适用；无效附带模式不产生新目标。
            for field,kind in (('concept_mode','concept'),('quote_mode','quote'),('catalog_target','catalog')):
                if goal.get('kind')!=kind:goal.pop(field,None)
    def edits(value):
        if not isinstance(value,dict):raise ValueError('Edits must be a field map')
        result=[]
        for field,items in value.items():
            if not isinstance(items,list) or not items or any(not isinstance(item,dict) or item.get('field',field)!=field for item in items):
                raise ValueError('An edit must match its field-map key')
            for item in items:
                item={**item,'field':field}
                if question is not None:item['text']=question
                result.append(item)
        return result
    raw['edits']=edits(raw.get('edits',{}))
    assignments=raw.get('assignments',[])
    if not isinstance(assignments,list) or any(not isinstance(a,dict) for a in assignments):raise ValueError('Assignments must be typed objects')
    for assignment in assignments:assignment['edits']=edits(assignment.get('edits',{}))
    return raw
