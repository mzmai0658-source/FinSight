"""作品说明：通过真实登录后的 Java/SSE 入口开展有界双模型对照，使用实际保存的对话状态。标准数值来自独立验收的原始单元格，不采用模型输出；失败回答不重试、不注入历史、不由模型评分。"""
import argparse,hashlib,json,re,statistics,sys,time
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.v3_native_client import NativeClient
from src.agent.v3.contracts import Request


def hashes():
    paths=[*ROOT.glob('src/agent/v3/*.py'),*ROOT.glob('src/api/*.py'),*ROOT.glob('src/utils/*.py')]
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


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
    # 作品说明：检查实际显示的金额单位，避免仅凭保存的请求判定单位正确。
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


def main():
    p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--base',default='http://127.0.0.1:18080')
    p.add_argument('--output',required=True);p.add_argument('--ids',default='');args=p.parse_args()
    out=ROOT/args.output;out.mkdir(parents=True,exist_ok=True)
    source=ROOT/'eval/local_model_cases_20261001.json';cases=json.loads(source.read_text(encoding='utf-8'))['cases']
    if args.ids:cases=[c for c in cases if c['id'] in args.ids.split(',')]
    if (out/'records.jsonl').exists():raise ValueError('Run folders are immutable; select a new output folder')
    audit=ROOT/'data/runtime/v3/audit-physical-scope';manifest=json.loads((audit/'manifest.json').read_text(encoding='utf-8'))
    acceptance=json.loads((audit/'acceptance.json').read_text(encoding='utf-8'))
    assert acceptance['facts_verified'] and acceptance['index_verified']
    gold_rows=json.loads((audit/'facts.json').read_text(encoding='utf-8'))
    gold={(f['stock_code'],f['year'],f['period'],f['metric'],f['scope']):f for f in gold_rows}
    baseline=hashes();(out/'source_hashes.json').write_text(json.dumps(baseline,indent=2),encoding='utf-8')
    (out/'cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'environment.json').write_text(json.dumps(dict(model=args.model,data_version=manifest['version'],cases_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),limits=dict(context=16384,temperature=0,num_predict=3072),scope='Real Java/SSE/Python/Ollama/MySQL; independently accepted PDF cells; actual saved dialogue'),indent=2),encoding='utf-8')
    client=NativeClient(args.base,ROOT/'.local_runtime/v3'/('local-adaptation-'+args.model.split(':')[0]+'.json'))
    groups={};records=[]
    for case in cases:
        if hashes()!=baseline:raise RuntimeError('Production code changed during this frozen run')
        try:
            run=client.turn(case['question'],groups.get(case['group']) if case['group'] else None)
            if case['group']:groups[case['group']]=run['session_uid']
            verdict=score(case,run,gold,args.model)
            result=run['task'].get('result') or {}
            if result.get('data_version') and result['data_version']!=manifest['version']:verdict['passed']=False;verdict['errors'].append('data_version_changed')
            row=dict(case=case,run=run,score=verdict)
        except Exception as exc:
            row=dict(case=case,error=type(exc).__name__,score=dict(passed=False,errors=['test_or_service_'+type(exc).__name__]))
        records.append(row)
        with (out/'records.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        print(json.dumps(dict(id=case['id'],passed=row['score']['passed'],errors=row['score']['errors'],seconds=row.get('run',{}).get('seconds')),ensure_ascii=False),flush=True)
    summary=dict(model=args.model,total=len(records),passed=sum(r['score']['passed'] for r in records),target=26 if len(records)==30 else None,
        source_unchanged=hashes()==baseline,by_category={})
    for category in ('classic','edge','dialogue'):
        rows=[r for r in records if r['case']['category']==category]
        summary['by_category'][category]=dict(total=len(rows),passed=sum(r['score']['passed'] for r in rows))
    summary['whole_dialogue_groups']={group:all(r['score']['passed'] for r in records if r['case']['group']==group) for group in groups}
    durations=[r['run']['seconds'] for r in records if 'run' in r]
    if durations:summary.update(median_seconds=statistics.median(durations),mean_seconds=statistics.mean(durations),p95_seconds=sorted(durations)[min(len(durations)-1,int(len(durations)*.95))])
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
