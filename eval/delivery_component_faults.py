"""作品说明：真实SQL故障与独立原生Python服务的模型/数据库连接故障；不使用替代服务。"""
from __future__ import annotations
import argparse, json, os, re, socket, subprocess, sys, time, uuid
from pathlib import Path
import pymysql, requests
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from config.db_config import get_db_config
from eval.v3_native_client import NativeClient


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--base',default='http://127.0.0.1:19080');args=parser.parse_args()
    load_dotenv(ROOT/'.env.demo',override=True)
    cfg=get_db_config()
    if cfg.host not in {'localhost','127.0.0.1'} or not cfg.database.startswith('finsight_demo_'):raise ValueError('Only the isolated synthetic database is permitted')
    out=ROOT/'data/runtime/delivery/component-faults.json';out.parent.mkdir(parents=True,exist_ok=True);rows=[]
    def record(name,passed,**evidence):
        rows.append(dict(name=name,passed=bool(passed),evidence=evidence));out.write_text(json.dumps(rows,ensure_ascii=False,indent=2),'utf-8');print(json.dumps(dict(name=name,passed=bool(passed))),flush=True)
        if not passed:raise AssertionError(name)
    client=NativeClient(args.base,ROOT/'.local_runtime/delivery/qa_credentials.json')
    reader=os.environ['SQL_DB_USER']
    if not re.fullmatch(r'fs_demo_r_[a-zA-Z0-9_]+',reader):raise ValueError('Not a dedicated demo reader')
    admin=pymysql.connect(host=cfg.host,port=cfg.port,user=cfg.user,password=cfg.password,database=cfg.database,autocommit=True)
    try:
        with admin.cursor() as cur:cur.execute(f'REVOKE SELECT ON `{cfg.database}`.`financial_canonical_facts` FROM %s@%s',(reader,'localhost'))
        failed=client.turn('星河医药2024年合并营业收入是多少？用亿元，保留两位小数。')
        payload=failed['task'].get('result') or {};outcome=payload.get('outcome') or {}
        record('java_sse_native_sql_failure',not payload.get('facts') and outcome.get('status') in {'query_failed','failed'},status=failed['task']['status'],outcome=outcome,answer=(payload.get('answer') or {}).get('content'))
    finally:
        with admin.cursor() as cur:cur.execute(f'GRANT SELECT ON `{cfg.database}`.`financial_canonical_facts` TO %s@%s',(reader,'localhost'))
        admin.close()
    # 作品说明：新端口、独立任务库启动实际Uvicorn组件，只修改该子进程连接地址；共享服务不中断。
    headers={'X-Internal-Token':os.environ['INTERNAL_API_TOKEN']}
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',19001))==0:raise RuntimeError('Fault service port already occupied')
    for mode in ('model_unreachable','database_unreachable'):
        env=os.environ.copy();env['FINSIGHT_TASK_DB']=f'.local_runtime/delivery/fault-{mode}-{uuid.uuid4().hex}.sqlite3'
        if mode=='model_unreachable':
            env['OLLAMA_BASE_URL']='http://127.0.0.1:19/v1';env['LLM_BASE_URL']='http://127.0.0.1:19/v1'
        else:env['DB_PORT']='19';env['MYSQL_PORT']='19'
        log=ROOT/'.local_runtime/delivery'/f'{mode}.log'
        with log.open('w',encoding='utf-8') as stream:
            process=subprocess.Popen([sys.executable,'-X','utf8','-m','uvicorn','src.api.main:app','--host','127.0.0.1','--port','19001'],cwd=ROOT,env=env,stdout=stream,stderr=stream,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                deadline=time.monotonic()+45;health=None
                while time.monotonic()<deadline:
                    if process.poll() is not None:raise RuntimeError('Fault service exited')
                    try:
                        response=requests.get('http://127.0.0.1:19001/api/health',timeout=10)
                        if response.status_code==200:health=response.json();break
                    except requests.RequestException:pass
                    time.sleep(.3)
                if health is None:raise TimeoutError('Native fault component did not start')
                if mode=='database_unreachable':
                    record('native_python_database_connection_unreachable',not health['database']['ok'],component='Real Uvicorn/MySQL protocol with wrong child-process port; not Java/SSE end-to-end')
                    continue
                response=requests.post('http://127.0.0.1:19001/internal/chat/stream',headers=headers,json=dict(question='星河医药2024年合并营业收入是多少？用亿元，保留两位小数。',task_id=str(uuid.uuid4()),session_uid=str(uuid.uuid4()),client_request_id=uuid.uuid4().hex),timeout=100)
                payloads=[]
                for block in response.text.replace('\r\n','\n').split('\n\n'):
                    lines=[line[5:].strip() for line in block.splitlines() if line.startswith('data:')]
                    if lines:
                        try:payloads.append(json.loads('\n'.join(lines)))
                        except json.JSONDecodeError:pass
                done=next((p for p in reversed(payloads) if 'result' in p),{}).get('result',{})
                record('native_python_model_connection_unreachable',response.status_code==200 and not done.get('facts') and done.get('outcome',{}).get('status')=='failed',component='Real Uvicorn and Ollama client with unreachable child-process endpoint; not Java/SSE end-to-end',outcome=done.get('outcome'),answer=(done.get('answer') or {}).get('content'))
            finally:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
    print('Native component faults completed; temporary SQL grant restored',flush=True)

if __name__=='__main__':main()
