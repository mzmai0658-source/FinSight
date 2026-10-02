import asyncio,threading
import pytest
from src.agent.model_admission import ModelAdmission,AdmissionFailure

def test_async_cancellation_removes_queued_claim_without_leaking_slot():
    async def run():
        gate=ModelAdmission();await gate.enter(2)
        queued=asyncio.create_task(gate.enter(2));await asyncio.sleep(.03)
        assert gate.snapshot()=={'active':1,'waiting':1}
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):await queued
        assert gate.snapshot()=={'active':1,'waiting':0}
        gate.leave();await gate.enter(2);gate.leave()
        assert gate.snapshot()=={'active':0,'waiting':0}
    asyncio.run(run())

def test_sync_etl_and_async_chat_share_the_same_model_slot():
    async def run():
        gate=ModelAdmission();ready=threading.Event();release=threading.Event()
        def etl():
            with gate.synchronous():
                with gate.synchronous():ready.set();release.wait(2)
        worker=threading.Thread(target=etl);worker.start()
        await asyncio.to_thread(ready.wait,1)
        chat=asyncio.create_task(gate.enter(2));await asyncio.sleep(.05)
        assert not chat.done() and gate.snapshot()['active']==1
        release.set();await chat;gate.leave();worker.join(2)
        assert gate.snapshot()=={'active':0,'waiting':0}
    asyncio.run(run())

def test_queue_admits_eight_waiters_and_rejects_the_ninth():
    async def run():
        gate=ModelAdmission();await gate.enter(2)
        waiters=[asyncio.create_task(gate.enter(2)) for _ in range(8)]
        await asyncio.sleep(.03)
        with pytest.raises(AdmissionFailure,match='model_queue_full'):await gate.enter(2)
        for waiter in waiters:waiter.cancel()
        await asyncio.gather(*waiters,return_exceptions=True);gate.leave()
        assert gate.snapshot()['waiting']==0
    asyncio.run(run())
