"""作品说明：通过隔离 Chrome 调试会话检查工作区页面的真实渲染。"""
from __future__ import annotations

import argparse
import atexit
import base64
import json
import os
from pathlib import Path
import sys
import time
import uuid

import requests
import websocket
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_SESSION_KEY = "finsight-active-session"
OUTCOME_LABELS = {
    "answered": "已完成", "partial": "部分结果可用", "no_data": "没有可用数据",
    "query_failed": "本轮查询失败", "needs_clarification": "需要补充条件",
    "unsupported": "暂不支持该请求",
}
FAILURE_QUESTION = "万邦德2024年营业收入多少"
UNAVAILABLE_DETAIL = "AI 微服务暂不可达"
ABSENCE_CLAIMS = (
    "数据库没有", "数据库中没有", "数据库里没有", "数据库尚未收录",
    "当前库中无", "当前查询范围内无", "财报未披露",
    "没有可用数据", "无可用数据", "无记录",
)

SCENARIOS = [
    ("万邦德2024年营业收入多少", "answered", False, False),
    ("那净利润呢", "answered", False, False),
    ("换盘龙药业呢", "answered", False, False),
    ("凯莱英2023和2024年主要指标列个表", "partial", True, False),
    ("同仁堂最新三年的营收画条线", "answered", True, True),
    ("凯莱英2024年全年营收多少", "no_data", False, False),
    ("万邦德24年营业收入同比多少", "answered", True, False),
    ("万邦德24年现金流怎么样", "needs_clarification", False, False),
    ("库里有哪些公司的财报", "answered", True, False),
    ("同仁堂24年营收和毛利率", "answered", True, False),
    ("同仁堂2024年第四季度营收", "unsupported", False, False),
    ("万邦德24年营收多少，原文出处在哪", "answered", False, False),
]

SEMANTIC_SCENARIOS = [
    ('金花股份2023年上半年经营活动产生的现金流量净额是多少？请用万元表示。','answered',False,False),
    ('你库里面有哪些公司的财报','answered',True,False),
    ('同仁堂最近三年的主要指标是多少，给我画个图','answered',True,True),
    ('你需要满足什么条件才能画图','answered',False,False),
    ('不是，我是在问你问题，你生成图表干嘛','answered',False,False),
    ('万邦德22和24年营业收入画图，中间没有本轮数据就空着','answered',True,True),
    ('凯莱英23和24年主要指标列个表，不画图','partial',True,False),
    ('为什么不补齐24年的数，先解释别查库','answered',False,False),
    ('那看它23年收入','answered',False,False),
    ('改查金花股份2023年上半年经营活动现金流量净额','answered',False,False),
    ('同仁堂2024年第四季度营业收入','unsupported',False,False),
    ('为什么单季度不能直接用前三季度那个数，讲讲道理','answered',False,False),
]
SEMANTIC_HELP_TURNS = {4,5,8,12}
DIALOGUE_V2_SCENARIOS = [*SEMANTIC_SCENARIOS,
    ('今天吃什么','unsupported',False,False),
    ('1','needs_clarification',False,False),
    ('你能干什么','answered',False,False),
    ('你数据库里有哪些公司','answered',True,False),
    ('不是我是想问你你有哪些公司的数据','answered',True,False),
    ('解释ROE，再查同仁堂24年的ROE','answered',False,False),
]


