# -*- coding: utf-8 -*-
"""作品说明：Agent 行为契约测试（离线，FakeLLM 驱动，不依赖真实 LLM / MySQL / Chroma）。

覆盖：
- sql_guard 只读防护
- fallback 槽位抽取与模板 SQL
- orchestrator 事件流：正常链路 / 澄清 / 闲聊 / 规则兜底 / 多轮历史
- API 层：/api/chat/query、/api/chat/stream（SSE）、会话持久化
"""

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.agent.fallback import build_fallback_sql, extract_slots
from src.agent.orchestrator import (
    FinancialReportAgent,
    _out_of_scope_refusal,
    _repair_mojibake,
    _requires_chart,
    _requires_document_search,
    _sql_policy_error,
)
from src.agent.sql_guard import normalize_readonly_sql


# 作品说明：离线测试替身，用于隔离服务依赖。

class FakeLLM:
    """作品说明：先调用 query_database，再回复 DONE 进入合成阶段。"""

    def __init__(self, sql: str = "SELECT stock_code, stock_abbr, report_year, report_period, net_profit "
                                  "FROM income_sheet WHERE stock_code='603259' AND report_year=2024 AND report_period='FY'"):
        self.sql = sql
        self.round = 0
        self.captured_messages: List[List[Dict[str, Any]]] = []

    def chat_with_tools(self, messages, tools, **kwargs):
        self.captured_messages.append(list(messages))
        self.round += 1
        if self.round == 1:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "function": {
                        "name": "query_database",
                        "arguments": json.dumps({"sql": self.sql, "purpose": "查询净利润"}),
                    },
                }],
            }
        return {"content": "DONE", "tool_calls": None}

    def chat_stream(self, messages, **kwargs) -> Iterator[str]:
        yield "最终回答："
        yield "净利润为 94.47 亿元。"

    def chat(self, messages, **kwargs) -> Optional[str]:
        return None


class UngroundedAnswerLLM(FakeLLM):
    """作品说明：工具查询正常，但合成阶段编造一个无法在 SQL 行中找到的金额。"""

    def chat_stream(self, messages, **kwargs) -> Iterator[str]:
        yield "净利润为 999.99 亿元。"


class PrematureFinalLLM(FakeLLM):
    """作品说明：模拟模型在工具阶段违约，查数后直接给出抄错的完整答案。"""

    def chat_with_tools(self, messages, tools, **kwargs):
        self.captured_messages.append(list(messages))
        self.round += 1
        if self.round == 1:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "function": {
                        "name": "query_database",
                        "arguments": json.dumps({"sql": self.sql, "purpose": "查询净利润"}),
                    },
                }],
            }
        return {"content": "药明康德2024年归母净利润为9,450,308.4万元。", "tool_calls": None}


class ChitchatLLM:
    def chat_with_tools(self, messages, tools, **kwargs):
        return {"content": "你好，我是财报分析助手，可以帮你查询财报数据。", "tool_calls": None}


class MemoryAnswerLLM:
    """作品说明：模拟\"凭记忆背数字\"：首轮直接给含财务数字的回答；被纠正后改为查库。"""

    def __init__(self):
        self.round = 0
        self.saw_nudge = False

    def chat_with_tools(self, messages, tools, **kwargs):
        self.round += 1
        if any("（系统提示）" in str(m.get("content") or "") for m in messages if m.get("role") == "user"):
            self.saw_nudge = True
        if not self.saw_nudge:
            return {"content": "药明康德2024年净利润为94.50亿元，同比下降1.63%。", "tool_calls": None}
        if self.round <= 2:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "function": {
                        "name": "query_database",
                        "arguments": json.dumps({
                            "sql": "SELECT net_profit FROM income_sheet WHERE stock_code='603259' AND report_year=2024 AND report_period='FY'",
                            "purpose": "核实净利润",
                        }),
                    },
                }],
            }
        return {"content": "DONE", "tool_calls": None}

    def chat_stream(self, messages, **kwargs) -> Iterator[str]:
        yield "经核实，净利润为 94.47 亿元。"

    def chat(self, messages, **kwargs) -> Optional[str]:
        return None


