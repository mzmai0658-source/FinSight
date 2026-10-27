"""完整正文、事实、图表和引用先组成待发布包，再统一核验；发布时只读取此包。"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass,asdict,is_dataclass
import hashlib,json,re
from .contracts import Check
from .rendering import render_financial,table_only_content,render_sign_correction,sign_reference_label

def plain(value):
    if hasattr(value,'model_dump'):return plain(value.model_dump(mode='json'))
    if is_dataclass(value):return plain(asdict(value))
    if isinstance(value,dict):return {str(k):plain(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [plain(v) for v in value]
    return value

@dataclass
class PendingPublication:
    content: str
    execution: object
    references: list
    bindings: dict
    computed_references: list
    fingerprint: str=''

    def digest(self):
        body=dict(content=self.content,execution=plain(self.execution),references=self.references,
            bindings=self.bindings,computed_references=plain(self.computed_references))
        return hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def seal(self):self.fingerprint=self.digest();return self
    def assert_sealed(self):
        if not self.fingerprint or self.fingerprint!=self.digest():raise ValueError('Publication changed after verification')

def premise_notices(state,result,bindings):
    """同一核实引用的前提纠正只有一个生成者，并保留全部目标归属。"""
    request=state['request'];claimed=state['understanding'].claimed_sign
    if claimed=='none':return []
    candidates={}
    def add(value,owners):
        if not owners:return
        item=candidates.setdefault(value.id,dict(value=value,goal_ids=[]))
        item['goal_ids']=list(dict.fromkeys([*item['goal_ids'],*owners]))
    for selected,block in result.blocks or [(request,result)]:
        if selected.conditions.calculation=='none' and len(block.facts)==1 and not all(g.kind=='quote' for g in selected.goals):
            add(block.facts[0],[g.id for g in selected.goals])
    context=[*state.get('context_facts',[]),*state.get('context_computed',[])]
    if len(context)==1:
        value=context[0]
        owners=[goal.id for goal in request.goals if any(v.id==value.id for v in [
            *state.get('context_by_goal',{}).get(goal.id,[]),*state.get('context_computed_by_goal',{}).get(goal.id,[])])]
        if not owners:
            owners=bindings['facts'].get(value.id,{}).get('goal_ids',[])
        add(value,owners)
    notices=[]
    for identifier,item in candidates.items():
        label=sign_reference_label(item['value']) if len(candidates)>1 else None
        if text:=render_sign_correction(item['value'].decimal,claimed,label):
            notices.append(dict(value_id=identifier,goal_ids=item['goal_ids'],claimed_sign=claimed,label=label,text=text))
    return notices


def prepare_publication(state):
    request=state['request'];result=deepcopy(state['executed']);refs=deepcopy(state['references'])
    previous_ids={f.id for f in state['previous'].recent_facts}
    bindings={'facts':{},'calculations':{},'charts':{},'evidence':{},'prose':deepcopy(state.get('prose_by_goal',{}))}
    for selected,block in result.blocks or [(request,result)]:
        ids=[g.id for g in selected.goals]
        for fact in block.facts:
            item=bindings['facts'].setdefault(fact.id,dict(goal_ids=[],purpose='reference' if block.input_kind=='reference' else 'calculation_input' if selected.conditions.calculation!='none' else 'answer'))
            item['goal_ids']=list(dict.fromkeys([*item['goal_ids'],*ids]))
        for calculation in block.derived:bindings['calculations'][calculation['id']]=dict(goal_ids=ids,purpose='answer')
        for i,chart in enumerate(block.charts):bindings['charts'][str(len(bindings['charts']))]=dict(goal_ids=chart.get('goal_ids',ids),purpose='answer')
    for reference in refs:
        owners=[goal.id for goal in result.goals if reference['id'] in goal.evidence_ids]
        bindings['evidence'][reference['id']]=dict(goal_ids=owners,purpose='source_row' if reference.get('granularity')=='row' else 'source_cell' if reference.get('fact_id') else 'narrative')
    bindings['notices']=premise_notices(state,result,bindings)
    # 核验前一次组装前提、财务正文及目标说明，发布阶段不再修改文字或追加历史事实。
    financial=render_financial(request,result,repeated_fact_ids=previous_ids)
    before=[]
    for goal in request.goals:
        if goal.kind in {'lookup','compare','rank','chart','quote','cause','sign'}:break
        before.extend(state.get('prose_by_goal',{}).get(goal.id,[]))
    remaining=[p for p in state['prose'] if p not in before]
    content='\n\n'.join(filter(None,[*(n['text'] for n in bindings['notices']),*dict.fromkeys(before),financial,
        *dict.fromkeys(remaining),*request.clarification,*request.unsupported]))
    table_only=bool(request.goals) and all(request.for_goal(g).conditions.presentation.format=='table' for g in request.goals)
    if table_only:content=table_only_content(content)
    if not content and not result.charts:
        for goal in result.goals:
            if goal.status=='completed':goal.status='partial';goal.detail='目标没有对应的可展示内容'
        content='本轮没有取得可发布的结果。'
        if table_only:content=table_only_content(content)
    return PendingPublication(content,result,refs,bindings,deepcopy(state.get('context_computed',[])))

def verify_publication(request,bundle,verification,previous):
    """检查实际交付，不以请求中的自报格式或完成状态代替。"""
    from .reference_selection import fact_matches
    from .executor import display_value
    from .units import fact_output_unit
    checks=[];planned={g.id:g for g in request.goals}
    notice_values={v.id:v for v in [*bundle.execution.facts,*bundle.computed_references]}
    for notice in bundle.bindings.get('notices',[]):
        value=notice_values.get(notice['value_id']);owners=notice.get('goal_ids',[])
        label=notice.get('label')
        valid=bool(value and owners) and set(owners)<=set(planned) and (label is None or label==sign_reference_label(value)) and notice['text']==render_sign_correction(value.decimal,notice['claimed_sign'],label)
        checks.append(Check(name='published_premise_'+notice['value_id'],status='pass' if valid else 'fail',detail='前提纠正绑定本轮核实值和实际目标'))
    for fact in bundle.execution.facts:
        binding=bundle.bindings['facts'].get(fact.id,{})
        owners=binding.get('goal_ids',[])
        allowed=bool(owners) and all(id in planned and fact_matches(request.for_goal(planned[id]).conditions,fact) for id in owners)
        checks.append(Check(name='published_fact_'+fact.id,status='pass' if allowed else 'fail',detail='包括历史引用在内的每项发布事实绑定当前目标及条件'))
    for reference in bundle.references:
        owners=bundle.bindings['evidence'].get(reference['id'],{}).get('goal_ids',[])
        checks.append(Check(name='published_evidence_'+reference['id'],status='pass' if owners and set(owners)<=set(planned) else 'fail',detail='原文证据必须绑定实际目标，比较列仅作为原表证据'))
    if request.goals and all(request.for_goal(g).conditions.presentation.format=='table' for g in request.goals):
        ok=all(not line.strip() or line.lstrip().startswith('|') for line in bundle.content.splitlines())
        checks.append(Check(name='published_table_only',status='pass' if ok else 'fail',detail='正文实际只有表格行'))
    prior_ids={f.id for f in previous.recent_facts}
    for goal in request.goals:
        c=request.for_goal(goal).conditions
        if c.restrictions.no_repeat:
            repeated=False
            for fact in bundle.execution.for_goal(goal.id).facts:
                if fact.id not in prior_ids:continue
                for unit in ([fact_output_unit(c,fact.unit,fact.metric),'元','万元','亿元'] if fact.unit=='元' else [fact.unit]):
                    amount,shown_unit=display_value(fact.value,fact.unit,unit,c.presentation.decimals)
                    value=re.escape(amount.replace(',',''))
                    repeated|=bool(re.search(r'(?<![\d.])'+value+r'\s*(?:\|\s*)?'+re.escape(shown_unit),bundle.content.replace(',','')))
            checks.append(Check(name='published_no_repeat_'+goal.id,status='fail' if repeated else 'pass',detail='禁止重复金额时核对实际正文'))
        if c.restrictions.no_chart:
            forbidden=any(goal.id in item.get('goal_ids',[]) for item in bundle.execution.charts)
            checks.append(Check(name='published_no_chart_'+goal.id,status='fail' if forbidden else 'pass',detail='禁止图表时不交付图表载荷'))
    verification.request.extend(checks)
    all_checks=[*verification.numeric,*verification.request,*verification.evidence]
    verification.status='fail' if any(c.status=='fail' for c in all_checks) else 'partial' if any(c.status=='unknown' for c in all_checks) else 'pass'
    return verification
