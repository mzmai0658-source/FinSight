"""作品说明：重新执行已保存的计算配方，旧回答文字不提供计算数值。"""
from .contracts import Conditions,ComputedReference,Request
from .executor import ExecutionResult,calculate
from .verification import verify


def provenance_tree(repository,facts):
    known={fact.id:fact for fact in facts};pending={id for fact in facts for id in fact.inputs}-set(known)
    for _ in range(8):
        if not pending:return list(known.values())
        if len(known)+len(pending)>2000:return []
        batch=repository.by_ids(sorted(pending))
        if {fact.id for fact in batch}!=pending:return []
        known.update({fact.id:fact for fact in batch})
        pending={id for fact in batch for id in fact.inputs}-set(known)
    return []


def revalidate_facts(repository,ids):
    if not ids:return []
    facts=repository.by_ids(ids)
    if {fact.id for fact in facts}!=set(ids) or any(f.data_version!=repository.version for f in facts):return []
    tree=provenance_tree(repository,facts)
    if not tree:return []
    selection=Conditions(codes=list(dict.fromkeys(f.stock_code for f in facts)),metrics=list(dict.fromkeys(f.metric for f in facts)),
        scope=facts[0].scope,time={'mode':'explicit','pairs':list(dict.fromkeys((f.year,f.period) for f in facts))})
    request=Request(turn_id='reference-recheck',question='重核受验证引用',conditions=selection,goals=[])
    verdict=verify(request,ExecutionResult(facts=facts),[],[],tree)
    # 作品说明：已登记引用的真实性与本轮选择分别检查，旧独立目标的不同口径仍保持分开的事实身份。
    return facts if all(check.status=='pass' for check in verdict.numeric) else []


def revalidate_computed(repository,reference):
    if reference.data_version!=repository.version:return None
    facts=revalidate_facts(repository,list(reference.inputs))
    if not facts:return None
    indexed={fact.id:fact for fact in facts};a,b=[indexed[id] for id in reference.inputs]
    if (a.metric,a.scope,a.unit,a.data_version)!=(b.metric,b.scope,b.unit,b.data_version):return None
    if reference.comparison_axis=='years' and a.stock_code!=b.stock_code:return None
    if reference.comparison_axis=='companies' and (a.year,a.period)!=(b.year,b.period):return None
    conditions=Conditions(codes=list(dict.fromkeys([a.stock_code,b.stock_code])),metrics=[a.metric],scope=a.scope,
        time={'mode':'explicit','pairs':[(a.year,a.period),(b.year,b.period)]},calculation=reference.calculation,comparison_axis=reference.comparison_axis)
    request=Request(turn_id='reference-recheck',question='重算受验证计算引用',conditions=conditions,goals=[])
    result=ExecutionResult(facts=[a,b]);calculate(request,result)
    expected=next((item for item in result.derived if item['id']==reference.id),None)
    if not expected:return None
    fields=('id','inputs','metric','company','year','period','value','unit','formula','label','detail')
    saved=reference.model_dump(mode='json')
    if any(expected[field]!=saved[field] for field in fields):return None
    if reference.scope!=a.scope:return None
    return reference


def computed_references(request,result,version):
    references={}
    for selected,block in result.blocks or [(request,result)]:
        c=selected.conditions
        if c.calculation=='none' or c.comparison_axis=='none':continue
        for item in block.derived:
            fields={key:item[key] for key in ('id','inputs','metric','company','year','period','value','unit','formula','label','detail')}
            references[item['id']]=ComputedReference(**fields,data_version=version,scope=c.scope,
                calculation=c.calculation,comparison_axis=c.comparison_axis)
    return list(references.values())


def visible_fact_references(request,result):
    return list({fact.id:fact for selected,block in result.blocks or [(request,result)]
        if selected.conditions.calculation=='none' or selected.conditions.presentation.include_inputs for fact in block.facts}.values())