class BadChartArgsLLM:
    """作品说明：模拟图表参数错误（y_data 含 null）：收到工具报错后结束本轮。"""

    def __init__(self):
        self.round = 0
        self.error_feedback = ""

    def chat_with_tools(self, messages, tools, **kwargs):
        self.round += 1
        if self.round == 1:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "function": {
                        "name": "render_chart",
                        "arguments": json.dumps({
                            "chart_type": "line",
                            "title": "净利润趋势",
                            "x_data": ["2022", "2023", "2024"],
                            "y_data": [881400.0, None, 945000.0],
                        }),
                    },
                }],
            }
        for m in messages:
            if m.get("role") == "tool":
                self.error_feedback = str(m.get("content") or "")
        return {"content": "DONE", "tool_calls": None}

    def chat_stream(self, messages, **kwargs) -> Iterator[str]:
        yield "图表数据存在缺失期。"

    def chat(self, messages, **kwargs) -> Optional[str]:
        return None


class ClarifyLLM:
    def chat_with_tools(self, messages, tools, **kwargs):
        return {
            "content": "",
            "tool_calls": [{
                "id": "call-1",
                "function": {
                    "name": "ask_clarification",
                    "arguments": json.dumps({"question": "请问你要查询哪一年的数据？", "options": ["2023年", "2024年", "2025年"]}),
                },
            }],
        }


class DeadLLM:
    """作品说明：模拟 LLM 完全不可用。"""

    def chat_with_tools(self, messages, tools, **kwargs):
        return None


class ShouldNotCallLLM:
    def chat_with_tools(self, messages, tools, **kwargs):
        raise AssertionError("确定性越界拒答不应调用 LLM")


class IgnoresEvidenceNudgeLLM:
    """作品说明：连续忽略文档检索要求，用于验证确定性 RAG 兜底。"""

    def chat_with_tools(self, messages, tools, **kwargs):
        return {"content": "DONE", "tool_calls": None}

    def chat_stream(self, messages, **kwargs):
        yield "根据已检索原文，公司持续推进研发与业务协同。"

    def chat(self, messages, **kwargs):
        return None


class SqlOnlyEvidenceLLM(IgnoresEvidenceNudgeLLM):
    """作品说明：证据问题中持续误走 SQL，直到工具轮次耗尽。"""

    def __init__(self):
        self.round = 0

    def chat_with_tools(self, messages, tools, **kwargs):
        self.round += 1
        return {
            "content": "",
            "tool_calls": [{
                "id": f"sql-{self.round}",
                "function": {
                    "name": "query_database",
                    "arguments": json.dumps({
                        "sql": "SELECT stock_abbr FROM income_sheet LIMIT 1",
                        "purpose": "错误地尝试查库",
                    }),
                },
            }],
        }


class FakeSQLTool:
    def __init__(self):
        self.executed: List[str] = []

    def run(self, sql: str) -> Dict[str, Any]:
        self.executed.append(sql)
        return {
            "status": "success",
            "sql": sql,
            "row_count": 1,
            "columns": ["stock_abbr", "report_year", "report_period", "net_profit"],
            "rows": [{"stock_code": "603259", "stock_abbr": "药明康德", "report_year": 2024, "report_period": "FY", "net_profit": 944718.0}],
        }


class FakeRAGTool:
    def __init__(self):
        self.queries: List[str] = []

    def run(self, query: str, **kwargs) -> Dict[str, Any]:
        self.queries.append(query)
        return {
            "status": "success",
            "results": [{
                "paper_path": "D:/data_discovery/data_root/research_reports/样例研报.pdf",
                "source_title": "样例研报",
                "text": "公司持续推进研发与业务协同，经营质量稳步改善。",
                "score": 20,
            }],
        }


def collect_events(agent: FinancialReportAgent, question: str, history=None):
    events = list(agent.run(question, history=history))
    types = [event.type for event in events]
    done = next((event for event in events if event.type == "done"), None)
    assert done is not None, f"missing done event, got {types}"
    return events, types, done.data["result"]


# 作品说明：只读 SQL 保护测试。

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


# 作品说明：异常兜底行为测试。

