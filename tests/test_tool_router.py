from typing import Any, Dict, List

from src.agent.orchestrator import AgentEvent
from src.agent.tool_router import ToolRouter


class DummyState:
    def __init__(self):
        self.tools_used = False


def test_tool_router_dispatches_registered_handler():
    calls: List[Dict[str, Any]] = []

    def handler(args, state):
        calls.append(args)
        state.tools_used = True
        return [AgentEvent("tool_call", {"tool": "query_database"})], {"status": "success"}

    router = ToolRouter({"query_database": handler}, AgentEvent)
    state = DummyState()

    events, payload = router.dispatch("query_database", {"sql": "SELECT 1"}, state)

    assert calls == [{"sql": "SELECT 1"}]
    assert events[0].type == "tool_call"
    assert payload == {"status": "success"}
    assert state.tools_used is True


def test_tool_router_unknown_tool_returns_error_payload():
    router = ToolRouter({}, AgentEvent)

    events, payload = router.dispatch("drop_database", {}, DummyState())

    assert events[0].type == "tool_result"
    assert events[0].data["tool"] == "drop_database"
    assert events[0].data["status"] == "error"
    assert payload["status"] == "error"
    assert "drop_database" in payload["message"]


def test_tool_router_converts_handler_exception_to_tool_result():
    def broken_handler(args, state):
        raise ValueError("bad y_data")

    state = DummyState()
    router = ToolRouter({"render_chart": broken_handler}, AgentEvent)

    events, payload = router.dispatch("render_chart", {"y_data": [None]}, state)

    assert [event.type for event in events] == ["tool_call", "tool_result"]
    assert events[-1].data["status"] == "error"
    assert "bad y_data" in events[-1].data["summary"]
    assert payload["status"] == "error"
    assert "bad y_data" in payload["message"]
    assert state.tools_used is True
