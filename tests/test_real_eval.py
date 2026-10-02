"""作品说明：离线验证真实 PDF 评分边界及执行、重放完整性。"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.experiment import fingerprint
from eval.real import oracle
from eval.real.run_eval import (PUBLIC_FIELDS, export_review_materials, jsonl, load_cases,
                               read_jsonl, render_report, rescore_records, run_records,
                               validate_run_coverage)
from eval.real.runtime import HTTPAudit, ModelDisconnected, assert_local_isolation


def fact(period="FY", year=2023, value=12345.67, unit="万元"):
    return {"stock_code": "600085", "stock_abbr": "同仁堂", "report_year": year,
            "report_period": period, "field": "total_operating_revenue", "metric_label": "营业收入",
            "value": value, "unit": unit, "tolerance": .01}


def case(identifier="real-1", period="FY"):
    return {"id": identifier, "question": "同仁堂2023年全年营业收入多少？", "category": "numeric",
            "split": "development", "label_status": "machine_candidate", "expected": {"values": [fact(period)], "tools": ["query_database"]}}


def record_for(case, answer=None):
    return {"case_id": case["id"], "case_fingerprint": fingerprint(case), "manifest_fingerprint": "manifest",
            "category": case["category"], "mode": "agent", "answer": answer or "同仁堂2023年全年营业收入为12345.67万元。",
            "terminal_status": "completed", "tools": ["query_database"], "tool_results": [], "events": [],
            "result": {"verification": {"status": "pass"}}, "error": "", "latency_seconds": 1.2,
            "label_status": case["label_status"]}


@pytest.mark.parametrize("period,label", [("FY", "全年"), ("HY", "上半年"), ("Q1", "一季度"), ("Q3", "前三季度")])
def test_financial_scope_supports_all_report_periods(period, label):
    assert oracle.expected_values_supported(f"同仁堂2023年{label}营业收入为12,345.67万元。", [fact(period)])
    other = "上半年" if period == "FY" else "全年"
    assert not oracle.expected_values_supported(f"同仁堂2023年{other}营业收入为12345.67万元。", [fact(period)])


def test_unit_conversion_tolerance_is_in_gold_unit():
    expected = fact(value=10000, unit="万元")
    assert oracle.amount_equal(1, "亿元", expected)
    assert oracle.amount_equal(100000000, "元", expected)
    assert not oracle.amount_equal(1.00001, "亿元", expected)
    assert not oracle.amount_equal(float("nan"), "万元", expected)
    assert not oracle.amount_equal(10000, "%", expected)


def test_retired_search_tool_name_is_normalized_without_changing_labels(tmp_path):
    candidate = {**case(), 'category': 'citation', 'expected': {'tools': ['search_knowledge'], 'sources': []}}
    record = {**record_for(candidate), 'tools': ['search_documents']}
    assert oracle.score_record(candidate, record, tmp_path)['tool_routing'] is True
    assert candidate['expected']['tools'] == ['search_knowledge']
    record['tools'] = ['query_database']
    assert oracle.score_record(candidate, record, tmp_path)['tool_routing'] is False


@pytest.mark.parametrize("unit", ["元", "元/股", "元／股"])
def test_eps_unit_normalization_does_not_change_other_amounts(unit):
    expected = {**fact(value=.0896), "field": "eps", "metric_label": "基本每股收益", "unit": "元/股", "tolerance": .00011}
    assert oracle.expected_values_supported(f"同仁堂2023年全年基本每股收益为0.0896{unit}。", [expected])
    assert not oracle.amount_equal(.0896, "元/股", fact(value=.0896, unit="元"))


def test_multi_year_claims_are_bound_to_year_and_metric():
    facts = [fact(year=2022, value=100), fact(year=2023, value=200)]
    assert oracle.expected_values_supported("同仁堂2022年全年营业收入为100万元；同仁堂2023年全年营业收入为200万元。", facts)
    assert not oracle.expected_values_supported("同仁堂2022年全年营业收入为200万元；同仁堂2023年全年营业收入为100万元。", facts)
    assert not oracle.expected_values_supported("同仁堂2023年全年净利润为200万元。营业收入", [facts[1]])
    assert not oracle.expected_values_supported("太极集团2023年全年营业收入为200万元。", [facts[1]])


@pytest.mark.parametrize("period,label", [("FY", "年度"), ("HY", "半年度"), ("Q1", "一季度"), ("Q3", "前三季度")])
def test_chart_scope_and_source_period(period, label):
    facts = [fact(period, 2022, 100), fact(period, 2023, 200)]
    chart = {"title": f"同仁堂{label}营业收入", "y_label": "营业收入（万元）", "x_data": [2022, 2023],
             "y_data": [100, 200], "data_source": {"report_period": period, "y_field": "total_operating_revenue"}}
    assert oracle.chart_values_supported({"chart_data_list": [chart]}, facts)
    chart["data_source"]["report_period"] = "Q1" if period != "Q1" else "FY"
    assert not oracle.chart_values_supported({"chart_data_list": [chart]}, facts)


@pytest.fixture
def pdf_source(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 30 700 Td (Revenue increased through domestic sales. 12345.67) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    path = tmp_path / "original.pdf"
    with path.open("wb") as output:
        writer.write(output)
    return {"document_id": "catalog-id-is-not-production-id", "stock_code": "600085", "stock_abbr": "同仁堂",
            "report_year": 2023, "report_period": "FY", "source_path": str(path),
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "page": 1,
            "excerpt": "Revenue increased through domestic sales."}


def test_pdf_hash_page_excerpt_and_path_boundary(pdf_source, tmp_path):
    assert pdf_source["excerpt"] in oracle.validate_source(pdf_source, tmp_path)
    for patch in ({"source_sha256": "bad"}, {"page": 2}, {"excerpt": "Invented sentence."}):
        with pytest.raises(ValueError):
            oracle.validate_source({**pdf_source, **patch}, tmp_path)
    with pytest.raises(ValueError):
        oracle.validate_source(pdf_source, tmp_path / "different-root")


def test_wrapped_table_label_uses_declared_pdf_layout(pdf_source, tmp_path):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, DecodedStreamObject
    path = Path(pdf_source['source_path'])
    writer = PdfWriter()
    writer.add_page(PdfReader(path).pages[0])
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 30 700 Td (Operating cash) Tj ET\n'
                    b'BT /F1 12 Tf 30 680 Td (flow) Tj ET\n'
                    b'BT /F1 12 Tf 220 700 Td (100.00) Tj ET')
    writer.pages[0][NameObject('/Contents')] = writer._add_object(stream)
    with path.open('wb') as output:
        writer.write(output)
    candidate = {**pdf_source, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                 'excerpt': 'Operating cash 100.00 flow', 'annotation_method': 'original_pdf_layout_regex'}
    assert '100.00' in oracle.validate_source(candidate, tmp_path)
    with pytest.raises(ValueError, match='excerpt'):
        oracle.validate_source({**candidate, 'annotation_method': '', 'excerpt': candidate['excerpt']}, tmp_path)
    with pytest.raises(ValueError, match='excerpt'):
        oracle.validate_source({**candidate, 'excerpt': 'Operating cash 200.00 flow'}, tmp_path)


def test_citation_uses_original_pdf_path_hash_page_not_production_id(pdf_source, tmp_path):
    candidate = {**case(), "category": "citation", "expected": {"sources": [pdf_source]}}
    ref = {**pdf_source, "document_id": "production-source-path-id", "document_version": "pdf-plus-ocr-fingerprint",
           "page_start": 1, "paper_path": pdf_source["source_path"], "text": pdf_source["excerpt"]}
    record = record_for(candidate)
    record["result"]["answer"] = {"content": record["answer"], "references": [ref]}
    assert oracle.citation_supported(candidate, record, tmp_path)
    for patch in ({"source_sha256": "bad"}, {"page_start": 2}, {"report_period": "HY"}, {"text": "Invented sentence."}):
        bad = copy.deepcopy(record)
        bad["result"]["answer"]["references"][0].update(patch)
        assert not oracle.citation_supported(candidate, bad, tmp_path)


def test_explanation_requires_answer_bound_human_review(pdf_source, tmp_path):
    candidate = {**case(), "category": "citation", "expected": {"sources": [pdf_source]}}
    record = record_for(candidate)
    assert oracle.score_record(candidate, record, tmp_path)["explanation_supported"] is None
    record["manual_review"] = {"reviewer": "reviewer-1", "supported": True, "answer_sha256": "changed"}
    assert oracle.score_record(candidate, record, tmp_path)["explanation_supported"] is None
    record["manual_review"]["answer_sha256"] = hashlib.sha256(record["answer"].encode()).hexdigest()
    assert oracle.score_record(candidate, record, tmp_path)["explanation_supported"] is True


def test_loader_preserves_missing_annotations_and_keeps_gold_separate(pdf_source, tmp_path):
    questions = [{k: v for k, v in case().items() if k in PUBLIC_FIELDS},
                 {k: v for k, v in case("missing-2").items() if k in PUBLIC_FIELDS}]
    gold = [{"id": "real-1", "label_status": "machine_candidate", "expected": {"values": [{**fact(), **pdf_source}]}},
            {"id": "missing-2", "label_status": "annotation_missing", "annotation_error": "Table unreadable", "expected": {}}]
    question_path, gold_path = tmp_path / "questions.jsonl", tmp_path / "gold.jsonl"
    question_path.write_text(jsonl(questions), encoding="utf-8")
    gold_path.write_text(jsonl(gold), encoding="utf-8")
    loaded = load_cases(question_path, gold_path, tmp_path)
    assert len(loaded) == 2
    assert loaded[1]["label_metadata"]["annotation_error"] == "Table unreadable"
    assert oracle.score_record(loaded[1], record_for(loaded[1]), tmp_path)["numeric_accuracy"] is None
    questions[0]["expected"] = gold[0]["expected"]
    question_path.write_text(jsonl(questions), encoding="utf-8")
    with pytest.raises(ValueError, match="keep labels and evidence"):
        load_cases(question_path, gold_path, tmp_path)


def test_loader_rejects_silent_dropped_question_ids(tmp_path):
    questions, gold = tmp_path / "questions.jsonl", tmp_path / "gold.jsonl"
    questions.write_text(jsonl([{k: v for k, v in case().items() if k in PUBLIC_FIELDS}]), encoding="utf-8")
    gold.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="IDs differ"):
        load_cases(questions, gold, tmp_path)


@pytest.mark.parametrize("database,host,path,endpoint", [
    ("financial_report", "localhost", "data/real_eval_chroma_db", "http://127.0.0.1:11434/v1"),
    ("finsight_real_eval", "remote.example", "data/real_eval_chroma_db", "http://127.0.0.1:11434/v1"),
    ("finsight_real_eval", "localhost", "data/demo_chroma_db", "http://127.0.0.1:11434/v1"),
    ("finsight_real_eval", "localhost", "data/real_eval_chroma_db", "https://remote.example/v1"),
])
def test_isolation_rejects_remote_or_other_dataset(database, host, path, endpoint):
    with pytest.raises(RuntimeError):
        assert_local_isolation(SimpleNamespace(database=database, host=host), path, endpoint)


def test_local_isolation_accepts_only_real_fixture():
    assert_local_isolation(SimpleNamespace(database="finsight_real_eval", host="localhost"),
                           "data/real_eval_chroma_db", "http://127.0.0.1:11434/v1")


def test_runner_passes_only_question_and_stops_on_disconnect_preserving_records(tmp_path):
    cases = [case("first"), case("second"), case("never-executed")]
    observed = []

    class FakeRuntime:
        def run(self, public, mode):
            assert set(public) == PUBLIC_FIELDS
            observed.append(public["id"])
            if public["id"] == "second":
                error = ModelDisconnected("connection reset")
                error.partial_output = {"events": [{"type": "tool_result", "data": {"rows": [{"source": {"page": 3}}]}}]}
                raise error
            return record_for(cases[0])

    manifest = {"selected_case_ids": [c["id"] for c in cases], "modes": ["agent"], "fingerprint": "manifest"}
    records, status = run_records(cases, FakeRuntime(), manifest, tmp_path, tmp_path / "run", SimpleNamespace(calls=[]))
    assert observed == ["first", "second"]
    assert status["status"] == "aborted_infrastructure"
    assert records[1]["events"][0]["data"]["rows"][0]["source"]["page"] == 3
    assert len(read_jsonl(tmp_path / "run/report.jsonl")) == 2
    assert "计划 3 题" in (tmp_path / "run/report.md").read_text(encoding="utf-8")
    replay = rescore_records(cases, records, manifest, tmp_path)
    assert [r["scores"] for r in replay] == [r["scores"] for r in records]


def test_http_audit_bypasses_swallowed_connection_errors_and_omits_credentials(monkeypatch):
    import requests
    from src.agent.llm_client import LLMClient

    class Session:
        def post(self, *args, **kwargs):
            raise requests.ConnectionError("offline")

    monkeypatch.setattr(LLMClient, "_build_session", staticmethod(Session))
    with HTTPAudit() as audit:
        with pytest.raises(ModelDisconnected):
            LLMClient._build_session().post("http://localhost/v1", json={"messages": [{"role": "user", "content": "question"}]}, headers={"Authorization": "secret"})
        assert audit.calls[0]["infrastructure_failure"] is True
        assert "secret" not in json.dumps(audit.calls)


def test_stream_disconnect_is_preserved_and_aborts(monkeypatch):
    import requests
    from src.agent.llm_client import LLMClient

    class Response:
        status_code = 200

        def iter_lines(self):
            yield b"data: first token"
            raise requests.exceptions.ChunkedEncodingError("partial response")

    class Session:
        def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(LLMClient, "_build_session", staticmethod(Session))
    with HTTPAudit() as audit:
        response = LLMClient._build_session().post("http://localhost/v1", json={}, stream=True)
        with pytest.raises(ModelDisconnected):
            list(response.iter_lines())
        assert audit.calls[0]["infrastructure_failure"] is True


def test_replay_rejects_changed_or_duplicate_records_and_complete_run_truncation(tmp_path):
    candidate = case()
    manifest = {"selected_case_ids": [candidate["id"]], "modes": ["agent"], "fingerprint": "manifest"}
    record = record_for(candidate)
    assert len(rescore_records([candidate], [record], manifest, tmp_path)) == 1
    with pytest.raises(ValueError, match="duplicate"):
        rescore_records([candidate], [record, record], manifest, tmp_path)
    with pytest.raises(ValueError, match="changed"):
        rescore_records([candidate], [{**record, "case_fingerprint": "edited"}], manifest, tmp_path)
    with pytest.raises(ValueError, match="missing planned"):
        validate_run_coverage([], manifest, {"status": "completed"})
    validate_run_coverage([], manifest, {"status": "aborted_infrastructure"})


def test_exported_manual_and_study_materials_are_unfilled(pdf_source, tmp_path):
    candidate = {**case(), "category": "citation", "expected": {"sources": [pdf_source]}}
    record = record_for(candidate)
    export_review_materials(tmp_path, [candidate], [record])
    manual = read_jsonl(tmp_path / "manual_review.jsonl")[0]
    study = read_jsonl(tmp_path / "user_study_results.template.jsonl")[0]
    task = read_jsonl(tmp_path / "user_study_tasks.jsonl")[0]
    assert manual["reviewer"] is None and manual["supported"] is None
    assert manual["answer_sha256"] == hashlib.sha256(record["answer"].encode()).hexdigest()
    assert study["participant_id"] is None and study["elapsed_seconds"] is None
    assert "expected" not in task and "answer" not in task