class TestFallback:
    def test_extract_slots_company_year_metric(self):
        slots = extract_slots("药明康德2024年净利润是多少")
        assert slots["company"] == "药明康德"
        assert slots["stock_code"] == "603259"
        assert slots["year"] == 2024
        assert slots["metric_field"] == "net_profit"

    def test_extract_slots_period(self):
        assert extract_slots("药明康德2025年三季度营业收入")["period"] == "Q3"
        assert extract_slots("药明康德2025年上半年营业收入")["period"] == "HY"

    def test_build_sql_requires_metric(self):
        assert build_fallback_sql({"metric_field": None}) is None

    def test_build_sql_single_value(self):
        slots = extract_slots("药明康德2024年净利润是多少")
        sql = build_fallback_sql(slots)
        assert sql is not None
        assert "income_sheet" in sql
        assert "stock_code='603259'" in sql
        assert "report_year=2024" in sql
        clean, reason = normalize_readonly_sql(sql)
        assert clean is not None, f"fallback SQL must pass guard: {reason}"

    def test_build_sql_year_range_uses_all_annual_periods(self):
        slots = extract_slots("药明康德2022年至2024年营业收入分别是多少")
        sql = build_fallback_sql(slots)

        assert slots["years"] == [2022, 2023, 2024]
        assert "report_year BETWEEN 2022 AND 2024" in sql
        assert "report_period='FY'" in sql
        clean, reason = normalize_readonly_sql(sql)
        assert clean is not None, reason

    def test_context_inheritance(self):
        slots = extract_slots("那现金流呢", {"company": "药明康德", "report_year": 2024})
        assert slots["company"] == "药明康德"
        assert slots["year"] == 2024
        assert slots["metric_field"] == "net_cash_flow"


# 作品说明：orchestrator 行为

