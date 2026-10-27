"""Independent acceptance scoring for saved v3 answers; no model calls."""
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from src.agent.v3.contracts import Request


def recorded_model(folder, records, selected=None):
    """Resolve saved identity without applying current cloud defaults to old evidence."""
    models = set()
    for name in ("environment.json", "run_manifest.json"):
        path = Path(folder) / name
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8")).get("model")
            if isinstance(value, dict):
                value = value.get("name") or value.get("model")
            if isinstance(value, str) and value:
                models.add(value)
    for row in records:
        result = ((row.get("run") or {}).get("task") or {}).get("result") or {}
        models.update(t["model"] for t in (result.get("diagnostics") or {}).get("timings", [])
                      if isinstance(t, dict) and isinstance(t.get("model"), str) and t["model"])
    if len(models) != 1 or (selected is not None and models != {selected}):
        raise ValueError("A single consistent recorded model identity is required; current defaults cannot relabel saved evidence")
    return next(iter(models))


def score(case,run,gold,selected):
    errors=[];e=case['expect'];task=run['task'];result=task.get('result') or {}
    body=(result.get('answer') or {}).get('content','')
    if task['status']!='completed':return dict(passed=False,errors=['task_'+task['status']],outcome=result.get('outcome'))
    if not result:return dict(passed=False,errors=['no_saved_result'])
    if not body and not result.get('chart_data_list'):errors.append('empty_deliverable')
    timings=(result.get('diagnostics') or {}).get('timings',[])
    if not timings or any(t.get('model')!=selected for t in timings):errors.append('actual_model_identity_not_confirmed')
    if re.search(r'(?<![A-Za-z0-9])s\d+(?![A-Za-z0-9])',body):errors.append('internal_span_id_visible')
    try:r=Request.model_validate(result['request_contract'])
    except Exception:return dict(passed=False,errors=[*errors,'invalid_request_contract'])
    if e.get('clarify'):
        if not result.get('needs_clarification'):errors.append('missing_clarification')
        if result.get('facts'):errors.append('ambiguous_query_published_numbers')
        if e.get('cleared_company'):
            state=result.get('dialogue_state') or {}
            bindings=[state.get('conditions') or {},state.get('suspended') or {},*(state.get('goal_conditions') or {}).values()]
            if any(c.get('codes') for c in bindings):errors.append('cleared_company_revived')
        return dict(passed=not errors,errors=errors,outcome=result.get('outcome'))
    if e.get('unsupported'):
        if not r.unsupported and not any(g.kind=='unsupported' for g in r.goals):errors.append('unsupported_request_not_identified')
        if result.get('facts'):errors.append('unsupported_request_substituted_data')
        return dict(passed=not errors,errors=errors,outcome=result.get('outcome'))
    kindset={g.kind for g in r.goals}
    if e.get('quote_mode') and any(g.quote_mode!=e['quote_mode'] for g in r.goals if g.kind=='quote'):errors.append('quote_mode_mismatch')
    if e.get('all_companies') and not r.conditions.all_companies:errors.append('whole_library_scope_missing')
    requested=set(e.get('goals',[e['goal']] if 'goal' in e else ['lookup']))
    if kindset-requested:errors.append('unrequested_extra_goal')
    for kind in requested:
        if kind not in kindset:errors.append('missing_goal_'+kind)
    conditions=[r.for_goal(g).conditions for g in r.goals if g.kind!='unsupported']
    codes={code for c in conditions for code in c.codes};metrics={m for c in conditions for m in c.metrics}
    if 'codes' in e and codes!=set(e['codes']):errors.append('company_set_mismatch')
    if 'metrics' in e and metrics!=set(e['metrics']):errors.append('metric_identity_mismatch')
    for key in ('scope','calculation'):
        if key in e and any(getattr(c,key)!=e[key] for c in conditions if c.metrics):errors.append(key+'_mismatch')
    for key in ('unit','decimals','format','chart_type','order','limit'):
        if key in e and any(getattr(c.presentation,key)!=e[key] for c in conditions):errors.append(key+'_mismatch')
    for key in ('no_query','no_chart','no_repeat'):
        if key in e and any(getattr(c.restrictions,key)!=e[key] for c in conditions):errors.append(key+'_mismatch')
    if 'years' in e:
        time_conditions=[r.for_goal(g).conditions for g in r.goals if g.kind==e['years_goal']] if e.get('years_goal') else conditions
        years={year for c in time_conditions for year in (c.time.years or [y for y,_ in c.time.pairs or []])}
        if years!=set(e['years']):errors.append('requested_years_mismatch')
    if e.get('time_mode') and any(c.time.mode!=e['time_mode'] for c in conditions):errors.append('time_policy_mismatch')
    facts=result.get('facts',[])
    if e.get('codes') and e.get('years') and e.get('metrics') and not e.get('no_query'):
        expected={(code,year,'FY',metric,e.get('scope','consolidated')) for code in e['codes'] for year in e['years'] for metric in e['metrics']}
        actual={(f['stock_code'],f['year'],f['period'],f['metric'],f['scope']) for f in facts}
        if expected-actual:errors.append('requested_fact_missing')
        if actual-expected and not e.get('evidence_missing'):errors.append('unrequested_fact_published')
    for f in facts:
        key=(f['stock_code'],f['year'],f['period'],f['metric'],f['scope']);original=gold.get(key)
        if not original or Decimal(f.get('value_exact',f['value']))!=Decimal(original['value']):errors.append('incorrect_numeric_fact')
        if original and original.get('source'):
            for field in ('source_sha256','page','raw_value','raw_unit'):
                if f.get('source',{}).get(field)!=original['source'].get(field):errors.append('source_'+field+'_mismatch')
    verification=result.get('verification_v3') or {}
    if any(ch.get('status')=='fail' for ch in verification.get('numeric',[])):errors.append('numeric_verification_failed')
    if e.get('no_query') and any(q.get('purpose')=='financial_facts' for q in result.get('query_trace',[])):errors.append('negative_query_executed')
    if e.get('no_chart') and result.get('chart_data_list'):errors.append('negative_chart_created')
    statuses=result.get('task_results',[])
    if e.get('evidence_missing'):
        if not facts:errors.append('reliable_numeric_part_missing')
        if any(g['kind']=='cause' and g['status']=='completed' for g in statuses):errors.append('unsupported_forecast_reason_completed')
        if not re.search(r'未确认|未找到|没有.*证据|尚未|未取得|未能',body):errors.append('missing_evidence_limitation')
    elif any(g['status']!='completed' for g in statuses) or (result.get('outcome') or {}).get('status')!='answered':
        errors.append('supported_request_not_completed')
    if e.get('correct_positive') and not re.search(r'正|大于\s*0|大于零',body):errors.append('false_negative_premise_not_corrected')
    if e.get('no_repeat') and any(v in body.replace(',','') for v in ('2280337512.47','228033.75')):errors.append('previous_amount_repeated')
    if e.get('format')=='table' and any(line.strip() and not line.strip().startswith('|') for line in body.splitlines()):errors.append('non_table_body')
    # 检查实际显示的金额单位，避免仅凭保存的请求判定单位正确。
    if facts and e.get('unit') and e.get('goal')!='chart' and not e.get('calculation'):
        factor={'元':Decimal(1),'万元':Decimal(10000),'亿元':Decimal(100000000),'元/股':Decimal(1)}.get(e['unit'])
        if factor:
            decimals=e.get('decimals',r.conditions.presentation.decimals if r.conditions.presentation.decimals is not None else 2)
            for f in facts:
                value=(Decimal(f['value_exact'])/factor).quantize(Decimal(1).scaleb(-decimals),rounding=ROUND_HALF_UP)
                if format(value,'f') not in body.replace(',',''):errors.append('requested_numeric_display_missing')
    if e.get('goal')=='quote' and not result.get('evidence'):errors.append('source_location_missing')
    if e.get('goal')=='chart':
        charts=result.get('chart_data_list') or []
        if not charts:errors.append('requested_chart_missing')
        for chart in charts:
            if chart.get('chart_type',chart.get('type')) not in (None,e['chart_type']):errors.append('actual_chart_type_mismatch')
    if e.get('goal')=='compare' and not result.get('comparisons'):errors.append('comparison_conclusion_missing')
    if e.get('calculation') and not result.get('derived_facts'):errors.append('requested_calculation_missing')
    if e.get('calculation')=='difference' and len(e.get('years',[]))==2:
        years=sorted(e['years'])
        for code in e['codes']:
            for metric in e['metrics']:
                pair=[gold.get((code,y,'FY',metric,e.get('scope','consolidated'))) for y in years]
                if all(pair):
                    value=Decimal(pair[1]['value'])-Decimal(pair[0]['value'])
                    matches=[d for d in result.get('derived_facts',[]) if d.get('metric')==metric and d.get('company')==pair[0]['company']]
                    if len(matches)!=1 or Decimal(matches[0]['value'])!=value:errors.append('wrong_calculated_difference')
    if e.get('goal')=='rank':
        candidates=[(key,Decimal(value['value'])) for key,value in gold.items() if key[1] in e['years'] and key[2]=='FY' and key[3] in e['metrics'] and key[4]==e.get('scope','consolidated')]
        candidates.sort(key=lambda row:(row[1],row[0][0]),reverse=e['order']=='desc')
        ordered=[(f['stock_code'],f['year'],f['period'],f['metric'],f['scope']) for f in facts]
        if ordered!=[key for key,_ in candidates[:e['limit']]]:errors.append('wrong_rank_order_or_members')
    return dict(passed=not errors,errors=sorted(set(errors)),outcome=result.get('outcome'),model_calls=(result.get('diagnostics') or {}).get('model_calls'))

