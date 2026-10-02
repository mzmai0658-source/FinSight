from collections import Counter
from copy import deepcopy
import hashlib
import json

import pytest

from eval.run_eval import (
    _chart_values_supported, _expected_values_supported, aggregate, load_detail_records,
    load_dataset, render_markdown, rescore_records, run_evaluation, score_record,
    validate_dataset, write_outputs,
)
from eval.experiment import FakeLLMRuntime, fingerprint


def case_for(category):
    return next(c for c in load_dataset() if c["category"] == category)


def fixture_record(case, mode="agent"):
    return {"case_id": case["id"], "category": case["category"], "mode": mode,
            "case_fingerprint": fingerprint(case), "manifest_fingerprint": "test-manifest", "fake": True,
            **FakeLLMRuntime().run(case, mode), "error": ""}


def test_public_dataset_quota_and_complete_fact_identity():
    cases = load_dataset()
    validate_dataset(cases)
    assert len(cases) == len({c["id"] for c in cases}) == 60
    assert Counter(c["category"] for c in cases) == {"numeric":25, "chart":10, "citation":10, "security":10, "out_of_scope":5}
    assert {c["split"] for c in cases} == {"development", "holdout"}


def test_fake_smoke_is_explicitly_not_model_measurement():
    records = run_evaluation(load_dataset()[:3], fake=True)
    assert len(records) == 6
    assert aggregate(records, "agent")["errors"] == 0
    report = render_markdown(records, 3)
    assert "裸模型" in report and "FinSight Agent" in report
    assert "不是模型实测" in report


@pytest.mark.parametrize("replacement", ["晨光医疗2024年全年营业收入为15000万元。", "星河医药2023年全年营业收入为15000万元。", "星河医药2024年上半年营业收入为15000万元。", "星河医药2024年全年归母净利润为15000万元。", "星河医药2024年全年营业收入为15000元。", "星河医药2024年全年营业收入不是15000万元，实际为1万元。", "星河医药2024年全年营业收入为15000万元，另有999万元。"])
def test_numeric_rejects_wrong_identity_units_negation_and_extra_claims(replacement):
    case = case_for("numeric")
    assert _expected_values_supported(fixture_record(case)["answer"], case["expected"]["values"])
    assert not _expected_values_supported(replacement, case["expected"]["values"])


def test_numeric_explicit_unit_conversion_and_negative_sign():
    case = case_for("numeric")
    assert _expected_values_supported("星河医药2024年全年营业收入为1.5亿元。", case["expected"]["values"])
    assert not _expected_values_supported("星河医药2024年全年营业收入为-15000万元。", case["expected"]["values"])
    assert not _expected_values_supported("星河医药2024年全年营业收入未知，归母净利润为15000万元。", case["expected"]["values"])


@pytest.mark.parametrize("mutation", ["years", "reverse", "company", "metric", "unit", "period"])
def test_chart_rejects_point_or_identity_mismatch(mutation):
    case = case_for("chart")
    result = fixture_record(case)["result"]
    assert _chart_values_supported(result, case["expected"]["values"])
    chart = result["chart_data_list"][0]
    if mutation == "years": chart["x_data"] = [2030,2031,2032]
    if mutation == "reverse": chart["y_data"].reverse()
    if mutation == "company": chart["title"] = "晨光医疗全年营业收入"
    if mutation == "metric": chart["data_source"]["y_field"] = "net_profit"
    if mutation == "unit": chart["y_label"] = "营业收入（亿元）"
    if mutation == "period": chart["data_source"]["report_period"] = "HY"
    assert not _chart_values_supported(result, case["expected"]["values"])


@pytest.mark.parametrize("change", [{"error":"TimeoutError"}, {"answer":""}, {"terminal_status":"incomplete"}, {"tool_results":[{"tool":"query_database","status":"error"}]}, {"tool_results":[{"tool":"query_database","status":"success"}]}])
def test_security_error_or_incomplete_is_not_a_block(change):
    case = case_for("security")
    record = fixture_record(case)
    assert score_record(case, record)["dangerous_sql_blocked"] is True
    record.update(change)
    assert score_record(case, record)["dangerous_sql_blocked"] is False


def test_no_sql_execution_without_refusal_is_not_security_success():
    case = case_for("security")
    record = fixture_record(case)
    record.update(answer="好的。", tool_results=[])
    assert score_record(case, record)["dangerous_sql_blocked"] is False


@pytest.mark.parametrize("mutation", ["company", "period", "version", "text", "irrelevant"])
def test_citation_oracle_does_not_trust_application_badge(mutation):
    case = case_for("citation")
    record = fixture_record(case)
    assert score_record(case, record)["citation_supported"] is True
    ref = record["result"]["answer"]["references"][0]
    record["result"]["verification"] = {"status":"pass"}
    if mutation == "company": ref["stock_code"] = "990002"
    if mutation == "period": ref["report_year"] = 2030
    if mutation == "version": ref["document_version"] = "bad"
    if mutation == "text": ref["text"] = "这是模型编造的原因。"
    if mutation == "irrelevant": ref["text"] = "不是上市公司披露。"
    scores = score_record(case, record)
    assert scores["citation_supported"] is False
    assert scores["false_acceptance"] is True
    assert scores["explanation_supported"] is None


def test_explanation_requires_review_bound_to_exact_answer():
    case = case_for("citation")
    record = fixture_record(case)
    record["manual_review"] = {"reviewer":"reviewer-1", "answer_sha256":hashlib.sha256(record["answer"].encode()).hexdigest(), "supported":True}
    assert score_record(case, record)["explanation_supported"] is True
    record["answer"] += "另一个未经支持的结论。"
    assert score_record(case, record)["explanation_supported"] is None


def test_bare_inapplicable_metrics_are_na():
    for category in ("numeric", "chart", "citation"):
        case = case_for(category)
        scores = score_record(case, fixture_record(case, "bare"))
        assert scores["tool_routing"] is None
        assert scores["citation_supported"] is None
        if category == "chart": assert scores["numeric_accuracy"] is None


def test_round_trip_preserves_nested_references_rows_events_and_recomputes_scores(tmp_path):
    case = case_for("citation")
    record = fixture_record(case)
    record["events"] = [{"type":"tool_result", "data":{"rows":[{"stock_code":"990001","report_year":2023,"net_profit":1200}]}}]
    record["result"]["facts"] = [{"fact_id":"fact-1","value":1200}]
    record["scores"] = {"citation_supported":False}
    output = tmp_path / "report.md"
    write_outputs([record], output, 1, {"fingerprint":"test-manifest"})
    saved = load_detail_records(output.with_suffix(".jsonl"), {"fingerprint":"test-manifest"})
    assert saved[0]["result"]["answer"]["references"] == record["result"]["answer"]["references"]
    assert saved[0]["events"] == record["events"]
    replayed = rescore_records([case], saved)
    assert replayed[0]["scores"]["citation_supported"] is True
    assert replayed[0]["result"]["facts"] == record["result"]["facts"]


def test_reuse_rejects_changed_manifest_and_replay_rejects_changed_oracle(tmp_path):
    case = case_for("numeric")
    record = fixture_record(case)
    path = tmp_path / "run.jsonl"
    path.write_text(json.dumps(record,ensure_ascii=False)+"\n",encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing reuse"):
        load_detail_records(path, {"fingerprint":"another-model-or-corpus"})
    changed = deepcopy(case)
    changed["expected"]["values"][0]["value"] += 1
    with pytest.raises(ValueError, match="changed oracle"):
        rescore_records([changed],[record])
    with pytest.raises(ValueError, match="duplicate"):
        rescore_records([case],[record,record])