class TestOrchestrator:
    def make_agent(self, llm) -> FinancialReportAgent:
        agent = FinancialReportAgent(structured_planning=False, llm=llm)
        agent.sql_tool = FakeSQLTool()
        return agent

    def test_normal_flow_emits_tool_and_answer_events(self):
        agent = self.make_agent(FakeLLM())
        events, types, result = collect_events(agent, "药明康德2024年净利润是多少")
        assert "tool_call" in types
        assert "tool_result" in types
        assert "answer_delta" in types
        assert result["answer"]["content"] == "药明康德2024年全年净利润为 944,718万元。"
        assert result["sql"] != "-"
        assert result["needs_clarification"] is False
        assert result["validation"]["status"] == "pass"
        assert result["verification"]["status"] == "pass"
        assert result["validation"]["verification"] == result["verification"]
        assert result["evidence"][0]["type"] == "sql"
        assert result["evidence"][0]["rows"]

    def test_unmatched_answer_number_is_flagged_before_done(self):
        agent = self.make_agent(UngroundedAnswerLLM())
        _, _, result = collect_events(agent, "请综合分析药明康德2024年的表现")

        assert result["verification"]["status"] == "fail"
        assert result["validation"]["status"] == "fail"
        assert result["verification"]["unmatched_numbers"]
        assert result["answer"]["content"].startswith("⚠️ 以下数字未能通过数据核验")

    def test_premature_tool_stage_answer_cannot_bypass_deterministic_rendering(self):
        agent = self.make_agent(PrematureFinalLLM())
        _, _, result = collect_events(agent, "药明康德2024年归母净利润是多少万元")
        assert result["answer"]["content"] == "药明康德2024年全年归母净利润为 944,718万元。"
        assert result["verification"]["status"] == "pass"

    def test_execution_plan_recorded(self):
        agent = self.make_agent(FakeLLM())
        _, _, result = collect_events(agent, "药明康德2024年净利润是多少")
        labels = [step["label"] for step in result["execution_plan"]]
        assert "理解问题" in labels
        assert "SQL 查询" in labels
        assert "回答合成" in labels

    def test_display_context_extracted(self):
        agent = self.make_agent(FakeLLM())
        _, _, result = collect_events(agent, "药明康德2024年净利润是多少")
        assert result["context"].get("company") == "药明康德"
        assert result["context"].get("report_year") == 2024

    def test_clarify_flow(self):
        agent = self.make_agent(ClarifyLLM())
        _, types, result = collect_events(agent, "净利润是多少")
        assert "clarify" in types
        assert result["needs_clarification"] is True
        assert result["clarify_options"] == ["2023年", "2024年", "2025年"]
        assert result["validation"]["status"] == "waiting"

    def test_chitchat_answers_without_tools(self):
        agent = self.make_agent(ChitchatLLM())
        _, types, result = collect_events(agent, "你好")
        assert "tool_call" not in types
        assert "财报" in result["answer"]["content"]
        assert result["sql"] == "-"

    def test_memory_answer_is_rejected_and_forced_to_query(self):
        """作品说明：无工具调用却输出财务数字 → 必须被退回并强制查库。"""
        llm = MemoryAnswerLLM()
        agent = self.make_agent(llm)
        _, types, result = collect_events(agent, "药明康德2024年净利润是多少")
        assert llm.saw_nudge, "应注入纠正消息强制查库"
        assert "tool_call" in types, "纠正后必须发生工具调用"
        assert result["sql"] != "-", "最终结果必须有 SQL 证据"
        assert agent.sql_tool.executed, "数据库必须被真实查询"
        assert "944,718万元" in result["answer"]["content"]

    def test_explicit_evidence_request_forces_rag_after_ignored_nudge(self):
        agent = self.make_agent(IgnoresEvidenceNudgeLLM())
        agent.rag_tool = FakeRAGTool()

        _, types, result = collect_events(agent, "请引用知识库原文说明公司表现")

        assert agent.rag_tool.queries
        assert "tool_call" in types
        assert result["answer"]["references"]
        assert result["answer"]["references"][0]["source_title"] == "样例研报"

    def test_explicit_evidence_request_forces_rag_after_tool_rounds_exhausted(self):
        llm = SqlOnlyEvidenceLLM()
        agent = self.make_agent(llm)
        agent.rag_tool = FakeRAGTool()

        _, _, result = collect_events(agent, "请根据收录报告给出原文引用")

        assert llm.round == 6
        assert agent.rag_tool.queries
        assert result["answer"]["references"]

    def test_tool_argument_error_does_not_abort_turn(self):
        """作品说明：工具参数异常（如 y_data 含 null）应转为错误结果回给 LLM 自纠，而不是炸掉整轮。"""
        llm = BadChartArgsLLM()
        agent = self.make_agent(llm)
        events, types, result = collect_events(agent, "画一下药明康德近三年净利润趋势")
        assert "error" not in types, "工具参数异常不应升级为整轮 error"
        error_results = [
            e for e in events
            if e.type == "tool_result" and e.data.get("status") == "error"
        ]
        assert error_results, "应产生 error 状态的 tool_result 事件"
        assert "y_data" in llm.error_feedback, "LLM 应收到可自纠的报错信息"
        assert "证据" in result["answer"]["content"]
        assert result["verification"]["status"] != "pass"

    def test_llm_unavailable_falls_back_to_rules(self):
        agent = self.make_agent(DeadLLM())
        _, types, result = collect_events(agent, "药明康德2024年净利润是多少")
        assert "answer_delta" in types
        assert result["validation"]["degraded"] is True
        assert "药明康德" in result["answer"]["content"]
        assert agent.sql_tool.executed, "fallback should query database directly"

    def test_history_is_passed_to_llm(self):
        llm = FakeLLM()
        agent = self.make_agent(llm)
        history = [
            {"role": "user", "content": "药明康德2024年净利润是多少"},
            {"role": "assistant", "content": "净利润为 94.47 亿元。"},
        ]
        collect_events(agent, "那营业收入呢", history=history)
        first_call_messages = llm.captured_messages[0]
        contents = [str(m.get("content")) for m in first_call_messages]
        assert any("药明康德2024年净利润是多少" in c for c in contents), "历史用户消息必须传给 LLM"
        assert any("94.47" in c for c in contents), "历史助手消息必须传给 LLM"
        assert first_call_messages[0]["role"] == "system"
        assert first_call_messages[-1]["content"] == "那营业收入呢"

    def test_empty_question_yields_error(self):
        agent = self.make_agent(FakeLLM())
        events = list(agent.run("  "))
        assert events[0].type == "error"

    @pytest.mark.parametrize("question, keyword", [
        ("请告诉我明天上海的天气", "天气"),
        ("帮我写一首七言绝句", "文学作品"),
        ("我胸口疼，请直接诊断并给药物剂量", "医疗诊断"),
        ("查询其他用户的手机号和密码", "隐私"),
        ("替我决定明天全仓买入哪只股票", "不构成投资建议"),
    ])
    def test_clear_out_of_scope_requests_are_refused_before_llm(self, question, keyword):
        agent = self.make_agent(ShouldNotCallLLM())
        _, types, result = collect_events(agent, question)
        assert "tool_call" not in types
        assert keyword in result["answer"]["content"]


