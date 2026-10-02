"""作品说明：请求固定已接受的数据版本，并使用只读账户查询标准事实。"""
from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Column, Integer, JSON, MetaData, String, Table, and_, select

from .contracts import Fact

metadata = MetaData()
releases = Table('financial_data_releases', metadata,
    Column('version', String(64), primary_key=True),
    Column('status', String(16), nullable=False),
    Column('index_collection', String(100), nullable=False),
    Column('manifest', JSON, nullable=False))
pointer = Table('financial_data_pointer', metadata,
    Column('name', String(32), primary_key=True),
    Column('version', String(64), nullable=False))
reports = Table('financial_canonical_reports', metadata,
    Column('data_version', String(64), primary_key=True),
    Column('stock_code', String(6), primary_key=True),
    Column('year', Integer, primary_key=True),
    Column('period', String(4), primary_key=True),
    Column('payload', JSON, nullable=False))
facts = Table('financial_canonical_facts', metadata,
    Column('data_version', String(64), primary_key=True),
    Column('id', String(64), primary_key=True),
    Column('stock_code', String(6), nullable=False, index=True),
    Column('year', Integer, nullable=False),
    Column('period', String(4), nullable=False),
    Column('metric', String(80), nullable=False),
    Column('scope', String(16), nullable=False),
    Column('status', String(20), nullable=False),
    Column('payload', JSON, nullable=False))


def json_payload(value):
    return json.loads(value) if isinstance(value, str) else value


def identity_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class CanonicalRepository:
    def __init__(self, engine, version: str | None = None):
        self.engine = engine
        self.query_trace = []
        # 作品说明：一轮请求只解析一次发布指针，执行中保持同一数据和索引版本。
        with self._connect() as conn:
            self.version = version or conn.execute(select(pointer.c.version).where(pointer.c.name == 'serving')).scalar_one()
            release = conn.execute(select(releases).where(releases.c.version == self.version)).mappings().one()
            if release['status'] != 'accepted':
                raise ValueError('Canonical release has not been accepted')
            self.collection = release['index_collection']
            self.manifest = json_payload(release['manifest'])

    @contextmanager
    def _connect(self):
        # 作品说明：连接池每次取出的连接都配置只读与超时，防止换连接后丢失约束。
        with self.engine.connect() as conn:
            if self.engine.dialect.name=='mysql':
                from sqlalchemy import text
                conn.execute(text('SET SESSION MAX_EXECUTION_TIME = 5000'))
                conn.execute(text('SET SESSION TRANSACTION READ ONLY'))
            yield conn

    def _select_payloads(self,statement,purpose):
        compiled=statement.compile(compile_kwargs={'render_postcompile':True})
        record=dict(purpose=purpose,sql=str(compiled),parameters=compiled.params,
            data_version=self.version,status='running',rows=0)
        started=time.monotonic()
        self.query_trace.append(record)
        try:
            with self._connect() as conn:
                values=list(conn.execute(statement).scalars())
            record.update(status='completed',rows=len(values))
            return values
        except Exception:
            record['status']='failed'
            raise
        finally:record['milliseconds']=round((time.monotonic()-started)*1000,3)

    def report_catalog(self, codes: list[str] = ()) -> list[dict]:
        query = select(reports.c.payload).where(reports.c.data_version == self.version)
        if codes:
            query = query.where(reports.c.stock_code.in_(codes))
        return [json_payload(p) for p in self._select_payloads(query,'report_catalog')]

    def query(self, codes: list[str], selections: dict[str, list[tuple[int, str]]],
              metrics: list[str], scope: str) -> list[Fact]:
        from sqlalchemy import or_
        if not codes or not metrics or not selections:
            return []
        periods = [and_(facts.c.stock_code == code, facts.c.year == year, facts.c.period == period)
                   for code in codes for year, period in selections.get(code, [])]
        if not periods:
            return []
        statement = select(facts.c.payload).where(facts.c.data_version == self.version,
            facts.c.metric.in_(metrics), facts.c.scope == scope,
            facts.c.status.in_(['verified', 'derived']), or_(*periods)).limit(2001)
        values = self._select_payloads(statement,'financial_facts')
        if len(values) > 2000:
            raise ValueError('Canonical fact result limit exceeded')
        return [Fact.model_validate(json_payload(value)) for value in values]

    def by_ids(self, ids: list[str]) -> list[Fact]:
        if len(ids) > 2000:
            raise ValueError('Fact reference limit exceeded')
        values = self._select_payloads(select(facts.c.payload).where(facts.c.data_version == self.version,
            facts.c.id.in_(ids), facts.c.status.in_(['verified', 'derived'])),'fact_references')
        return [Fact.model_validate(json_payload(value)) for value in values]


def stage_release(engine, version: str, fact_list: list[Fact], report_list: list[dict], collection: str, manifest: dict):
    """作品说明：写入仅供ETL准备候选版本；重复准备相同数据保持幂等。"""
    metadata.create_all(engine)
    keys = defaultdict(list)
    for fact in fact_list:
        if fact.data_version != version:
            raise ValueError('Mixed fact data versions')
        keys[(fact.stock_code, fact.year, fact.period, fact.metric, fact.scope)].append(fact)
    if any(len(values) > 1 for values in keys.values()):
        raise ValueError('Conflicts must be resolved or excluded before staging')
    with engine.begin() as conn:
        old = conn.execute(select(releases.c.status).where(releases.c.version == version)).scalar_one_or_none()
        if old == 'accepted':
            raise ValueError('Accepted releases are immutable')
        conn.execute(facts.delete().where(facts.c.data_version == version))
        conn.execute(reports.delete().where(reports.c.data_version == version))
        conn.execute(releases.delete().where(releases.c.version == version))
        conn.execute(releases.insert().values(version=version, status='staged', index_collection=collection, manifest=manifest))
        for report in report_list:
            conn.execute(reports.insert().values(data_version=version, stock_code=report['stock_code'],
                year=report['year'], period=report['period'], payload=report))
        for start in range(0, len(fact_list), 250):
            conn.execute(facts.insert(), [dict(data_version=version, id=f.id, stock_code=f.stock_code,
                year=f.year, period=f.period, metric=f.metric, scope=f.scope,
                status=f.status, payload=f.model_dump(mode='json')) for f in fact_list[start:start + 250]])


def publish_release(engine, version: str, acceptance: dict, project=None):
    """作品说明：以事务同时发布已验收事实和索引，失败时继续使用上一发布指针。"""
    if not all(acceptance.get(k) is True for k in ('facts_verified', 'reports_verified', 'index_verified', 'projections_verified')):
        raise ValueError('Release acceptance is incomplete')
    with engine.begin() as conn:
        release = conn.execute(select(releases).where(releases.c.version == version).with_for_update()).mappings().one()
        if project is not None:
            project(conn)
        manifest = {**json_payload(release['manifest']), 'acceptance': acceptance}
        conn.execute(releases.update().where(releases.c.version == version).values(status='accepted', manifest=manifest))
        conn.execute(pointer.delete().where(pointer.c.name == 'serving'))
        conn.execute(pointer.insert().values(name='serving', version=version))
