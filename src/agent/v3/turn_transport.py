"""将本轮编辑约束为有限类型，同时向两模型开放全部任务类别。"""
from copy import deepcopy
from .catalog import METRICS
import re


def normalize_turn_response(raw,question):
    """仅规范已确认无业务含义的传输外壳，业务字段仍完整接受类型检查。"""
    value=deepcopy(raw);changes=[]
    def remove_marker(item):
        if item.get('type')=='json_object':
            item.pop('type');changes.append('drop_json_mode_marker')
    remove_marker(value)
    if (set(value)=={'question','context','output'} and value['question']==question
            and value['context']=={} and isinstance(value['output'],dict)):
        value=value['output'];changes.append('unwrap_example_output')
        remove_marker(value)
    return value,changes


def turn_schema(schema, payload):
    """传输合同只约束字段形状和目录，不用关键词提前裁定用户含义。"""
    schema=deepcopy(schema)
    definitions=schema['$defs']
    companies=payload.get('companies',{})
    memory=payload.get('state') or {}
    refs={}
    from .contracts import SET_PATHS,GoalKind,CatalogTarget
    from typing import get_args
    for name,definition in list(definitions.items()):
        props=definition.get('properties',{})
        field=props.get('field',{}).get('const')
        if not field:continue
        # 未填写lifetime时采用程序默认；普通展示不必由模型猜保存策略。
        if field.startswith(('presentation.','restrictions.')):
            props['lifetime']={'type':'string','enum':['turn','task','session'],
                'description':'可省略。普通展示随任务保存，不查数/不重复默认本轮；仅明确例外时填写turn或session。'}
        else:
            props.pop('lifetime',None)
        definition['required']=[key for key in definition.get('required',[]) if key not in {'field','text','lifetime'}]
        if field=='codes':props['value']['items']['enum']=list(companies)
        if field=='metrics':props['value']['items']['enum']=list(METRICS)
        if 'enum' in props.get('operation',{}):
            props['operation']['enum']=[value for value in props['operation']['enum'] if value!='inherit']
        props.pop('field',None);props.pop('text',None)
        refs[field]={'type':'array','items':{'$ref':'#/$defs/'+name},'minItems':1,'maxItems':1}
        if field in SET_PATHS and {'add','remove'}<=set(props['operation'].get('enum',[])):
            operation_name=name+'SetOperations'
            operation_definition=deepcopy(definition)
            operation_definition['properties']['operation']={'type':'string','enum':['add','remove']}
            definitions[operation_name]=operation_definition
            refs[field]={'anyOf':[refs[field],{'type':'array','items':{'$ref':'#/$defs/'+operation_name},'minItems':2,'maxItems':4}]}
    mapping={'type':'object','properties':refs,'required':[],'additionalProperties':False}
    schema['properties']['edits']=deepcopy(mapping)
    # 旧诊断提案仍可解码；生产示范使用目标内条件，不要求交叉维护目标编号。
    definitions['GoalAssignment']['properties']['edits']=deepcopy(mapping)
    financial={'lookup','compare','rank','chart','quote','cause','sign'}
    pending=memory.get('pending')
    if pending:
        retained_ids=[g['id'] for g in pending.get('goals',[]) if g.get('kind') in financial and g.get('id')]
    else:
        retained_ids=list(memory.get('goal_conditions') or {}) or [g['id'] for g in memory.get('active_goals',[]) if g.get('kind') in financial and g.get('id')]
    legacy_goals=pending.get('goals',[]) if pending else memory.get('active_goals',[])
    unidentified=not retained_ids and any(g.get('kind') in financial and not g.get('id') for g in legacy_goals)
    context_identity={'anyOf':[{'type':'string'},{'type':'null'}]} if unidentified else {'enum':list(dict.fromkeys([*retained_ids,None]))}
    goal=dict(type='object',properties={
        'kind':dict(type='string',enum=list(get_args(GoalKind))),
        'edits':deepcopy(mapping),
        'source_ref':dict(type='string',minLength=1),
        'clarification':dict(type='array',items=dict(type='string')),
        'concept_mode':dict(type='string',enum=['definition','difference','implication']),
        'quote_mode':dict(type='string',enum=['literal','location']),
        'quote_granularity':dict(type='string',enum=['location','cell','row']),
        'catalog_target':dict(type='string',enum=list(get_args(CatalogTarget))),
        'context_goal_id':context_identity,
        'execution_ref':{'anyOf':[{'type':'string'},{'type':'null'}]},
    },required=['kind'],additionalProperties=False,
        allOf=[{'if':{'properties':{'kind':{'const':kind}},'required':['kind']},'then':{'required':[field]}}
               for kind,field in [('quote','quote_mode'),('concept','concept_mode'),('catalog','catalog_target')]])
    schema['properties']['goals']={'type':'array','items':goal,'minItems':1,'maxItems':6}
    schema['required']=['goals','continuity']
    return schema