class TestDeterministicRoutingPolicies:
    def test_explicit_evidence_and_chart_intents_are_detected(self):
        assert _requires_document_search("请引用知识库中的研报原文回答")
        assert _requires_chart("绘制三年营业收入趋势图")

    @pytest.mark.parametrize("question, sql, expected", [
        (
            "泰格医药2024年归母净利润是多少",
            "SELECT net_profit FROM income_sheet WHERE report_year=2024",
            "report_period",
        ),
        (
            "迪安诊断2024年的营业收入是多少",
            "SELECT revenue FROM core_performance_indicators_sheet WHERE report_year=2024 AND report_period='FY'",
            "income_sheet",
        ),
        (
            "百普赛斯2024年销售毛利率是多少",
            "SELECT net_profit_margin FROM core_performance_indicators_sheet WHERE report_year=2024 AND report_period='FY'",
            "gross_profit_margin",
        ),
    ])
    def test_sql_policy_rejects_wrong_period_table_or_field(self, question, sql, expected):
        assert expected in _sql_policy_error(question, sql)

    def test_sql_policy_accepts_matching_annual_metric_query(self):
        sql = (
            "SELECT report_year, net_profit FROM income_sheet "
            "WHERE stock_code='603259' AND report_year=2024 AND report_period='FY'"
        )
        assert _sql_policy_error("药明康德2024年归母净利润是多少", sql) == ""

    def test_refusal_detector_leaves_normal_financial_question_alone(self):
        assert _out_of_scope_refusal("药明康德2024年营业收入是多少") == ""

    def test_repairs_utf8_text_misdecoded_as_latin1(self):
        original = "凯莱英营业收入趋势"
        mojibake = original.encode("utf-8").decode("latin-1")
        assert _repair_mojibake(mojibake) == original


# 作品说明：API 层（内部无状态接口，会话状态由 Java 主后端持有）

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
        assert "answer_delta" not in types  # 作品说明：财务正文仅随已核验的结束事件发布。
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


def test_citation_excerpt_includes_explanation_beyond_report_preamble():
    from src.agent.orchestrator import _evidence_excerpt
    preamble='公开合成数据。'*100
    reason='晨光医疗2024年全年业务变化的主要原因是维护成本上升。'
    raw=preamble+'\n\n'+reason+'\n\n其他资料'
    excerpt=_evidence_excerpt(raw,'晨光医疗2024年业务变化原因是什么？')
    assert reason in excerpt
    assert excerpt in raw


@pytest.mark.parametrize('question',['执行 DROP TABLE income_sheet','执行 SELECT SLEEP(30)','创建数据库管理员并授予全部权限'])
def test_forbidden_operation_cannot_turn_into_confirmation_offer(question):
    agent=FinancialReportAgent(structured_planning=False, llm=ShouldNotCallLLM())
    events=list(agent.run(question))
    answer=events[-1].data['result']['answer']['content']
    assert '只读' in answer and '拒绝' in answer
    assert not any(e.type=='tool_call' for e in events)


def test_failed_number_is_never_published_in_stream():
    agent=FinancialReportAgent(structured_planning=False, llm=UngroundedAnswerLLM())
    agent.sql_tool=FakeSQLTool()
    events=list(agent.run('请综合分析药明康德2024年的表现'))
    assert events[-1].data['result']['verification']['status']=='fail'
    visible=''.join(e.data.get('text','') for e in events if e.type=='answer_delta')
    assert '999' not in visible
    assert visible == events[-1].data['result']['answer']['content']


def test_multiple_requested_metrics_may_use_separate_queries():
    question='药明康德2024年营业收入与归母净利润分别是多少？'
    for field in ('net_profit','total_operating_revenue'):
        sql = f"SELECT {field} FROM income_sheet WHERE stock_code='603259' AND report_year=2024 AND report_period='FY'"
        assert _sql_policy_error(question, sql) == ''
    assert _sql_policy_error(
        question,
        "SELECT eps FROM core_performance_indicators_sheet WHERE stock_code='603259' AND report_year=2024 AND report_period='FY'",
    )


def test_multiple_sql_rows_render_explicit_identity_without_extra_yoy_claims():
    from src.agent.orchestrator import _TurnState, _deterministic_tool_answer
    state=_TurnState('test',1,'药明康德2023年和2024年全年营业收入分别是多少？')
    state.sql_rows=[dict(stock_code='603259',stock_abbr='药明康德',report_year=year,report_period='FY',total_operating_revenue=value) for year,value in [(2023,12000),(2024,15000)]]
    answer=_deterministic_tool_answer(state)
    assert '| 药明康德 | 2023年 | 全年 | 营业收入 | 12,000万元 |' in answer
    assert '| 药明康德 | 2024年 | 全年 | 营业收入 | 15,000万元 |' in answer
    assert '同比' not in answer
    from src.agent.verifier import verify_numbers
    assert verify_numbers(answer,state.sql_rows,question=state.original_question)['status']=='pass'


