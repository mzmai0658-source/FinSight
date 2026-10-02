"""作品说明：每轮预算与有界诊断，不记录提示词或内部思考。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from loguru import logger


class AgentFailure(ValueError):
    def __init__(self, code, stage, detail, *, retryable=False):
        super().__init__(detail)
        self.code = code
        self.stage = stage
        self.retryable = retryable


@dataclass
class TurnRuntime:
    id: str = field(default_factory=lambda: uuid4().hex)
    started: float = field(default_factory=time.monotonic)
    seconds: float = 240
    max_calls: int = 4
    calls: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def remaining(self):
        return self.seconds - (time.monotonic() - self.started)

    def start_call(self, schema):
        if self.remaining() <= 2 or len(self.calls) >= self.max_calls:
            record_failure('turn_budget_exhausted',schema,'本轮推理时间或调用次数已达上限')
            raise AgentFailure('turn_budget_exhausted', schema, '本轮推理时间或调用次数已达上限')
        record = {'schema': schema, 'status': 'started', 'sequence': len(self.calls) + 1}
        self.calls.append(record)
        return record

    def summary(self):
        return {'id': self.id, 'elapsed_seconds': round(time.monotonic()-self.started, 3),
                'limits': {'seconds': self.seconds, 'model_calls': self.max_calls},
                'model_calls': self.calls, 'errors': self.errors}


_current = ContextVar('agent_turn_runtime', default=None)


def runtime():
    return _current.get()


def record_failure(code, stage, detail, **extra):
    record = {'code': code, 'stage': stage, 'detail': str(detail)[:700], **extra}
    current = runtime()
    if current:
        current.errors.append(record)
    logger.warning('Agent failure turn={} stage={} code={} detail={}',
                   current.id if current else '-', stage, code, record['detail'])
    return record


@contextmanager
def turn_runtime():
    # 作品说明：执行预算低于 Java 超时，为本轮一次修正留出时间。
    seconds = min(240, max(30, float(os.getenv('AGENT_TURN_TIMEOUT_SECONDS', '240'))))
    current = TurnRuntime(seconds=seconds)
    token = _current.set(current)
    try:
        yield current
    finally:
        if current.errors:
            try:
                folder = Path(__file__).resolve().parents[2] / 'data/runtime/agent_diagnostics'
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f'{current.id}.json').write_text(
                    json.dumps(current.summary(), ensure_ascii=False, indent=2), encoding='utf-8')
            except OSError:
                logger.warning('Unable to persist agent diagnostics turn={}', current.id)
        _current.reset(token)
