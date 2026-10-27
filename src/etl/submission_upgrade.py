"""新公司整链路验收入口；私有配置、任务和首次结果均放在隔离工作区。"""
from __future__ import annotations

import argparse
from collections import Counter
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
PROFILE='cloud-upload120-v1'
DATABASE='finsight_upload120_20261021'


def _write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.writing')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(temporary,path)


def configure(root=ROOT,source_env=None,*,prompts=True,provider_environment=None,embedding_endpoint=None):
    from dotenv import dotenv_values
    root=Path(root).resolve();path=root/'.env.upload120'
    if path.exists():
        values=dict(dotenv_values(path))
        if not prompts and provider_environment is None and embedding_endpoint is None:return path
    else:
        source=dict(dotenv_values(source_env)) if source_env and Path(source_env).is_file() else {}
        allowed=('DB_HOST','DB_PORT','DB_USER','DB_PASSWORD','PADDLEOCR_API_TOKEN','OCR_API_TOKEN','OCR_API_URL',
                 'LLM_API_KEY','DEEPSEEK_API_KEY','LLM_BASE_URL','DASHSCOPE_API_KEY','DASHSCOPE_HTTP_BASE_URL',
                 'DASHSCOPE_EMBEDDING_ENDPOINT','REDIS_EXECUTABLE','ERLANG_HOME','RABBITMQ_SERVER_EXECUTABLE')
        values={key:source[key] for key in allowed if source.get(key)}
        values.update(FINSIGHT_UPGRADE_PROFILE=PROFILE,DB_NAME=DATABASE,SQL_DB_NAME=DATABASE,
            MYSQL_DATABASE=DATABASE,DB_HOST=values.get('DB_HOST','127.0.0.1'),DB_PORT=values.get('DB_PORT','3306'),
            DB_USER=values.get('DB_USER','root'),SQL_DB_USER='fs_up120_20261021',SQL_DB_PASSWORD=secrets.token_urlsafe(30),
            LLM_PROVIDER='deepseek',LLM_MODEL='deepseek-flash',LLM_TEMPERATURE='0',LLM_MAX_TOKENS='3072',
            LLM_TIMEOUT_SECONDS='60',LLM_MAX_RETRIES='0',LLM_THINKING='false',
            EMBEDDING_PROVIDER='dashscope',EMBEDDING_MODEL='text-embedding-v4',EMBEDDING_DIMENSION='1024',
            DASHSCOPE_MAX_RETRIES='0',DASHSCOPE_TIMEOUT_SECONDS='30',CHROMA_DB_PATH='data/chroma_cloud_v4_1024',
            OCR_MODEL='PaddleOCR-VL-1.6',OCR_API_PROTOCOL='paddle_async',OCR_DAILY_PAGE_LIMIT='20000',OCR_CACHE_ALLOW_LEGACY='false',
            FINANCIAL_SUBMISSION_DB='data/runtime/submissions/state.sqlite3',FINSIGHT_TASK_DB='data/runtime/submissions/tasks.sqlite3',
            API_HOST='127.0.0.1',API_PORT='8120',SERVER_PORT='18280',AGENT_BASE_URL='http://127.0.0.1:8120',
            REDIS_HOST='127.0.0.1',REDIS_PORT='6391',REDIS_DATABASE='0',REDIS_PASSWORD=secrets.token_urlsafe(24),
            RABBITMQ_HOST='127.0.0.1',RABBITMQ_PORT='5673',RABBITMQ_VHOST='/',RABBITMQ_USERNAME='upload120',RABBITMQ_PASSWORD=secrets.token_urlsafe(24),
            RABBITMQ_ERLANG_COOKIE=secrets.token_hex(24),
            INTERNAL_API_TOKEN=secrets.token_urlsafe(40),JWT_SECRET=secrets.token_urlsafe(48),
            ADMIN_USERNAME='upload120_admin',ADMIN_PASSWORD=secrets.token_urlsafe(24),
            UPLOAD200_USERNAME='upload120_tester',UPLOAD200_PASSWORD=secrets.token_urlsafe(24),
            CHAT_QUOTA_PER_DAY='500',CHAT_MAX_SESSIONS_PER_USER='200',ETL_INGEST_RAG='true',
            ETL_UPLOAD_DIR=str(root/'data_root/uploads'),ETL_REQUEST_TIMEOUT='2h',ETL_RUN_TIMEOUT='3h',
            CORS_ALLOWED_ORIGINS='http://127.0.0.1:5199',VITE_API_BASE_URL='http://127.0.0.1:18280',
            FINSIGHT_MODEL_AUDIT_PATH=str(root/'data/runtime/submissions/model-calls.jsonl'),ADVISOR_ENABLED='false')
        if not values.get('LLM_API_KEY') and values.get('DEEPSEEK_API_KEY'):values['LLM_API_KEY']=values.pop('DEEPSEEK_API_KEY')
    if provider_environment is not None:
        for target,aliases in (('LLM_API_KEY',('LLM_API_KEY','DEEPSEEK_API_KEY')),('DASHSCOPE_API_KEY',('DASHSCOPE_API_KEY',))):
            if not values.get(target):
                for alias in aliases:
                    if provider_environment.get(alias):values[target]=provider_environment[alias];break
    if embedding_endpoint and not (values.get('DASHSCOPE_EMBEDDING_ENDPOINT') or values.get('DASHSCOPE_HTTP_BASE_URL')):
        values['DASHSCOPE_EMBEDDING_ENDPOINT']=embedding_endpoint
    if prompts:
        for key,label in (('LLM_API_KEY','DeepSeek API key'),('DASHSCOPE_API_KEY','Bailian/DashScope API key')):
            if not values.get(key):values[key]=getpass.getpass(label+' (hidden; Enter leaves unconfigured): ').strip()
        if not (values.get('DASHSCOPE_EMBEDDING_ENDPOINT') or values.get('DASHSCOPE_HTTP_BASE_URL')):
            values['DASHSCOPE_EMBEDDING_ENDPOINT']=input('Actual Bailian text-embedding HTTPS endpoint (no key in URL): ').strip()
    text=''.join(key+'='+json.dumps(str(value or ''),ensure_ascii=False)+'\n' for key,value in sorted(values.items()))
    path.write_text(text,encoding='utf-8')
    try:path.chmod(0o600)
    except OSError:pass
    if (root/'frontend').is_dir():
        (root/'frontend/.env.local').write_text('VITE_API_BASE_URL=http://127.0.0.1:18280\n',encoding='utf-8')
    return path