def turn_guide(schema):
    """以同源紧凑说明解释有限字段，减少重复完整合同带来的上下文负担。"""
    definitions=schema['$defs']
    fields={}
    for field,rule in schema['properties']['edits']['properties'].items():
        rule=rule.get('anyOf',[rule])[0]
        props=definitions[rule['items']['$ref'].rsplit('/',1)[-1]]['properties']
        fields[field]={'operation':props['operation'],'value':props['value'],
            **({'lifetime':props['lifetime']} if 'lifetime' in props else {}),
            'required':definitions[rule['items']['$ref'].rsplit('/',1)[-1]].get('required',[])}
    goal_rule=schema['properties']['goals']
    mode_rules=(goal_rule['items'] if goal_rule['type']=='array' else next(iter(goal_rule['properties'].values())))
    required_modes={rule['if']['properties']['kind']['const']:rule['then']['required'] for rule in mode_rules.get('allOf',[])}
    # 云端JSON模式不会替程序执行采样合同，模型可见说明必须保留同一份目标类型、必填项和非空约束。
    guide=dict(type=schema['type'],top_level=[key for key in schema['properties'] if key!='assignments'],required=list(schema.get('required',[])),
        additionalProperties=schema.get('additionalProperties',False),
        goals=deepcopy(goal_rule),required_modes_by_kind=required_modes,
        edits=dict(format='每个goals对象内的edits: field -> [{operation,value}]；相同条件才使用顶层edits。普通展示省略lifetime；明确临时/长期偏好才填写。编号由程序生成，不填写assignments。',required=schema['properties']['edits']['required'],fields=fields),
        continuity=schema['properties']['continuity'],
        context_references=definitions['ContextReference']['properties'],
        memory_edits=definitions['MemoryEdit']['properties'],
        defaults='edits={}, clarification=[], memory_edits=[], context_references=[], unknown_companies=[], uncertain_companies=[]; collection_text=null, unresolved_reference=null, claimed_sign=none; 每个目标的edits={}，lifetime省略时按程序默认')
    # 紧凑合同只附仍被引用的嵌套定义，云端JSON模式也能看到计算角色的实际形状。
    def references(value):
        if isinstance(value,dict):
            return ({value['$ref'].rsplit('/',1)[-1]} if '$ref' in value else set()).union(*(references(item) for item in value.values()))
        if isinstance(value,list):return set().union(*(references(item) for item in value))
        return set()
    needed=references(guide);included={}
    while needed-set(included):
        for name in sorted(needed-set(included)):
            included[name]=deepcopy(definitions[name])
            needed.update(references(included[name]))
    if included:guide['$defs']=included
    return guide


def decode_turn(raw,question=None):
    if isinstance(raw.get('goals'),dict):
        container=raw['goals']
        if set(container)-{'lookup','cause','concept','remaining'} or not isinstance(container.get('remaining'),list):raise ValueError('Correction goals must use the declared typed container')
        mandatory=[]
        for kind in ('lookup','cause','concept'):
            if kind not in container:continue
            value=container[kind]
            if not isinstance(value,dict) or value.get('kind')!=kind:raise ValueError('Correction goal field does not match its declared kind')
            mandatory.append(value)
        raw['goals']=[*mandatory,*container['remaining']]
    if not isinstance(raw.get('goals'),list) or any(not isinstance(g,dict) for g in raw['goals']):raise ValueError('Goals must be typed objects')
    owned=[]
    if question is not None:
        for index,goal in enumerate(raw.get('goals',[]),1):
            goal['id']='g'+str(index)
            goal['source_ref']=goal.get('source_ref') or question
            if goal['source_ref'] not in question:raise ValueError('Goal source must be a literal span of the current input')
            local=goal.pop('edits',{})
            questions=goal.pop('clarification',[])
            if local or questions:owned.append(dict(id=goal['id'],edits=local,clarification=questions))
            # 紧凑传输共用一个目标形状，kind决定附带模式是否适用；无效附带模式不产生新目标。
            for field,kind in (('concept_mode','concept'),('quote_mode','quote'),('quote_granularity','quote'),('catalog_target','catalog')):
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
    assignments=[*raw.get('assignments',[]),*owned]
    if not isinstance(assignments,list) or any(not isinstance(a,dict) for a in assignments):raise ValueError('Assignments must be typed objects')
    for assignment in assignments:assignment['edits']=edits(assignment.get('edits',{}))
    raw['assignments']=assignments
    return raw


def encode_turn_proposal(plan):
    """纠错与首次理解使用同一目标内条件形状，不回灌内部编号和并行关联表。"""
    raw=plan.model_dump(mode='json') if hasattr(plan,'model_dump') else deepcopy(plan)
    def mapping(edits):
        grouped={}
        for edit in edits:
            item={key:edit[key] for key in ('operation','value') if key in edit}
            if edit['field'].startswith(('presentation.','restrictions.')) and edit.get('lifetime') is not None:
                item['lifetime']=edit['lifetime']
            grouped.setdefault(edit['field'],[]).append(item)
        return grouped
    assigned={assignment['id']:assignment for assignment in raw.pop('assignments',[])}
    fields={'kind','concept_mode','quote_mode','quote_granularity','catalog_target','context_goal_id','execution_ref','source_ref'}
    goals=[]
    for original in raw['goals']:
        goal={key:value for key,value in original.items() if key in fields and value is not None and value!=''}
        assignment=assigned.get(original.get('id'),{})
        goal['edits']=mapping(assignment.get('edits',[]))
        if assignment.get('clarification'):goal['clarification']=assignment['clarification']
        goals.append(goal)
    raw['goals']=goals
    raw['edits']=mapping(raw.get('edits',[]))
    return raw
