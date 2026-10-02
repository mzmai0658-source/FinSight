"""作品说明：固定真实财报题库的原页标准、真实入口运行和独立评分；不调用生产规划器或计算器评分。"""
from __future__ import annotations
import argparse,hashlib,json,math,re,statistics,sys,time,unicodedata
from collections import Counter
from decimal import Decimal,localcontext,ROUND_HALF_UP
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.v3_native_client import NativeClient
from eval.delivery_acceptance import hashes
from eval.score_v3_regressions import selectors,check_calculations,check_chart
FACTORS={'元':Decimal(1),'千元':Decimal(1000),'万元':Decimal(10000),'百万元':Decimal(1000000),'亿元':Decimal(100000000),'%':Decimal(1),'元/股':Decimal(1)}

def wanted(spec,reference):
    selection=selectors(spec,reference)
    found=[f for f in reference['facts'].values() if f['stock_code'] in selection and (f['year'],f['period']) in selection[f['stock_code']] and f['metric'] in spec['metrics'] and f['scope']==spec['scope']]
    if 'rank' in spec['goals']:
        found.sort(key=lambda f:(Decimal(f['value']),f['stock_code']),reverse=spec['order']=='desc');found=found[:spec['limit']]
    return found

def reference(folder):
    accepted=json.loads((folder/'acceptance.json').read_text(encoding='utf-8'))
    assert all(accepted.get(k) for k in ['facts_verified','reports_verified','index_verified','projections_verified'])
    for n,digest in accepted['artifacts'].items():assert hashlib.sha256((folder/n).read_bytes()).hexdigest()==digest,n
    fs=json.loads((folder/'facts.json').read_text(encoding='utf-8'))
    return {'version':fs[0]['data_version'],'facts':{f['id']:f for f in fs},'reports':json.loads((folder/'reports.json').read_text(encoding='utf-8'))}

def normalize(value):return re.sub(r'[\s,，%]','',unicodedata.normalize('NFKC',value)).replace('−','-').replace('－','-')

