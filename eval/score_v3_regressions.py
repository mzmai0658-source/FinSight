"""作品说明：对保留请求及有原件依据的数值进行独立断言，不导入生产规划器、执行器或其核验结论；叙述含义仍需另行审查原文。"""
from __future__ import annotations
import argparse,hashlib,json,re
from collections import Counter
from decimal import Decimal,localcontext
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MULTIPLIERS={'元':Decimal(1),'万元':Decimal(10000),'亿元':Decimal(100000000),'%':Decimal(1),'元/股':Decimal(1)}

def fixture(folder):
    accepted=json.loads((folder/'acceptance.json').read_text(encoding='utf-8'))
    assert accepted['facts_verified'] and accepted['index_verified']
    path=folder/'facts.json'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==accepted['artifacts'][path.name]
    values=json.loads(path.read_text(encoding='utf-8'))
    narrative_path=folder/'narratives.jsonl'
    pages={}
    if 'narratives.jsonl' in accepted['artifacts']:
        assert hashlib.sha256(narrative_path.read_bytes()).hexdigest()==accepted['artifacts']['narratives.jsonl']
        pages={(r['document_version'],r['page']):r['text'] for r in
            (json.loads(line) for line in narrative_path.read_text(encoding='utf-8').splitlines())}
    return dict(version=values[0]['data_version'],facts={v['id']:v for v in values},
        reports=json.loads((folder/'reports.json').read_text(encoding='utf-8')),
        original_pages=pages,
        fact_sha256=accepted['artifacts']['facts.json'])

def selectors(spec,reference):
    catalog={(r['stock_code'],r['year'],r['period']) for r in reference['reports']}
    codes=spec['codes']; periods=spec['periods']; years=spec['years']; policy=spec.get('time_policy')
    result={}
    for code in codes:
        if spec.get('pairs'): pairs={tuple(v) for v in spec['pairs']}
        elif years: pairs={(year,period) for year in years for period in periods}
        elif policy=='calendar_years': pairs={(year,period) for year in range(2026-spec['span'],2026) for period in periods}
        else:
            available={year for c,year,period in catalog if c==code and period in periods}
            if policy=='latest_common':
                common=None
                for c in codes:
                    candidates={year for cc,year,p in catalog if cc==c and p in periods}
                    common=candidates if common is None else common & candidates
                available=common or set()
            end=max(available) if available else None
            pairs={(year,period) for year in range(end-spec.get('span',1)+1,end+1) for period in periods} if end else set()
        if spec.get('calculation')=='yoy' and len({year for year,_ in pairs})==1:
            pairs |= {(year-1,period) for year,period in list(pairs)}
        result[code]=pairs
    return result

def effective_conditions(request):
    output=[]
    for goal in request.get('goals',[]):
        if goal['kind'] not in {'lookup','compare','rank','chart','quote','cause','sign'}:continue
        base=json.loads(json.dumps(request.get('conditions',{})))
        for key,value in (goal.get('selection') or {}).items():
            if value is None:continue
            if key in {'time','presentation'}:base[key].update(value)
            else:base[key]=value
        output.append(base)
    return output or [request.get('conditions',{})]

