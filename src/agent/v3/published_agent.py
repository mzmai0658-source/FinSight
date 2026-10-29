"""每轮读取已发布目录并固定版本，新公司无需重启，未审核候选不可进入目录。"""
from __future__ import annotations

import asyncio
from threading import Lock
from sqlalchemy.exc import SQLAlchemyError

from .agent import V3Agent
from .model import AsyncModel
from .repository import CanonicalRepository


class PublishedAgent:
    def __init__(self,engine,*,model=None,repository_factory=CanonicalRepository,agent_factory=V3Agent):
        self.engine=engine
        self.model=model or AsyncModel()
        self.repository_factory=repository_factory
        self.agent_factory=agent_factory
        self._companies={}
        self._lock=Lock()

    def _snapshot(self):
        try:
            repository=self.repository_factory(self.engine)
            reports=repository.report_catalog()
        except SQLAlchemyError:
            with self._lock:return None,dict(self._companies)
        companies={}
        for report in reports:
            code,name=report['stock_code'],report['company']
            if companies.setdefault(code,name)!=name:
                raise ValueError('Published company identity is ambiguous')
        with self._lock:self._companies=dict(companies)
        return repository,companies

    @property
    def companies(self):return self._snapshot()[1]

    async def run(self,question,history,turn_id,deadline,emit):
        repository,companies=await asyncio.to_thread(self._snapshot)
        def pinned(engine,version=None):
            if repository is not None and (version is None or version==repository.version):return repository
            return self.repository_factory(engine,version=version)
        agent=self.agent_factory(self.engine,companies,model=self.model,repository_factory=pinned)
        return await agent.run(question,history,turn_id,deadline,emit)
