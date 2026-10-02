"""作品说明：统一控制异步聊天、同步提取及标题任务的进程级模型并发。"""
from __future__ import annotations
import asyncio,contextlib,functools,inspect,threading,time

class AdmissionFailure(RuntimeError):
    def __init__(self,reason):self.reason=reason;super().__init__(reason)

class ModelAdmission:
    def __init__(self):
        self.condition=threading.Condition();self.queue=[];self.active=False;self.local=threading.local()
    def _register(self):
        with self.condition:
            if self.active and len(self.queue)>=8 or len(self.queue)>=9:
                raise AdmissionFailure('model_queue_full')
            token=object();self.queue.append(token);return token
    def _take(self,token):
        with self.condition:
            if not self.active and self.queue and self.queue[0] is token:
                self.queue.pop(0);self.active=True;return True
            return False
    def _remove(self,token):
        with self.condition:
            if token in self.queue:self.queue.remove(token)
            self.condition.notify_all()
    async def enter(self,remaining):
        token=self._register();deadline=time.monotonic()+min(30,remaining)
        try:
            while not self._take(token):
                if time.monotonic()>=deadline:raise AdmissionFailure('model_queue_timeout')
                await asyncio.sleep(min(.025,max(.001,deadline-time.monotonic())))
        except BaseException:self._remove(token);raise
    def leave(self):
        with self.condition:
            self.active=False;self.condition.notify_all()
    @contextlib.contextmanager
    def synchronous(self):
        if getattr(self.local,'depth',0):
            self.local.depth+=1
            try:yield
            finally:self.local.depth-=1
            return
        token=self._register();deadline=time.monotonic()+30;acquired=False
        try:
            with self.condition:
                while not self._take(token):
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise AdmissionFailure('model_queue_timeout')
                    self.condition.wait(remaining)
            acquired=True;self.local.depth=1
            yield
        finally:
            self.local.depth=0
            if acquired:self.leave()
            else:self._remove(token)
    def snapshot(self):
        with self.condition:return {'active':int(self.active),'waiting':len(self.queue)}

SCHEDULER=ModelAdmission()

def admitted(fn):
    if inspect.isgeneratorfunction(fn):
        @functools.wraps(fn)
        def generator(*args,**kwargs):
            with SCHEDULER.synchronous():yield from fn(*args,**kwargs)
        return generator
    @functools.wraps(fn)
    def call(*args,**kwargs):
        with SCHEDULER.synchronous():return fn(*args,**kwargs)
    return call