def load_environment(root=ROOT,path=None):
    from dotenv import dotenv_values
    root=Path(root).resolve();path=Path(path or root/'.env.upload120')
    if not path.is_file():raise ValueError('Run configure first; private isolated configuration is missing')
    env={key:str(value or '') for key,value in dotenv_values(path).items()}
    if env.get('FINSIGHT_UPGRADE_PROFILE')!=PROFILE or any(env.get(key)!=DATABASE for key in ('DB_NAME','MYSQL_DATABASE','SQL_DB_NAME')):
        raise ValueError('Refusing non-isolated database configuration')
    for key,expected in (('API_PORT','8120'),('SERVER_PORT','18280'),('REDIS_PORT','6391'),('RABBITMQ_PORT','5673')):
        if env.get(key)!=expected:raise ValueError('Refusing non-isolated service ports')
    for key in ('CHROMA_DB_PATH','FINANCIAL_SUBMISSION_DB','FINSIGHT_TASK_DB'):
        if not (root/env[key]).resolve().is_relative_to(root):raise ValueError('Runtime path escapes isolated workspace')
    if env.get('DB_USER')==env.get('SQL_DB_USER'):raise ValueError('Readonly query identity must be separate')
    env.update(MYSQL_HOST=env.get('DB_HOST','127.0.0.1'),MYSQL_PORT=env.get('DB_PORT','3306'),
        MYSQL_USER=env.get('DB_USER','root'),MYSQL_PASSWORD=env.get('DB_PASSWORD',''))
    return env


def selected_originals(manifest,*,root=ROOT,partition='all',limit=None,report_ids=None):
    manifest=json.loads(Path(manifest).read_text(encoding='utf-8'))
    rows=[r for r in manifest['reports'] if partition=='all' or r.get('split')==partition]
    if report_ids:
        rows=[r for r in rows if r['report_id'] in set(report_ids)]
        if {r['report_id'] for r in rows}!=set(report_ids):raise ValueError('Requested report-id is absent from the selected source inventory')
    if limit is not None:rows=rows[:limit]
    for row in rows:
        path=Path(row.get('local_path') or '')
        if not path.is_absolute():path=(Path(root)/path).resolve()
        if row.get('status')!='verified' or not path.is_file() or not path.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError('Source manifest has an unverified or non-local original')
        with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
        if sha!=row.get('sha256'):raise ValueError('Original PDF hash changed before submission')
        row['local_path']=str(path)
    return rows