def prepare(suite,ref,output):
    """作品说明：标准先于回答生成。数值从原页行区域核对，派生值使用独立公式。"""
    from pypdf import PdfReader
    readers={};pages={};cards={};documents={};needed=set()
    for case in suite['cases']:
        e=case['expect']
        if e['kind'] in {'financial','reference'}:needed.update(f['id'] for f in wanted(e,ref))
    pending=list(needed)
    while pending:
        f=ref['facts'][pending.pop()]
        for id in f.get('inputs',[]):
            if id not in needed:needed.add(id);pending.append(id)
    def cell(f):
        if f['id'] in cards:return cards[f['id']]
        source=f.get('source')
        if source:
            path=(ROOT/source['source_path']).resolve()
            assert path.is_relative_to((ROOT/'data_root').resolve()) and path.suffix=='.pdf'
            key=source['source_sha256']
            if key not in readers:
                actual=hashlib.sha256(path.read_bytes()).hexdigest();assert actual==key
                readers[key]=PdfReader(path);documents[key]={'source_path':source['source_path'],'sha256':actual,'pages':len(readers[key].pages),'kind':'real'}
            page_key=(key,source['page'])
            if page_key not in pages:pages[page_key]=readers[key].pages[source['page']-1].extract_text() or ''
            original=pages[page_key];proof=source.get('original_cell') or source.get('scope_proof')
            if source.get('original_cell'):
                box=source['original_cell']['value']['bbox']
            elif source.get('scope_proof'):box=source['scope_proof']['value_region']['bbox']
            else:box=None
            # 作品说明：直接读取PDF区域；不使用数据库输出或运行时核验标记替代原件。
            region=original
            if box:
                import pdfplumber
                with pdfplumber.open(path) as pdf:region=pdf.pages[source['page']-1].crop(tuple(box)).extract_text() or ''
            raw=normalize(source['raw_value']);assert raw in normalize(region),(f['id'],'raw value absent from PDF row')
            if raw.startswith('(') and raw.endswith(')'):raw='-'+raw[1:-1]
            raw_number=Decimal(raw);exact=raw_number*FACTORS[source['raw_unit']]
            assert exact==Decimal(f['value'])
            label=normalize(source['row']).replace('其中：','').replace('其中:','')
            assert label in normalize(original) or source.get('original_cell'),(f['id'],'row label absent')
            card={'identity':{k:f[k] for k in ['stock_code','company','year','period','metric','scope','unit']},'value_exact':format(exact,'f'),'source':source,'original_row_text':region,'original_page_excerpt':original[:18000],'verification':'independent PDF text/region and Decimal normalization; existing identity/scope annotations also reaccepted; not a human-audit claim'}
        else:
            a,b=[cell(ref['facts'][id]) for id in f['inputs']]
            with localcontext() as ctx:
                ctx.prec=50;x,y=Decimal(a['value_exact']),Decimal(b['value_exact']);exact=x-y if f['metric']=='gross_profit' else x/y*100
            assert exact==Decimal(f['value'])
            card={'identity':{k:f[k] for k in ['stock_code','company','year','period','metric','scope','unit']},'value_exact':format(exact,'f'),'formula':f['formula'],'inputs':f['inputs'],'verification':'independent Decimal formula using PDF-bound bases'}
        cards[f['id']]=card;return card
    for i,id in enumerate(sorted(needed),1):
        cell(ref['facts'][id])
        if i%25==0:print(f'PDF oracle {i}/{len(needed)}',flush=True)
    golden={'version':3,'data_version':ref['version'],'suite_sha256':hashlib.sha256(json.dumps(suite,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),'documents':documents,'cards':cards,'review_method':'Independent PDF/Decimal checks plus explicit assistant narrative review; no user/member manual review asserted'}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(golden,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'oracle_facts':len(cards),'original_reports':len(documents),'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}))

def effective(goal,request):
    c=json.loads(json.dumps(goal.get('context_conditions') or request.get('conditions',{})))
    for edit in [*(request.get('modifications',[]) if goal.get('context_conditions') else []),*goal.get('condition_edits',[])]:
        field=edit['field'];op=edit['operation'];v=edit.get('value');parts=field.split('.');target=c
        for p in parts[:-1]:target=target.setdefault(p,{})
        leaf=parts[-1];old=target.get(leaf)
        if op in {'replace','keep'}:target[leaf]=v
        elif op=='add':target[leaf]=list(dict.fromkeys([*(old or []),*v]))
        elif op=='remove':target[leaf]=[x for x in old or [] if x not in v]
        elif op=='clear':target[leaf]=[] if isinstance(old,list) else None
    for k,v in (goal.get('selection') or {}).items():
        if v is not None:c[k]=dict(c.get(k) or {},**v) if k in {'time','presentation','restrictions'} else v
    return c

def score(case,run,ref,oracle,model,prior=None):
    e=case['expect'];task=run.get('task') or {};result=task.get('result') or {};body=(result.get('answer') or {}).get('content','');r=result.get('request_contract') or {}
    actual=result.get('facts') or [];charts=result.get('chart_data_list') or [];errors=[];critical=[];review=[]
    def require(test,message,severe=False):
        if not test:
            errors.append(message)
            if severe:critical.append(message)
    require(task.get('status')=='completed' and task.get('saved') is True,'task_not_completed_and_saved')
    require(bool(body.strip() or charts),'empty_output')
    require(bool(r),'missing_request_contract')
    timings=(result.get('diagnostics') or {}).get('timings',[])
    require(bool(timings) and all(t.get('model')==model for t in timings),'model_identity_not_confirmed')
    outcome=(result.get('outcome') or {}).get('status')
    planned={g['kind'] for g in r.get('goals',[])}
    conditions=[effective(g,r) for g in r.get('goals',[]) if g['kind'] in {'lookup','rank','compare','chart','quote','cause','sign'}]
    traces=result.get('query_trace') or []
    if e.get('no_query'):require(not any(q.get('purpose')=='financial_facts' for q in traces),'forbidden_numeric_query',True)
    if e.get('no_chart'):require(not charts,'forbidden_chart',True)
    if e['kind']=='clarify':
        require(outcome=='needs_clarification' or result.get('needs_clarification'),'required_clarification_missing')
        require(not actual and not charts,'financial_output_before_clarification',bool(actual or charts))
    elif e['kind']=='unsupported':
        require(outcome in {'unsupported','needs_clarification'} or outcome=='partial' and r.get('unsupported'),'boundary_not_explained')
        if e.get('outside_company'):require(e['outside_company'] in body,'uncovered_company_not_named')
        if not e.get('unsafe'):require(not actual and not charts,'unsupported_substituted_data',bool(actual or charts))
        if e.get('unsafe'):
            require(not any(re.search(r'\b(?:DELETE|UPDATE|INSERT|DROP|ALTER|CREATE|GRANT)\b',q.get('sql',''),re.I) for q in traces),'write_sql_executed',True)
    elif e['kind']=='concept':
        require('concept' in planned and outcome=='answered','concept_not_completed')
        require(not any(q.get('purpose')=='financial_facts' for q in traces),'concept_queried_numbers',True)
        review.append('concept')
    elif e['kind']=='reference':
        require(bool(prior),'prior_verified_fact_missing')
        require(outcome=='answered','reference_not_completed')
        if prior:
            require({f['id'] for f in actual}<={f['id'] for f in prior},'reference_changed_facts',True)
            if e.get('must_correct_sign'):require(bool(re.search('正数|为正|不是负|并非负',body)),'false_negative_not_corrected',True)
            if e.get('no_repeat'):
                for f in prior:
                    for unit in ('元','万元','亿元'):
                        d=(Decimal(f['value_exact'])/FACTORS[unit]).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                        require(format(d,'f') not in body.replace(',',''),'previous_amount_repeated')
        review.append('reference')
    else:
        expected=wanted(e,ref);expected_ids={f['id'] for f in expected};seen_ids={f['id'] for f in actual}
        require(result.get('data_version')==ref['version'],'data_version_changed' if result.get('data_version') else 'accepted_data_version_missing')
        require(expected_ids==seen_ids,'missing_or_extra_requested_facts')
        require(seen_ids<=expected_ids,'out_of_request_fact_published',bool(seen_ids-expected_ids))
        expected_goals=set(e['goals'])
        if e.get('calculation') and 'lookup' in expected_goals and 'compare' in planned:
            expected_goals=(expected_goals-{'lookup'})|{'compare'}
        require(expected_goals<=planned,'requested_goal_missing')
        require(not planned-{'lookup','compare','rank','chart','quote','cause','concept','sign'},'unrequested_goal')
        seen_codes=set().union(*(set(c.get('codes',[])) for c in conditions)) if conditions else set()
        seen_metrics=set().union(*(set(c.get('metrics',[])) for c in conditions)) if conditions else set()
        require(seen_codes==set(e['codes']),'company_set_mismatch')
        require(seen_metrics==set(e['metrics']),'metric_set_mismatch')
        for c in conditions:
            if e.get('cause_missing_year') and c.get('time',{}).get('years')==[e['cause_missing_year']]:continue
            require(c.get('scope')==e['scope'],'scope_not_retained',bool(actual))
            t=c.get('time') or {}
            if e['years']:
                years={p[0] for p in t.get('pairs') or []} or set(t.get('years',[]))
                periods={p[1] for p in t.get('pairs') or []} or set(t.get('periods',[]))
                require(years==set(e['years']),'requested_years_not_retained')
                require(periods==set(e['periods']),'requested_periods_not_retained')
            if e.get('time_policy'):require(t.get('mode')==e['time_policy'],'latest_period_policy_changed')
            for key in ('unit','decimals','format','order','limit'):
                if key in e:require(c.get('presentation',{}).get(key)==e[key],'presentation_'+key+'_mismatch')
            if e.get('calculation'):require(c.get('calculation')==e['calculation'],'calculation_not_retained')
        if 'rank' in e['goals']:require([f['id'] for f in actual]==[f['id'] for f in expected],'rank_order_or_count_wrong')
        for f in actual:
            original=ref['facts'].get(f['id']);card=oracle['cards'].get(f['id'])
            require(original is not None and card is not None,'unverified_fact_published',True)
            if original and card:
                require(all(f.get(k)==original[k] for k in ['stock_code','year','period','metric','scope','unit','data_version']),'fact_identity_changed',True)
                require(Decimal(f.get('value_exact','NaN'))==Decimal(card['value_exact']),'wrong_numeric_fact',True)
                if f.get('source'):
                    for k in ['source_sha256','page','row','raw_value','raw_unit']:require(f['source'].get(k)==original['source'].get(k),'wrong_source_'+k,True)
        if e.get('calculation'):
            check_calculations(actual,result.get('derived_facts') or [],e,require)
            for d in result.get('derived_facts') or []:
                formula=('(first-second)/abs(second)*100' if e.get('axis')=='companies' else '(current-base)/abs(base)*100') if e['calculation'] in {'yoy','relative_percent'} else ('first-second' if e.get('axis')=='companies' else 'current-base')
                require(d.get('formula')==formula,'wrong_calculation_formula',True)
                if d.get('value') is None:
                    require(any(t in body for t in ['未定义','无定义','不能计算']),'undefined_rate_not_explained')
                else:
                    unit=e.get('unit') if d['unit']=='元' and e.get('unit') else ('万元' if d['unit']=='元' else d['unit'])
                    value=(Decimal(d['value'])/(FACTORS[unit] if d['unit']=='元' else 1)).quantize(Decimal(1).scaleb(-e.get('decimals',2)),rounding=ROUND_HALF_UP)
                    require(format(value,'f') in body.replace(',',''),'derived_display_missing')
                    require(('个百分点' if unit=='百分点' else unit) in body,'derived_unit_display_missing')
        if 'compare' in planned:
            comparisons=result.get('comparisons') or [];indexed={f['id']:f for f in actual}
            require(bool(comparisons),'comparison_conclusion_missing')
            for item in comparisons:
                a,b=indexed.get(item.get('first')),indexed.get(item.get('second'))
                valid=bool(a and b)
                if valid:
                    x,y=Decimal(a['value_exact']),Decimal(b['value_exact'])
                    valid=item.get('relation')==('greater' if x>y else 'less' if x<y else 'equal') and all(a[k]==b[k] for k in ['metric','scope','period','unit','data_version'])
                    valid=valid and (a['year']==b['year'] if item.get('axis')=='companies' else a['stock_code']==b['stock_code'])
                require(valid,'incorrect_comparison_conclusion',True)
        statuses=result.get('task_results') or []
        for g in statuses:
            if g['kind'] not in expected_goals:continue
            permitted=e.get('evidence_gap_allowed') and g['kind']=='cause' or e.get('allow_missing') or e.get('unrenderable_chart')
            require(g['status']=='completed' or permitted,'goal_'+g['kind']+'_not_completed')
        if e.get('allow_missing') and not expected:
            require(outcome in {'no_data','partial'} and any(t in body for t in ['没有','未','无数据']),'missing_data_not_explained')
            require(not actual,'missing_data_replaced',True)
        elif e.get('evidence_gap_allowed'):
            require(outcome in {'answered','partial'},'reliable_part_lost')
            if any(g['kind']=='cause' and g['status']!='completed' for g in statuses):require(any(t in body for t in ['尚未','未找到','未确认','没有','未取得']),'evidence_limit_not_explained')
        elif not e.get('allow_missing') and not e.get('unrenderable_chart'):require(outcome=='answered','supported_request_not_completed')
        if e.get('format')=='table':require(all(line.strip().startswith('|') for line in body.splitlines() if line.strip()),'table_only_has_prose')
        if e.get('chart'):
            if e.get('unrenderable_chart'):require(not charts and any(x in body for x in ['单点','趋势','不能','无法']),'single_point_trend_fabricated',bool(charts))
            else:require(bool(charts),'chart_missing')
            for chart in charts:
                require(chart.get('chart_type')==e['chart'],'wrong_chart_type',True)
                if e.get('unit'):require(chart.get('unit')==e['unit'],'wrong_chart_unit',True)
                check_chart(chart,ref,require)
                if chart.get('chart_type')=='pie':
                    for series in chart.get('series',[]):
                        for point in series.get('values',[]):
                            if isinstance(point,dict) and point.get('fact_id'):
                                original=ref['facts'].get(point['fact_id']);require(original and Decimal(point['value_exact'])==Decimal(original['value'])/FACTORS[chart['unit']],'wrong_pie_point',True)
        if not e.get('calculation') and not set(e['goals'])&{'chart','quote','compare'}:
            clean=body.replace(',','')
            for f in actual:
                selected=next((c.get('presentation',{}).get('unit') for c in conditions if f['metric'] in c.get('metrics',[]) and c.get('presentation',{}).get('unit')),None)
                unit=e.get('unit') or (selected if f['unit']=='元' else f['unit']) or ('万元' if f['unit']=='元' else f['unit'])
                if unit not in FACTORS:continue
                digits=e.get('decimals',4 if f['unit']=='元/股' else 2)
                value=(Decimal(f['value_exact'])/(FACTORS[unit] if f['unit']=='元' else 1)).quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP)
                require(format(value,'f') in clean,'requested_number_display_missing')
                require(unit in body,'requested_unit_display_missing')
        if 'quote' in e['goals']:
            refs=[v for v in result.get('evidence',[]) if v.get('fact_id')]
            require(bool(refs),'original_page_missing')
            for v in refs:
                original=ref['facts'].get(v.get('fact_id'));s=(original or {}).get('source') or {}
                require(v.get('document_version')==s.get('document_version') and v.get('page_start')==s.get('page'),'wrong_quote_location',True)
                if e.get('quote_mode')=='literal':
                    card=oracle['cards'].get(v['fact_id'],{});literal=v.get('value_literal','')
                    require(bool(literal) and normalize(literal) in normalize(card.get('original_row_text','')),'fabricated_quote',True)
            if e.get('no_values_in_body'):require(not re.search(r'\d[\d,.]*\s*(?:万元|亿元|元/股|%)',body),'repeated_amount_in_location_only')
        if e.get('review'):review.append(e['review'])
    for terms in e.get('must_terms',[]):
        equivalent='比例' in terms and bool(re.search(r'毛利[额額]\s*/\s*营业收入\s*[×*]\s*100',body))
        require(any(t in body for t in terms) or equivalent,'missing_required_explanation_'+terms[0])
    if e.get('clear_company'):
        state=result.get('dialogue_state') or {};require(not (state.get('conditions') or {}).get('codes'),'cleared_company_revived',True)
    if e.get('no_old_fact'):require(not actual,'old_fact_used_for_missing_period',bool(actual))
    return {'passed':not errors and not review,'automated_pass':not errors,'errors':sorted(set(errors)),'critical':sorted(set(critical)),'review_required':sorted(set(review)),'review_status':'pending' if review else 'not_required'}

def summarize(records,target=170):
    groups={x['case']['group'] for x in records if x['case'].get('group')};durations=[x['run']['seconds'] for x in records if x.get('run')]
    summary={'total':len(records),'passed':sum(x['score']['passed'] for x in records),'automated_pass':sum(x['score']['automated_pass'] for x in records),'target':target,'source_review_pending':sum(x['score']['review_status']=='pending' for x in records),'critical':Counter(v for x in records for v in x['score']['critical']),'by_category':{}}
    for cat in dict.fromkeys(x['case']['category'] for x in records):
        rows=[x for x in records if x['case']['category']==cat];summary['by_category'][cat]={'total':len(rows),'passed':sum(x['score']['passed'] for x in rows)}
    summary['dialogue_groups']={g:all(x['score']['passed'] for x in records if x['case'].get('group')==g) for g in sorted(groups)}
    summary['accuracy']=round(summary['passed']/len(records),4) if records else 0
    summary['acceptance_passed']=len(records)==200 and summary['passed']>=target and not summary['critical'] and not summary['source_review_pending']
    if durations:summary.update(median_seconds=statistics.median(durations),mean_seconds=statistics.mean(durations),max_seconds=max(durations),p95_seconds=sorted(durations)[math.ceil(len(durations)*.95)-1],percentile_method='nearest_rank')
    return summary

def main():
    p=argparse.ArgumentParser();p.add_argument('--suite',default='eval/real200_cases.json');p.add_argument('--audit',default='data/runtime/qa200/real-release');p.add_argument('--oracle',default='data/runtime/qa200/oracle.json');p.add_argument('--output',default='data/runtime/qa200/run0');p.add_argument('--base',default='http://127.0.0.1:19100');p.add_argument('--model',default='qwen3.5:9b-q4_K_M');p.add_argument('--prepare',action='store_true');p.add_argument('--ids',default='');p.add_argument('--resume',action='store_true');a=p.parse_args()
    suite_path=ROOT/a.suite;suite=json.loads(suite_path.read_text(encoding='utf-8'));ref=reference(ROOT/a.audit);oracle_path=ROOT/a.oracle
    assert suite['source_data_version']==ref['version']
    if a.prepare:prepare(suite,ref,oracle_path);return
    oracle=json.loads(oracle_path.read_text(encoding='utf-8'));assert oracle['data_version']==ref['version']
    assert oracle['suite_sha256']==hashlib.sha256(json.dumps(suite,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),'Question suite changed after reference preparation'
    out=ROOT/a.output;out.mkdir(parents=True,exist_ok=True);path=out/'records.jsonl'
    initial=hashes();initial['eval/real200_cases.json']=hashlib.sha256(suite_path.read_bytes()).hexdigest();initial['eval/real200_acceptance.py']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();initial['oracle']=hashlib.sha256(oracle_path.read_bytes()).hexdigest()
    records=[];groups={};prior={}
    if path.exists():
        if not a.resume:raise FileExistsError('不可覆盖已有运行；使用新目录或按固定版本恢复未提交题目')
        assert json.loads((out/'source_hashes.json').read_text())==initial,'Cannot resume another version'
        records=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        for row in records:
            if row['case'].get('group') and row.get('run'):
                group=row['case']['group'];groups[group]=row['run']['session_uid']
                fs=(row['run'].get('task',{}).get('result') or {}).get('facts') or []
                if row['score']['automated_pass'] and fs:prior[group]=fs
    else:
        (out/'source_hashes.json').write_text(json.dumps(initial,indent=2),encoding='utf-8')
        (out/'cases.json').write_bytes(suite_path.read_bytes())
        import requests
        tags=requests.get('http://127.0.0.1:11434/api/tags',timeout=5).json()['models'];identity=next(x for x in tags if x['name']==a.model)
        (out/'environment.json').write_text(json.dumps({'model':identity,'data_version':ref['version'],'index_collection':json.loads((ROOT/a.audit/'index_coverage.json').read_text())['collection'],'reference_date':suite['reference_date'],'context':16384,'temperature':0,'num_predict':3072,'thinking':False,'entry':'Java/SSE -> Python Agent -> real Ollama/MySQL/PDF/Chroma','data_kind':'real','quota_per_day':1000,'test_identity':'isolated local test account; credentials excluded'},ensure_ascii=False,indent=2),encoding='utf-8')
    clients={};completed={r['case']['id'] for r in records}
    cases=[c for c in suite['cases'] if (not a.ids or c['id'] in a.ids.split(',')) and c['id'] not in completed]
    for case in cases:
        current=hashes()
        if any(current.get(k)!=v for k,v in initial.items() if k not in {'eval/real200_cases.json','eval/real200_acceptance.py','oracle'}):raise RuntimeError('Frozen production changed')
        assert hashlib.sha256(suite_path.read_bytes()).hexdigest()==initial['eval/real200_cases.json']
        assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==initial['eval/real200_acceptance.py']
        try:
            # 作品说明：两个隔离账号各少于100个会话，避免验收自身触发现有历史保留上限；每组对话始终使用同一真实账号。
            cohort=0 if int(case['id'][1:])<=80 else 1
            credential_name='private_credentials.json' if cohort==0 else 'private_credentials_b.json'
            if cohort not in clients:clients[cohort]=NativeClient(a.base,out/credential_name)
            client=clients[cohort]
            run=client.turn(case['question'],groups.get(case['group']) if case['group'] else None)
            run['credential_file']=credential_name
            if case['group']:groups[case['group']]=run['session_uid']
            verdict=score(case,run,ref,oracle,a.model,prior.get(case['group']))
            fs=(run['task'].get('result') or {}).get('facts') or []
            if case['group'] and verdict['automated_pass'] and fs:prior[case['group']]=fs
            row={'case':case,'run':run,'score':verdict}
        except Exception as exc:row={'case':case,'error':type(exc).__name__,'score':{'passed':False,'automated_pass':False,'errors':['test_or_service_'+type(exc).__name__],'critical':[],'review_required':[],'review_status':'not_required'}}
        records.append(row)
        with path.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        summary=summarize(records,suite['target']);(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'id':case['id'],'auto':row['score']['automated_pass'],'errors':row['score']['errors'],'critical':row['score']['critical'],'seconds':row.get('run',{}).get('seconds'),'done':len(records),'pending_review':summary['source_review_pending']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
