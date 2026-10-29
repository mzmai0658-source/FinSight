"""保留旧测试中独立的SQL只读边界与现行v3接口契约。"""
import json
import pytest
from src.agent.sql_guard import normalize_readonly_sql

class TestSqlGuard:
    def test_select_passes_and_gets_limit(self):
        clean, reason = normalize_readonly_sql("SELECT * FROM income_sheet WHERE report_year=2024")
        assert clean is not None and reason == ""
        assert clean.endswith("LIMIT 50")

    def test_oversized_limit_is_tightened(self):
        clean, _ = normalize_readonly_sql("SELECT net_profit FROM income_sheet LIMIT 99999")
        assert clean is not None
        assert "LIMIT 500" in clean

    @pytest.mark.parametrize("bad_sql", [
        "DROP TABLE income_sheet",
        "UPDATE income_sheet SET net_profit=0",
        "DELETE FROM income_sheet",
        "SELECT 1; SELECT 2",
        "SELECT * FROM information_schema.tables",
        "INSERT INTO income_sheet VALUES (1)",
    ])
    def test_write_and_multi_statement_rejected(self, bad_sql):
        clean, reason = normalize_readonly_sql(bad_sql)
        assert clean is None
        assert reason

    def test_unknown_table_rejected(self):
        clean, reason = normalize_readonly_sql("SELECT * FROM users")
        assert clean is None
        assert "users" in reason

    def test_cte_allowed(self):
        clean, _ = normalize_readonly_sql(
            "WITH t AS (SELECT stock_code FROM income_sheet) SELECT * FROM t"
        )
        assert clean is not None

    def test_string_literals_are_not_treated_as_dangerous_schema_names(self):
        clean, reason = normalize_readonly_sql("SELECT 'mysql' AS label FROM income_sheet")
        assert clean is not None, reason
        assert "LIMIT 50" in clean


class FakeAsyncAgent:
    def __init__(self): self.histories=[]
    async def run(self, question, history, turn_id, deadline, emit):
        self.histories.append(history)
        await emit("tool_call", {"tool":"query_database","label":"读取事实"})
        await emit("tool_result", {"tool":"query_database","status":"success"})
        return dict(version=3,task_id=turn_id,answer=dict(content="核验后的回答",references=[],image=[]),
                    sql="SELECT payload FROM financial_canonical_facts",needs_clarification=False,
                    execution_plan=[],verification=dict(status="pass"),evidence=[dict(id="query1",type="sql")])


@pytest.fixture()
def api_client(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    import src.api.main as api_main
    from src.agent.v3.tasks import TaskManager,TaskStore
    agent=FakeAsyncAgent()
    monkeypatch.setattr(api_main,"_agent_instance",agent)
    manager=TaskManager(agent,TaskStore(tmp_path/"tasks.sqlite"))
    monkeypatch.setattr(api_main,"_get_tasks",lambda:manager)
    token="test-internal-credential-0000000000000000"
    monkeypatch.setenv("INTERNAL_API_TOKEN",token)
    monkeypatch.setattr(api_main,"probe_knowledge",lambda path:(True,"mocked collection"))
    monkeypatch.setattr(api_main,"probe_llm",lambda:(True,"mocked model"))
    with TestClient(api_main.app,headers={"X-Internal-Token":token}) as client:
        yield client


def _parse_sse(raw: str):
    events = []
    for block in raw.strip().split("\n\n"):
        lines = block.split("\n")
        etype = next((l[len("event: "):] for l in lines if l.startswith("event: ")), "")
        data = next((l[len("data: "):] for l in lines if l.startswith("data: ")), "{}")
        events.append((etype, json.loads(data)))
    return events


class TestInternalApi:
    def test_stream_sse_event_order_and_done_contract(self, api_client):
        with api_client.stream(
            "POST",
            "/internal/chat/stream",
            json={"question": "药明康德2024年净利润是多少", "history": [], "session_uid": "abcd1234-0000-0000-0000-000000000000"},
            headers={"X-Request-Id": "trace-test-1"},
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            assert resp.headers.get("x-request-id") == "trace-test-1"
            raw = "".join(resp.iter_text())

        events = _parse_sse(raw)
        types = [item[0] for item in events]
        assert "tool_call" in types
        assert "tool_result" in types
        assert "answer_delta" not in types  # 财务正文仅随已核验的结束事件发布。
        assert types[-1] == "done"

        done_payload = events[-1][1]
        result = done_payload["result"]
        assert result["answer"]["content"]
        assert result["sql"] != "-"
        assert result["needs_clarification"] is False
        assert isinstance(result["execution_plan"], list)
        assert result["verification"]["status"] == "pass"
        assert result["evidence"][0]["type"] == "sql"

    def test_stream_uses_provided_history(self, api_client):
        import src.api.main as api_main
        history=[{"role":"user","content":"查询上一轮"},
                 {"role":"assistant","content":"旧数字不作为事实","metadata":{"dialogue_state":{"version":3,"topic":"financial","conditions":{"codes":["600085"],"metrics":["operating_revenue"]}}}}]
        with api_client.stream("POST","/internal/chat/stream",json={"question":"那营业收入呢","history":history}) as resp:
            assert resp.status_code==200
            "".join(resp.iter_text())
        assert api_main._agent_instance.histories[-1]==history

    def test_old_assistant_prose_never_becomes_financial_facts(self, api_client):
        from src.agent.v3.state import read_state
        history=[{"role":"user","content":"随便聊聊"},{"role":"assistant","content":"利润123456。"*500}]
        with api_client.stream("POST","/internal/chat/stream",json={"question":"继续","history":history}) as resp:
            assert resp.status_code==200
            "".join(resp.iter_text())
        assert read_state(history).recent_facts==[]
        assert read_state(history).last_execution is None

    def test_empty_question_rejected(self, api_client):
        resp = api_client.post("/internal/chat/stream", json={"question": "", "history": []})
        assert resp.status_code == 422

    def test_internal_health(self, api_client):
        resp = api_client.get("/internal/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["service"]["ok"] is True

    def test_delete_session_charts(self, api_client):
        resp = api_client.delete("/internal/charts/abcd1234-0000-0000-0000-000000000000")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