def _runtime(root=ROOT):return Path(root)/'.local_runtime/upgrade120'


def _configure_process(env):
    os.environ.update(env)


def require_cloud():
    from src.agent.providers import resolve_chat_provider,resolve_embedding_function
    if not resolve_chat_provider().is_configured:raise ValueError('LLM_API_KEY is missing; run configure to enter it privately')
    return resolve_embedding_function('preflight')


def initialize(env):
    from sqlalchemy import create_engine,text
    from config.db_config import get_db_config
    from src.init_db import Base
    from src.agent.v3.repository import metadata
    from src.etl.provenance_store import metadata as provenance
    config=get_db_config()
    server=create_engine(config.connection_string_no_db,pool_pre_ping=True)
    try:
        with server.begin() as conn:conn.execute(text(f'CREATE DATABASE IF NOT EXISTS `{DATABASE}` CHARACTER SET utf8mb4'))
    finally:server.dispose()
    engine=create_engine(config.connection_string,pool_pre_ping=True)
    try:
        Base.metadata.create_all(engine);metadata.create_all(engine);provenance.create_all(engine)
        sql=(ROOT/'backend-java/src/main/resources/db/migration/V2__company_table.sql').read_text(encoding='utf-8')
        sql=sql[sql.index('CREATE TABLE'):];sql=sql[:sql.index(';')+1]
        with engine.begin() as conn:conn.execute(text(sql))
    finally:engine.dispose()
    from src.etl.real_delivery import create_reader
    permissions=create_reader()
    return dict(status='initialized',database=DATABASE,readonly_tables=permissions['granted'])


def _listen(port):
    try:
        with socket.create_connection(('127.0.0.1',int(port)),timeout=1):return True
    except OSError:return False


def _tool(env,key,pattern):
    if env.get(key) and Path(env[key]).is_file():return Path(env[key])
    for parent in (ROOT,*ROOT.parents):
        folder=parent/'.local_runtime'
        if folder.is_dir():
            found=list(folder.glob(pattern))
            if found:return found[0]
    found=shutil.which('redis-server.exe' if key=='REDIS_EXECUTABLE' else 'rabbitmq-server.bat')
    if found:return Path(found)
    raise ValueError('Dependency executable missing; configure '+key+' in .env.upload120')