def score(record,spec,references,prior_facts=None):
    errors=[];critical=[];pending=[]
    def require(condition,message,zero=False):
        if not condition:
            errors.append(message)
            # 作品说明：传输结束不等于用户目标完成。格式错误或被阻断的计划仍记失败；已发布结果或声称完成时，进一步检查是否发生条件替换。
            if zero and (outcome=='answered' or actual or charts):critical.append(message)
    task=record['run']['task'];result=task.get('result') or {};request=result.get('request_contract') or {}
    outcome=(result.get('outcome') or {}).get('status');body=(result.get('answer') or {}).get('content','')
    actual=result.get('facts') or [];charts=result.get('chart_data_list') or []
    require(task['status']=='completed','Task did not complete through the real service')
    require(task.get('saved') is True,'Final task was not durably saved')
    require(result.get('task_id')==record['run']['task_id'],'Result/task identity mismatch',True)
    require(bool(body.strip() or charts),'No deliverable')
    require(outcome not in {'failed','cancelled'},'Interpreter or service failed')
    calls=(result.get('diagnostics') or {}).get('model_calls',0)
    require(calls<=6,'Model call budget exceeded')
    if spec.get('no_query'):
        require(not actual and not result.get('derived_facts') and result.get('sql') in {None,'','-'},'Executed an unrequested financial query',True)
    if spec['kind']=='clarify':
        require(outcome=='needs_clarification','Missing/conflicting conditions were not clarified')
        require(not actual and not charts,'Published financial output while clarification was required',True)
        conditions=request.get('conditions',{})
        for key,actual_key in [('known_codes','codes'),('known_metrics','metrics')]:
            if key in spec:require(set(conditions.get(actual_key,[]))==set(spec[key]),'Lost known partial '+actual_key,True)
        if spec.get('known_years'):require(set(conditions.get('time',{}).get('years',[]))==set(spec['known_years']),'Lost known partial year',True)
    elif spec['kind']=='unsupported':
        require(outcome=='unsupported' or outcome=='partial' and bool(request.get('unsupported')),'Capability/unsafe request was not explicitly rejected')
        if spec.get('quarters'):
            time=request.get('conditions',{}).get('time',{})
            require(time.get('single_quarter') and set(time.get('quarters',[]))==set(spec['quarters']),'Single-quarter intent was substituted',True)
    elif spec['kind'] in {'concept','rules','catalog','other'}:
        allowed={'rules':{'rules','catalog'},'catalog':{'catalog','rules'},'concept':{'concept'},'other':{'unsupported'}}[spec['kind']]
        kinds={g['kind'] for g in request.get('goals',[])}
        require(bool(kinds & allowed) or spec['kind']=='other' and not kinds,'Wrong nonfinancial goal')
        require(outcome in {'answered','unsupported'} if spec['kind']=='other' else outcome=='answered','Nonfinancial goal was not fulfilled')
        if spec['kind']=='concept':pending.append('Check the concept definition and any factual correction against the documented definition/source.')
    else:
        reference=references.get(result.get('data_version'))
        require(reference is not None,'Expected financial task was not executed or used an unaccepted data version',bool(actual or charts or outcome=='answered'))
        if reference:
            selection=selectors(spec,reference)
            conditions=effective_conditions(request)
            require(set().union(*(set(c.get('codes',[])) for c in conditions))==set(spec['codes']),'Company selection differs from original request',True)
            require(set().union(*(set(c.get('metrics',[])) for c in conditions))==set(spec['metrics']),'Metric silently substituted or omitted',True)
            require(all(c.get('scope')==spec['scope'] for c in conditions),'Report scope silently substituted',True)
            for c in conditions:
                if spec['years']:require(set(c.get('time',{}).get('years',[]))==set(spec['years']) or bool(spec.get('pairs')) and {tuple(p) for p in c.get('time',{}).get('pairs') or []}=={tuple(p) for p in spec['pairs']},'Requested years not retained',True)
                for key in ('unit','decimals','format','order','limit'):
                    if key in spec:require(c.get('presentation',{}).get(key)==spec[key],'Output '+key+' differs from request',True)
                if spec.get('time_policy'):require(c.get('time',{}).get('mode')==spec['time_policy'],'Wrong latest/calendar-year policy',True)
                if spec.get('span'):require(c.get('time',{}).get('span')==spec['span'],'Wrong number of years',True)
                if spec.get('calculation'):require(c.get('calculation')==spec['calculation'],'Requested calculation was replaced',True)
                if spec.get('axis'):require(c.get('comparison_axis')==spec['axis'],'Wrong calculation comparison axis',True)
            wanted=[f for f in reference['facts'].values() if f['stock_code'] in selection and
                (f['year'],f['period']) in selection[f['stock_code']] and f['metric'] in spec['metrics'] and f['scope']==spec['scope']]
            if 'rank' in spec.get('goals',[]):
                wanted.sort(key=lambda f:(Decimal(f['value']),f['stock_code']),reverse=spec['order']=='desc')
                wanted=wanted[:spec['limit']]
            wanted_ids={f['id'] for f in wanted}
            for fact in actual:
                truth=reference['facts'].get(fact['id'])
                require(truth is not None,'Fabricated fact ID',True)
                if truth:
                    require(all(fact.get(k)==truth[k] for k in ('stock_code','year','period','metric','scope','unit','data_version')),'Fact identity or unit mismatch',True)
                    require(Decimal(fact['value_exact'])==Decimal(truth['value']),'Fabricated/incorrect exact value',True)
                    require(fact['id'] in wanted_ids,'Out-of-request fact or cross-period replacement',True)
                    if truth.get('source'):require(fact.get('source',{}).get('source_sha256')==truth['source']['source_sha256'],'Fabricated source',True)
            require({f['id'] for f in actual}==wanted_ids,'Available requested facts were omitted')
            if 'rank' in spec.get('goals',[]):require([f['id'] for f in actual]==[f['id'] for f in wanted],'Wrong ranking count/order',True)
            expected_goals=set(spec.get('goals',[])) | ({'chart'} if spec.get('chart') else set())
            planned={g['kind'] for g in request.get('goals',[])}
            require(expected_goals<=planned,'Requested objective omitted from execution plan',True)
            for goal in result.get('task_results',[]):
                if goal['kind'] in expected_goals and wanted and goal['kind']!='unsupported':
                    require(goal['status']=='completed','Requested '+goal['kind']+' was not completed')
            require(not wanted or outcome not in {'needs_clarification','no_data'},'Supported complete request did not produce its available result')
            if spec.get('calculation'):check_calculations(actual,result.get('derived_facts') or [],spec,require)
            for chart in charts:check_chart(chart,reference,require)
            if spec.get('chart'):
                require(bool(charts),'Requested chart missing')
                require(all(c.get('chart_type')==spec['chart'] for c in charts),'Chart type silently changed',True)
                if spec.get('unit'):require(all(c.get('unit')==spec['unit'] for c in charts),'Chart unit silently changed',True)
            if spec.get('no_chart') or spec.get('format')=='table':require(not charts,'Negated chart generated',True)
            if spec.get('allow_unrenderable_chart'):
                require(not charts,'A one-point trend was fabricated',True)
                require(any(term in body for term in ('单点','趋势','无法','不能','尚未')),'One-point chart limitation was not explained')
            if spec.get('format')=='table':require(all(line.startswith('|') for line in body.splitlines() if line.strip()),'Table-only request has prose outside the table')
            if spec.get('format')=='chart':require('|---' not in body,'Chart-only request includes a table')
            if spec.get('quote_mode'):
                require(all(g.get('quote_mode')==spec['quote_mode'] for g in request.get('goals',[]) if g['kind']=='quote'),'Wrong quote display mode',True)
            if 'quote' in expected_goals:
                quote_refs=[r for r in result.get('evidence',[]) if r.get('fact_id')]
                require(bool(quote_refs) or not wanted,'Requested original location/quotation missing',True)
                for ref in quote_refs:
                    fact=reference['facts'].get(ref['fact_id']);source=(fact or {}).get('source') or {}
                    literal=ref.get('value_literal','')
                    proof=source.get('original_cell')
                    if proof:
                        import pdfplumber
                        path=(ROOT/source['source_path']).resolve()
                        original=''
                        if path.is_relative_to((ROOT/'data_root').resolve()) and hashlib.sha256(path.read_bytes()).hexdigest()==source['source_sha256']:
                            with pdfplumber.open(path) as document:original=document.pages[proof['value']['page']-1].crop(tuple(proof['value']['bbox'])).extract_text() or ''
                        original_match=literal==original==source['raw_value']
                    else:
                        page=reference.get('original_pages',{}).get((source.get('document_version'),source.get('page')),'')
                        if not page and source.get('source_path'):
                            from pypdf import PdfReader
                            path=(ROOT/source['source_path']).resolve()
                            if path.is_relative_to((ROOT/'data_root').resolve()) and hashlib.sha256(path.read_bytes()).hexdigest()==source['source_sha256']:
                                page=PdfReader(path).pages[source['page']-1].extract_text() or ''
                        def number(value):
                            text=re.sub(r'[\s,，%]','',value).replace('−','-').replace('－','-').replace('（','(').replace('）',')')
                            if text.startswith('(') and text.endswith(')'):text='-'+text[1:-1]
                            return Decimal(text) if re.fullmatch(r'-?\d+(?:\.\d+)?',text) else None
                        original_match=bool(literal and literal in page and number(literal)==number(source.get('raw_value','')))
                    require(original_match and ref.get('page_start')==source.get('page') and ref.get('document_version')==source.get('document_version'),'Quote/source does not match the original fact',True)
            if 'compare' in expected_goals:
                comparisons=result.get('comparisons',[]);lookup={f['id']:f for f in actual}
                require(bool(comparisons),'Requested comparison conclusion missing')
                for comparison in comparisons:
                    a,b=lookup.get(comparison.get('first')),lookup.get(comparison.get('second'))
                    valid=bool(a and b)
                    if valid:
                        first,second=Decimal(a['value_exact']),Decimal(b['value_exact'])
                        relation='greater' if first>second else 'less' if first<second else 'equal'
                        valid=comparison.get('relation')==relation and all(a[k]==b[k] for k in ['metric','scope','period','unit','data_version']) and (
                            a['year']==b['year'] if comparison.get('axis')=='companies' else a['stock_code']==b['stock_code'])
                    require(valid,'Comparison conclusion contradicts exact facts or crosses accounting periods',True)
            if spec.get('no_values_in_body'):require(not re.search(r'\d[\d,.]*\s*(?:亿?万元|亿元|元/股|%)',body),'Repeated financial values when only location was requested',True)
            if 'cause' in expected_goals:pending.append('Review each stated cause against its exact company/period/metric narrative evidence.')
    if spec.get('must_correct_sign'):
        positive_reference=prior_facts is None or len(prior_facts)==1 and Decimal(prior_facts[0]['value'])>0
        if positive_reference:
            require(any(word in body for word in ('正数','为正','实际为正','先核对','并非负','不是负')),'Did not correct the false negative premise',True)
        else:
            # 作品说明：前次查询失败后，后续回答不能假装见过正数。原场景仍算失败，但询问指代对象不等于编造财务事实。
            require(False,'Referenced lookup produced no unique verified positive fact; original conversation scenario remains unfulfilled')
    if spec.get('must_say_loss'):require('亏损' in body,'Loss conclusion missing or reversed',True)
    if spec.get('must_absolute_base'):require('绝对' in body and ('扭亏' in body or '由亏转盈' in body),'Negative base convention not explained')
    if spec.get('must_mention_no_data'):require(any(word in body for word in ('未取得','未查到','没有查到','无数据','未找到')),'Falsely claimed a previous missing result completed',True)
    if spec.get('must_warn_period_span'):require(any(word in body for word in ('跨度','不同期间','不同报告期','不同累计','不能比较')),'Unmatched cumulative spans not disclosed')
    return dict(id=record['id'],automated_pass=not errors,errors=errors,zero_tolerance_violations=critical,
        source_review_required=pending,acceptance_complete=not errors and not pending)

