"""作品说明：后台任务持久化独立于SSE订阅连接，刷新或断网继续执行。"""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
from pathlib import Path

from .agent import AgentEvent
from .state import read_state
from .contracts import DialogueState,InterruptedRequest,Request,Execution

TERMINAL = {'completed', 'cancelled', 'failed'}
ERROR_MESSAGES = {
    'model_queue_full': '服务当前忙碌，等待队列已满，请稍后重试。',
    'model_queue_timeout': '排队超过30秒，本轮未开始，请稍后重试。',
    'queue_or_turn_timeout': '排队或执行超过服务时限，本轮未完成。',
    'turn_timeout': '本轮执行超过服务时限，未完成的结果不再发布。',
    'model_invalid_schema': '模型没有返回有效的请求清单，本轮未执行财务查询。',
    'model_empty_output': '模型返回空内容，本轮未完成。',
    'model_connection_failed': '本地模型连接失败，本轮未完成。',
    'model_timeout': '本地模型调用超时，本轮未完成。',
    'semantic_request_audit_failed': '本轮理解结果未通过要求核对，未发布财务答案。',
    'worker_restarted': '分析服务重启，这轮任务已结束，请重新提交。',
    'worker_shutdown': '分析服务关闭，这轮任务已结束，请重新提交。',
    'InvalidUnderstanding': '模型提出的条件不符合请求规范，本轮未执行财务查询。',
}


class TaskConflict(ValueError):
    pass


def diagnostic_value(value,depth=0):
    """作品说明：诊断只序列化普通数据，异常对象不能阻碍失败终态保存，也不能泄露驱动信息或凭据。"""
    if value is None or isinstance(value,(str,bool,int)):return value
    if isinstance(value,float):return value if math.isfinite(value) else None
    if depth>=20:return {'type':'depth_limit'}
    if isinstance(value,dict):return {key:diagnostic_value(item,depth+1) for key,item in value.items() if isinstance(key,str)}
    if isinstance(value,(list,tuple)):return [diagnostic_value(item,depth+1) for item in value]
    return {'type':type(value).__name__}


def failure_context(record):
    state=DialogueState.model_validate(record.get('context') or {})
    state.recent_facts=[];state.recent_computed=[]
    checkpoint=record.get('request_checkpoint') or {}
    request=Request.model_validate(checkpoint['request']) if checkpoint.get('request') else None
    from .request_bindings import literal_condition_paths,explicit_condition_amendment
    financial=bool(request and any(g.kind in {'lookup','compare','rank','chart','quote','cause','sign'} for g in request.goals))
    paths=set()
    if not checkpoint.get('approved'):
        paths.update(checkpoint.get('uncertain_paths',[]))
        if financial:
            paths.update(e.field for e in request.modifications if e.operation!='inherit')
            paths.update(e.field for g in request.goals for e in g.condition_edits if e.operation!='inherit')
        question=record.get('question','')
        # 作品说明：被拒绝的提案保留明确修改的未确认字段，不能静默清掉不确定性或猜值。
        if financial or explicit_condition_amendment(question):
            paths.update(literal_condition_paths(question,record.get('registered_companies',{})))
    state.interrupted_request=InterruptedRequest(turn_id=record['id'],question=record.get('question',''),status=record['status'],uncertain_paths=sorted(paths))
    if financial or paths:
        executed=Execution(turn_id=record['id'],data_version='',resolved_periods={},
            time_rule='条件已确认，查询未完成' if checkpoint.get('approved') else '请求未通过确认，没有可发布的本轮查询结果',
            fact_refs=[],status=record['status'],conditions=state.conditions.model_copy(deep=True) if checkpoint.get('approved') else None,
            goal_conditions=state.goal_conditions if checkpoint.get('approved') else {})
        state.last_execution=executed
        state.executions=[e for e in state.executions if e.turn_id!=record['id']]+[executed]
        state.executions=state.executions[-12:]
    return state.model_dump(mode='json')


