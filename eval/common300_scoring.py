"""以封存的逐目标合同和原件标准评分，区分系统成绩、首发缺陷和对话连带问题。"""
from __future__ import annotations

import copy
import json
import re
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from itertools import permutations

from eval.real200_acceptance import FACTORS, effective as legacy_effective, normalize, wanted
from eval.score_v3_regressions import check_calculations, check_chart, selectors

FINANCIAL={'lookup','compare','rank','chart','quote','cause','sign'}


def effective(observed,request):
    resolved=(request.get('resolved_goal_conditions') or {}).get(observed.get('id'))
    if isinstance(resolved,dict):return copy.deepcopy(resolved)
    return legacy_effective(observed,request)


def expected_facts(case,reference):
    values={}
    for item in case['expect']['goal_expectations']:
        if item['kind'] not in FINANCIAL or item.get('completion')=='missing_report':continue
        for f in wanted({**item,'goals':[item['kind']]},reference):values[f['id']]=f
    return values


def condition_errors(spec,conditions):
    errors=[]
    if 'all_companies' in spec and conditions.get('all_companies',False)!=spec['all_companies']:errors.append('all_companies')
    for field in ('codes','metrics'):
        if spec.get(field) and set(conditions.get(field) or [])!=set(spec[field]):errors.append(field)
    if spec.get('codes') and conditions.get('scope','consolidated')!=spec.get('scope','consolidated'):errors.append('scope')
    time=conditions.get('time') or {}
    if spec.get('years'):
        actual={pair[0] for pair in time.get('pairs') or []} or set(time.get('years') or [])
        if actual!=set(spec['years']):errors.append('years')
        periods={pair[1] for pair in time.get('pairs') or []} or set(time.get('periods') or ['FY'])
        if periods!=set(spec.get('periods') or ['FY']):errors.append('periods')
    for field in ('unit','decimals','format','order','limit'):
        if field in spec and (conditions.get('presentation') or {}).get(field)!=spec[field]:errors.append(field)
    if spec.get('chart') and (conditions.get('presentation') or {}).get('chart_type','line')!=spec['chart']:errors.append('chart_type')
    if spec.get('calculation') and conditions.get('calculation','none')!=spec['calculation']:errors.append('calculation')
    if spec.get('axis') and conditions.get('comparison_axis','none')!=spec['axis']:errors.append('axis')
    return errors


def match_goals(expectations,request):
    """目标按类型及有效条件匹配；顺序与内部编号不能成为题目答案。"""
    actual=request.get('goals') or []
    choices=[]
    for spec in expectations:
        candidates=[]
        for observed in actual:
            kinds={spec['kind']}
            if spec['kind']=='lookup' and spec.get('calculation'):kinds.add('compare')
            if spec['kind']=='sign' and observed.get('concept_mode')=='implication':kinds.add('concept')
            if observed.get('kind') not in kinds:continue
            errors=condition_errors(spec,effective(observed,request))
            if spec.get('concept_mode') and observed.get('concept_mode')!=spec['concept_mode']:errors.append('concept_mode')
            if spec.get('quote_mode') and observed.get('quote_mode')!=spec['quote_mode']:errors.append('quote_mode')
            candidates.append((len(errors),str(observed.get('id')),observed,errors))
        choices.append(sorted(candidates,key=lambda item:item[:2]))
    best=None
    def visit(index,used,selected,cost):
        nonlocal best
        if best and cost>=best[0]:return
        if index==len(choices):best=(cost,list(selected));return
        available=[item for item in choices[index] if item[1] not in used]
        if not available:
            visit(index+1,used,[*selected,(None,['missing_goal'])],cost+100);return
        for penalty,id_,observed,errors in available:
            visit(index+1,used|{id_},[*selected,(observed,errors)],cost+penalty)
    visit(0,set(),[],0)
    return (best or (0,[]))[1]