def check_calculations(facts,derived,spec,require):
    indexed={f['id']:f for f in facts}
    require(bool(derived),'Requested calculation missing')
    for d in derived:
        inputs=[indexed.get(i) for i in d.get('inputs',[])]
        require(len(inputs)==2 and all(inputs),'Calculation bases missing',True)
        if len(inputs)!=2 or not all(inputs):continue
        a,b=inputs
        with localcontext() as context:
            context.prec=50
            x,y=Decimal(a['value_exact']),Decimal(b['value_exact'])
            method=spec['calculation']
            if method in {'yoy','relative_percent'}:
                expected=(y-x)/abs(x)*100 if x else None;unit='%'
            else:
                expected=x-y if spec.get('axis')=='companies' else y-x
                unit='百分点' if method=='percentage_points' else a['unit']
        require(d.get('unit')==unit,'Calculation unit wrong',True)
        require(d.get('value') is None if expected is None else Decimal(d.get('value','NaN'))==expected,'Wrong Decimal calculation',True)

def check_chart(chart,reference,require):
    unit=chart.get('unit');multiplier=MULTIPLIERS.get(unit)
    if not multiplier:return
    for series in chart.get('series',[]):
        for ref,number in zip(series.get('fact_ids',[]),series.get('values_exact',[])):
            if ref is None:require(number is None,'Missing chart point was filled',True);continue
            fact=reference['facts'].get(ref)
            require(fact is not None,'Fabricated chart fact',True)
            if fact:
                with localcontext() as context:
                    context.prec=50;expected=Decimal(fact['value'])/multiplier
                require(number is not None and Decimal(number)==expected,'Wrong chart value, sign or unit',True)