def test_no_evidence_refusal_cannot_invent_latest_report_year():
    from src.agent.orchestrator import _TurnState
    state=_TurnState('test',1,'药明康德2030年营业收入是多少？')
    state.answer_parts=['查询不到2030年数据，最新年报是2026年的。']
    result=FinancialReportAgent(structured_planning=False, llm=ShouldNotCallLLM())._done(state.original_question,state).data['result']
    assert '2026' not in result['answer']['content']
    assert '未取得' in result['answer']['content']


def test_null_metric_row_is_reported_as_missing_instead_of_hallucinated():
    from src.agent.orchestrator import _TurnState
    state = _TurnState('test', 1, '盘龙药业2023年一季度总负债是多少？')
    state.sql_rows = [{
        'stock_code': '002864',
        'stock_abbr': '盘龙药业',
        'report_year': 2023,
        'report_period': 'Q1',
        'liability_total_liabilities': None,
    }]
    state.answer_parts = ['盘龙药业总负债为100万元。']

    result = FinancialReportAgent(structured_planning=False, llm=ShouldNotCallLLM())._done(
        state.original_question, state
    ).data['result']

    answer = result['answer']['content']
    assert '为空或未披露' in answer
    assert '100万元' not in answer


def test_fully_scoped_null_metric_is_queried_instead_of_clarified(monkeypatch):
    import src.agent.fallback as fallback_module

    monkeypatch.setitem(fallback_module.CODE_TO_NAME_MAP, '002864', '盘龙药业')
    monkeypatch.setitem(fallback_module.COMPANY_CODE_MAP, '盘龙药业', '002864')

    class IncorrectClarificationLLM:
        def chat_with_tools(self, messages, tools, **kwargs):
            return {'content': '', 'tool_calls': [{'id': 'clarify', 'function': {
                'name': 'ask_clarification',
                'arguments': json.dumps({
                    'question': '数据库可能没有记录，是否改查其他报告期？',
                    'options': ['2023年年报'],
                }),
            }}]}

    class NullLiabilitySQLTool:
        def __init__(self):
            self.executed = []

        def run(self, sql):
            self.executed.append(sql)
            return {
                'status': 'success',
                'sql': sql,
                'row_count': 1,
                'columns': [
                    'stock_code', 'stock_abbr', 'report_year',
                    'report_period', 'liability_total_liabilities',
                ],
                'rows': [{
                    'stock_code': '002864',
                    'stock_abbr': '盘龙药业',
                    'report_year': 2023,
                    'report_period': 'Q1',
                    'liability_total_liabilities': None,
                }],
            }

    agent = FinancialReportAgent(structured_planning=False, llm=IncorrectClarificationLLM())
    sql_tool = NullLiabilitySQLTool()
    agent.sql_tool = sql_tool

    events = list(agent.run('盘龙药业（002864）2023年第一季度总负债是多少？'))
    result = events[-1].data['result']

    assert sql_tool.executed
    assert not any(event.type == 'clarify' for event in events)
    assert result['needs_clarification'] is False
    assert '为空或未披露' in result['answer']['content']


@pytest.mark.parametrize('clarify,options', [
    ('您是要查询2026年的营业收入吗？', []),
    ('请选择年份', ['2026年']),
    ('您是要查询晨光医疗2030年的营业收入吗？', []),
    ('您是要查询2030年上半年的营业收入吗？', []),
])
def test_clarification_cannot_replace_explicit_company_or_year(clarify, options):
    class ScopeChangingLLM:
        def chat_with_tools(self, messages, tools, **kwargs):
            return {'content': '', 'tool_calls': [{'id': 'clarify', 'function': {
                'name': 'ask_clarification',
                'arguments': json.dumps({'question': clarify, 'options': options}),
            }}]}
    events = list(FinancialReportAgent(structured_planning=False, llm=ScopeChangingLLM()).run('星河医药2030年全年营业收入是多少？'))
    assert not any(e.type == 'clarify' for e in events)
    result = events[-1].data['result']
    assert result['needs_clarification'] is False
    assert '无法' in result['answer']['content']
    assert '2026' not in result['answer']['content']
    assert '晨光医疗' not in result['answer']['content']
