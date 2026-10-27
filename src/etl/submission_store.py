"""按原件摘要持久保存候选、远端作业和审核，重启不重新猜测处理状态。"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from threading import Event,Thread


class SubmissionBusy(RuntimeError):
    pass


class SubmissionConflict(RuntimeError):
    pass


class LeaseKeeper:
    """长时间同步OCR、模型与原件核验期间仍续租；失去所有权后禁止下一阶段写入。"""
    def __init__(self,callback,*,interval=20):
        self.callback,self.interval=callback,interval
        self.stop=Event();self.error=None
        self.thread=Thread(target=self._run,name='financial-lease-renewal',daemon=True)
    def __enter__(self):
        self.callback();self.thread.start();return self
    def _run(self):
        while not self.stop.wait(self.interval):
            try:self.callback()
            except Exception:
                self.error=SubmissionBusy('Financial processing lease renewal failed')
                return
    def pulse(self):
        if self.error:raise self.error
        self.callback()
    def __exit__(self,*args):
        self.stop.set();self.thread.join(timeout=12)


def china_day():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Invalid financial document identity')
    return value


class SubmissionStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS submission_assets (
                  id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                  lease TEXT, lease_until REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS submission_events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL,
                  kind TEXT NOT NULL, created_at REAL NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS submission_ocr_usage (
                  id TEXT PRIMARY KEY, day TEXT NOT NULL, pages INTEGER NOT NULL,
                  state TEXT NOT NULL, job_id TEXT);
                CREATE TABLE IF NOT EXISTS submission_operations (
                  name TEXT PRIMARY KEY, token TEXT NOT NULL, lease_until REAL NOT NULL);
            ''')

    @contextmanager
    def _connection(self, write=False):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=10000')
        try:
            if write:
                conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _payload(row):
        if row is None:
            raise KeyError('Financial submission not found')
        return {**json.loads(row['payload']), 'revision': row['revision']}

    @staticmethod
    def _event(conn, id, kind, payload):
        conn.execute('INSERT INTO submission_events(id,kind,created_at,payload) VALUES(?,?,?,?)',
                     (id, kind, time.time(), json.dumps(payload, ensure_ascii=False)))

    def register(self, id, payload):
        valid_id(id)
        initial = {'candidate_id': id, 'source_sha256': id, 'status': 'pending',
                   'review_status': 'PROCESSING', 'steps': [], 'facts': [], 'excluded': [],
                   'created_at': time.time(), **deepcopy(payload)}
        if initial.get('source_sha256') != id:
            raise ValueError('Original content hash does not match candidate identity')
        with self._connection(write=True) as conn:
            conn.execute('INSERT OR IGNORE INTO submission_assets(id,payload) VALUES(?,?)',
                         (id, json.dumps(initial, ensure_ascii=False)))
        return self.get(id)

    def get(self, id):
        valid_id(id)
        with self._connection() as conn:
            return self._payload(conn.execute('SELECT * FROM submission_assets WHERE id=?', (id,)).fetchone())

    def list(self, review_status=None):
        with self._connection() as conn:
            rows = [self._payload(row) for row in conn.execute('SELECT * FROM submission_assets ORDER BY id')]
        return [row for row in rows if not review_status or row.get('review_status') == review_status]

    def claim(self, id, *, ttl=180):
        valid_id(id)
        token = uuid.uuid4().hex
        with self._connection(write=True) as conn:
            row = conn.execute('SELECT * FROM submission_assets WHERE id=?', (id,)).fetchone()
            self._payload(row)
            if row['lease'] and row['lease_until'] > time.time():
                raise SubmissionBusy('Financial document is already processing')
            conn.execute('UPDATE submission_assets SET lease=?,lease_until=? WHERE id=?',
                         (token, time.time() + ttl, id))
        return token

    def heartbeat(self, id, token, ttl=180):
        with self._connection(write=True) as conn:
            changed = conn.execute('UPDATE submission_assets SET lease_until=? WHERE id=? AND lease=?',
                                   (time.time() + ttl, valid_id(id), token)).rowcount
            if changed != 1:
                raise SubmissionBusy('Financial processing lease no longer belongs to this worker')

    def release(self, id, token):
        with self._connection(write=True) as conn:
            conn.execute('UPDATE submission_assets SET lease=NULL,lease_until=0 WHERE id=? AND lease=?',
                         (valid_id(id), token))

    def save(self, id, payload, *, token=None):
        valid_id(id)
        with self._connection(write=True) as conn:
            row = conn.execute('SELECT * FROM submission_assets WHERE id=?', (id,)).fetchone()
            old = self._payload(row)
            if token is not None and (row['lease'] != token or row['lease_until'] <= time.time()):
                raise SubmissionBusy('Financial processing lease no longer belongs to this worker')
            if token is None and row['lease'] and row['lease_until'] > time.time():
                raise SubmissionBusy('Financial document is processing')
            if payload.get('revision', old['revision']) != old['revision']:
                raise SubmissionConflict('Financial candidate revision changed')
            if old.get('review_status') == 'PUBLISHED':
                raise SubmissionConflict('Published financial candidates are immutable')
            value = {key: item for key, item in payload.items() if key != 'revision'}
            value.update(candidate_id=id, source_sha256=id, updated_at=time.time())
            conn.execute('UPDATE submission_assets SET payload=?,revision=revision+1 WHERE id=?',
                         (json.dumps(value, ensure_ascii=False), id))
        return self.get(id)

    def review(self, id, decision, *, operator_id, identity=None, reason='', expected_revision=None):
        if type(operator_id) is not int or operator_id < 1 or decision not in {'approve', 'reject'}:
            raise ValueError('A trusted administrator and valid review decision are required')
        with self._connection(write=True) as conn:
            row = conn.execute('SELECT * FROM submission_assets WHERE id=?', (valid_id(id),)).fetchone()
            value = self._payload(row)
            if expected_revision is not None and value['revision'] != expected_revision:
                raise SubmissionConflict('Financial candidate changed after validation')
            if row['lease'] and row['lease_until'] > time.time():
                raise SubmissionBusy('Processing must finish before review')
            if value.get('review_status') == 'PUBLISHED':
                raise ValueError('Published documents require a new version')
            if decision == 'reject':
                if not str(reason).strip():
                    raise ValueError('A rejection reason is required')
                value['review_status'] = 'REJECTED'
            else:
                quality = value.get('quality') or {}
                checks = ('identity_verified', 'facts_verified', 'reports_verified', 'index_verified')
                if value.get('review_status') not in {'WAITING', 'APPROVED'} or not all(quality.get(k) is True for k in checks) or quality.get('verified_count', 0) < 1 or quality.get('conflicts'):
                    raise ValueError('Candidate is not fully verified; 核验未完成不能批准')
                expected = value.get('identity') or {}
                if not isinstance(identity, dict) or any(identity.get(k) != expected.get(k) for k in ('stock_code', 'company', 'year', 'period')):
                    raise ValueError('Confirmed identity differs from the original 身份')
                aliases = identity.get('aliases') or []
                if not isinstance(aliases, list) or any(not isinstance(a, str) or not 1 <= len(a.strip()) <= 80 for a in aliases):
                    raise ValueError('Invalid company aliases')
                value['identity'] = {**expected, 'aliases': list(dict.fromkeys(a.strip() for a in aliases))}
                value['review_status'] = 'APPROVED'
                value['approval_invalidated'] = False
            value['review'] = {'decision': decision, 'operator_id': operator_id, 'reason': str(reason).strip()[:1000],
                               'at': time.time(), 'verification_kind': 'administrator_confirmation'}
            value.pop('revision', None)
            conn.execute('UPDATE submission_assets SET payload=?,revision=revision+1 WHERE id=?',
                         (json.dumps(value, ensure_ascii=False), id))
            self._event(conn, id, 'review', value['review'])
        return self.get(id)

    def mark_published(self, ids, receipt, operator_id):
        with self._connection(write=True) as conn:
            for id in ids:
                row = conn.execute('SELECT * FROM submission_assets WHERE id=?', (valid_id(id),)).fetchone()
                value = self._payload(row)
                if value.get('review_status') not in {'APPROVED', 'PUBLISHED'}:
                    raise ValueError('Only approved documents may be published')
                value.update(review_status='PUBLISHED', published_version=receipt['version'], publication=receipt)
                value.pop('revision', None)
                conn.execute('UPDATE submission_assets SET payload=?,revision=revision+1 WHERE id=?',
                             (json.dumps(value, ensure_ascii=False), id))
                self._event(conn, id, 'published', {'version': receipt['version'], 'operator_id': operator_id})

    @contextmanager
    def publishing(self,ids):
        """跨进程发布租约与候选租约一起续期，云端调用不占用SQLite写事务。"""
        token=uuid.uuid4().hex
        claimed=[]
        with self._connection(write=True) as conn:
            old=conn.execute('SELECT * FROM submission_operations WHERE name=?',('publish',)).fetchone()
            if old and old['lease_until']>time.time():
                raise SubmissionBusy('Another financial publication is active')
            conn.execute('INSERT OR REPLACE INTO submission_operations VALUES(?,?,?)',('publish',token,time.time()+180))
        try:
            for id in sorted(set(ids)):
                claimed.append((id,self.claim(id)))
            def heartbeat():
                with self._connection(write=True) as conn:
                    updated=conn.execute('UPDATE submission_operations SET lease_until=? WHERE name=? AND token=? AND lease_until>?',
                        (time.time()+180,'publish',token,time.time())).rowcount
                    if updated!=1:raise SubmissionBusy('Publication lease was lost')
                for id,lease in claimed:self.heartbeat(id,lease)
            with LeaseKeeper(heartbeat) as keeper:
                yield keeper.pulse
        finally:
            for id,lease in claimed:self.release(id,lease)
            with self._connection(write=True) as conn:
                conn.execute('DELETE FROM submission_operations WHERE name=? AND token=?',('publish',token))

    def ocr_submission(self, id):
        with self._connection() as conn:
            row = conn.execute('SELECT * FROM submission_ocr_usage WHERE id=?', (valid_id(id),)).fetchone()
            return dict(row) if row else None

    def ocr_usage(self, day=None):
        with self._connection() as conn:
            return conn.execute('SELECT COALESCE(SUM(pages),0) FROM submission_ocr_usage WHERE day=?',
                                (day or china_day(),)).fetchone()[0]

    def reserve_ocr(self, id, pages, *, day=None, limit=20000):
        if not isinstance(pages, int) or pages < 1 or limit < 1:
            raise ValueError('OCR reservation requires a positive page count')
        day = day or china_day()
        with self._connection(write=True) as conn:
            old = conn.execute('SELECT * FROM submission_ocr_usage WHERE id=?', (valid_id(id),)).fetchone()
            if old:
                if old['pages'] != pages:
                    raise ValueError('OCR reservation page count changed')
                if old['state'] in {'uncertain', 'submitting'}:
                    raise ValueError('OCR submission uncertain; 请确认远端作业后关联原 job_id，不能重复提交')
                return dict(old)
            used = conn.execute('SELECT COALESCE(SUM(pages),0) FROM submission_ocr_usage WHERE day=?', (day,)).fetchone()[0]
            if used + pages > limit:
                raise ValueError('OCR daily quota 额度不足；保留进度，次日继续')
            conn.execute('INSERT INTO submission_ocr_usage(id,day,pages,state) VALUES(?,?,?,?)',
                         (id, day, pages, 'reserved'))
        return self.ocr_submission(id)

    def record_ocr_submission(self, id, *, state, job_id=None):
        if state not in {'reserved', 'submitting', 'uncertain', 'submitted', 'completed'}:
            raise ValueError('Invalid OCR submission state')
        with self._connection(write=True) as conn:
            old = conn.execute('SELECT * FROM submission_ocr_usage WHERE id=?', (valid_id(id),)).fetchone()
            if not old:
                raise ValueError('OCR reservation is missing')
            if old['job_id'] and job_id and old['job_id'] != job_id:
                raise ValueError('Cannot replace the bound remote OCR job')
            if state in {'submitted', 'completed'} and not (job_id or old['job_id']):
                raise ValueError('Remote job identity is required')
            conn.execute('UPDATE submission_ocr_usage SET state=?,job_id=? WHERE id=?',
                         (state, job_id or old['job_id'], id))