def terminal_result(record):
    """作品说明：SSE、轮询、取消和重启使用同一份持久失败结果。"""
    if record['result'] is not None:return record['result']
    if record['status'] not in {'failed','cancelled'}:return None
    return dict(version=3,task_id=record['id'],task_status=record['status'],question=record.get('question',''),
        answer=dict(content='本轮已取消。' if record['status']=='cancelled' else ERROR_MESSAGES.get(record['error'],
            '本轮执行失败，未取得可发布的结果。'),image=[],references=[]),
        facts=[],evidence=[],chart_data_list=[],dialogue_state=failure_context(record),
        outcome=dict(status=record['status'],reason_codes=[record['error']]),
        verification=dict(status='fail',checks=[],unmatched_numbers=[]),task_results=[])


class TaskStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, client_id TEXT NOT NULL, session TEXT NOT NULL, status TEXT NOT NULL, queued_at REAL NOT NULL, deadline REAL NOT NULL, result TEXT, error TEXT, saved INTEGER NOT NULL DEFAULT 0, UNIQUE(session,client_id))')
            db.execute('CREATE TABLE IF NOT EXISTS events (task_id TEXT NOT NULL, sequence INTEGER NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(task_id,sequence))')
            if 'context' not in {r[1] for r in db.execute('PRAGMA table_info(tasks)')}:
                db.execute('ALTER TABLE tasks ADD COLUMN context TEXT')
            if 'question' not in {r[1] for r in db.execute('PRAGMA table_info(tasks)')}:
                db.execute("ALTER TABLE tasks ADD COLUMN question TEXT NOT NULL DEFAULT ''")
            if 'request_checkpoint' not in {r[1] for r in db.execute('PRAGMA table_info(tasks)')}:
                db.execute('ALTER TABLE tasks ADD COLUMN request_checkpoint TEXT')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def recover_restart(self):
        # 作品说明：重启后没有原异步调用，遗留任务明确终结，避免重复调用模型或工具，并释放对应会话锁。
        with self.connect() as db:
            db.execute("UPDATE tasks SET status='failed', error='worker_restarted' WHERE status IN ('queued','running','cancelling')")

    def create(self, id, client_id, session, deadline, context=None, question='',safety_paths=()):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT * FROM tasks WHERE session=? AND client_id=?', (session, client_id)).fetchone()
            if prior:
                return dict(prior), False
            active = db.execute("SELECT id FROM tasks WHERE session=? AND status IN ('queued','running','cancelling')", (session,)).fetchone()
            if active:
                raise TaskConflict('session_has_active_turn')
            db.execute("INSERT INTO tasks(id,client_id,session,status,queued_at,deadline,context,question,request_checkpoint) VALUES(?,?,?,'queued',?,?,?,?,?)", (id, client_id, session, time.time(), deadline, json.dumps(context or {}, ensure_ascii=False), question,json.dumps(dict(approved=False,uncertain_paths=list(safety_paths)))))
        return self.get(id), True

    def get(self, id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result['result'] = json.loads(result['result']) if result['result'] else None
            result['saved'] = bool(result['saved'])
            result['context'] = json.loads(result['context']) if result['context'] else {}
            result['request_checkpoint'] = json.loads(result['request_checkpoint']) if result['request_checkpoint'] else {}
            return result

    def checkpoint(self,id,request,dialogue=None,approved=False):
        # 作品说明：原子更新前检查请求和状态；迟到检查点不能覆盖正在取消或终态的任务。
        parsed=Request.model_validate(request)
        if parsed.turn_id!=id:raise ValueError('Checkpoint task identity mismatch')
        state=DialogueState.model_validate(dialogue) if dialogue is not None else None
        if approved and state is None:raise ValueError('Confirmed checkpoint needs a dialogue snapshot')
        payload=json.dumps(dict(request=parsed.model_dump(mode='json'),approved=bool(approved)),ensure_ascii=False)
        with self.connect() as db:
            if approved:
                state.recent_facts=[];state.recent_computed=[]
                return db.execute("UPDATE tasks SET context=?,request_checkpoint=? WHERE id=? AND status='running'",(state.model_dump_json(),payload,id)).rowcount==1
            return db.execute("UPDATE tasks SET request_checkpoint=? WHERE id=? AND status='running'",(payload,id)).rowcount==1

    def by_client(self, session, client_id):
        with self.connect() as db:
            row = db.execute('SELECT id FROM tasks WHERE session=? AND client_id=?', (session, client_id)).fetchone()
        return self.get(row['id']) if row else None

    def transition(self, id, expected, status, result=None, error=None):
        values = ','.join('?' for _ in expected)
        with self.connect() as db:
            update = db.execute(f'UPDATE tasks SET status=?, result=?, error=? WHERE id=? AND status IN ({values})',
                (status, json.dumps(result, ensure_ascii=False) if result is not None else None, error, id, *expected))
            return update.rowcount == 1

    def append(self, id, event):
        with self.connect() as db:
            sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE task_id=?', (id,)).fetchone()[0]
            db.execute('INSERT INTO events VALUES(?,?,?,?)', (id, sequence, event.type, json.dumps(event.data, ensure_ascii=False)))
        return sequence

    def events(self, id, after=0):
        with self.connect() as db:
            return [(r['sequence'], AgentEvent(r['type'], json.loads(r['payload']))) for r in db.execute('SELECT * FROM events WHERE task_id=? AND sequence>? ORDER BY sequence', (id, after))]

    def acknowledge(self, id, session):
        with self.connect() as db:
            return db.execute("UPDATE tasks SET saved=1 WHERE id=? AND session=? AND status IN ('completed','cancelled','failed')", (id, session)).rowcount == 1


class TaskManager:
    def __init__(self, agent, store: TaskStore):
        self.agent, self.store = agent, store
        self.store.recover_restart()
        self.capacity = asyncio.Semaphore(1)
        self.waiting = 0
        self.runners = {}
        self.changed = {}
        self.closing = False

    async def submit(self, id, client_id, session, question, history, deadline):
        prior = self.store.by_client(session, client_id)
        if prior:
            if prior.get('question') and prior['question'] != question:
                raise TaskConflict('client_request_conflict')
            return prior
        if len(self.runners) >= 9:
            raise TaskConflict('model_queue_full')
        state = read_state(history)
        from .request_bindings import literal_memory_edits
        from .memory_updates import apply_memory_edits
        state=apply_memory_edits(state,literal_memory_edits(question,getattr(self.agent,'companies',{})),question,id)
        state.recent_facts = []
        state.recent_computed = []
        from .request_bindings import literal_condition_paths
        safety_paths=literal_condition_paths(question,getattr(self.agent,'companies',{}))
        record, created = self.store.create(id, client_id, session, min(deadline, time.time() + 270), state.model_dump(mode='json'), question,safety_paths)
        if created:
            self.changed[id] = asyncio.Event()
            # 作品说明：持有后台协程引用，订阅断开不取消执行任务。
            self.runners[id] = asyncio.create_task(self._run(record, question, history), name='financial-turn-' + id)
        return record

    async def _emit(self, id, type, data):
        record = self.store.get(id)
        if record['status'] in {'cancelling', 'cancelled', 'failed'}:
            return
        if record['status'] == 'completed' and type != 'done':
            return
        if type=='plan' and '_request_checkpoint' in data:
            checkpoint=data['_request_checkpoint']
            self.store.checkpoint(id,**checkpoint)
            data={key:value for key,value in data.items() if key!='_request_checkpoint'}
        self.store.append(id, AgentEvent(type, data))
        self.changed.setdefault(id, asyncio.Event()).set()

    async def _run(self, record, question, history):
        id, acquired, waiting = record['id'], False, True
        self.waiting += 1
        try:
            await self._emit(id, 'plan', {'label': '等待执行', 'detail': '正在等待执行名额，超过30秒会结束排队',
                'task_id': id, 'task_status': 'queued', 'deadline': int(record['deadline'] * 1000)})
            await asyncio.wait_for(self.capacity.acquire(), timeout=max(.001, min(30, record['deadline'] - time.time())))
            acquired = True
            self.waiting -= 1
            waiting = False
            if not self.store.transition(id, ['queued'], 'running'):
                return
            self.changed[id].set()
            await self._emit(id, 'plan', {'label': '开始执行', 'detail': '已取得执行名额',
                'task_id': id, 'task_status': 'running', 'deadline': int(record['deadline'] * 1000)})
            async def emit(type, data):
                await self._emit(id, type, data)
            result = await self.agent.run(question, history, id, record['deadline'], emit)
            # 作品说明：取消终态优先，模型迟到结果不再发布为完成。
            if self.store.transition(id, ['running'], 'completed', result=result):
                await self._emit(id, 'done', {'result': result})
        except asyncio.CancelledError:
            self.store.transition(id, ['queued', 'running', 'cancelling'], 'failed' if self.closing else 'cancelled',
                error='worker_shutdown' if self.closing else 'user_cancelled')
            self.changed.setdefault(id, asyncio.Event()).set()
        except TimeoutError:
            self.store.transition(id, ['queued', 'running'], 'failed',
                error='turn_timeout' if acquired else 'model_queue_timeout')
            self.changed.setdefault(id, asyncio.Event()).set()
        except Exception as exc:
            reason = getattr(exc, 'reason', None) or type(exc).__name__
            current=self.store.get(id)
            # 作品说明：编译失败仅保留有效提案用于定位未确认编辑，不将它当作执行授权。
            if not current.get('request_checkpoint',{}).get('approved') and isinstance(getattr(exc,'request',None),dict):
                try:self.store.checkpoint(id,exc.request)
                except ValueError:pass
                current=self.store.get(id)
            failure=dict(version=3,task_id=id,task_status='failed',question=question,
                answer=dict(content=ERROR_MESSAGES.get(reason,'本轮执行失败，未取得可发布的结果。'),image=[],references=[]),
                facts=[],evidence=[],chart_data_list=[],dialogue_state=failure_context({**current,'status':'failed','registered_companies':getattr(self.agent,'companies',{})}),
                outcome=dict(status='failed',reason_codes=[reason]),diagnostics={})
            if getattr(exc,'audit',None): failure['diagnostics']['failed_request_audit']=diagnostic_value(exc.audit)
            if getattr(exc,'request',None): failure['diagnostics']['failed_request_contract']=diagnostic_value(exc.request)
            if getattr(exc,'diagnostics',None):failure['diagnostics'].update(diagnostic_value(exc.diagnostics))
            self.store.transition(id, ['queued', 'running'], 'failed', result=failure,error=reason)
            self.changed.setdefault(id, asyncio.Event()).set()
        finally:
            if waiting:
                self.waiting -= 1
            if acquired:
                self.capacity.release()
            self.runners.pop(id, None)

    async def cancel(self, id, session):
        record = self.store.get(id)
        if not record or record['session'] != session:
            raise KeyError('task_not_found')
        if not self.store.transition(id, ['queued', 'running'], 'cancelling'):
            return self.store.get(id)
        runner = self.runners.get(id)
        if runner:
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                # 作品说明：处理协程进入保护块之前就收到取消的情况。
                self.store.transition(id, ['cancelling'], 'cancelled', error='user_cancelled')
                self.runners.pop(id, None)
        else:
            self.store.transition(id, ['cancelling'], 'cancelled', error='user_cancelled')
        self.changed.setdefault(id, asyncio.Event()).set()
        return self.store.get(id)

    async def stream(self, id, after=0):
        sequence = after
        delivered_done = False
        while True:
            signal = self.changed.setdefault(id, asyncio.Event())
            signal.clear()
            for sequence, event in self.store.events(id, sequence):
                delivered_done = delivered_done or event.type == 'done'
                yield event
            record = self.store.get(id)
            if record['status'] in TERMINAL:
                # 作品说明：终态发布前读取已提交事件，避免快速执行丢失工具进度或提前发出done。
                for sequence,event in self.store.events(id,sequence):
                    delivered_done=delivered_done or event.type=='done'
                    yield event
                result=terminal_result(record)
                if not delivered_done and result is not None:
                    yield AgentEvent('done', {'result': result})
                break
            try:
                await asyncio.wait_for(signal.wait(), timeout=15)
            except TimeoutError:
                yield AgentEvent('plan', {'label': '任务仍在执行', 'detail': '可刷新恢复；停止按钮才会取消任务', 'task_id': id, 'task_status': record['status'], 'deadline': int(record['deadline'] * 1000)})

    async def close(self):
        self.closing = True
        runners = list(self.runners.values())
        for runner in runners:
            runner.cancel()
        await asyncio.gather(*runners, return_exceptions=True)
