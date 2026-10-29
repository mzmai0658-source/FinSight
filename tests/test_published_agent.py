import asyncio
from types import SimpleNamespace
import pytest

from src.agent.v3.published_agent import PublishedAgent


def test_new_published_company_is_seen_but_inflight_turn_keeps_its_version():
    asyncio.run(_catalog_race())


async def _catalog_race():
    serving={'version':'old','reports':[dict(stock_code='000789',company='原企业')]}
    entered=asyncio.Event();resume=asyncio.Event()
    def repository(engine,version=None):
        snapshot=SimpleNamespace(version=version or serving['version'],rows=list(serving['reports']))
        snapshot.report_catalog=lambda:list(snapshot.rows)
        return snapshot
    class Agent:
        def __init__(self,engine,companies,**kw):self.companies=companies;self.repo=kw['repository_factory']
        async def run(self,*args):
            if args[2]=='first':entered.set();await resume.wait()
            return self.companies,self.repo(None).version
    agent=PublishedAgent(None,model=object(),repository_factory=repository,agent_factory=Agent)
    first=asyncio.create_task(agent.run('q',[],'first',0,None))
    await entered.wait()
    serving.update(version='new',reports=serving['reports']+[dict(stock_code='000790',company='新增企业')])
    second=await agent.run('q',[],'second',0,None)
    resume.set();before=await first
    assert before==({'000789':'原企业'},'old')
    assert second==({'000789':'原企业','000790':'新增企业'},'new')
