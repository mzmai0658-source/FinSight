"""作品说明：为任务枚举提供可逆中文传输编码；只解释合同取值，目标选择仍由理解阶段提出。"""

LABELS={
    'representation':{'represented':'请求清单已有对应目标','missing':'请求清单缺少对应目标'},
    'kind':{
        'lookup':'查询财务数值','compare':'比较财务数值并给出结论','rank':'财务指标排名',
        'chart':'生成图形','quote':'定位报告原文或页码','cause':'解释经营原因',
        'sign':'判断盈利或亏损','concept':'解释会计定义或已查数含义',
        'rules':'解释本系统的实际执行记录或规则','catalog':'列出数据库覆盖或系统能力',
        'unsupported':'日常生活请求或禁止操作；财务缺参须澄清','other':'日常非财务话题','greeting':'问候或产品介绍',
    },
    'continuity':{
        'new':'独立新问题，不沿用旧财务条件','continue':'沿用已确认任务，只改本轮条件',
        'resume':'明确恢复此前财务任务','clear':'明确清空此前财务任务及引用',
    },
    'concept_mode':{
        'definition':'会计概念定义','difference':'会计概念之间的区别',
        'implication':'已核实数值的正负或变化含义',
    },
    'quote_mode':{'literal':'原文逐字摘录','location':'仅定位来源和页码'},
    'catalog_target':{'companies':'公司覆盖','periods':'报告期覆盖','metrics':'指标目录','capabilities':'系统能力'},
}
REVERSE={key:{label:value for value,label in pairs.items()} for key,pairs in LABELS.items()}


def intent_context_schema(schema,new_financial_allowed=True,requires_fact_reference=False):
    """作品说明：目标、连续关系和引用结构由同一个提案表达，防止分别选择产生冲突。"""
    from copy import deepcopy
    schema=deepcopy(schema);properties=schema['properties']
    modes=properties.pop('continuity')['enum'];references=properties.pop('context_references');goals=properties.pop('goals')
    if requires_fact_reference:modes=[mode for mode in modes if mode in {'continue','resume'}]
    if not new_financial_allowed:modes=sorted(modes,key=lambda mode:{'continue':0,'resume':1,'new':2,'clear':3}[mode])
    choices=[]
    for mode in modes:
        refs=deepcopy(references)
        if requires_fact_reference:refs['minItems']=1
        if mode in {'new','clear'}:refs['maxItems']=0
        selected=deepcopy(goals)
        if mode=='new' and not new_financial_allowed:
            branches=[]
            for branch in selected['items'].get('oneOf',[]):
                definition=deepcopy(schema['$defs'][branch['$ref'].rsplit('/',1)[-1]])
                kind=definition['properties']['kind'];values=kind.get('enum',[kind.get('const')])
                values=[v for v in values if v not in {'lookup','compare','rank','chart','quote','cause','sign'}]
                if not values:continue
                definition['properties']['kind']={'type':'string','enum':values};branches.append(definition)
            selected['items']={'oneOf':branches}
        choices.append(dict(type='object',properties={'goals':selected,'continuity':dict(type='string',const=mode),'context_references':refs},
            required=['goals','continuity','context_references'],additionalProperties=False))
    schema['properties']={'context_selection':{'anyOf':choices},**properties}
    schema['required']=['context_selection',*[key for key in schema['required'] if key not in {'goals','continuity','context_references'}]]
    return schema


def decode_intent_context(value):
    value=dict(value);selection=value.pop('context_selection',None)
    if not isinstance(selection,dict) or set(selection)!={'goals','continuity','context_references'}:
        raise ValueError('Native intent must contain one typed context selection')
    if {'goals','continuity','context_references'} & set(value):raise ValueError('Duplicate owners of context selection')
    if selection['continuity'] in {'new','clear'} and selection['context_references']:
        raise ValueError('Independent or cleared requests cannot borrow context references')
    return {**value,**selection}


def encode_schema(value,field=None):
    if isinstance(value,list):return [encode_schema(item,field) for item in value]
    if not isinstance(value,dict):return value
    result={}
    for key,item in value.items():
        if key=='properties':
            result[key]={name:encode_schema(rule,name) for name,rule in item.items()}
        elif key=='enum' and field in LABELS:
            result[key]=[LABELS[field].get(v,v) for v in item]
        elif key=='const' and field in LABELS:
            result[key]=LABELS[field].get(item,item)
        else:result[key]=encode_schema(item,field)
    return result


def translate_data(value,decode=False):
    mapping=REVERSE if decode else LABELS
    if isinstance(value,list):return [translate_data(item,decode) for item in value]
    if not isinstance(value,dict):return value
    return {key:mapping[key].get(item,item) if key in mapping and isinstance(item,str)
        else translate_data(item,decode) for key,item in value.items()}
