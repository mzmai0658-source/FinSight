"""旧120保持原题原预期，独立准备和复测；正式300成绩不混入这些已见题。"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from eval.common300_run import allocation,frozen_hashes
from eval.real200_acceptance import prepare,reference,score,summarize
from eval.v3_native_client import NativeClient

ROOT=Path(__file__).resolve().parents[1]


def resolved_contract_adapter(run):
    """兼容新合同有效条件；只复制生产已公布条件，不修改预期或填补结果。"""
    result=copy.deepcopy(run);r=((result.get('task') or {}).get('result') or {}).get('request_contract') or {}
    resolved=r.get('resolved_goal_conditions') or {}
    if not resolved:return result
    assert all(g.get('id') in resolved for g in r.get('goals') or []),'Incomplete resolved contract cannot be silently filled'
    r['modifications']=[]
    for g in r['goals']:
        g['context_conditions']=copy.deepcopy(resolved[g['id']]);g['condition_edits']=[];g['selection']=None
    return result


def build(paths,audit,output):
    output.mkdir(parents=True,exist_ok=False)
    suites=[json.loads(path.read_text(encoding='utf-8')) for path in paths]
    assert len({s['source_data_version'] for s in suites})==1
    cases=[copy.deepcopy(c) for s in suites for c in s['cases']]
    assert len(cases)==120 and len({c['id'] for c in cases})==120
    suite={**suites[0],'name':'Known120 strict regression; not new blind questions',
        'classification':'seen regression; keep original questions and expectations',
        'cases':cases,'target':120,
        'source_suites':{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}}
    path=output/'cases.json';path.write_text(json.dumps(suite,ensure_ascii=False,indent=2),encoding='utf-8')
    ref=reference(audit);assert ref['version']==suite['source_data_version']
    prepare(suite,ref,output/'oracle.json')
    manifest=dict(total=120,classification=suite['classification'],files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()})
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(total=120,files=manifest['files']),ensure_ascii=False))


def main():
    p=argparse.ArgumentParser();p.add_argument('--build',action='store_true');p.add_argument('--sources',nargs=2)
    p.add_argument('--audit',required=True);p.add_argument('--output',required=True);p.add_argument('--suite');p.add_argument('--oracle')
    p.add_argument('--runtime-root');p.add_argument('--base',default='http://127.0.0.1:19110')
    p.add_argument('--model',default='deepseek-flash');p.add_argument('--resume',action='store_true');a=p.parse_args()
    out=Path(a.output).resolve();assert out.is_relative_to(ROOT.resolve())
    if a.build:
        assert a.sources;build([Path(s).resolve() for s in a.sources],Path(a.audit).resolve(),out);return
    assert a.suite and a.oracle and a.runtime_root
    suite=json.loads(Path(a.suite).read_text(encoding='utf-8'));oracle=json.loads(Path(a.oracle).read_text(encoding='utf-8'));ref=reference(Path(a.audit).resolve())
    assert len(suite['cases'])==120 and suite['target']==120
    assert oracle['suite_sha256']==hashlib.sha256(json.dumps(suite,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    runtime=Path(a.runtime_root).resolve();initial=frozen_hashes(runtime);initial['known120_adapter']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    initial.update(suite=hashlib.sha256(Path(a.suite).read_bytes()).hexdigest(),oracle=hashlib.sha256(Path(a.oracle).read_bytes()).hexdigest())
    out.mkdir(parents=True,exist_ok=True);path=out/'records.jsonl'
    if path.exists():
        assert a.resume and json.loads((out/'source_hashes.json').read_text(encoding='utf-8'))==initial
        rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    else:
        rows=[];(out/'source_hashes.json').write_text(json.dumps(initial,indent=2),encoding='utf-8')
        (out/'cases.json').write_text(json.dumps(suite,ensure_ascii=False,indent=2),encoding='utf-8')
    cohorts,_=allocation(suite['cases']);clients={};groups={};prior={}
    for row in rows:
        group=row['case'].get('group')
        if group and row.get('run'):
            groups[group]=row['run']['session_uid']
            from eval.common300_scoring import validated_visible_context
            prior[group]=validated_visible_context(row['run'],ref,oracle,prior.get(group,[]))
    done={r['case']['id'] for r in rows}
    for case in suite['cases']:
        if case['id'] in done:continue
        now=frozen_hashes(runtime);assert all(now.get(k)==v for k,v in initial.items() if k.startswith(('runtime/','evaluation/')))
        try:
            cohort=cohorts[case['id']]
            if cohort not in clients:clients[cohort]=NativeClient(a.base,out/f'private_credentials_{cohort}.json')
            group=case.get('group');run=clients[cohort].turn(case['question'],groups.get(group) if group else None)
            verdict=score(case,resolved_contract_adapter(run),ref,oracle,a.model,prior.get(group))
            row=dict(case=case,run=run,score=verdict,adapter='resolved_goal_conditions only; original result preserved')
            if group:
                groups[group]=run['session_uid']
                from eval.common300_scoring import validated_visible_context
                prior[group]=validated_visible_context(run,ref,oracle,prior.get(group,[]))
        except Exception as exc:
            row=dict(case=case,error=type(exc).__name__,score=dict(passed=False,automated_pass=False,errors=['test_or_service_'+type(exc).__name__],critical=[],review_required=[],review_status='not_required'))
        rows.append(row)
        with path.open('a',encoding='utf-8') as stream:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
        summary=summarize(rows,120,120);(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(id=case['id'],automatic=row['score']['automated_pass'],errors=row['score']['errors'],done=len(rows)),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
