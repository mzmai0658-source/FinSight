import asyncio
import time

from src.agent.v3.tasks import TaskConflict, TaskManager, TaskStore


class ControllableAgent:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.published = 0

    async def run(self, question, history, id, deadline, emit):
        self.started.set()
        await emit('plan', {'label': 'started'})
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.published += 1
        return {'version':3, 'task_id':id, 'answer':{'content':'verified result'}}


def test_stop_cancels_invocation_and_releases_worker(tmp_path):
    async def scenario():
        agent=ControllableAgent(); manager=TaskManager(agent,TaskStore(tmp_path/'tasks.db'))
        await manager.submit('t1','c1','s','q',[],time.time()+270)
        await agent.started.wait()
        stopped=await manager.cancel('t1','s')
        assert stopped['status']=='cancelled'
        assert agent.cancelled and agent.published==0
        assert not manager.capacity.locked()
        events=[e async for e in manager.stream('t1')]
        assert len([e for e in events if e.type=='done'])==1
        assert events[-1].data['result']['task_status']=='cancelled'
        agent.release.set()
        await manager.submit('t2','c2','s','q2',[],time.time()+270)
        events=[e async for e in manager.stream('t2')]
        assert events[-1].data['result']['task_id']=='t2'
        await manager.close()
    asyncio.run(scenario())


def test_disconnect_does_not_cancel_and_duplicate_submission_recovers(tmp_path):
    async def scenario():
        agent=ControllableAgent(); manager=TaskManager(agent,TaskStore(tmp_path/'tasks.db'))
        await manager.submit('t1','c1','s','q',[],time.time()+270)
        stream=manager.stream('t1')
        first=await anext(stream);assert first.type=='plan'
        await stream.aclose()
        duplicate=await manager.submit('new-id','c1','s','q',[],time.time()+270)
        assert duplicate['id']=='t1'
        assert not agent.cancelled
        agent.release.set()
        events=[e async for e in manager.stream('t1')]
        assert events[-1].data['result']['answer']['content']=='verified result'
        assert manager.store.get('t1')['status']=='completed'
        assert agent.published==1
        assert (await manager.cancel('t1','s'))['status']=='completed'
        await manager.close()
    asyncio.run(scenario())


def test_same_session_conflict_and_cancel_ownership(tmp_path):
    async def scenario():
        agent=ControllableAgent();manager=TaskManager(agent,TaskStore(tmp_path/'tasks.db'))
        await manager.submit('t1','c1','s','q',[],time.time()+270)
        try:
            await manager.submit('t2','c2','s','q',[],time.time()+270)
            assert False, 'same session should conflict'
        except TaskConflict:
            pass
        try:
            await manager.cancel('t1','another-session')
            assert False, 'another session should not own the task'
        except KeyError:
            pass
        await manager.cancel('t1','s')
        await manager.close()
    asyncio.run(scenario())


def test_restart_explicitly_ends_orphaned_tasks(tmp_path):
    path=tmp_path/'tasks.db';store=TaskStore(path)
    store.create('t','c','s',time.time()+270)
    store.transition('t',['queued'],'running')
    reopened=TaskStore(path);reopened.recover_restart()
    assert reopened.get('t')['status']=='failed'
    assert reopened.get('t')['error']=='worker_restarted'
    assert not reopened.transition('t',['running'],'completed',result={'fake':'late'})


def test_queue_deadline_and_execution_timeout_are_distinct(tmp_path):
    async def scenario():
        agent=ControllableAgent();manager=TaskManager(agent,TaskStore(tmp_path/'queue.db'))
        await manager.submit('active','c1','s1','q',[],time.time()+270)
        await agent.started.wait()
        await manager.submit('waiting','c2','s2','q',[],time.time()+.02)
        events=[event async for event in manager.stream('waiting')]
        assert manager.store.get('waiting')['error']=='model_queue_timeout'
        assert '排队' in events[-1].data['result']['answer']['content']
        assert agent.published==0
        await manager.cancel('active','s1');await manager.close()
        class TimedOutAgent:
            async def run(self,*args):raise TimeoutError()
        manager=TaskManager(TimedOutAgent(),TaskStore(tmp_path/'execution.db'))
        await manager.submit('timed','c3','s3','q',[],time.time()+270)
        events=[event async for event in manager.stream('timed')]
        assert manager.store.get('timed')['error']=='turn_timeout'
        assert '执行' in events[-1].data['result']['answer']['content']
        assert not manager.capacity.locked()
        await manager.close()
    asyncio.run(scenario())


def test_validation_exception_objects_cannot_strand_failure_or_expose_driver_text(tmp_path):
    from src.agent.v3.state import InvalidUnderstanding
    class FaultyAgent:
        async def run(self,question,history,id,deadline,emit):
            if question=='bad':
                error=InvalidUnderstanding('bad condition')
                error.audit={'details':[{'ctx':{'error':ValueError('private-driver-password')}}]}
                raise error
            return {'version':3,'task_id':id,'answer':{'content':'next query'}}
    async def scenario():
        manager=TaskManager(FaultyAgent(),TaskStore(tmp_path/'tasks.db'))
        await manager.submit('bad','c1','s','bad',[],time.time()+270)
        events=[event async for event in manager.stream('bad')]
        assert manager.store.get('bad')['status']=='failed'
        assert len([event for event in events if event.type=='done'])==1
        assert 'private-driver-password' not in str(events[-1].data)
        assert not manager.runners and not manager.capacity.locked()
        await manager.submit('next','c2','s','good',[],time.time()+270)
        events=[event async for event in manager.stream('next')]
        assert events[-1].data['result']['answer']['content']=='next query'
        await manager.close()
    asyncio.run(scenario())