def observed_fact_context(run,references,previous):
    """作品说明：用封存原件样例核对实际发布的事实，不依赖生产记忆引用或核验徽标。失败、取消和无数据的财务轮次清空事实上下文；概念插话保留上下文，直到明确清空或新的财务查询。"""
    task=run['task'];result=task.get('result') or {};request=result.get('request_contract') or {}
    if task['status'] in {'failed','cancelled'} or request.get('continuity')=='clear':return []
    reference=references.get(result.get('data_version'))
    observed=[]
    for value in result.get('facts',[]):
        truth=(reference or {}).get('facts',{}).get(value['id'])
        if truth and value.get('value_exact')==truth['value'] and all(value.get(key)==truth[key] for key in ('stock_code','year','period','metric','scope','unit')):
            observed.append(truth)
    if observed:return observed
    if result.get('response_kind')=='financial' or any(g['kind'] in {'lookup','compare','rank','chart','quote','cause','sign'} for g in request.get('goals',[])):return []
    return previous

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--fixtures',default='data/runtime/v3/audit-ordered,data/runtime/v3/audit-row-units,data/runtime/v3/audit-caption-columns,data/runtime/v3/audit-signed-cached,data/runtime/v3/audit-original-regions,data/runtime/v3/audit-disclosed-basis,data/runtime/v3/audit-annual-end-columns,data/runtime/v3/audit-rate-provenance');args=p.parse_args()
    definitions=json.loads((ROOT/'eval/regression_v3/expectations.json').read_text(encoding='utf-8'))['expectations']
    references={r['version']:r for r in (fixture(ROOT/f) for f in args.fixtures.split(','))}
    folder=ROOT/args.run;records=[json.loads(line) for line in (folder/'records.jsonl').read_text(encoding='utf-8').splitlines()]
    replay={}
    replay_path=folder/'context_replay.jsonl'
    if replay_path.exists():
        for line in replay_path.read_text(encoding='utf-8').splitlines():
            value=json.loads(line);replay.setdefault(value['for_case'],[]).append(value['run'])
    contexts={};scores=[]
    for record in records:
        uid=record['run']['session_uid']
        if uid not in contexts:
            contexts[uid]=[]
            for run in replay.get(record['id'],[]):contexts[uid]=observed_fact_context(run,references,contexts[uid])
        scores.append(score(record,definitions[record['id']],references,contexts[uid]))
        contexts[uid]=observed_fact_context(record['run'],references,contexts[uid])
    result=dict(version=3,turns=len(scores),automated_pass=sum(s['automated_pass'] for s in scores),
        zero_tolerance_violations=sum(len(s['zero_tolerance_violations']) for s in scores),
        independent_expectations_sha256=hashlib.sha256((ROOT/'eval/regression_v3/expectations.json').read_bytes()).hexdigest(),
        complete_historical_run=len({r['id'] for r in records})==153,source_review_pending=sum(bool(s['source_review_required']) for s in scores),
        acceptance_complete=bool(scores) and len(scores)==153 and all(s['acceptance_complete'] for s in scores),
        fixture_sha256={r['version']:r['fact_sha256'] for r in references.values()},scores=scores)
    result['scorer_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    previous=folder/'independent_score.json'
    if previous.exists():
        history=folder/'scoring-history';history.mkdir(exist_ok=True)
        (history/(hashlib.sha256(previous.read_bytes()).hexdigest()+'.json')).write_bytes(previous.read_bytes())
    (folder/'independent_score.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'scores','fixture_sha256'}},ensure_ascii=False))

if __name__=='__main__':main()