def start(env):
    runtime=_runtime();runtime.mkdir(parents=True,exist_ok=True)
    records_path=runtime/'services.json'
    records=json.loads(records_path.read_text(encoding='utf-8')) if records_path.exists() else {}
    process_env={**os.environ,**env,'PYTHONPATH':str(ROOT),'PYTHONUNBUFFERED':'1','PYTHONUTF8':'1'}
    def spawn(name,args,cwd,extra=None):
        out=(runtime/(name+'.stdout.log')).open('ab');err=(runtime/(name+'.stderr.log')).open('ab')
        try:
            p=subprocess.Popen([str(a) for a in args],cwd=cwd,env={**process_env,**(extra or {})},
                stdout=out,stderr=err,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),stdin=subprocess.DEVNULL)
        finally:out.close();err.close()
        import psutil
        records[name]=dict(pid=p.pid,created=psutil.Process(p.pid).create_time(),root=str(ROOT),
            configuration_sha256=hashlib.sha256(json.dumps(env,sort_keys=True).encode()).hexdigest())
        _write(records_path,records)
    def existing(name,port):
        if not _listen(port):return False
        if name not in records:raise ValueError(f'Port {port} is already occupied by an unrecorded service; it will not be stopped')
        if name in {'python','java'} and records[name].get('configuration_sha256')!=hashlib.sha256(json.dumps(env,sort_keys=True).encode()).hexdigest():
            raise ValueError('Private configuration changed; run restart to load it in the isolated application services')
        return True
    if not existing('redis',6391):
        exe=_tool(env,'REDIS_EXECUTABLE','redis/**/redis-server.exe')
        config=runtime/'redis.conf';directory=runtime/'redis';directory.mkdir(exist_ok=True)
        config.write_text('\n'.join(['bind 127.0.0.1','port 6391','protected-mode yes','appendonly yes',
            'dir "'+directory.as_posix()+'"','requirepass '+env['REDIS_PASSWORD']])+'\n',encoding='utf-8')
        spawn('redis',[exe,config],runtime)
    if not existing('rabbitmq',5673):
        exe=_tool(env,'RABBITMQ_SERVER_EXECUTABLE','native/rabbitmq/*/sbin/rabbitmq-server.bat')
        erlang=env.get('ERLANG_HOME')
        if not erlang or not (Path(erlang)/'bin/erl.exe').exists():
            erlang=str(next((parent/'.local_runtime/native/erlang' for parent in ROOT.parents
                if (parent/'.local_runtime/native/erlang/bin/erl.exe').is_file()),Path('missing-erlang')))
        if not (Path(erlang)/'bin/erl.exe').exists():raise ValueError('Configure installed ERLANG_HOME')
        base=runtime/'rabbitmq';base.mkdir(exist_ok=True)
        (base/'rabbitmq.conf').write_text('\n'.join(['listeners.tcp.1 = 127.0.0.1:5673','distribution.listener.port_range.min = 25673',
            'distribution.listener.port_range.max = 25673','distribution.listener.interface = 127.0.0.1',
            'default_user = '+env['RABBITMQ_USERNAME'],'default_pass = '+env['RABBITMQ_PASSWORD']])+'\n',encoding='utf-8')
        extra=dict(ERLANG_HOME=erlang,RABBITMQ_BASE=str(base),RABBITMQ_CONFIG_FILE=str(base/'rabbitmq'),
            RABBITMQ_NODENAME='finsight_upload120@localhost',RABBITMQ_DIST_PORT='25673',
            ERL_FLAGS='-setcookie '+env['RABBITMQ_ERLANG_COOKIE'],RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS='+S 4:4')
        spawn('rabbitmq',[os.environ.get('COMSPEC','cmd.exe'),'/c',exe],base,extra)
    if not existing('python',8120):spawn('python',[sys.executable,'-m','uvicorn','src.api.main:app','--host','127.0.0.1','--port','8120'],ROOT)
    if not existing('java',18280):
        jars=[p for p in (ROOT/'backend-java/target').glob('*.jar') if not p.name.endswith('.original')]
        if len(jars)!=1:raise ValueError('Build Java first: mvn -o verify')
        java=shutil.which('java')
        if not java:raise ValueError('Java executable is missing')
        spawn('java',[java,'-jar',jars[0]],ROOT/'backend-java')
    return dict(status='startup_requested',services=records,logs=str(runtime),api='http://127.0.0.1:18280',
        frontend_command='npm run dev -- --host 127.0.0.1 --port 5199; use .env.upload120 VITE_API_BASE_URL')


def restart(env):
    import psutil
    from sqlalchemy import create_engine,text
    from config.db_config import get_db_config
    engine=create_engine(get_db_config().connection_string)
    try:
        with engine.connect() as conn:
            active=conn.execute(text("SELECT COUNT(*) FROM etl_task WHERE status IN ('PENDING','RUNNING')")).scalar_one()
        if active:raise ValueError('Wait for active isolated uploads before restarting; remote OCR jobs will not be resubmitted')
    finally:engine.dispose()
    records_path=_runtime()/'services.json'
    records=json.loads(records_path.read_text(encoding='utf-8'))
    for name in ('python','java'):
        record=records.get(name)
        if not record:continue
        try:
            process=psutil.Process(record['pid'])
            if abs(process.create_time()-record['created'])>.01 or record['root']!=str(ROOT):
                raise ValueError('Recorded application process identity changed; no process was stopped')
            children=process.children(recursive=True)
            for item in reversed(children):item.terminate()
            process.terminate()
            psutil.wait_procs([process,*children],timeout=10)
        except psutil.NoSuchProcess:pass
        records.pop(name,None)
    _write(records_path,records)
    return start(env)


def check(env,probe_cloud=False):
    result=dict(configuration={key:bool(env.get(key)) for key in ('LLM_API_KEY','DASHSCOPE_API_KEY','DASHSCOPE_EMBEDDING_ENDPOINT','OCR_API_URL','PADDLEOCR_API_TOKEN')},
        ports={str(p):_listen(p) for p in (3306,6391,5673,8120,18280)})
    if probe_cloud:
        embedding=require_cloud()
        vector=embedding.embed_query('营业收入与净利润')
        result['embedding']=dict(dimensions=len(vector),fingerprint=embedding.fingerprint,verified=len(vector)==1024)
        from src.agent.llm_client import LLMClient
        model=LLMClient();answer=model.chat_json([dict(role='user',content='仅返回JSON：{"connected":true}')],max_parse_retries=0)
        result['generation']=dict(verified=answer=={'connected':True},diagnostics=model.last_diagnostics)
    return result