def validated_visible_context(run,reference,oracle,previous=()):
    """独立核对可见事实；不以整题格式得分决定事实是否存在。"""
    task=run.get('task') or {};result=task.get('result') or {};request=result.get('request_contract') or {}
    if task.get('status') in {'failed','cancelled'} or request.get('continuity')=='clear':return []
    values=[]
    for value in result.get('facts') or []:
        f=reference['facts'].get(value.get('id'));card=oracle['cards'].get(value.get('id'))
        if not f or not card:continue
        try:correct=Decimal(value.get('value_exact','NaN'))==Decimal(card['value_exact'])
        except Exception:correct=False
        correct=correct and all(value.get(k)==f.get(k) for k in ('stock_code','year','period','metric','scope','unit','data_version'))
        if f.get('source'):
            correct=correct and all((value.get('source') or {}).get(k)==f['source'].get(k) for k in ('source_sha256','page','row','raw_value','raw_unit'))
        if correct:values.append({**f,'value_exact':card['value_exact']})
    if values:return values
    if any(g.get('kind') in FINANCIAL for g in request.get('goals') or []):return []
    return list(previous)


def _proposal_request(proposal,before_state):
    """诊断投影只解释类型化编辑，不调用生产中文解析器；不用于替代正式请求。"""
    defaults=dict(codes=[],metrics=[],scope='consolidated',time=dict(mode='latest',years=[],periods=['FY']),
        presentation=dict(unit=None,decimals=None,format='auto',order='desc',limit=None,chart_type='line'),
        restrictions=dict(no_query=False,no_chart=False,no_repeat=False),calculation='none',comparison_axis='none')
    action=proposal.get('turn_action')
    continuity=proposal.get('continuity','new')
    if action in {'modify_main','resume_main','fact_followup','answer_clarification'}:continuity='continue'
    elif action in {'start_main','clear_context'}:continuity='new'
    base=copy.deepcopy(before_state.get('conditions') or defaults) if continuity in {'continue','resume'} else copy.deepcopy(defaults)
    if continuity=='resume' and before_state.get('suspended'):base=copy.deepcopy(before_state['suspended'])
    main=before_state.get('main_task') or {}
    if continuity in {'continue','resume'} and isinstance(main.get('conditions'),dict):base=copy.deepcopy(main['conditions'])
    if continuity in {'continue','resume'} and before_state.get('pending'):base=copy.deepcopy(before_state['pending']['conditions'])
    def edits(c,values):
        for value in values or []:
            field=value.get('field');op=value.get('operation');new=value.get('value')
            if not field or op=='inherit':continue
            parts=field.split('.');target=c
            for part in parts[:-1]:target=target.setdefault(part,{})
            leaf=parts[-1];old=target.get(leaf)
            if op in {'replace','keep'}:target[leaf]=copy.deepcopy(new)
            elif op=='add':target[leaf]=list(dict.fromkeys([*(old or []),*(new or [])]))
            elif op=='remove':target[leaf]=[x for x in old or [] if x not in (new or [])]
            elif op=='clear':target[leaf]=[] if isinstance(old,list) else copy.deepcopy(defaults.get(leaf))
        return c
    assignments={a['id']:a for a in proposal.get('assignments') or []}
    result=[]
    for index,g in enumerate(proposal.get('goals') or [],1):
        c=copy.deepcopy(base)
        if ref:=g.get('context_goal_id'):
            c=copy.deepcopy((before_state.get('goal_conditions') or {}).get(ref,c))
        c=edits(c,proposal.get('edits'))
        c=edits(c,(assignments.get(g.get('id','g'+str(index))) or {}).get('edits'))
        result.append({**g,'id':g.get('id','g'+str(index)),'context_conditions':c})
    return dict(conditions=base,goals=result)


