import time
import pytest
from src.etl.submission_store import SubmissionStore,SubmissionBusy


def test_long_synchronous_work_keeps_candidate_owned(tmp_path):
    from src.etl.submission_store import LeaseKeeper
    store=SubmissionStore(tmp_path/'s.db');id='a'*64
    store.register(id,{});token=store.claim(id,ttl=.15)
    with LeaseKeeper(lambda:store.heartbeat(id,token,ttl=.15),interval=.025):
        time.sleep(.3)
        with pytest.raises(SubmissionBusy):store.claim(id)
        store.save(id,store.get(id),token=token)
    store.release(id,token)


def test_lost_lease_is_reported_before_next_stage():
    from src.etl.submission_store import LeaseKeeper
    state={'failed':False}
    def pulse():
        if state['failed']:raise SubmissionBusy('lost')
    with LeaseKeeper(pulse,interval=.01) as keeper:
        state['failed']=True;time.sleep(.06)
        with pytest.raises(SubmissionBusy):keeper.pulse()
