"""作品说明：真实Java取消与完成交界的观测；不把少量时序实测当成所有竞争证明。"""
import json, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.v3_native_client import NativeClient
from eval.v3_native_faults import detached,terminal

def main():
    client=NativeClient('http://127.0.0.1:19080',ROOT/'.local_runtime/delivery/qa_race_credentials.json')
    output=ROOT/'data/runtime/delivery/terminal-race.json';rows=[]
    for delay in (0,14,20):
        event,_=detached(client,'星河医药2024年合并营业收入是多少？用亿元，保留两位小数。')
        time.sleep(delay)
        response=client.call('POST','/api/chat/tasks/'+event['task_id']+'/cancel')
        final=terminal(client,event['task_id']);time.sleep(2)
        late=client.call('GET','/api/chat/tasks/'+event['task_id'])
        detail=client.call('GET','/api/chat/sessions/'+event['session_uid'])
        facts=(late.get('result') or {}).get('facts') or []
        passed=final['status']==late['status'] and late['saved'] and len(detail['messages'])==2 and final['status'] in {'completed','cancelled'}
        if final['status']=='cancelled':passed=passed and not facts and not (late.get('result') or {}).get('chart_data_list')
        else:passed=passed and len(facts)==1 and facts[0]['value_exact']=='150000000.00'
        rows.append(dict(delay_seconds=delay,task_id=event['task_id'],status=final['status'],later_status=late['status'],saved=late['saved'],messages=len(detail['messages']),passed=bool(passed)))
        output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(rows,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps(rows[-1]),flush=True)
        if not passed:raise AssertionError('Native terminal race check failed')

if __name__=='__main__':main()