class Browser:
    def __init__(self, debugger="http://127.0.0.1:19225"):
        self.debugger = debugger
        response = requests.put(debugger + "/json/new?http://127.0.0.1:5176/", timeout=10)
        response.raise_for_status()
        target = response.json()
        self.target_id = target["id"]
        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"], origin="http://localhost:19225", timeout=30)
        self.counter = 0

    def close(self):
        self.ws.close()
        try:
            requests.get(f"{self.debugger}/json/close/{self.target_id}", timeout=5)
        except requests.RequestException:
            pass

    def call(self, method, params=None):
        self.counter += 1
        self.ws.send(json.dumps({"id": self.counter, "method": method, "params": params or {}}))
        while True:
            data = json.loads(self.ws.recv())
            if data.get("id") == self.counter:
                if data.get("error"):
                    raise RuntimeError(f"Chrome DevTools command failed: {method}")
                return data.get("result") or {}

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            # 作品说明：初始化表达式含认证令牌，错误信息不得包含该表达式。
            raise RuntimeError("Browser JavaScript evaluation failed")
        return (result.get("result") or {}).get("value")

    def reload(self):
        marker = str(uuid.uuid4())
        self.evaluate(f"window.__acceptanceReload = {json.dumps(marker)}; location.reload(); true")
        self.wait_for(f"document.readyState === 'complete' && window.__acceptanceReload !== {json.dumps(marker)}", timeout=30)

    def navigate(self, url):
        self.wait_for("document.readyState === 'complete'", timeout=30)
        marker = str(uuid.uuid4())
        self.evaluate(f"window.__browserAcceptanceNavigationToken = {json.dumps(marker)}")
        self.call("Page.navigate", {"url": url})
        self.wait_for(
            f"location.href.startsWith({json.dumps(url)}) && document.readyState === 'complete'"
            f" && window.__browserAcceptanceNavigationToken !== {json.dumps(marker)}",
            timeout=30,
        )

    def wait_for(self, expression, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                value = self.evaluate(expression)
            except RuntimeError:
                # 作品说明：页面跳转期间可能短暂丢弃上一份 JavaScript 执行上下文。
                value = False
            if value:
                return value
            time.sleep(.25)
        raise TimeoutError("Browser condition did not become true before timeout")

    def wait_for_answer(self, previous_assistants, timeout=900):
        deadline = time.monotonic() + timeout
        pending_seen = False
        while time.monotonic() < deadline:
            state = self.evaluate("""(() => {
                const assistants = [...document.querySelectorAll('.msg--assistant')];
                const last = assistants.at(-1);
                return {
                    count: assistants.length,
                    status: last?.querySelector('.msg__outcome')?.textContent?.trim() || '',
                    pending: !!document.querySelector('.generation-status'),
                    error: document.querySelector('.ws__error')?.textContent?.trim() || ''
                };
            })()""")
            if state["error"]:
                raise RuntimeError(f"Workspace rejected the question: {state['error']}")
            pending_seen |= state["pending"]
            if state["count"] > previous_assistants:
                if state["status"] and not state["pending"]:
                    return pending_seen
            time.sleep(.25)
        raise TimeoutError("No completed answer with a result-status badge within 240 seconds")

    def screenshot(self, path):
        result = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        path.write_bytes(base64.b64decode(result["data"]))


def api_data(path, access_token, method="get"):
    response = requests.request(method, "http://127.0.0.1:18080" + path,
                                headers={"Authorization": "Bearer " + access_token}, timeout=20)
    response.raise_for_status()
    envelope = response.json()
    if envelope.get("code") != 0:
        raise RuntimeError(f"Java API returned a business error for {path}")
    return envelope["data"]


def browser_access_token(browser):
    token = browser.evaluate("JSON.parse(localStorage.getItem('finsight-auth') || '{}').accessToken || ''")
    if not token:
        raise RuntimeError("Browser authentication was lost")
    return token


def persisted_turn_checks(messages, scenarios):
    turns = []
    for index, (question, expected_status, _table, _chart) in enumerate(scenarios):
        user = messages[2 * index] if len(messages) > 2 * index else {}
        assistant = messages[2 * index + 1] if len(messages) > 2 * index + 1 else {}
        actual_status = ((assistant.get("metadata") or {}).get("outcome") or {}).get("status")
        turns.append({
            "index": index + 1,
            "question_ok": user.get("role") == "user" and user.get("content") == question,
            "assistant_ok": assistant.get("role") == "assistant" and bool(assistant.get("content")),
            "expected_status": expected_status,
            "actual_status": actual_status,
            "status_ok": actual_status == expected_status,
        })
    expected_count = 2 * len(scenarios)
    return {
        "message_count": len(messages),
        "expected_message_count": expected_count,
        "turns": turns,
        "passed": len(messages) == expected_count and all(
            turn["question_ok"] and turn["assistant_ok"] and turn["status_ok"] for turn in turns
        ),
    }


def java_reports_upstream_unavailable(envelope):
    """作品说明：只有 Java 自身的异常分支能证明其配置的 Python 上游不可达。"""
    if not isinstance(envelope, dict) or envelope.get("code") != 0:
        return False
    health = envelope.get("data") or {}
    if not isinstance(health, dict):
        return False
    return all(
        isinstance(health.get(key), dict)
        and health[key].get("ok") is False
        and health[key].get("detail") == UNAVAILABLE_DETAIL
        for key in ("service", "database", "knowledge_base", "llm", "examples")
    )


def assert_java_upstream_unavailable():
    response = requests.get("http://127.0.0.1:18080/api/meta/health", timeout=30)
    response.raise_for_status()
    if not java_reports_upstream_unavailable(response.json()):
        raise RuntimeError(
            "Java still reaches the Python AI service; stop that service before --failure-scenario"
        )


def failure_answer_is_clear(text):
    return bool(text) and any(marker in text for marker in ("连接失败", "未完成", "请重试")) \
        and not any(claim in text for claim in ABSENCE_CLAIMS)


def generation_feedback_observed(scenarios):
    return any(item.get("pending_seen") for item in scenarios)


def run_scenarios(browser, output, turn_interval, semantic=False):
    report = {"scenarios": [], "passed": False}
    report_path = output / "checks.json"
    previous_uid = browser.evaluate(f"localStorage.getItem({json.dumps(ACTIVE_SESSION_KEY)}) || ''")
    try:
        browser.wait_for("Boolean(document.querySelector('.ws__topbar-new:not(:disabled)'))", timeout=30)
        browser.evaluate("document.querySelector('.ws__topbar-new').click()")
        browser.wait_for(
            f"localStorage.getItem({json.dumps(ACTIVE_SESSION_KEY)}) !== {json.dumps(previous_uid)}"
            " && document.querySelectorAll('.msg').length === 0"
            " && Boolean(document.querySelector('.ws__session--active'))",
            timeout=30,
        )
        session_uid = browser.evaluate(f"localStorage.getItem({json.dumps(ACTIVE_SESSION_KEY)}) || ''")
        if not session_uid:
            raise RuntimeError("New browser session has no UID")
        report["session_uid"] = session_uid
        last_sent_at = None
        for index, (question, expected_status, expect_table, expect_chart) in enumerate(SCENARIOS, 1):
            report["stage"] = f"scenario-{index:02}"
            if last_sent_at is not None:
                time.sleep(max(0.0, turn_interval - (time.monotonic() - last_sent_at)))
            prior = browser.evaluate("document.querySelectorAll('.msg--assistant').length")
            browser.evaluate(
                "(()=>{const e=document.querySelector('.chat-input__textarea');e.value="
                + json.dumps(question, ensure_ascii=False)
                + ";e.dispatchEvent(new Event('input',{bubbles:true}))})()"
            )
            send_button = ".chat-input__btn:not(.chat-input__btn--stop):not(:disabled)"
            browser.wait_for(f"Boolean(document.querySelector({json.dumps(send_button)}))")
            browser.evaluate(f"document.querySelector({json.dumps(send_button)}).click()")
            last_sent_at = time.monotonic()
            pending_seen = browser.wait_for_answer(prior)
            view = browser.evaluate("""(() => {
                const a = [...document.querySelectorAll('.msg--assistant')].at(-1);
                return {
                    status: a?.querySelector('.msg__outcome')?.textContent?.trim() || '',
                    text: a?.querySelector('.msg__content')?.textContent?.trim() || '',
                    table: !!a?.querySelector('.msg__content table'),
                    chart: !!a?.querySelector('.chart-card'),
                    sql_count: a?.querySelectorAll('.sql-card').length || 0,
                    verification_badge: !!a?.querySelector('.verification-badge'),
                    literal_newline: !!a?.querySelector('.msg__content')?.textContent?.includes('\\\\n'),
                    clarify_options: a?.querySelectorAll('.clarify__option').length || 0
                };
            })()""")
            view.update({
                "index": index,
                "question": question,
                "expected_status": expected_status,
                "pending_seen": pending_seen,
                "status_ok": view["status"] == OUTCOME_LABELS[expected_status],
                "text_ok": bool(view["text"]),
                "table_ok": not expect_table or view["table"],
                "chart_ok": not expect_chart or view["chart"],
                "clarify_ok": expected_status != "needs_clarification" or bool(view["text"]),
                "help_tools_ok": not semantic or index not in SEMANTIC_HELP_TURNS or (not view['chart'] and view['sql_count']==0 and not view['verification_badge']),
                "newline_ok": not view['literal_newline'],
            })
            report["scenarios"].append(view)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.screenshot(output / f"scenario-{index:02}.png")
            print(f"{index}/{len(SCENARIOS)} {expected_status}: {view['status']} table={view['table']} chart={view['chart']}", flush=True)

        report["stage"] = "refresh"
        browser.navigate("http://127.0.0.1:5176/workspace")
        browser.wait_for(
            f"localStorage.getItem({json.dumps(ACTIVE_SESSION_KEY)}) === {json.dumps(session_uid)}"
            f" && document.querySelectorAll('.msg').length === {2 * len(SCENARIOS)}",
            timeout=30,
        )
        report["refreshed_message_count"] = browser.evaluate("document.querySelectorAll('.msg').length")
        report["refresh_statuses"] = browser.evaluate(
            "[...document.querySelectorAll('.msg--assistant .msg__outcome')].map(e => e.textContent.trim())"
        )

        report["stage"] = "session-switch"
        original_index = browser.evaluate(
            "[...document.querySelectorAll('.ws__session')].findIndex(e => e.classList.contains('ws__session--active'))"
        )
        if original_index < 0:
            raise RuntimeError("Current session is missing from the sidebar")
        alternate_index = browser.evaluate(
            "[...document.querySelectorAll('.ws__session')].findIndex((e,i) => i !== "
            + str(original_index) + ")"
        )
        if alternate_index < 0:
            api_data("/api/chat/sessions", browser_access_token(browser), method="post")
            browser.navigate("http://127.0.0.1:5176/workspace")
            browser.wait_for("document.querySelectorAll('.ws__session').length >= 2", timeout=30)
            sessions = api_data("/api/chat/sessions", browser_access_token(browser))
            original_index = next(i for i, item in enumerate(sessions) if item["sessionUid"] == session_uid)
            alternate_index = next(i for i in range(len(sessions)) if i != original_index)
        browser.evaluate(f"document.querySelectorAll('.ws__session-title')[{alternate_index}].click()")
        browser.wait_for(
            f"localStorage.getItem({json.dumps(ACTIVE_SESSION_KEY)}) !== {json.dumps(session_uid)}",
            timeout=30,
        )
        browser.evaluate(f"document.querySelectorAll('.ws__session-title')[{original_index}].click()")
        browser.wait_for(
            f"localStorage.getItem({json.dumps(ACTIVE_SESSION_KEY)}) === {json.dumps(session_uid)}"
            f" && document.querySelectorAll('.msg').length === {2 * len(SCENARIOS)}",
            timeout=30,
        )
        report["switch_restored"] = True

        report["stage"] = "scroll"
        scroll = browser.evaluate("""(() => {
            const e = document.querySelector('.ws__scroll');
            e.scrollTop = 0;
            const top = e.scrollTop;
            e.scrollTop = e.scrollHeight;
            return {height: e.scrollHeight, viewport: e.clientHeight, top, bottom: e.scrollTop};
        })()""")
        report["scroll"] = scroll
        browser.screenshot(output / "refreshed-and-scrolled.png")

        report["stage"] = "java-persistence"
        detail = api_data(f"/api/chat/sessions/{session_uid}", browser_access_token(browser))
        report["persistence"] = persisted_turn_checks(detail.get("messages") or [], SCENARIOS)
        report["persistence"]["session_uid_ok"] = detail.get("sessionUid") == session_uid
        report["persistence"]["summary_count_ok"] = detail.get("messageCount") == 2 * len(SCENARIOS)
        if semantic:
            assistants=[m for m in detail.get('messages',[]) if m.get('role')=='assistant']
            report['semantic_state_saved']=all((m.get('metadata') or {}).get('dialogue_state',{}).get('version')==2 for m in assistants)
            report['answer_assessment_saved']=all(isinstance((m.get('metadata') or {}).get('answer_assessment'),dict) for m in assistants)
            report['help_kinds_saved']=all((assistants[i-1].get('metadata') or {}).get('response_kind')=='conversation' for i in SEMANTIC_HELP_TURNS)
        report["generation_feedback_observed"] = generation_feedback_observed(report["scenarios"])
        report["passed"] = (
            all(item["status_ok"] and item["text_ok"] and item["table_ok"]
                and item["chart_ok"] and item["clarify_ok"] and item['help_tools_ok'] and item['newline_ok'] for item in report["scenarios"])
            and report["generation_feedback_observed"]
            and report["refresh_statuses"] == [OUTCOME_LABELS[item[1]] for item in SCENARIOS]
            and report["switch_restored"]
            and scroll["height"] > scroll["viewport"]
            and scroll["bottom"] > scroll["top"]
            and abs(scroll["bottom"] - (scroll["height"] - scroll["viewport"])) <= 2
            and report["persistence"]["passed"]
            and report["persistence"]["session_uid_ok"]
            and report["persistence"]["summary_count_ok"]
            and (not semantic or report['semantic_state_saved'] and report['help_kinds_saved'] and report['answer_assessment_saved'])
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "passed": report["passed"],
            "refreshed_message_count": report["refreshed_message_count"],
            "switch_restored": report["switch_restored"],
            "scroll": scroll,
            "persisted_turns_ok": report["persistence"]["passed"],
        }, ensure_ascii=False))
        if not report["passed"]:
            raise RuntimeError(f"Browser acceptance failed; inspect {report_path}")
        return report
    except Exception as error:
        report["error_type"] = type(error).__name__
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise


