"""作品说明：真实财报隔离副本的取消、恢复、隔离和受限数据库故障验收。"""
from __future__ import annotations
import argparse, csv, io, json, os, subprocess, sys, time, uuid
from pathlib import Path
import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.v3_native_client import NativeClient
from eval.v3_native_faults import detached, terminal
from config.db_config import get_db_config, get_readonly_db_config


def run(args):
    load_dotenv(ROOT / args.env, override=True)
    config = get_db_config()
    if config.host not in {'localhost', '127.0.0.1'} or config.database != 'finsight_demo_real200_20261002':
        raise ValueError('Fault acceptance is restricted to the explicitly named localhost QA database')
    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    client = NativeClient(args.base, ROOT / 'data/runtime/qa200/lifecycle/private_credentials.json')
    rows = []
    def record(name, passed, **evidence):
        rows.append(dict(name=name, passed=bool(passed), evidence=evidence))
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), 'utf-8')
        print(json.dumps(dict(name=name, passed=bool(passed)), ensure_ascii=False), flush=True)
        if not passed: raise AssertionError(name)
    headers = {'X-Internal-Token': (ROOT/'data/runtime/internal-api-token').read_text().strip()}
    def runtime():
        r = requests.get(args.agent + '/internal/runtime', headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()
    def gpu_sample(stage):
        raw = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10, check=True).stdout
        values = next(csv.reader(io.StringIO(raw)))
        current = runtime()
        return dict(stage=stage, seconds=time.time(), utilization=int(values[0]), memory_mb=int(values[1]), active=current['model']['active'], workers=current['worker_tasks'])
    before = runtime()
    if before['model']['active'] or before['worker_tasks']:
        raise RuntimeError('Acceptance starts only when this isolated app is idle')
    baseline = gpu_sample('baseline')
    event, _ = detached(client, '比较万邦德、同仁堂2022、2023、2024年归母净利润，逐年解释变化原因并给出原文。')
    busy = []
    until = time.monotonic() + 35
    while time.monotonic() < until:
        sample = gpu_sample('inference')
        if sample['active'] and sample['utilization'] >= 40: busy.append(sample)
        if len(busy) >= 2: break
        time.sleep(.3)
    second = client.http.post(client.base+'/api/chat/stream', json=dict(sessionUid=event['session_uid'], question='查询万邦德收入', clientRequestId=uuid.uuid4().hex), timeout=12)
    record('same_session_concurrency', second.status_code == 409, http=second.status_code)
    deletion = client.http.delete(client.base+'/api/chat/sessions/'+event['session_uid'], timeout=12)
    record('active_session_delete_guard', deletion.status_code == 409, http=deletion.status_code)
    started = time.monotonic()
    client.call('POST', '/api/chat/tasks/'+event['task_id']+'/cancel')
    cancelled = terminal(client, event['task_id'])
    latency = round(time.monotonic()-started, 3)
    idle = []; samples = []
    until = time.monotonic() + 30
    threshold = max(20, baseline['utilization']+10)
    while time.monotonic() < until:
        sample = gpu_sample('after_cancel'); samples.append(sample)
        if sample['active'] == 0 and sample['workers'] == 0 and sample['utilization'] <= threshold: idle.append(sample)
        else: idle = []
        if len(idle) >= 5: break
        time.sleep(.5)
    record('real_ollama_cancel_gpu_and_slots', len(busy)>=2 and len(idle)>=5 and cancelled['status']=='cancelled' and cancelled['saved'], baseline=baseline, busy=busy, samples=samples, cancel_seconds=latency, task_id=event['task_id'])
    cancelled_result=cancelled.get('result') or {}
    record('cancelled_no_financial_publication',not cancelled_result.get('facts') and not cancelled_result.get('derived_facts') and not cancelled_result.get('chart_data_list'),outcome=cancelled_result.get('outcome'),fact_count=len(cancelled_result.get('facts') or []))
    # 作品说明：真正关闭订阅连接，后台任务照常完成，再按固定任务ID恢复。
    next_run = client.turn('万邦德2024年营业收入是多少？用万元。', disconnect=True)
    result = next_run['task'].get('result') or {}
    record('disconnect_recovers_saved_task', next_run['task']['saved'] and result.get('outcome',{}).get('status')=='answered', task_id=next_run['task_id'], seconds=next_run['seconds'])
    repeat = client.turn(next_run['question'], next_run['session_uid'], next_run['client_request_id'])
    detail = client.call('GET', '/api/chat/sessions/'+next_run['session_uid'])
    record('duplicate_submission_one_saved_turn', repeat['task_id']==next_run['task_id'] and len(detail['messages'])==2, task_id=repeat['task_id'], messages=len(detail['messages']))
    post_complete = client.call('POST', '/api/chat/tasks/'+next_run['task_id']+'/cancel')
    unchanged = client.call('GET', '/api/chat/tasks/'+next_run['task_id'])
    still_cancelled = client.call('GET', '/api/chat/tasks/'+event['task_id'])
    record('terminal_status_not_overwritten', unchanged['status']=='completed' and still_cancelled['status']=='cancelled', completed=unchanged['status'], cancelled=still_cancelled['status'])
    other = NativeClient(args.base, ROOT/'data/runtime/qa200/lifecycle/private_other_credentials.json')
    denials = []
    for method, path in [('GET','/api/chat/tasks/'+next_run['task_id']),('POST','/api/chat/tasks/'+next_run['task_id']+'/cancel'),('GET','/api/chat/sessions/'+next_run['session_uid'])]:
        r = other.http.request(method, other.base+path, timeout=10)
        denials.append(dict(method=method, http=r.status_code))
    record('native_user_isolation', all(r['http'] in {403,404} for r in denials), responses=denials)
    invalid=[]
    for value in [' \n\t\u3000', 'a'*2001, '\U00010000'*2001]:
        r=client.http.post(client.base+'/api/chat/stream',json=dict(question=value,clientRequestId=uuid.uuid4().hex),timeout=12)
        invalid.append(dict(codepoints=len(value),http=r.status_code))
    record('blank_and_2000_codepoint_boundary',all(r['http']==400 for r in invalid),responses=invalid)
    session=client.call('POST','/api/chat/sessions')
    engine=create_engine(config.connection_string)
    with engine.connect() as conn:
        sid=conn.execute(text('SELECT id FROM chat_session WHERE session_uid=:uid'),dict(uid=session['sessionUid'])).scalar_one()
    trigger='delivery_save_'+uuid.uuid4().hex[:12]
    try:
        with engine.begin() as conn:
            conn.execute(text(f"CREATE TRIGGER {trigger} BEFORE INSERT ON chat_message FOR EACH ROW BEGIN IF NEW.session_id={int(sid)} AND NEW.role='assistant' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='delivery_save_failure'; END IF; END"))
        failure=client.turn('万邦德2024年营业收入是多少？',session['sessionUid'])
        detail=client.call('GET','/api/chat/sessions/'+session['sessionUid'])
        record('native_save_failure_rollback', failure['task']['status']=='failed' and not failure['task']['saved'] and not detail['messages'],status=failure['task']['status'],saved=failure['task']['saved'],messages=len(detail['messages']))
    finally:
        with engine.begin() as conn:conn.execute(text(f'DROP TRIGGER IF EXISTS {trigger}'))
    ro=create_engine(get_readonly_db_config().connection_string)
    with ro.connect() as conn:grants=[str(row[0]).upper() for row in conn.execute(text('SHOW GRANTS'))]
    record('native_reader_no_write_or_admin_grants',all(not any(word in grant for word in ('ALL PRIVILEGES','INSERT','UPDATE','DELETE','CREATE','DROP','ALTER','GRANT OPTION')) for grant in grants),grant_count=len(grants))
    # 作品说明：仅暂时撤销本次隔离库的事实表读取授权，验证真实SQL失败不伪装为无数据。
    import re, pymysql
    reader=os.environ['SQL_DB_USER']
    if not re.fullmatch(r'fs_demo_r_[a-zA-Z0-9_]+',reader):raise ValueError('Only the dedicated demo reader may be used for fault injection')
    admin=pymysql.connect(host=config.host,port=config.port,user=config.user,password=config.password,database=config.database,autocommit=True)
    try:
        with admin.cursor() as cursor:cursor.execute(f'REVOKE SELECT ON `{config.database}`.`financial_canonical_facts` FROM %s@%s',(reader,'localhost'))
        failed=client.turn('万邦德2024年合并营业收入是多少？用亿元，保留两位小数。')
        payload=failed['task'].get('result') or {}
        outcome=payload.get('outcome') or {}
        record('native_sql_permission_failure_no_fake_data',not payload.get('facts') and outcome.get('status') in {'query_failed','failed'},status=failed['task']['status'],outcome=outcome,answer=(payload.get('answer') or {}).get('content'))
    finally:
        with admin.cursor() as cursor:cursor.execute(f'GRANT SELECT ON `{config.database}`.`financial_canonical_facts` TO %s@%s',(reader,'localhost'))
        admin.close()
    engine.dispose();ro.dispose()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env',default='.env.qa200');p.add_argument('--base',default='http://127.0.0.1:19100');p.add_argument('--agent',default='http://127.0.0.1:19020')
    p.add_argument('--output',default='data/runtime/qa200/lifecycle/checks.json');run(p.parse_args())
