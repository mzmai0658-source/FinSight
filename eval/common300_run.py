"""常见问题从真实Java入口串行运行，隔离开发集和封存集，保存可复核的每阶段结果。"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

from eval.common300_scoring import attribution, expected_facts, score_case, summarize, validated_visible_context
from eval.real200_acceptance import reference
from eval.v3_native_client import NativeClient

ROOT=Path(__file__).resolve().parents[1]


def allocation(cases,max_sessions=80):
    """每个对话组使用一个真实会话；按会话数分配账号，而不是依赖题目ID。"""
    cohorts={};keys={};counts=Counter()
    for case in cases:
        key=('group',case['group']) if case.get('group') else ('single',case['id'])
        if key not in keys:
            number=len(keys);cohort=number//max_sessions;keys[key]=cohort;counts[cohort]+=1
        cohorts[case['id']]=keys[key]
    assert all(n<=max_sessions for n in counts.values())
    return cohorts,dict(counts)


def frozen_hashes(runtime_root):
    files=[p for p in (runtime_root/'src/agent/v3').glob('*.py')]
    output={'runtime/'+str(p.relative_to(runtime_root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    for path in [ROOT/'eval/common300_run.py',ROOT/'eval/common300_scoring.py',ROOT/'eval/common300_strict.py',ROOT/'eval/real200_acceptance.py',ROOT/'eval/score_v3_regressions.py',ROOT/'eval/v3_native_client.py']:
        output['evaluation/'+path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
    return output


def saved_state(client,uid):
    if not uid:return {},'empty_new_session'
    detail=client.call('GET','/api/chat/sessions/'+uid)
    for message in reversed(detail.get('messages') or []):
        state=(message.get('metadata') or {}).get('dialogue_state')
        if message.get('role')=='assistant' and isinstance(state,dict) and state.get('version')==3:
            return copy.deepcopy(state),'authenticated_saved_session_metadata'
    return {},'no_v3_state_in_saved_session'


def select_cases(suite,phase,ids=''):
    cases=suite['cases']
    if phase=='baseline':
        assert len(cases)==240 and all(c['partition']=='development' for c in cases)
        assert not ids,'Baseline must run the whole development240'
    if phase=='final':
        assert len(cases)==300 and Counter(c['partition'] for c in cases)==Counter(development=240,sealed=60)
        assert not ids,'Final result must run all300 at one frozen version'
    if phase=='adjustment':
        assert len(cases)==240 and all(c['partition']=='development' for c in cases)
        selected=set(ids.split(','));assert ids and selected<={c['id'] for c in cases}
        groups={c.get('group') for c in cases if c['id'] in selected and c.get('group')}
        families={c.get('family') for c in cases if c['id'] in selected and c.get('family')}
        cases=[c for c in cases if c['id'] in selected or c.get('group') in groups or c.get('family') in families]
        assert len(cases)<=60,'One adjustment may run at most60 real turns, including expanded whole groups/families'
    return cases


def register_run(ledger_path,phase,round_,output,source_hashes,case_ids,resume=False):
    ledger=json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else dict(version=1,runs={})
    runs=ledger['runs'];key=phase+str(round_) if phase=='adjustment' else phase
    if key in runs:
        saved=runs[key]
        assert resume and saved['output']==str(output) and saved['source_hashes']==source_hashes and saved['case_ids']==case_ids,'One fixed run per phase/round; cannot replace a failed result with another version'
        return key
    if phase!='baseline':assert runs.get('baseline',{}).get('terminal'),'Finish baseline240 before structural replay phases'
    if phase=='adjustment':
        assert round_ in {1,2} and len(case_ids)<=60
        if round_==2:assert runs.get('adjustment1',{}).get('terminal'),'Adjustment2 follows terminal adjustment1'
        assert 'final' not in runs,'No further tuning after the sealed final run starts'
    if phase=='final':assert all(v.get('terminal') for k,v in runs.items() if k.startswith('adjustment')),'Cannot freeze final while an adjustment remains running'
    runs[key]=dict(output=str(output),source_hashes=source_hashes,case_ids=case_ids,terminal=False,
        classification='full development baseline' if phase=='baseline' else 'bounded seen adjustment' if phase=='adjustment' else 'frozen all300 including sealed60')
    ledger_path.parent.mkdir(parents=True,exist_ok=True)
    ledger_path.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
    return key


def export(folder,rows,expected_total):
    summary=summarize(rows,expected_total)
    durations=[r['run']['seconds'] for r in rows if r.get('run')]
    calls=[((r.get('run') or {}).get('task') or {}).get('result',{}).get('diagnostics',{}).get('model_calls') for r in rows]
    if durations:summary['timing']=dict(mean=statistics.mean(durations),median=statistics.median(durations),max=max(durations))
    summary['model_calls']=dict(Counter(str(n) for n in calls))
    (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 常见问题逐题实际结果','',f'已运行{len(rows)}/{expected_total}；自动通过{summary["automated_pass"]}；叙述待复核{summary["source_review_pending"]}。',
        '本报告不设置85%门槛。正式成绩只来自真实入口；反事实诊断与复测另外保存。','']
    answers=[]
    for row in rows:
        result=((row.get('run') or {}).get('task') or {}).get('result') or {}
        status='待叙述复核' if row['score']['review_status']=='pending' else '通过' if row['score']['passed'] else '未通过'
        lines.extend([f'## {row["case"]["id"]} · {status}','',row['case']['question'],'',
            (result.get('answer') or {}).get('content') or '没有可展示回答。','',
            '失败项：'+('；'.join(row['score']['errors']) or '无自动失败项。'),
            '归因（待复核）：'+row['attribution']['primary']+'；'+row['attribution']['reason'],''])
        answers.append(dict(case=row['case'],score=row['score'],attribution=row['attribution'],
            state_before=row.get('state_before'),state_provenance=row.get('state_provenance'),
            actual_context=row.get('actual_context'),expected_context=row.get('expected_context'),
            seconds=(row.get('run') or {}).get('seconds'),actual={k:result.get(k) for k in
                ('request_contract','answer','facts','derived_facts','chart_data_list','comparisons','outcome','task_results','evidence','query_trace','diagnostics','verification_v3','dialogue_state')}))
    (folder/'RESULTS.md').write_text('\n'.join(lines),encoding='utf-8')
    (folder/'answers.json').write_text(json.dumps(answers,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--suite',required=True);parser.add_argument('--oracle',required=True);parser.add_argument('--audit',required=True)
    parser.add_argument('--output',required=True);parser.add_argument('--runtime-root',required=True)
    parser.add_argument('--base',default='http://127.0.0.1:19110');parser.add_argument('--model',default='deepseek-flash')
    parser.add_argument('--phase',required=True,choices=['baseline','adjustment','final'])
    parser.add_argument('--round',type=int,choices=[1,2]);parser.add_argument('--ids',default='')
    parser.add_argument('--confirm-final',action='store_true');parser.add_argument('--resume',action='store_true')
    parser.add_argument('--ledger',default='.local_runtime/structure300/execution_ledger.json')
    args=parser.parse_args()
    if args.phase=='adjustment':assert args.round in {1,2}
    if args.phase=='final':assert args.confirm_final,'Only launch sealed cases after development ends and final source freezes'
    suite_path=Path(args.suite).resolve();oracle_path=Path(args.oracle).resolve();folder=Path(args.output).resolve()
    assert folder.is_relative_to(ROOT.resolve())
    suite=json.loads(suite_path.read_text(encoding='utf-8'));oracle=json.loads(oracle_path.read_text(encoding='utf-8'))
    assert oracle['suite_sha256']==hashlib.sha256(json.dumps(suite,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    ref=reference(Path(args.audit).resolve());assert suite['source_data_version']==ref['version']==oracle['data_version']
    cases=select_cases(suite,args.phase,args.ids);cohorts,counts=allocation(cases)
    runtime_root=Path(args.runtime_root).resolve();initial=frozen_hashes(runtime_root)
    initial.update(suite=hashlib.sha256(suite_path.read_bytes()).hexdigest(),oracle=hashlib.sha256(oracle_path.read_bytes()).hexdigest())
    ledger_path=Path(args.ledger).resolve();assert ledger_path.is_relative_to(ROOT.resolve())
    ledger_key=register_run(ledger_path,args.phase,args.round,folder,initial,[c['id'] for c in cases],args.resume)
    folder.mkdir(parents=True,exist_ok=True);path=folder/'records.jsonl';records=[]
    if path.exists():
        assert args.resume,'Never overwrite an existing run'
        assert json.loads((folder/'source_hashes.json').read_text(encoding='utf-8'))==initial,'Cannot resume another source/data/scorer version'
        records=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    else:
        (folder/'source_hashes.json').write_text(json.dumps(initial,indent=2),encoding='utf-8')
        (folder/'cases.json').write_text(json.dumps({**suite,'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
        (folder/'run_manifest.json').write_text(json.dumps(dict(phase=args.phase,adjustment_round=args.round,
            cases=len(cases),cohort_sessions=counts,model=args.model,data_version=ref['version'],
            entry='authenticated Java/SSE -> Python Agent -> cloud model/MySQL/PDF/Chroma',
            classification='assistant-authored and reviewed; not third-party testing',
            raw_outputs='captured when present; no schema failure is attributed without available evidence',
            no_accuracy_threshold=True,sealed=args.phase=='final'),ensure_ascii=False,indent=2),encoding='utf-8')
    clients={};uids={};actual={};expected_context={};failures={}
    for row in records:
        case=row['case'];g=case.get('group')
        if not g:continue
        if row.get('run'):
            uids[g]=row['run']['session_uid'];actual[g]=validated_visible_context(row['run'],ref,oracle,actual.get(g,[]))
        expected_context[g]=[{**f,'value_exact':oracle['cards'][f['id']]['value_exact']} for f in expected_facts(case,ref).values()]
        if not row['score']['automated_pass']:failures.setdefault(g,[]).append(case['id'])
    done={r['case']['id'] for r in records}
    for case in cases:
        if case['id'] in done:continue
        now=frozen_hashes(runtime_root)
        assert all(now.get(k)==v for k,v in initial.items() if k not in {'suite','oracle'}),'Frozen runtime/scorer changed; never restart with mixed results'
        assert hashlib.sha256(suite_path.read_bytes()).hexdigest()==initial['suite']
        assert hashlib.sha256(oracle_path.read_bytes()).hexdigest()==initial['oracle']
        group=case.get('group');before={};provenance='empty_new_session';run=None
        try:
            cohort=cohorts[case['id']]
            if cohort not in clients:clients[cohort]=NativeClient(args.base,folder/f'private_credentials_{cohort}.json')
            client=clients[cohort];before,provenance=saved_state(client,uids.get(group))
            run=client.turn(case['question'],uids.get(group) if group else None)
            from eval.common300_strict import strict_score
            verdict=strict_score(case,run,ref,oracle,args.model,actual.get(group,[]),expected_context.get(group,[]))
            row=dict(case=case,run=run,score=verdict,state_before=before,state_provenance=provenance,
                actual_context=actual.get(group,[]),expected_context=expected_context.get(group,[]))
            row['attribution']=attribution(case,run,verdict,before,failures.get(group,[]))
        except Exception as exc:
            verdict=dict(passed=False,automated_pass=False,errors=['test_or_service_'+type(exc).__name__],critical=[],
                review_required=[],review_status='not_required',result_class='execution_failure')
            row=dict(case=case,error=type(exc).__name__,run=run,score=verdict,state_before=before,state_provenance=provenance,
                actual_context=actual.get(group,[]),expected_context=expected_context.get(group,[]),
                attribution=dict(primary='unresolved',reason='Service/evaluator failure; no unsafe driver text recorded',
                    potential_cascade=bool(failures.get(group)),upstream_failures=failures.get(group,[]),counterfactual_status='not_run',stage_evidence={}))
        if group:
            if run:
                uids[group]=run['session_uid'];actual[group]=validated_visible_context(run,ref,oracle,actual.get(group,[]))
            # 概念插话不覆盖预期金融事实；失败后的标准上下文只用于评分归因，绝不注入正式会话。
            fresh=expected_facts(case,ref)
            if fresh:expected_context[group]=[{**f,'value_exact':oracle['cards'][f['id']]['value_exact']} for f in fresh.values()]
            elif case['expect'].get('clear_company') or case['expect']['kind'] in {'clarify','missing'}:expected_context[group]=[]
            if not verdict['automated_pass']:failures.setdefault(group,[]).append(case['id'])
        records.append(row)
        with path.open('a',encoding='utf-8') as stream:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
        summary=export(folder,records,len(cases))
        print(json.dumps(dict(id=case['id'],automatic=verdict['automated_pass'],errors=verdict['errors'],
            primary=row['attribution']['primary'],seconds=(run or {}).get('seconds'),done=len(records),
            expected_total=len(cases),pending_review=summary['source_review_pending']),ensure_ascii=False),flush=True)
    summary=export(folder,records,len(cases))
    ledger=json.loads(ledger_path.read_text(encoding='utf-8'));ledger['runs'][ledger_key].update(terminal=True,
        total=len(records),records_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),complete_run=summary['complete_run'])
    ledger_path.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(terminal=True,total=summary['total'],expected_total=len(cases),complete_run=summary['complete_run'],pending_review=summary['source_review_pending']),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
