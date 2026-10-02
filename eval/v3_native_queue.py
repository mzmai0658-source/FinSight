"""作品说明：通过验收账号填充真实任务队列，结束时取消尚未完成的任务。"""
import argparse,json,sys,time,os,uuid
from pathlib import Path
import requests
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.v3_native_client import NativeClient
from eval.v3_native_faults import detached,terminal

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='data/runtime/v3/native-queue-r3.json');args=parser.parse_args()
    load_dotenv(ROOT/'.env.integration',override=True)
    # 作品说明：仅复用本次验收创建的账号；单账号每分钟六次的限制不足以在三十秒内填满全局八个排队位。
    paths=[ROOT/'.local_runtime/v3'/name for name in ('qa_credentials.json','qa_other_credentials.json',
        'lifecycle_credentials.json')]
    if not all(path.exists() for path in paths):raise RuntimeError('Owned QA credentials are required')
    pool=[NativeClient(credential=path) for path in paths]
    clients=[pool[i%len(pool)] for i in range(10)]
    owned=[];records=[];output=ROOT/args.output
    def record(name,passed,**evidence):
        records.append(dict(name=name,passed=bool(passed),evidence=evidence))
        output.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(name=name,passed=bool(passed)),ensure_ascii=False),flush=True)
        assert passed,name
    def runtime():
        response=requests.get('http://127.0.0.1:18000/internal/runtime',headers={'X-Internal-Token':os.environ['INTERNAL_API_TOKEN']},timeout=10)
        response.raise_for_status();return response.json()
    try:
        first,_=detached(clients[0],'请逐项查同仁堂2022、2023、2024年归母净利润，并分别分析每年同比变化的经营原因，逐条给出对应报告原文。')
        owned.append((clients[0],first))
        for _ in range(100):
            if runtime()['model']['active']==1:break
            time.sleep(.1)
        for client in clients[1:9]:
            turn,_=detached(client,'同仁堂2024年归母净利润是多少？',client_id=uuid.uuid4().hex)
            owned.append((client,turn))
        # 作品说明：Java 会话事件先于异步工作线程进入执行队列。
        for _ in range(50):
            snapshot=runtime()
            if snapshot['worker_tasks']==9:break
            time.sleep(.1)
        statuses=[client.call('GET','/api/chat/tasks/'+turn['task_id']) for client,turn in owned]
        record('real_one_active_eight_waiting',snapshot['worker_tasks']==9 and sum(s['status']=='queued' for s in statuses)==8,
            runtime=snapshot,tasks=[dict(task_id=s['task_id'],status=s['status']) for s in statuses])
        extra,_=detached(clients[9],'万邦德2024年营收是多少？',client_id=uuid.uuid4().hex);owned.append((clients[9],extra))
        overflow=terminal(clients[9],extra['task_id']);result=overflow.get('result') or {}
        message=(result.get('answer') or {}).get('content','')
        record('native_queue_full_is_explicit',overflow['status']=='failed' and any(s in message for s in ('忙碌','队列','排队')),
            task_id=extra['task_id'],status=overflow['status'],message=message)
        timeout=terminal(clients[1],owned[1][1]['task_id'])
        message=((timeout.get('result') or {}).get('answer') or {}).get('content','')
        record('native_thirty_second_queue_timeout',timeout['status']=='failed' and any(s in message for s in ('排队','30秒')),
            task_id=timeout['task_id'],status=timeout['status'],message=message)
    finally:
        for client,turn in owned:
            status=client.call('GET','/api/chat/tasks/'+turn['task_id'])
            if status['status'] in {'queued','running','cancelling'}:
                client.call('POST','/api/chat/tasks/'+turn['task_id']+'/cancel');terminal(client,turn['task_id'])
        snapshot=runtime()
        records.append(dict(name='owned_queue_cleanup',passed=snapshot['worker_tasks']==0,evidence=snapshot))
        output.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