class SubmissionAPI:
    def __init__(self,env,admin=False):
        import requests
        self.base='http://127.0.0.1:'+env['SERVER_PORT'];self.http=requests.Session()
        name,password=(env['ADMIN_USERNAME'],env['ADMIN_PASSWORD']) if admin else (env['UPLOAD200_USERNAME'],env['UPLOAD200_PASSWORD'])
        login_path='/api/auth/admin/login' if admin else '/api/auth/login'
        response=self.http.post(self.base+login_path,json=dict(username=name,password=password),timeout=15)
        try:missing_login=response.status_code==401 or response.status_code==400 and response.json().get('code')==41002
        except ValueError:missing_login=False
        if missing_login and not admin:
            created=self.http.post(self.base+'/api/auth/register',json=dict(username=name,password=password,nickname='新财报链路验收'),timeout=15)
            if created.status_code not in {200,201}:raise ValueError('Test user registration failed; inspect isolated Java log')
            response=self.http.post(self.base+'/api/auth/login',json=dict(username=name,password=password),timeout=15)
        response.raise_for_status();value=response.json()
        if value.get('code')!=0:raise ValueError('Java login failed; inspect private account settings')
        from src.utils.java_tokens import RotatingJavaTokens
        self._tokens=RotatingJavaTokens(self.http,self.base,value['data'])
    def call(self,method,path,*,timeout=120,**kwargs):
        positions=[]
        for value in (kwargs.get('files') or {}).values():
            stream=value[1] if isinstance(value,tuple) else value
            if hasattr(stream,'read'):
                try:position=stream.tell()
                except (OSError,AttributeError):position=None
                positions.append((stream,position))
        response=self.http.request(method,self.base+path,timeout=(10,timeout),**kwargs)
        try:
            if response.status_code==401:
                response.close();self._tokens.renew()
                for stream,position in positions:
                    try:
                        if position is None:raise OSError()
                        stream.seek(position)
                    except (OSError,AttributeError):
                        raise ValueError('Upload credential renewed but the file cannot be replayed; rerun the same command') from None
                response=self.http.request(method,self.base+path,timeout=(10,timeout),**kwargs)
            response.raise_for_status();value=response.json()
            if value.get('code')!=0:raise ValueError('Java did not accept the operation')
            return value.get('data')
        finally:response.close()
    def listing(self,admin=False):
        rows=[];page=1
        while True:
            result=self.call('GET','/api/admin/financial-submissions' if admin else '/api/financial-submissions',params=dict(page=page,size=50))
            batch=result.get('records',[]) if isinstance(result,dict) else result
            rows.extend(batch)
            if len(batch)<50:break
            page+=1
        return rows


def upload(env,rows,*,retry=False):
    require_cloud()
    api=SubmissionAPI(env)
    folder=_runtime()/'submissions';folder.mkdir(parents=True,exist_ok=True)
    completed=[];failures=0
    known={r.get('sourceSha256'):r for r in api.listing()}
    for row in rows:
        path=folder/(row['report_id']+'.json')
        saved=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        view=api.call('GET','/api/financial-submissions/'+str(saved['id'])) if saved.get('id') else known.get(row['sha256'])
        executed=view is None or view.get('status') in {'PENDING','RUNNING'}
        if view is None:
            with Path(row['local_path']).open('rb') as f:
                view=api.call('POST','/api/financial-submissions',files={'file':(row['report_id']+'.pdf',f,'application/pdf')})
        _write(path,dict(report_id=row['report_id'],sha256=row['sha256'],id=view['id']))
        if retry and view['status'] in {'FAILED','PARTIAL'}:
            executed=True
            view=api.call('POST',f"/api/financial-submissions/{view['id']}/retry")
        started=time.monotonic()
        while view['status'] in {'PENDING','RUNNING'}:
            if time.monotonic()-started>7600:raise TimeoutError('Financial processing still active; rerun to recover the same upload')
            time.sleep(5);view=api.call('GET',f"/api/financial-submissions/{view['id']}")
        output=folder/(row['report_id']+'.result-'+str(time.time_ns())+'.json')
        _write(output,view);completed.append(view)
        print(json.dumps(dict(report=row['report_id'],status=view['status'],review=view.get('reviewStatus'),message=view.get('message')),ensure_ascii=False),flush=True)
        if executed:
            remote_failure=view['status']=='FAILED' and any(term in (view.get('message') or '') for term in
                ('OCR未完成','云端向量入库未完成','云端身份识别未完成'))
            failures=failures+1 if remote_failure else 0
            if failures>=2:raise RuntimeError('Two consecutive provider-stage failures; progress saved. Check local diagnostics before resuming')
    return dict(processed=len(completed),statuses=dict(Counter(row['status'] for row in completed)))


