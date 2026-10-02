"""作品说明：使用真实服务验证归属校验、取消、幂等与 MySQL 保存失败。"""
from __future__ import annotations
import argparse,json,os,sys,time,uuid
from pathlib import Path
import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine,text
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.v3_native_client import NativeClient
from config.db_config import get_db_config,get_readonly_db_config

def detached(client,question,uid=None,client_id=None):
    body=dict(question=question,clientRequestId=client_id or uuid.uuid4().hex)
    if uid:body['sessionUid']=uid
    response=client.http.post(client.base+'/api/chat/stream',json=body,stream=True,timeout=20)
    response.raise_for_status();response.encoding='utf-8';kind='';payload=[]
    for line in response.iter_lines(decode_unicode=True,chunk_size=1):
        if line.startswith('event:'):kind=line[6:].strip()
        elif line.startswith('data:'):payload.append(line[5:].strip())
        elif not line and payload:
            value=json.loads('\n'.join(payload));payload=[]
            if kind=='session':response.close();return value,body
    raise RuntimeError('No session event')

def terminal(client,task_id):
    for _ in range(280):
        status=client.call('GET','/api/chat/tasks/'+task_id)
        if status['status'] in {'failed','completed','cancelled'}:return status
        time.sleep(1)
    raise RuntimeError('Task did not reach a real terminal state')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--env',default='.env.integration');parser.add_argument('--base',default='http://127.0.0.1:18080');parser.add_argument('--agent',default='http://127.0.0.1:18000');parser.add_argument('--output',default='data/runtime/v3/native-faults.json');args=parser.parse_args()
    load_dotenv(ROOT/args.env,override=True)
    client=NativeClient(args.base);records=[]
    target=ROOT/args.output;target.parent.mkdir(parents=True,exist_ok=True)
    def record(name,evidence,passed):
        records.append(dict(name=name,passed=passed,evidence=evidence));target.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(name=name,passed=passed),ensure_ascii=False),flush=True)
        assert passed,name
    internal={'X-Internal-Token':os.environ['INTERNAL_API_TOKEN']}
    def runtime():
        response=requests.get(args.agent+'/internal/runtime',headers=internal,timeout=10);response.raise_for_status();return response.json()
    event,body=detached(client,'请逐项解释同仁堂近三年归母净利润变化的原因，给出每年的原文依据。')
    time.sleep(4);before=runtime()
    second=client.http.post(client.base+'/api/chat/stream',json={'sessionUid':event['session_uid'],'question':'查万邦德收入','clientRequestId':uuid.uuid4().hex},timeout=12)
    record('same_session_concurrency',dict(http=second.status_code,task_id=event['task_id']),second.status_code==409)
    deletion=client.http.delete(client.base+'/api/chat/sessions/'+event['session_uid'],timeout=12)
    record('active_session_delete_guard',dict(http=deletion.status_code),deletion.status_code==409)
    client.call('POST','/api/chat/tasks/'+event['task_id']+'/cancel');cancelled=terminal(client,event['task_id'])
    for _ in range(30):
        after=runtime()
        if after['model']['active']==0 and after['worker_tasks']==0:break
        time.sleep(.2)
    next_task=client.turn('万邦德2024年营业收入是多少？')
    record('real_ollama_cancel_and_next_task',dict(before=before,after=after,cancelled=cancelled,next_task=next_task),
        cancelled['status']=='cancelled' and cancelled['saved'] and after['model']['active']==0 and after['worker_tasks']==0 and
        (next_task['task'].get('result') or {}).get('outcome',{}).get('status')=='answered')
    repeat=client.turn(next_task['question'],next_task['session_uid'],next_task['client_request_id'])
    detail=client.call('GET','/api/chat/sessions/'+next_task['session_uid'])
    record('duplicate_submit_one_saved_turn',dict(task_id=repeat['task_id'],message_count=len(detail['messages'])),repeat['task_id']==next_task['task_id'] and len(detail['messages'])==2)
    other=NativeClient(args.base,ROOT/'.local_runtime/v3/qa_other_credentials.json')
    denial=[]
    for method,path in [('GET','/api/chat/tasks/'+next_task['task_id']),('POST','/api/chat/tasks/'+next_task['task_id']+'/cancel'),('GET','/api/chat/sessions/'+next_task['session_uid'])]:
        response=other.http.request(method,other.base+path,timeout=10);denial.append(dict(method=method,http=response.status_code))
    record('real_user_isolation',denial,all(row['http'] in {403,404} for row in denial))
    bad=[]
    for value in [' \n\t\u3000','a'*2001,'\U00010000'*2001]:
        response=client.http.post(client.base+'/api/chat/stream',json=dict(question=value,clientRequestId=uuid.uuid4().hex),timeout=12)
        bad.append(dict(codepoints=len(value),http=response.status_code))
    record('input_blank_and_limit',bad,all(row['http']==400 for row in bad))
    exact,body=detached(client,'\U00010000'*2000)
    client.call('POST','/api/chat/tasks/'+exact['task_id']+'/cancel');limit=terminal(client,exact['task_id'])
    record('input_2000_unicode_codepoints',dict(task_id=limit['task_id'],status=limit['status']),limit['status']=='cancelled')
    # 作品说明：故障触发器只作用于本次新建的验收会话，不影响其他用户。
    session=client.call('POST','/api/chat/sessions');engine=create_engine(get_db_config().connection_string)
    with engine.connect() as conn:
        sid=conn.execute(text('SELECT id FROM chat_session WHERE session_uid=:uid'),{'uid':session['sessionUid']}).scalar_one()
    trigger='v3_qa_save_'+uuid.uuid4().hex[:12]
    try:
        with engine.begin() as conn:
            conn.execute(text(f"CREATE TRIGGER {trigger} BEFORE INSERT ON chat_message FOR EACH ROW BEGIN IF NEW.session_id={int(sid)} AND NEW.role='assistant' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='v3_qa_save_failure'; END IF; END"))
        failure=client.turn('同仁堂2024年营业收入是多少？',session['sessionUid'])
        detail=client.call('GET','/api/chat/sessions/'+session['sessionUid'])
        record('real_mysql_save_failure_and_rollback',dict(run=failure,message_count=len(detail['messages'])),failure['task']['status']=='failed' and not failure['task']['saved'] and not detail['messages'])
    finally:
        with engine.begin() as conn:conn.execute(text(f'DROP TRIGGER IF EXISTS {trigger}'))
    ro=create_engine(get_readonly_db_config().connection_string)
    with ro.connect() as conn:
        grants=[str(row[0]).upper() for row in conn.execute(text('SHOW GRANTS'))]
    record('native_readonly_privileges',dict(grant_count=len(grants),readonly=True),all(not any(word in grant for word in ('ALL PRIVILEGES','INSERT','UPDATE','DELETE','CREATE','DROP','ALTER','GRANT OPTION')) for grant in grants))

if __name__=='__main__':main()