def attribution(case,run,score,before_state=None,upstream=()):
    result=(run.get('task') or {}).get('result') or {};diag=result.get('diagnostics') or {}
    proposals=[t.get('proposal') for t in diag.get('structured_trace') or [] if t.get('step')=='TurnPlan' and isinstance(t.get('proposal'),dict)]
    contract=result.get('request_contract') or {}
    expected=case['expect']['goal_expectations']
    raw_errors=[];compiled_errors=[]
    if proposals:
        projected=_proposal_request(proposals[-1],before_state or {})
        raw_errors=[dict(kind=e['kind'],errors=errors) for e,(_,errors) in zip(expected,match_goals(expected,projected)) if errors]
    if contract:
        compiled_errors=[dict(kind=e['kind'],errors=errors) for e,(_,errors) in zip(expected,match_goals(expected,contract)) if errors]
    primary='none';reason='Formal answer passed all automated checks; narrative review may still be pending'
    actual_facts=result.get('facts') or []
    def authorized(f):
        for g in contract.get('goals') or []:
            if g.get('kind') not in FINANCIAL and (g.get('input') or {}).get('kind')!='published_reference':continue
            c=effective(g,contract);t=c.get('time') or {}
            pairs=t.get('pairs') or []
            years={p[0] for p in pairs} or set(t.get('years') or [])
            periods={p[1] for p in pairs} or set(t.get('periods') or ['FY'])
            if (f.get('stock_code') in (c.get('codes') or []) and f.get('metric') in (c.get('metrics') or [])
                and f.get('scope')==c.get('scope','consolidated') and f.get('period') in periods
                and (not years or f.get('year') in years)):return True
        return False
    program_divergence=bool(contract and any(not authorized(f) for f in actual_facts))
    numerical_divergence=bool(set(score['critical'])&{'wrong_numeric_fact','fact_identity_changed','write_sql_executed','wrong_source','wrong_calculation','wrong_chart_point'})
    reasons=set((result.get('outcome') or {}).get('reason_codes') or [])|{(run.get('task') or {}).get('error')}
    if not score['automated_pass']:
        if 'model_contract_failed' in reasons:primary='code';reason='Model contract preparation/response handling failed before a validated interpretation; not a connectivity or model-capacity diagnosis'
        elif reasons&{'model_timeout','model_connection_failed','model_queue_timeout','model_busy','save_failed'}:
            primary='environment';reason='Observed service/deadline/persistence failure; does not establish a semantic capability limit'
        elif not proposals and diag.get('raw_model_outputs') and 'model_invalid_schema' in reasons:
            primary='model';reason='Captured model output violates the explicit format/literal-source contract under the current prompt; not proof of intrinsic capacity'
        elif not proposals:primary='unresolved';reason='No validated raw TurnPlan; inspect bounded raw_model_outputs before attributing model or code'
        elif not raw_errors and compiled_errors:primary='code';reason='Raw typed proposal meets expected conditions; compiled request does not'
        elif raw_errors:
            if program_divergence or numerical_divergence:primary='mixed';reason='Proposal mismatch plus independently observable violation of the compiled contract or verified numeric/source rules'
            else:primary='model';reason='Raw proposal already differs; no separately established program divergence in captured stages'
        elif program_divergence or numerical_divergence:
            primary='code';reason='Raw and compiled request match, but execution or publication violates independent facts/calculation'
        else:primary='unresolved';reason='Request appears correct; evidence/retrieval/rendering/service origin needs independent replay'
    return dict(primary=primary,reason=reason,confidence='preliminary; requires independent stage/replay review, not proof of intrinsic model capacity',
        raw_proposal_available=bool(proposals),raw_errors=raw_errors,
        compiled_errors=compiled_errors,upstream_failures=list(upstream),
        potential_cascade=bool(upstream and case.get('group')),counterfactual_status='not_run',
        violates_compiled_contract=program_divergence,
        stage_evidence=dict(structured_trace=diag.get('structured_trace') or [],literal_bindings=diag.get('literal_bindings') or [],
            raw_model_outputs=diag.get('raw_model_outputs') or [],request_contract=contract,
            cause_evidence=diag.get('cause_evidence') or [],query_trace=result.get('query_trace') or [],
            task_results=result.get('task_results') or [],verification_v3=result.get('verification_v3')))