def review(env,rows):
    api=SubmissionAPI(env,admin=True);known={r.get('sourceSha256'):r for r in api.listing(admin=True)}
    reviewed=[];skipped=[]
    for row in rows:
        view=known.get(row['sha256'])
        if not view or view.get('reviewStatus')!='WAITING':continue
        candidate=api.call('GET',f"/api/admin/financial-submissions/{view['id']}/candidate")
        actual=candidate.get('identity') or {}
        identity={key:row[key] for key in ('stock_code','company','year','period')}
        if any(actual.get(key)!=value for key,value in identity.items()):
            skipped.append(dict(report_id=row['report_id'],reason='Candidate identity differs from official source manifest; manual review required'))
            continue
        quality=candidate.get('quality') or {}
        if not all(quality.get(k) is True for k in ('identity_verified','facts_verified','reports_verified','index_verified')):
            skipped.append(dict(report_id=row['report_id'],reason='Candidate verification incomplete'))
            continue
        identity['aliases']=[]
        api.call('POST',f"/api/admin/financial-submissions/{view['id']}/review",json=dict(decision='approve',
            reason='隔离验收：管理员通过官方来源清单批量确认身份；仅批准程序已核实的事实子集，不宣称人工逐数字审核',identity=identity))
        reviewed.append(view['id'])
    _write(_runtime()/('administrator-batch-review-'+str(time.time_ns())+'.json'),dict(ids=reviewed,skipped=skipped,verification='admin confirmation using source manifest; no claimed human cell audit'))
    return dict(approved=len(reviewed),skipped=skipped)


def publish(env,rows):
    api=SubmissionAPI(env,admin=True);selected={r['sha256'] for r in rows}
    ids=list(dict.fromkeys(r['id'] for r in api.listing(admin=True) if r.get('sourceSha256') in selected and r.get('reviewStatus')=='APPROVED'))
    if not ids:return dict(status='no_new_approved_candidates')
    receipt=api.call('POST','/api/admin/financial-submissions/publish',json=dict(ids=ids),timeout=7200)
    _write(_runtime()/('publication-'+str(time.time_ns())+'.json'),receipt)
    _write(_runtime()/'current-publication.json',receipt)
    return receipt


