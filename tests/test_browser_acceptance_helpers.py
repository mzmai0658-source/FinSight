"""作品说明：浏览器验收必须核对本次创建的准确 Java 会话。"""

from eval.browser_acceptance import (
    Browser,
    failure_answer_is_clear,
    generation_feedback_observed,
    java_reports_upstream_unavailable,
    persisted_turn_checks,
)


SCENARIOS = [
    ("万邦德营收", "answered", False, False),
    ("那净利润呢", "partial", True, False),
]


def message(role, content, status=None):
    metadata = {"outcome": {"status": status}} if status else None
    return {"role": role, "content": content, "metadata": metadata}


def test_persistence_checks_exact_user_turns_and_assistant_statuses():
    messages = [
        message("user", "万邦德营收"),
        message("assistant", "2024年收入为…", "answered"),
        message("user", "那净利润呢"),
        message("assistant", "部分年份没有值", "partial"),
    ]
    assert persisted_turn_checks(messages, SCENARIOS)["passed"]
    wrong_history = messages.copy()
    wrong_history[2] = message("user", "换一家呢")
    assert not persisted_turn_checks(wrong_history, SCENARIOS)["passed"]
    wrong_status = messages.copy()
    wrong_status[3] = message("assistant", "部分年份没有值", "answered")
    assert not persisted_turn_checks(wrong_status, SCENARIOS)["passed"]
    assert not persisted_turn_checks(messages[:-1], SCENARIOS)["passed"]


def test_live_clarification_is_not_treated_as_a_completed_turn():
    class View:
        def __init__(self):
            self.states = iter([
                {"count": 1, "status": "", "pending": True, "error": ""},
                {"count": 1, "status": "需要补充条件", "pending": False, "error": ""},
            ])

        def evaluate(self, _expression):
            return next(self.states)

    assert Browser.wait_for_answer(View(), previous_assistants=0, timeout=1)


def test_generation_feedback_is_required_and_detected_before_assistant_appears():
    assert not generation_feedback_observed([{"pending_seen": False} for _ in range(12)])
    assert generation_feedback_observed([{"pending_seen": False}, {"pending_seen": True}])

    class View:
        def __init__(self):
            self.states = iter([
                {"count": 0, "status": "", "pending": True, "error": ""},
                {"count": 1, "status": "已完成", "pending": False, "error": ""},
            ])

        def evaluate(self, _expression):
            return next(self.states)

    assert Browser.wait_for_answer(View(), previous_assistants=0, timeout=1)


def test_failure_preflight_requires_javas_exact_upstream_fallback():
    fallback = {
        "code": 0,
        "data": {name: {"ok": False, "detail": "AI 微服务暂不可达"}
                 for name in ("service", "database", "knowledge_base", "llm", "examples")},
    }
    assert java_reports_upstream_unavailable(fallback)
    healthy_python = {"code": 0, "data": {**fallback["data"], "service": {"ok": True}}}
    assert not java_reports_upstream_unavailable(healthy_python)
    partial_python_health = {"code": 0, "data": {**fallback["data"], "service": {
        "ok": False, "detail": "内部依赖异常",
    }}}
    assert not java_reports_upstream_unavailable(partial_python_health)


def test_failure_explanation_cannot_claim_absent_data():
    assert failure_answer_is_clear("AI 分析服务连接失败，本轮未完成，请重试。")
    assert not failure_answer_is_clear("数据库没有万邦德的数据，请重试。")
    assert not failure_answer_is_clear("数据库中没有万邦德的数据，请重试。")
    assert not failure_answer_is_clear("没有可用数据。")
