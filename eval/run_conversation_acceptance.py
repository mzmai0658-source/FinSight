"""作品说明：通过本机服务运行固定对话用例，记录全部 SSE 事件。"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
import requests
from eval.conversation_cases import cases as original_cases
from eval.agent_dialogue_cases import cases as dialogue_cases

def cases(split):
    return dialogue_cases(split) if split.startswith('dialogue_') else original_cases(split)
from scripts.smoke_demo_stack import iter_sse

def save(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

def snapshot(*, require_thinking=False):
    from src.agent.sql_tool import SQLTool
    from src.agent.domain import ALLOWED_TABLES
    from src.agent.llm_client import LLMClient
    model=LLMClient()
    if urlparse(model.api_url).hostname not in {'localhost','127.0.0.1','::1'}:
        raise RuntimeError('Local model required')
    if require_thinking and model.reasoning_effort == 'none':
        raise RuntimeError('Thinking must be enabled')
    tables={}
    tool=SQLTool()
    for table in sorted(ALLOWED_TABLES):
        result=tool.run(f'SELECT * FROM {table} ORDER BY stock_code, report_year, report_period LIMIT 500')
        if result['status']!='success':
            raise RuntimeError('Cannot freeze database: '+table)
        if result['row_count']>=500:
            raise RuntimeError('Snapshot cap reached; pagination required')
        tables[table]=result['rows']
    return dict(tables=tables, model=model.describe(), revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--split',choices=['development','holdout','holdout_fresh','holdout_final','dialogue_development','dialogue_holdout'],default='development')
    parser.add_argument('--url',default='http://127.0.0.1:18000')
    parser.add_argument('--env-file',default='.env.integration')
    parser.add_argument('--timeout',type=int,default=240)
    parser.add_argument('--thinking',choices=['on','off'],default='off')
    parser.add_argument('--max-cases',type=int,default=0,help='Pilot only; full acceptance omits this limit')
    parser.add_argument('--pilot-spread',action='store_true',help='Pilot first case from each independent family')
    parser.add_argument('--ids-file',type=Path,help='Run a frozen subset of case IDs, preserving conversation order')
    parser.add_argument('--cases-file',type=Path,help='Explicit frozen cases; bypass built-in split selection')
    parser.add_argument('--snapshot-file',type=Path,help='Shared immutable database snapshot for the suite')
    parser.add_argument('--resume',action='store_true',help='Continue an interrupted run without replacing existing records')
    parser.add_argument('--fail-fast',type=int,default=2,help='Stop after this many consecutive failed turns; 0 disables')
    args=parser.parse_args()
    if urlparse(args.url).hostname not in {'localhost','127.0.0.1','::1'}:
        raise RuntimeError('Local service required')
    load_dotenv(ROOT/args.env_file,override=True)
    os.environ['OLLAMA_REASONING_EFFORT'] = 'none' if args.thinking == 'off' else 'low'
    args.output.mkdir(parents=True,exist_ok=True)
    records=args.output/'records.jsonl'
    if records.exists() and not args.resume:
        raise RuntimeError('Use a new output directory; existing runs are immutable')
    selected=json.loads(args.cases_file.read_text(encoding='utf-8')) if args.cases_file else cases(args.split)
    if args.pilot_spread:
        selected=[item for item in selected if item['category']!='dialog' and item['id'].endswith('-1')]
    if args.ids_file:
        ids=json.loads(args.ids_file.read_text(encoding='utf-8'))
        if len(ids)!=len(set(ids)): raise ValueError('Duplicate IDs')
        selected=[item for item in selected if item['id'] in ids]
        if {item['id'] for item in selected}!=set(ids): raise ValueError('Unknown case ID')
    if args.max_cases: selected=selected[:args.max_cases]
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
         for folder in ['src/agent','src/api','frontend/src','backend-java/src/main/java/com/finsight/chat']
         for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.ts','.vue','.java'}}
    if records.exists():
        if json.loads((args.output/'cases.json').read_text(encoding='utf-8'))!=selected:
            raise RuntimeError('Cases changed; use a new run directory')
        if json.loads((args.output/'source_hashes.json').read_text(encoding='utf-8'))!=hashes:
            raise RuntimeError('Source changed; do not mix versions in a resumed run')
    else:
        save(args.output/'cases.json',selected)
        save(args.output/'snapshot.json',json.loads(args.snapshot_file.read_text(encoding='utf-8')) if args.snapshot_file else snapshot(require_thinking=args.thinking=='on'))
        save(args.output/'source_hashes.json',hashes)
    # 作品说明：开发运行开始前封存留出问题。
    holdout_split=args.split if args.split in {'holdout','holdout_fresh'} else 'holdout_final'
    if not args.cases_file:
        save(args.output/'holdout-frozen.json',cases(holdout_split))
    headers={'X-Internal-Token':os.environ.get('INTERNAL_API_TOKEN','')}
    if not headers['X-Internal-Token']:
        from config.runtime_security import internal_api_token
        headers['X-Internal-Token']=internal_api_token()
    histories={}
    completed=[]
    if records.exists():
        completed=[json.loads(line) for line in records.read_text(encoding='utf-8').splitlines() if line.strip()]
        if [r['case']['id'] for r in completed]!=[c['id'] for c in selected[:len(completed)]]:
            raise RuntimeError('Run is not a contiguous prefix of frozen cases')
        for record in completed:
            result=record.get('result',{})
            history=histories.setdefault(record['case']['group'],[])
            history.extend([{'role':'user','content':record['case']['question']},
                {'role':'assistant','content':(result.get('answer') or {}).get('content',''),
                 'metadata':{k:result[k] for k in ('dialogue_state','response_kind') if k in result}}])
    failed_streak=0
    for index,case in enumerate(selected,1):
        if index<=len(completed): continue
        history=histories.setdefault(case['group'],[])
        record=dict(case=case,history=list(history),events=[],started=time.time())
        started=time.perf_counter()
        try:
            with requests.post(args.url+'/internal/chat/stream',headers=headers,
                 json={'question':case['question'],'history':history,'chart_prefix':case['id']},
                 stream=True,timeout=(10,args.timeout)) as response:
                response.raise_for_status()
                for event,payload in iter_sse(response):
                    record['events'].append({'event':event,'data':payload})
                    if event=='done': record['result']=payload.get('result',{})
            if 'result' not in record: record['error']='missing_done'
        except Exception as exc:
            record['error']=type(exc).__name__+': '+str(exc)
        record['elapsed']=round(time.perf_counter()-started,3)
        result=record.get('result',{})
        answer=(result.get('answer') or {}).get('content','')
        metadata={k:result[k] for k in ('dialogue_state','response_kind') if k in result}
        history.extend([{'role':'user','content':case['question']},{'role':'assistant','content':answer,'metadata':metadata}])
        with records.open('a',encoding='utf-8') as file:
            file.write(json.dumps(record,ensure_ascii=False,default=str)+'\n')
        failed = bool(record.get('error')) or result.get('outcome',{}).get('status')=='query_failed' or (result.get('answer_assessment') or {}).get('accepted') is False
        failed_streak = failed_streak+1 if failed else 0
        reasons = result.get('outcome',{}).get('reason_codes',[])
        progress={'complete':index,'planned':len(selected),'last':case['id'],'failed_streak':failed_streak}
        save(args.output/'progress.json',progress)
        print(f'{index}/{len(selected)} {case["id"]}: {record["elapsed"]}s status={result.get("outcome",{}).get("status")} reasons={reasons} error={record.get("error","")}',flush=True)
        for issue in (result.get('diagnostics') or {}).get('errors',[]):
            print(f"  {issue['stage']}: {issue['code']} {issue['detail']}",flush=True)
        if record.get('error') or 'model_connection_failed' in reasons or (args.fail_fast and failed_streak>=args.fail_fast):
            save(args.output/'halt.json',{**progress,'reason':'transport_failure' if record.get('error') else 'consecutive_failures','reason_codes':reasons})
            print('Stopped early; inspect records.jsonl and diagnostics before continuing.',flush=True)
            return 2
    return 0

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    raise SystemExit(main())
