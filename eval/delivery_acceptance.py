"""作品说明：通过真实登录后的 Java/SSE 入口开展有界双模型对照，使用实际保存的对话状态。标准数值来自独立验收的原始单元格，不采用模型输出；失败回答不重试、不注入历史、不由模型评分。"""
import argparse,hashlib,json,re,statistics,sys,time,math
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.v3_native_client import NativeClient
from src.agent.v3.contracts import Request


def hashes():
    paths=[*(ROOT/'src').rglob('*.py'),*[p for p in ROOT.glob('config/*.py') if p.name!='local_keys_private.py'],*ROOT.glob('config/*.json'),*ROOT.glob('backend-java/src/main/**/*.java'),*ROOT.glob('backend-java/src/main/resources/**/*.yml'),*ROOT.glob('backend-java/src/main/resources/**/*.sql'),*ROOT.glob('frontend/src/**/*.ts'),*ROOT.glob('frontend/src/**/*.vue'),*ROOT.glob('frontend/src/**/*.css'),*ROOT.glob('shared/**/*.json'),ROOT/'requirements.lock.txt',ROOT/'frontend/package-lock.json',ROOT/'backend-java/pom.xml',ROOT/'demo/v3/spec.json',ROOT/'demo/v3/generate.py']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


from eval.local_model_evaluation import score as common_score


def score(case,run,gold,selected):
    if case['expect'].get('outside_company'):
        result=(run.get('task') or {}).get('result') or {}
        body=(result.get('answer') or {}).get('content','')
        passed=run['task']['status']=='completed' and not result.get('facts') and case['expect']['outside_company'] in body and (result.get('needs_clarification') or result.get('outcome',{}).get('status')=='unsupported')
        return dict(passed=passed,errors=[] if passed else ['outside_company_boundary_not_explained'],outcome=result.get('outcome'),model_calls=result.get('diagnostics',{}).get('model_calls'))
    result=common_score(case,run,gold,selected)
    body=((run.get('task',{}).get('result') or {}).get('answer') or {}).get('content','').replace(',','')
    if case['expect'].get('no_repeat') and any(v in body for v in ('160000000','16000.00')):
        result['passed']=False
        result['errors'].append('previous_amount_repeated')
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--base',default='http://127.0.0.1:18080')
    p.add_argument('--output',required=True);p.add_argument('--ids',default='');args=p.parse_args()
    out=ROOT/args.output;out.mkdir(parents=True,exist_ok=True)
    source=ROOT/'eval/delivery_cases.json';cases=json.loads(source.read_text(encoding='utf-8'))['cases']
    if args.ids:cases=[c for c in cases if c['id'] in args.ids.split(',')]
    if (out/'records.jsonl').exists():raise ValueError('Run folders are immutable; select a new output folder')
    audit=ROOT/'data/runtime/demo-v3';manifest=json.loads((audit/'manifest.json').read_text(encoding='utf-8'))
    acceptance=json.loads((audit/'acceptance.json').read_text(encoding='utf-8'))
    assert acceptance['facts_verified'] and acceptance['index_verified']
    gold_rows=json.loads((audit/'facts.json').read_text(encoding='utf-8'))
    gold={(f['stock_code'],f['year'],f['period'],f['metric'],f['scope']):f for f in gold_rows}
    baseline=hashes();(out/'source_hashes.json').write_text(json.dumps(baseline,indent=2),encoding='utf-8')
    (out/'cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'environment.json').write_text(json.dumps(dict(model=args.model,data_version=manifest['version'],cases_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),limits=dict(context=16384,temperature=0,num_predict=3072),scope='Real Java/SSE/Python/Ollama/MySQL; independently accepted PDF cells; actual saved dialogue'),indent=2),encoding='utf-8')
    client=NativeClient(args.base,ROOT/'.local_runtime/delivery/qa_credentials.json')
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
    summary=dict(model=args.model,total=len(records),passed=sum(r['score']['passed'] for r in records),target=17 if len(records)==20 else None,
        source_unchanged=hashes()==baseline,by_category={})
    for category in ('classic','edge','dialogue'):
        rows=[r for r in records if r['case']['category']==category]
        summary['by_category'][category]=dict(total=len(rows),passed=sum(r['score']['passed'] for r in rows))
    summary['whole_dialogue_groups']={group:all(r['score']['passed'] for r in records if r['case']['group']==group) for group in groups}
    durations=[r['run']['seconds'] for r in records if 'run' in r]
    if durations:summary.update(median_seconds=statistics.median(durations),mean_seconds=statistics.mean(durations),max_seconds=max(durations),percentile_method='nearest_rank',p95_seconds=sorted(durations)[min(len(durations)-1,math.ceil(len(durations)*.95)-1)])
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