def score_case(case,run,reference,oracle,model,actual_context=(),expected_context=()):
    e=case['expect'];task=run.get('task') or {};result=task.get('result') or {};r=result.get('request_contract') or {}
    body=(result.get('answer') or {}).get('content','');facts=result.get('facts') or [];charts=result.get('chart_data_list') or []
    errors=[];critical=[];review=[]
    def require(value,label,severe=False):
        if not value:errors.append(label)
        if not value and severe:critical.append(label)
    require(task.get('status')=='completed' and task.get('saved') is True,'task_not_completed_and_saved')
    require(bool(body.strip() or charts),'empty_output');require(bool(r),'missing_request_contract')
    timings=(result.get('diagnostics') or {}).get('timings') or []
    require(bool(timings) and all(t.get('model')==model for t in timings),'model_identity_not_confirmed')
    outcome=(result.get('outcome') or {}).get('status');goals=r.get('goals') or [];planned={g.get('kind') for g in goals}
    mode=e['kind'];expected=e['goal_expectations'];matched=match_goals(expected,r)
    traces=result.get('query_trace') or []
    require(not any(re.search(r'\b(?:DELETE|UPDATE|INSERT|DROP|ALTER|CREATE|GRANT)\b',q.get('sql',''),re.I) for q in traces),'write_sql_executed',True)
    if e.get('no_query'):require(not any(q.get('purpose')=='financial_facts' for q in traces),'forbidden_numeric_query',True)
    if e.get('no_chart'):require(not charts,'forbidden_chart',True)
    if mode=='clarify':
        require(outcome=='needs_clarification' or result.get('needs_clarification'),'required_clarification_missing')
        require(not facts and not charts,'financial_output_before_clarification',bool(facts or charts))
        require(bool(planned & {g['kind'] for g in expected}),'clarification_goal_changed')
        cs=[effective(g,r) for g in goals]
        for field in ('codes','metrics'):
            if 'known_'+field in e:
                observed=set().union(*(set(c.get(field) or []) for c in cs)) if cs else set()
                require(observed==set(e['known_'+field]),'clarification_'+field+'_lost')
        if 'known_years' in e:
            observed=set().union(*(set((c.get('time') or {}).get('years') or []) for c in cs)) if cs else set()
            require(observed==set(e['known_years']),'clarification_years_lost')
    elif mode=='unsupported':
        require(outcome in {'unsupported','needs_clarification'} or bool(r.get('unsupported')),'boundary_not_explained')
        require(not facts and not charts,'unsupported_substituted_data',bool(facts or charts))
        if name:=e.get('outside_company'):
            require(name in body and bool(re.search(r'未(?:覆盖|收录|纳入)|不在.{0,12}(?:库|名单|范围)',body)),'outside_company_not_explained')
    else:
        for index,(spec,(observed,issues)) in enumerate(zip(expected,matched)):
            for issue in issues:require(False,f'goal_{index+1}_{spec["kind"]}_{issue}')
        allowed=set(expected_facts(case,reference));seen={f.get('id') for f in facts}
        if mode=='reference':
            require(bool(actual_context),'prior_verified_fact_missing')
            expected_ids={f['id'] for f in expected_context}
            require(bool(expected_ids) and seen<=expected_ids,'reference_changed_facts',bool(seen-expected_ids))
            allowed=expected_ids
        elif mode=='concept':require(not facts and not any(q.get('purpose')=='financial_facts' for q in traces),'concept_queried_numbers',True)
        elif mode=='missing':require(not facts and not charts,'missing_data_replaced',bool(facts or charts))
        else:require(seen==allowed,'missing_or_extra_requested_facts')
        require(seen<=allowed,'out_of_request_fact_published',bool(seen-allowed))
        if facts:require(result.get('data_version')==reference['version'],'data_version_changed',True)
        for f in facts:
            truth=reference['facts'].get(f.get('id'));card=oracle['cards'].get(f.get('id'))
            require(bool(truth),'unverified_fact_published',True)
            if not truth:continue
            if not card:errors.append('published_fact_outside_prepared_original_oracle')
            require(all(f.get(k)==truth[k] for k in ('stock_code','year','period','metric','scope','unit','data_version')),'fact_identity_changed',True)
            try:correct=Decimal(f.get('value_exact','NaN'))==Decimal(card['value_exact'] if card else truth['value'])
            except Exception:correct=False
            require(correct,'wrong_numeric_fact',True)
            if truth.get('source'):
                require(all((f.get('source') or {}).get(k)==truth['source'].get(k) for k in ('source_sha256','page','row','raw_value','raw_unit')),'wrong_source',True)
        statuses={g['id']:g for g in result.get('task_results') or []}
        for spec,(observed,_) in zip(expected,matched):
            if not observed:continue
            if spec.get('all_companies') and not spec.get('codes'):
                require(set(effective(observed,r).get('codes') or [])==set(selectors(spec,reference)),'requested_company_universe_changed')
            status=(statuses.get(observed.get('id')) or {}).get('status')
            if spec.get('completion')=='missing_report':
                require(status in {'no_data','partial','unsupported'},'missing_report_goal_falsely_completed')
                require(any(t in body for t in ('没有','未找到','未取得','未确认','缺少','未收录')),'missing_report_not_explained')
                continue
            require(status=='completed','goal_'+spec['kind']+'_not_completed')
            subset=[f for f in facts if f.get('id') in {v['id'] for v in wanted({**spec,'goals':[spec['kind']]},reference)}] if spec['kind'] in FINANCIAL else []
            if spec['kind']=='sign' and observed.get('kind')=='concept':
                # 内部目标允许等价实现；明确数值引用和结论交付仍分别检查，不把通用定义算作盈亏回答。
                require(bool(subset) and {f['id'] for f in subset}<=set((statuses.get(observed['id']) or {}).get('fact_ids',[])),
                        'sign_result_fact_missing')
                review.append('sign_conclusion')
            if spec.get('calculation'):
                derived=[d for d in result.get('derived_facts') or [] if set(d.get('inputs') or [])<=set(f['id'] for f in subset)]
                check_calculations(subset,derived,spec,lambda test,label,severe=False:require(test,'wrong_calculation' if severe else label,severe))
                formula='(current-base)/abs(base)*100' if spec['calculation'] in {'yoy','relative_percent'} else 'current-base' if spec.get('axis')=='years' else 'first-second'
                require(all(d.get('formula')==formula for d in derived),'wrong_calculation_formula',True)
            if spec['kind']=='rank':require([f['id'] for f in facts]==[f['id'] for f in wanted({**spec,'goals':['rank']},reference)],'rank_order_or_count_wrong')
            if spec.get('chart'):
                selected=[c for c in charts if set(c.get('source_fact_ids') or [])<=set(f['id'] for f in subset)]
                require(bool(selected),'chart_missing')
                for chart in selected:
                    require(chart.get('chart_type')==spec['chart'],'wrong_chart_type',True)
                    require(chart.get('unit')==spec.get('unit'),'wrong_chart_unit',True)
                    check_chart(chart,reference,lambda test,label,severe=False:require(test,'wrong_chart_point' if severe else label,severe))
            if spec.get('format')=='table':require(all(line.strip().startswith('|') for line in body.splitlines() if line.strip()),'table_only_has_prose')
            if spec['kind']=='quote':
                evidence=[v for v in result.get('evidence') or [] if v.get('fact_id') in {f['id'] for f in subset}]
                require(bool(evidence),'original_page_missing')
                for v in evidence:
                    original=reference['facts'][v['fact_id']];source=original.get('source') or {}
                    require(v.get('page_start')==source.get('page') and v.get('document_version')==source.get('document_version'),'wrong_quote_location',True)
                    if spec.get('quote_mode')=='literal':
                        literal=v.get('value_literal','');card=oracle['cards'][v['fact_id']]
                        require(bool(literal) and normalize(literal) in normalize(card.get('original_row_text','')),'fabricated_quote',True)
                        require(bool(literal) and normalize(literal) in normalize(body),'requested_literal_excerpt_missing')
            if spec['kind']=='cause' and spec.get('narrative'):
                evidence=[v for v in result.get('evidence') or [] if not v.get('fact_id')]
                proof=spec['narrative'];allowed_pages=[p for p in oracle.get('narrative_pages') or [] if p['case_id']==case['id']]
                require(bool(evidence),'cause_evidence_missing')
                if evidence:
                    require(any(v.get('page_start')==proof['physical_page'] and any(v.get('document_version')==p['source_sha256'] for p in allowed_pages) for v in evidence),'cause_expected_original_page_missing')
                review.append('cause')
            if spec['kind']=='concept':review.append('concept')
            if spec['kind'] in {'lookup','rank'} and not spec.get('calculation'):
                for f in subset:
                    unit=spec.get('unit') or ('万元' if f['unit']=='元' else f['unit']);digits=spec.get('decimals',4 if f['unit']=='元/股' else 2)
                    with __import__('decimal').localcontext() as ctx:
                        ctx.prec=50;v=(Decimal(f['value_exact'])/(FACTORS[unit] if f['unit']=='元' else 1)).quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP)
                    require(format(v,'f') in body.replace(',',''),'requested_number_display_missing')
                    require(unit in body,'requested_unit_display_missing')
        if mode=='missing':require(outcome in {'no_data','partial','unsupported'},'missing_report_not_reported')
        elif e.get('allow_expected_partial'):require(outcome in {'answered','partial'},'reliable_part_lost')
        else:require(outcome=='answered','supported_request_not_completed')
    if mode=='reference':
        review.append('reference')
        if e.get('no_repeat'):
            for f in actual_context:
                for unit in ('元','万元','亿元'):
                    value=(Decimal(f['value_exact'])/FACTORS[unit]).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                    require(format(value,'f') not in body.replace(',',''),'previous_amount_repeated')
    for alternatives in e.get('must_terms') or []:require(any(word in body for word in alternatives),'missing_explanation_'+alternatives[0])
    if e.get('clear_company'):require(not (result.get('dialogue_state') or {}).get('conditions',{}).get('codes'),'cleared_company_revived',True)
    if e.get('no_old_fact'):require(not facts,'old_fact_used',bool(facts))
    if e.get('excluded_metrics'):require(not any(f.get('metric') in e['excluded_metrics'] for f in facts),'excluded_metric_queried',True)
    return dict(passed=not errors and not review,automated_pass=not errors,errors=sorted(set(errors)),critical=sorted(set(critical)),
        review_required=sorted(set(review)),review_status='pending' if review else 'not_required',
        result_class='expected_boundary' if mode in {'clarify','unsupported','missing'} else 'expected_partial' if e.get('allow_expected_partial') else 'full_answer')