def run_failure_scenario(browser, output):
    """作品说明：关闭实际配置的 Python 服务，验证 Java 的终态兜底。"""
    report = {"mode": "upstream_failure", "question": FAILURE_QUESTION, "passed": False}
    report_path = output / "checks.json"
    previous_uid = browser.evaluate(f"localStorage.getItem({json.dumps(ACTIVE_SESSION_KEY)}) || ''")
    try:
        report["stage"] = "new-session"
        browser.wait_for("Boolean(document.querySelector('.ws__topbar-new:not(:disabled)'))", timeout=30)
        browser.evaluate("document.querySelector('.ws__topbar-new').click()")
        browser.wait_for(
            f"localStorage.getItem({json.dumps(ACTIVE_SESSION_KEY)}) !== {json.dumps(previous_uid)}"
            " && document.querySelectorAll('.msg').length === 0"
            " && Boolean(document.querySelector('.ws__session--active'))",
            timeout=30,
        )
        session_uid = browser.evaluate(f"localStorage.getItem({json.dumps(ACTIVE_SESSION_KEY)}) || ''")
        if not session_uid:
            raise RuntimeError("New browser session has no UID")
        report["session_uid"] = session_uid

        report["stage"] = "terminal-failure"
        assert_java_upstream_unavailable()
        browser.evaluate(
            "(()=>{const e=document.querySelector('.chat-input__textarea');e.value="
            + json.dumps(FAILURE_QUESTION, ensure_ascii=False)
            + ";e.dispatchEvent(new Event('input',{bubbles:true}))})()"
        )
        send_button = ".chat-input__btn:not(.chat-input__btn--stop):not(:disabled)"
        browser.wait_for(f"Boolean(document.querySelector({json.dumps(send_button)}))")
        browser.evaluate(f"document.querySelector({json.dumps(send_button)}).click()")
        report["pending_seen"] = browser.wait_for_answer(0, timeout=120)
        view = browser.evaluate("""(() => {
            const a = [...document.querySelectorAll('.msg--assistant')].at(-1);
            return {
                status: a?.querySelector('.msg__outcome')?.textContent?.trim() || '',
                text: a?.querySelector('.msg__content')?.textContent?.trim() || '',
                error: a?.querySelector('.msg__error')?.textContent?.trim() || ''
            };
        })()""")
        report["browser"] = {
            **view,
            "status_ok": view["status"] == OUTCOME_LABELS["query_failed"],
            "explanation_ok": failure_answer_is_clear(view["text"]),
        }
        browser.screenshot(output / "failure-before-refresh.png")

        report["stage"] = "java-persistence"
        detail = api_data(f"/api/chat/sessions/{session_uid}", browser_access_token(browser))
        messages = detail.get("messages") or []
        assistant = messages[1] if len(messages) > 1 else {}
        metadata = assistant.get("metadata") or {}
        outcome = metadata.get("outcome") or {}
        report["persistence"] = persisted_turn_checks(
            messages, [(FAILURE_QUESTION, "query_failed", False, False)]
        )
        report["persistence"].update({
            "session_uid_ok": detail.get("sessionUid") == session_uid,
            "summary_count_ok": detail.get("messageCount") == 2,
            "upstream_reason_ok": "upstream_failure" in (outcome.get("reason_codes") or []),
            "verification_failed": (metadata.get("verification") or {}).get("status") == "fail",
            "no_facts": not (metadata.get("facts") or []),
            "explanation_ok": failure_answer_is_clear(assistant.get("content") or ""),
        })

        report["stage"] = "refresh"
        browser.navigate("http://127.0.0.1:5176/workspace")
        browser.wait_for(
            f"localStorage.getItem({json.dumps(ACTIVE_SESSION_KEY)}) === {json.dumps(session_uid)}"
            " && document.querySelectorAll('.msg').length === 2",
            timeout=30,
        )
        report["refreshed_status"] = browser.evaluate(
            "[...document.querySelectorAll('.msg--assistant .msg__outcome')].at(-1)?.textContent?.trim() || ''"
        )
        browser.screenshot(output / "failure-after-refresh.png")
        persisted = report["persistence"]
        report["passed"] = (
            report["browser"]["status_ok"] and report["browser"]["explanation_ok"]
            and persisted["passed"] and persisted["session_uid_ok"]
            and persisted["summary_count_ok"] and persisted["upstream_reason_ok"]
            and persisted["verification_failed"] and persisted["no_facts"]
            and persisted["explanation_ok"]
            and report["refreshed_status"] == OUTCOME_LABELS["query_failed"]
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "passed": report["passed"],
            "browser_status": view["status"],
            "persisted_status": outcome.get("status"),
            "refreshed_status": report["refreshed_status"],
        }, ensure_ascii=False))
        if not report["passed"]:
            raise RuntimeError(f"Upstream failure acceptance failed; inspect {report_path}")
        return report
    except Exception as error:
        report["error_type"] = type(error).__name__
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise


