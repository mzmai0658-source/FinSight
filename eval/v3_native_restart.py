"""作品说明：通过真实服务重启观察本次创建的任务如何恢复或终结。"""
import argparse,json,os,sys,time
from pathlib import Path
import requests
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.v3_native_client import NativeClient
from eval.v3_native_faults import detached,terminal

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--service',choices=['agent','java'],required=True)
    parser.add_argument('--run',default='r1',choices=['r1','r2','r3','r4']);args=parser.parse_args()
    load_dotenv(ROOT/'.env.integration',override=True)
    root=ROOT/'data/runtime/v3';output=root/f'native-{args.service}-restart-{args.run}.json';ready=root/f'{args.service}-restart-ready-{args.run}.json'
    if ready.exists():raise RuntimeError('Use a new handshake receipt after a previous restart')
    client=NativeClient();rows=[]
    def record(name,passed,**evidence):
        rows.append(dict(name=name,passed=bool(passed),evidence=evidence))
        output.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(name=name,passed=bool(passed)),ensure_ascii=False),flush=True)
        assert passed,name
    event,_=detached(client,'请分别查同仁堂2022、2023、2024年归母净利润，并逐年分析经营原因，给出各年的原文证据。')
    headers={'X-Internal-Token':os.environ['INTERNAL_API_TOKEN']}
    for _ in range(100):
        snapshot=requests.get('http://127.0.0.1:18000/internal/runtime',headers=headers,timeout=10).json()
        if snapshot['model']['active']==1:break
        time.sleep(.1)
    assert snapshot['model']['active']==1
    ready.write_text(json.dumps(dict(task_id=event['task_id'],session_uid=event['session_uid'],service=args.service,model=snapshot['model'])),encoding='utf-8')
    # 作品说明：PowerShell 编排先核对进程号与启动时间，再停止本次启动的服务；不以替代端点模拟重启。
    unavailable=False;url='http://127.0.0.1:18000/api/health' if args.service=='agent' else client.base+'/actuator/health'
    until=time.monotonic()+150
    while time.monotonic()<until:
        try:ok=requests.get(url,timeout=2).status_code==200
        except requests.RequestException:ok=False
        unavailable=unavailable or not ok
        if unavailable and ok:break
        time.sleep(.2)
    record('real_service_was_restarted',unavailable and ok,service=args.service,task_id=event['task_id'])
    result=terminal(client,event['task_id']);payload=result.get('result') or {}
    if args.service=='agent':
        record('orphan_ends_and_saves_explicit_failure',result['status']=='failed' and result['saved'] and
            'worker_restarted' in payload.get('outcome',{}).get('reason_codes',[]) and not payload.get('facts'),
            task_id=event['task_id'],status=result['status'],saved=result['saved'],outcome=payload.get('outcome'))
    else:
        record('java_restart_recovers_same_worker_identity',result['saved'] and result['task_id']==event['task_id'],
            task_id=result['task_id'],status=result['status'],saved=result['saved'])
    next_run=client.turn('同仁堂2024年归母净利润是多少？',event['session_uid'])
    next_result=next_run['task'].get('result') or {}
    record('same_session_is_released_for_new_query',next_run['task']['saved'] and next_result.get('outcome',{}).get('status')=='answered',
        task_id=next_run['task_id'],status=next_run['task']['status'],outcome=next_result.get('outcome'))

if __name__=='__main__':main()