def summarize(records,expected_total):
    by_category={};groups={};families={}
    for r in records:
        c=r['case'];s=r['score'];entry=by_category.setdefault(c['category'],dict(total=0,passed=0));entry['total']+=1;entry['passed']+=bool(s['passed'])
        if c.get('group'):groups.setdefault(c['group'],[]).append(r)
        if c.get('family'):families.setdefault(c['family'],[]).append(r)
    counts=Counter(r['score']['result_class'] for r in records)
    correct=Counter(r['score']['result_class'] for r in records if r['score']['passed'])
    return dict(total=len(records),expected_total=expected_total,passed=sum(r['score']['passed'] for r in records),
        automated_pass=sum(r['score']['automated_pass'] for r in records),
        accuracy=sum(r['score']['passed'] for r in records)/len(records) if records else None,
        complete_run=len(records)==expected_total,executed_tasks=sum(bool((r.get('run') or {}).get('task')) for r in records),
        service_or_evaluator_failures=sum(any(x.startswith('test_or_service_') for x in r['score'].get('errors') or []) for r in records),
        source_review_pending=sum(r['score']['review_status']=='pending' for r in records),
        critical=dict(Counter(v for r in records for v in r['score']['critical'])),by_category=by_category,
        by_result_class={k:dict(total=n,passed=correct[k]) for k,n in counts.items()},
        dialogue_groups={g:dict(turns=len(rs),complete=len(rs)==4,passed=len(rs)==4 and all(r['score']['passed'] for r in rs)) for g,rs in groups.items()},
        variant_families={f:dict(turns=len(rs),complete=len(rs)==3,all_passed=len(rs)==3 and all(r['score']['passed'] for r in rs)) for f,rs in families.items()},
        attribution=dict(Counter(r.get('attribution',{}).get('primary','unresolved') for r in records if not r['score']['passed'])),
        potential_cascades=sum(bool(r.get('attribution',{}).get('potential_cascade')) for r in records),
        no_accuracy_threshold=True,claim='Observed system results, not an estimate of model intrinsic capacity or third-party review')