def main():
    global SCENARIOS, SEMANTIC_HELP_TURNS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--session-uid", default="")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scenarios", action="store_true", help="Send and verify 12 real workspace turns")
    mode.add_argument('--semantic-scenarios', action='store_true', help='Verify 12 turns including usage questions, corrections and chart gaps')
    mode.add_argument('--dialogue-v2-scenarios', action='store_true', help='Verify 18 turns plus refresh, session switching and Java persistence')
    mode.add_argument("--failure-scenario", action="store_true",
                      help="With Python offline, verify Java and UI terminal query_failed behavior")
    parser.add_argument("--turn-interval", type=float, default=12.5,
                        help="Minimum seconds between sends (Java allows six per minute)")
    args = parser.parse_args()
    if args.failure_scenario and args.session_uid:
        parser.error("--session-uid cannot be combined with --failure-scenario")
    if args.turn_interval < 10:
        parser.error("--turn-interval must be at least 10 seconds to respect the Java rate limit")
    args.output.mkdir(parents=True, exist_ok=True)
    load_dotenv(ROOT / ".env.integration", override=True)
    if args.failure_scenario:
        assert_java_upstream_unavailable()
    api = "http://127.0.0.1:18080"
    response = requests.post(api + "/api/auth/login", json={
        "username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]}, timeout=15)
    response.raise_for_status()
    envelope = response.json()
    if envelope.get("code") != 0:
        raise RuntimeError("Local Java login failed")
    pair = envelope["data"]
    browser = Browser()
    atexit.register(browser.close)
    browser.call("Page.enable")
    browser.call("Runtime.enable")
    browser.call("Emulation.setDeviceMetricsOverride", {"width": 1600, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    browser.navigate("http://127.0.0.1:5176/")
    stored = {key: pair[key] for key in ("accessToken", "refreshToken", "user")}
    browser.evaluate("localStorage.setItem('finsight-auth', " + json.dumps(json.dumps(stored, ensure_ascii=False)) + ")")
    remembered_uid = browser.evaluate(f"localStorage.getItem({json.dumps(ACTIVE_SESSION_KEY)}) || ''")
    initial_sessions = api_data("/api/chat/sessions", pair["accessToken"])
    browser.navigate("http://127.0.0.1:5176/workspace")
    browser.wait_for("Boolean(document.querySelector('.ws__scroll'))", timeout=30)
    browser.wait_for(f"document.querySelectorAll('.ws__session').length === {len(initial_sessions)}", timeout=30)
    if initial_sessions:
        target = next((item for item in initial_sessions if item["sessionUid"] == remembered_uid),
                      initial_sessions[0])
        browser.wait_for(
            f"localStorage.getItem({json.dumps(ACTIVE_SESSION_KEY)}) === {json.dumps(target['sessionUid'])}"
            f" && document.querySelectorAll('.msg').length === {target['messageCount']}",
            timeout=30,
        )
    else:
        time.sleep(0.5)
    if args.scenarios or args.semantic_scenarios or args.dialogue_v2_scenarios:
        if args.semantic_scenarios: SCENARIOS=SEMANTIC_SCENARIOS
        if args.dialogue_v2_scenarios:
            SCENARIOS=DIALOGUE_V2_SCENARIOS
            SEMANTIC_HELP_TURNS=SEMANTIC_HELP_TURNS | {13,14,15}
        run_scenarios(browser, args.output, args.turn_interval, semantic=args.semantic_scenarios or args.dialogue_v2_scenarios)
        return
    if args.failure_scenario:
        run_failure_scenario(browser, args.output)
        return
    if args.session_uid:
        sessions = api_data("/api/chat/sessions", browser_access_token(browser))
        index = next((i for i, item in enumerate(sessions) if item["sessionUid"] == args.session_uid), None)
        if index is None:
            raise RuntimeError("Requested session is not owned by this account")
        browser.wait_for(f"document.querySelectorAll('.ws__session').length > {index}")
        browser.evaluate(f"document.querySelectorAll('.ws__session-title')[{index}].click()")
        browser.wait_for(
            f"localStorage.getItem({json.dumps(ACTIVE_SESSION_KEY)}) === {json.dumps(args.session_uid)}",
            timeout=30,
        )
    checks = {
        "url": browser.evaluate("location.href"),
        "workspace_loaded": browser.evaluate("Boolean(document.querySelector('.ws__scroll'))"),
        "message_count": browser.evaluate("document.querySelectorAll('.msg').length"),
        "can_scroll": browser.evaluate("(()=>{const e=document.querySelector('.ws__scroll');return e && e.scrollHeight > e.clientHeight})()"),
        "status_badges": browser.evaluate("Array.from(document.querySelectorAll('.msg__outcome')).map(e=>e.textContent.trim())"),
    }
    browser.screenshot(args.output / "workspace.png")
    (args.output / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
