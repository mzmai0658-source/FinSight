from src.api.observability import ApiMetrics, current_trace_id, trace_context


def test_trace_context_restores_previous_value():
    assert current_trace_id() == "-"

    with trace_context("trace-python-1"):
        assert current_trace_id() == "trace-python-1"

    assert current_trace_id() == "-"


def test_api_metrics_render_prometheus_text():
    from prometheus_client import CollectorRegistry

    registry = CollectorRegistry()
    metrics = ApiMetrics(registry=registry)

    metrics.record_request("/internal/health", "success", 0.01)
    metrics.record_agent_turn("success", 0.2)
    metrics.record_agent_tool("query_database", "success")
    metrics.record_agent_tool_error("render_chart")
    metrics.record_sql_rejected()
    metrics.record_rag_empty()

    output = metrics.render()

    assert "finsight_python_internal_requests_total" in output
    assert 'endpoint="/internal/health"' in output
    assert 'status="success"' in output
    assert "finsight_python_agent_turn_seconds" in output
    assert "finsight_python_agent_tool_call_total" in output
    assert 'tool="query_database"' in output
    assert "finsight_python_agent_tool_error_total" in output
    assert "finsight_python_agent_sql_rejected_total" in output
    assert "finsight_python_agent_rag_empty_total" in output


def test_internal_metrics_endpoint_exposes_request_counters(monkeypatch):
    fastapi_testclient = __import__("fastapi.testclient", fromlist=["TestClient"])

    import src.api.main as api_main

    token = "test-internal-credential-0000000000000000"
    monkeypatch.setenv("INTERNAL_API_TOKEN", token)
    monkeypatch.setattr(api_main, "probe_knowledge", lambda path: (True, "mocked collection"))
    monkeypatch.setattr(api_main, "probe_llm", lambda: (True, "mocked model"))
    client = fastapi_testclient.TestClient(api_main.app, headers={"X-Internal-Token": token})

    health = client.get("/internal/health", headers={"X-Request-Id": "metrics-test-1"})
    assert health.status_code == 200

    metrics_response = client.get("/internal/metrics")

    assert metrics_response.status_code == 200
    assert "text/plain" in metrics_response.headers["content-type"]
    assert "finsight_python_internal_requests_total" in metrics_response.text
    assert 'endpoint="/internal/health"' in metrics_response.text
