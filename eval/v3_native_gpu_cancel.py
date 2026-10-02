"""作品说明：观测从 Java 到 Python 的真实取消前后 Ollama 的 GPU 工作状态。"""
import argparse,csv,io,json,os,subprocess,sys,time
from pathlib import Path
import requests
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.v3_native_client import NativeClient
from eval.v3_native_faults import detached,terminal


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='data/runtime/v3/native-gpu-cancel-r1.json');args=parser.parse_args()
    load_dotenv(ROOT/'.env.integration',override=True)
    headers={'X-Internal-Token':os.environ['INTERNAL_API_TOKEN']}
    target=ROOT/args.output;target.parent.mkdir(parents=True,exist_ok=True)
    evidence=dict(samples=[],passed=False,claim='GPU utilization and native task/model slot release; retained model VRAM does not mean ongoing inference')
    import hashlib
    evidence['environment_sha256']=hashlib.sha256((ROOT/'.env.integration').read_bytes()).hexdigest()
    version=requests.get('http://127.0.0.1:11434/api/version',timeout=10);version.raise_for_status()
    evidence['ollama_version']=version.json().get('version')
    agent=requests.get('http://127.0.0.1:18000/internal/agent-version',headers=headers,timeout=10);agent.raise_for_status()
    evidence['agent_version']=agent.json()
    def save():target.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    def sample(stage):
        response=requests.get('http://127.0.0.1:18000/internal/runtime',headers=headers,timeout=10);response.raise_for_status()
        runtime=response.json()
        raw=subprocess.run([r'C:\Windows\system32\nvidia-smi.exe','--query-gpu=timestamp,name,utilization.gpu,memory.used,memory.total','--format=csv,noheader,nounits'],capture_output=True,text=True,check=True,timeout=10).stdout
        gpu=next(csv.reader(io.StringIO(raw)))
        row=dict(stage=stage,time=time.time(),gpu_timestamp=gpu[0].strip(),gpu=gpu[1].strip(),utilization=int(gpu[2]),memory_used_mb=int(gpu[3]),memory_total_mb=int(gpu[4]),model_active=runtime['model']['active'],worker_tasks=runtime['worker_tasks'])
        evidence['samples'].append(row);save();return row
    client=NativeClient(credential=ROOT/'.local_runtime/v3/qa_lifecycle_credentials.json')
    baseline=[sample('baseline') for _ in range(3)]
    if any(row['model_active'] or row['worker_tasks'] for row in baseline):raise RuntimeError('Native system is not idle; no unrelated task was cancelled')
    event,_=detached(client,'请比较同仁堂、万邦德和太极集团最近三个共同年报的归母净利润，并按公司逐年解释变化的原文依据。')
    evidence['task_id']=event['task_id'];save()
    deadline=time.monotonic()+40;busy=[]
    while time.monotonic()<deadline:
        row=sample('before_cancel')
        if row['model_active'] and row['utilization']>=40:busy.append(row)
        if len(busy)>=3:break
        time.sleep(.4)
    evidence['observed_gpu_inference']=len(busy)>=3;save()
    loaded=requests.get('http://127.0.0.1:11434/api/ps',timeout=10);loaded.raise_for_status()
    evidence['loaded_models']=[{key:row.get(key) for key in ('name','digest','size','size_vram')} for row in loaded.json().get('models',[])];save()
    started=time.monotonic();cancel=client.call('POST','/api/chat/tasks/'+event['task_id']+'/cancel')
    evidence['cancel_http_seconds']=round(time.monotonic()-started,3);evidence['cancel_response']=cancel;save()
    stopped=terminal(client,event['task_id']);evidence['terminal']=stopped;save()
    threshold=max(20,max(row['utilization'] for row in baseline)+10)
    deadline=time.monotonic()+30;idle=[]
    while time.monotonic()<deadline:
        row=sample('after_cancel')
        if row['model_active']==0 and row['worker_tasks']==0 and row['utilization']<=threshold:idle.append(row)
        else:idle=[]
        if len(idle)>=5:break
        time.sleep(.5)
    evidence['idle_gpu_threshold']=threshold;evidence['gpu_idle_and_slots_released']=len(idle)>=5;save()
    followup=client.turn('万邦德2024年营业收入是多少？')
    evidence['next_task']=dict(task_id=followup['task_id'],seconds=followup['seconds'],status=followup['task']['status'],saved=followup['task']['saved'],outcome=followup['task'].get('result',{}).get('outcome'))
    repeated=client.call('GET','/api/chat/tasks/'+event['task_id'])
    evidence['cancel_terminal_after_followup']=repeated['status']
    evidence['passed']=bool(evidence['observed_gpu_inference'] and evidence['gpu_idle_and_slots_released'] and stopped['status']=='cancelled' and stopped['saved'] and repeated['status']=='cancelled' and evidence['next_task']['outcome']['status']=='answered')
    save();print(json.dumps({key:evidence[key] for key in ('task_id','observed_gpu_inference','gpu_idle_and_slots_released','cancel_http_seconds','passed')}),flush=True)
    if not evidence['passed']:raise RuntimeError('Actual GPU cancellation evidence incomplete')


if __name__=='__main__':main()