def evaluation_collection_digest(collection,dimensions):
    """冻结选中集合的文本、来源和实际向量，不把相同条数当作内容未变。"""
    import numpy as np
    identifiers=sorted(collection.get(include=[])['ids'])
    if not identifiers or len(identifiers)!=len(set(identifiers)) or collection.count()!=len(identifiers):
        raise ValueError('Evaluation collection inventory is empty or inconsistent')
    digest=hashlib.sha256(b'chroma-dense-content-v1\n')
    for start in range(0,len(identifiers),256):
        selected=identifiers[start:start+256]
        values=collection.get(ids=selected,include=['documents','metadatas','embeddings'])
        returned=values.get('ids',[])
        if len(returned)!=len(selected) or set(returned)!=set(selected):
            raise ValueError('Evaluation collection changed while reading its content')
        vectors=np.asarray(values.get('embeddings'),dtype='<f4')
        if vectors.shape!=(len(selected),dimensions) or not np.isfinite(vectors).all():
            raise ValueError('Evaluation collection contains invalid vectors')
        rows={key:position for position,key in enumerate(returned)}
        for key in selected:
            position=rows[key]
            content=json.dumps([key,values['documents'][position],values['metadatas'][position]],
                ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
            digest.update(len(content).to_bytes(8,'big'));digest.update(content)
            digest.update(vectors[position].tobytes())
    return digest.hexdigest()


def evaluation_target(engine, env):
    """评测与实际聊天选择同一发布指针，保持试验库和严格库边界。"""
    from src.api.readiness import selected_repository
    repository=selected_repository(engine)
    export=repository.manifest.get('export_path')
    if not export:
        if repository.pointer_name!='serving':
            raise ValueError('Selected trial release has no immutable artifact path')
        export='data/runtime/submissions/releases/'+repository.version
    folder=(ROOT/export).resolve()
    if not folder.is_relative_to(ROOT.resolve()):
        raise ValueError('Selected evaluation artifacts escape the isolated workspace')
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version',manifest.get('data_version'))!=repository.version:
        raise ValueError('Selected repository and evaluation artifacts have different versions')
    index_path=next((folder/name for name in ('index.json','index_coverage.json') if (folder/name).is_file()),None)
    if index_path is None:raise ValueError('Selected evaluation release has no accepted index artifact')
    index=json.loads(index_path.read_text(encoding='utf-8'))
    if index.get('accepted') is not True or index.get('collection')!=repository.collection:
        raise ValueError('Selected evaluation index has not passed acceptance')
    # 验收回执不能替代现存向量；冻结实际集合身份、条数和维度。
    import chromadb
    from src.agent.tool_shared import resolve_chroma_db_path
    from src.agent.providers import resolve_embedding_function,assert_collection_embedding,embedding_fingerprint,resolve_chat_provider
    embedding=resolve_embedding_function('health')
    collection=chromadb.PersistentClient(path=str(resolve_chroma_db_path())).get_collection(repository.collection,embedding_function=None)
    assert_collection_embedding(collection,embedding)
    metadata=collection.metadata or {}
    if metadata.get('data_version')!=repository.version or collection.count()!=index.get('chunks'):
        raise ValueError('Live evaluation collection differs from the frozen release')
    sample=collection.get(limit=1,include=['embeddings']).get('embeddings')
    expected_dimensions=index.get('dimensions') or getattr(embedding,'dimensions',None)
    if sample is None or not len(sample) or not expected_dimensions or len(sample[0])!=expected_dimensions:
        raise ValueError('Live evaluation vector dimensions differ from the configured embedding')
    fingerprint=embedding_fingerprint(embedding)
    if index.get('embedding_fingerprint',index.get('fingerprint'))!=fingerprint:
        raise ValueError('Evaluation index receipt uses another embedding fingerprint')
    provider=resolve_chat_provider()
    profile=(repository.manifest.get('dataset_profile') or {}).get('id') or env.get('FINSIGHT_DATA_PROFILE','strict')
    snapshot=dict(profile=profile,pointer=repository.pointer_name,
        version=repository.version,collection=repository.collection,
        review_mode=repository.manifest.get('review_mode'),
        source_audit_complete=repository.manifest.get('source_audit_complete'),
        index_file=index_path.name,index_sha256=hashlib.sha256(index_path.read_bytes()).hexdigest(),
        index_chunks=index.get('chunks'),index_fingerprint=fingerprint,index_dimensions=expected_dimensions,
        index_metadata_sha256=hashlib.sha256(json.dumps(metadata,sort_keys=True).encode()).hexdigest(),
        index_content_sha256=evaluation_collection_digest(collection,expected_dimensions),
        model_policy=dict(provider=provider.provider,model=provider.model,thinking='disabled',temperature=0,
            output_limit=provider.max_tokens,timeout_seconds=provider.timeout_seconds,network_retries=provider.max_retries,
            endpoint_sha256=hashlib.sha256(provider.base_url.rstrip('/').encode('utf-8')).hexdigest()))
    return folder,snapshot


def evaluate_selected(env,output,*,partition='all',prepare_only=False):
    """正式问答前可离线建原件卡；续跑检查同一资料指针。"""
    from eval.cloud_upload200 import run,prepare
    from src.agent.providers import resolve_chat_provider
    from sqlalchemy import create_engine
    from config.db_config import get_readonly_db_config
    engine=create_engine(get_readonly_db_config().connection_string)
    try:
        folder,snapshot=evaluation_target(engine,env)
        provider=resolve_chat_provider()
        options=dict(root=ROOT,model_version=env['LLM_MODEL'],partition=partition,record_data_gaps=True,
            runtime_snapshot=snapshot,model_policy=dict(provider=provider.provider,temperature=0,thinking='disabled',
                output_limit=provider.max_tokens,timeout_seconds=provider.timeout_seconds,network_retries=provider.max_retries))
        if prepare_only:
            prepared=prepare(folder,output,**options)
            return dict(status='prepared_without_cloud_calls',output=str(prepared),runtime_snapshot=snapshot)
        return run(folder,output,base_url='http://127.0.0.1:18280',resume=True,
            username=env['UPLOAD200_USERNAME'],password=env['UPLOAD200_PASSWORD'],
            verify=lambda:evaluation_target(engine,env)[1],**options)
    finally:
        engine.dispose()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('configure','init','start','restart','check','status','ocr','upload','review','publish','evaluate','reconcile-ocr'))
    parser.add_argument('--source-env');parser.add_argument('--env',default=str(ROOT/'.env.upload120'))
    parser.add_argument('--no-prompts',action='store_true');parser.add_argument('--probe-cloud',action='store_true')
    parser.add_argument('--reuse-process-credentials',action='store_true',help='Copy already-authorized provider credentials from this process into the private server config; never print them')
    parser.add_argument('--embedding-endpoint',help='Actual HTTPS embedding endpoint without credentials')
    parser.add_argument('--partition',choices=('all','development','holdout'),default='all')
    parser.add_argument('--limit',type=int);parser.add_argument('--retry',action='store_true')
    parser.add_argument('--report-id',action='append',help='Select a source manifest report identity; repeat for multiple reports')
    parser.add_argument('--confirm-identities',action='store_true')
    parser.add_argument('--output',default=str(_runtime()/'final200'))
    parser.add_argument('--prepare-only',action='store_true',help='Freeze originals and grading coverage without login or paid model requests')
    parser.add_argument('--candidate-id');parser.add_argument('--job-id')
    args=parser.parse_args(argv)
    if args.limit is not None and args.limit<1:parser.error('--limit must be positive')
    if args.prepare_only and args.stage!='evaluate':parser.error('--prepare-only is available only with evaluate; no processing request was sent')
    try:
        if args.stage=='configure':
            result=dict(status='private_configuration_saved',file=str(configure(ROOT,args.source_env,prompts=not args.no_prompts,
                provider_environment=os.environ if args.reuse_process_credentials else None,embedding_endpoint=args.embedding_endpoint)))
        else:
            env=load_environment(ROOT,args.env);_configure_process(env)
            manifest=ROOT/'data/runtime/cloud_upload120_sources.json'
            if args.stage=='init':result=initialize(env)
            elif args.stage=='start':result=start(env)
            elif args.stage=='restart':result=restart(env)
            elif args.stage=='check':result=check(env,args.probe_cloud)
            elif args.stage=='status':
                from src.etl.submission_service import get_store
                records=get_store().list()
                result=dict(source_counts=json.loads(manifest.read_text(encoding='utf-8'))['counts'],
                    processing=dict(Counter(r['status'] for r in records)),review=dict(Counter(r['review_status'] for r in records)),ocr_pages_reserved_today=get_store().ocr_usage())
            elif args.stage=='evaluate':
                result=evaluate_selected(env,args.output,partition=args.partition,prepare_only=args.prepare_only)
            elif args.stage=='reconcile-ocr':
                from src.etl.submission_service import get_store
                if not args.candidate_id or not args.job_id:raise ValueError('Provide the confirmed candidate-id and existing remote job-id; no new OCR submission is made')
                get_store().record_ocr_submission(args.candidate_id,state='submitted',job_id=args.job_id)
                result=dict(status='existing_job_bound',candidate_id=args.candidate_id)
            else:
                rows=selected_originals(manifest,partition=args.partition,limit=args.limit,report_ids=args.report_id)
                if args.stage=='ocr':
                    from src.etl.submission_service import get_store
                    from src.etl.submission_ocr import fresh_ocr
                    store=get_store();count=0
                    for row in rows:
                        id=row['sha256'];store.register(id,dict(source_path=Path(row['local_path']).relative_to(ROOT).as_posix(),page_count=row['page_count']))
                        lease=store.claim(id)
                        try:
                            value=fresh_ocr(row['local_path'],id,store,ROOT/'data/runtime/submissions/assets'/id,heartbeat=lambda:store.heartbeat(id,lease))
                            count+=1;print(json.dumps(dict(report=row['report_id'],pages=value['pages'],job_id=value['job_id']),ensure_ascii=False),flush=True)
                        finally:store.release(id,lease)
                    result=dict(fresh_ocr_completed=count)
                elif args.stage=='upload':result=upload(env,rows,retry=args.retry)
                elif args.stage=='review':
                    if not args.confirm_identities:raise ValueError('Use --confirm-identities after reviewing the source manifest and candidate summary')
                    result=review(env,rows)
                else:result=publish(env,rows)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (KeyboardInterrupt,SystemExit):raise
    except Exception as exc:
        from src.etl.submission_service import ProcessingFailure
        safe=str(exc) if isinstance(exc,(ValueError,ProcessingFailure)) else type(exc).__name__+'; inspect saved local progress and service logs; rerun the same command'
        print(json.dumps(dict(status='paused',reason=safe),ensure_ascii=False),file=sys.stderr);return 2
