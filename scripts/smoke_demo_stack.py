# -*- coding: utf-8 -*-
"""作品说明：通过 Java 对外接口验收真实财报演示链路。

默认问题覆盖单值、跨年比较和数据库字段缺失三种情况。脚本只从私有
env 文件读取账号密码，不打印凭据或 token。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


ROOT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DemoCase:
    question: str
    expected_fragments: tuple[str, ...]


DEFAULT_CASES = (
    DemoCase("万邦德2024年营业收入是多少？", ("144,336.52",)),
    DemoCase(
        "葵花药业2022年至2024年营业收入分别是多少？",
        ("509,451.13", "570,028.67", "337,704.77"),
    ),
    DemoCase(
        "盘龙药业2023年第一季度总负债是多少？",
        ("为空或未披露",),
    ),
)


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def iter_sse(response: requests.Response) -> Iterable[tuple[str, dict[str, Any]]]:
    """作品说明：兼容标准空行分隔和连续 event 行的 SSE 输出。"""

    event_name = ""
    data_lines: list[str] = []

    def emit() -> tuple[str, dict[str, Any]] | None:
        nonlocal event_name, data_lines
        if not event_name or not data_lines:
            event_name = ""
            data_lines = []
            return None
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            payload = {}
        result = (event_name, payload)
        event_name = ""
        data_lines = []
        return result

    for raw_line in response.iter_lines(decode_unicode=False):
        line = raw_line.decode("utf-8", errors="replace")
        if line.startswith("event:"):
            pending = emit()
            if pending is not None:
                yield pending
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
        elif not line:
            pending = emit()
            if pending is not None:
                yield pending

    pending = emit()
    if pending is not None:
        yield pending


def login(session: requests.Session, base_url: str, env: dict[str, str], timeout: float) -> str:
    username = env.get("ADMIN_USERNAME", "")
    password = env.get("ADMIN_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("env 文件缺少 ADMIN_USERNAME 或 ADMIN_PASSWORD")
    response = session.post(
        f"{base_url}/api/auth/login",
        json={"username": username, "password": password},
        timeout=timeout,
    )
    response.raise_for_status()
    envelope = response.json()
    if envelope.get("code") != 0:
        raise RuntimeError(f"登录失败：{envelope.get('message', '未知错误')}")
    return str(envelope["data"]["accessToken"])


def ask(
    session: requests.Session,
    base_url: str,
    token: str,
    case: DemoCase,
    timeout: float,
) -> bool:
    started = time.perf_counter()
    response = session.post(
        f"{base_url}/api/chat/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "question": case.question,
            "clientRequestId": f"demo-smoke-{uuid.uuid4().hex}",
        },
        timeout=(10, timeout),
        stream=True,
    )
    response.raise_for_status()

    done: dict[str, Any] | None = None
    terminal_error = ""
    for event_name, payload in iter_sse(response):
        if event_name == "done":
            done = payload
        elif event_name == "error" and payload.get("terminal"):
            terminal_error = str(payload.get("message", "未知错误"))

    elapsed = time.perf_counter() - started
    if done is None:
        print(f"[FAIL] {case.question}\n  未收到 done 事件；{terminal_error or '连接提前结束'}")
        return False

    result = done.get("result") or {}
    answer = str((result.get("answer") or {}).get("content") or "")
    validation = str((result.get("validation") or {}).get("status") or "unknown")
    verification = str((result.get("verification") or {}).get("status") or "unknown")
    facts = result.get("facts") or []
    fragments_ok = all(fragment in answer for fragment in case.expected_fragments)
    persistence_ok = done.get("persistence_status") == "saved"
    passed = fragments_ok and persistence_ok and not result.get("error")

    print(f"[{'PASS' if passed else 'FAIL'}] {case.question}")
    print(f"  回答：{answer}")
    print(
        f"  耗时：{elapsed:.1f}s；validation={validation}；"
        f"verification={verification}；facts={len(facts)}；已保存={persistence_ok}"
    )
    if not fragments_ok:
        print(f"  缺少预期片段：{list(case.expected_fragments)}")
    return passed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验收真实财报 10 公司演示链路")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--env-file", type=Path, default=ROOT_DIR / ".env.integration")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--question",
        action="append",
        help="自定义问题；可重复传入。自定义问题只检查是否完整返回和保存。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env = load_env_file(args.env_file.resolve())
    cases = (
        tuple(DemoCase(question, ()) for question in args.question)
        if args.question
        else DEFAULT_CASES
    )

    with requests.Session() as session:
        token = login(session, args.base_url.rstrip("/"), env, args.timeout)
        outcomes = [
            ask(session, args.base_url.rstrip("/"), token, case, args.timeout)
            for case in cases
        ]
    passed = sum(outcomes)
    print(f"结果：{passed}/{len(outcomes)} 通过")
    return 0 if all(outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
