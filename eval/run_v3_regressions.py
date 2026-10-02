"""作品说明：通过真实服务重放历史问题，禁止悄悄改走旧执行链路。"""
from __future__ import annotations
import argparse,hashlib,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.v3_native_client import NativeClient


def production_hashes(root=ROOT):
    """作品说明：固定生产层代码与实际联调配置，仅记录摘要，不将环境值或凭据写入回归记录。"""
    paths=set()
    for folder,extensions in (('src',('.py',)),('config',('.py',)),('backend-java/src',('.java','.yml','.yaml','.properties')),
                              ('frontend/src',('.vue','.ts','.js','.css','.json'))):
        paths.update(p for p in (root/folder).rglob('*') if p.is_file() and p.suffix in extensions)
    for name in ('.env.integration','requirements.txt','pyproject.toml','backend-java/pom.xml',
                 'frontend/package.json','frontend/package-lock.json'):
        if (root/name).is_file():paths.add(root/name)
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}

def load():
    folder=ROOT/'eval/regression_v3'
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    result=[]
    for name,digest in manifest['artifacts'].items():
        path=folder/name
        assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
        result.extend(json.loads(line) for line in path.read_text(encoding='utf-8').splitlines())
    assert len(result)==153
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:18080');p.add_argument('--batch',default='expanded');p.add_argument('--ids',default='');p.add_argument('--output',default='data/runtime/v3/regression');p.add_argument('--limit',type=int,default=0)
    args=p.parse_args();cases=load()
    cases=[row for row in cases if args.batch=='all' or args.batch=='later' and row['batch']!='initial' or row['batch']==args.batch]
    if args.ids:
        wanted=set(args.ids.split(','))
        known={identifier for row in cases for identifier in (row['id'],row['case']['id'])}
        if missing:=wanted-known:raise ValueError('Unknown regression IDs: '+','.join(sorted(missing)))
        cases=[row for row in cases if row['case']['id'] in wanted or row['id'] in wanted]
    if args.limit:cases=cases[:args.limit]
    if not cases:raise ValueError('No regression cases selected')
    folder=ROOT/args.output;folder.mkdir(parents=True,exist_ok=True)
    (folder/'runner.pid').write_text(str(os.getpid()),encoding='ascii')
    client=NativeClient(args.base);groups={}
    source_hashes=production_hashes()
    (folder/'source_hashes.json').write_text(json.dumps(source_hashes,indent=2),encoding='utf-8')
    target=folder/'records.jsonl';finished=set()
    if target.exists():
        for line in target.read_text(encoding='utf-8').splitlines():
            old=json.loads(line);finished.add(old['id']);groups[old['group']]=old['run']['session_uid']
    for index,row in enumerate(cases,1):
        if row['id'] in finished:continue
        current=production_hashes()
        if current != source_hashes:
            raise RuntimeError('Production source changed during regression; start a separately versioned run')
        if (folder/'pause').exists():
            print('Paused at a turn boundary; completed records retained.',flush=True)
            break
        case=row['case'];group=row['batch']+'/'+case.get('group',row['id'])
        # 作品说明：通过新链路重建历史追问的用户条件，旧回答中的数值不能直接当作可信事实。
        if group not in groups and row['history']:
            for prior in row['history']:
                if prior.get('role')=='user':
                    prep=client.turn(prior['content'],groups.get(group));groups[group]=prep['session_uid']
                    with (folder/'context_replay.jsonl').open('a',encoding='utf-8') as output:
                        output.write(json.dumps(dict(for_case=row['id'],run=prep),ensure_ascii=False)+'\n')
        run=client.turn(case['question'],groups.get(group));groups[group]=run['session_uid']
        record=dict(id=row['id'],group=group,case=case,run=run,expectation_status='awaiting_independent_assertions_and_review')
        with target.open('a',encoding='utf-8') as output:output.write(json.dumps(record,ensure_ascii=False)+'\n')
        result=run['task'].get('result') or {}
        print(json.dumps(dict(progress=f'{index}/{len(cases)}',id=row['id'],seconds=run['seconds'],status=run['task']['status'],outcome=result.get('outcome')),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
