"""作品说明：验证候选标注解析器完整性；构造布局文本不能计为模型评估数据。"""
import json

import pytest

from eval.real.build_candidates import (
    build_labels, build_plan, citation_candidate, digest, freeze_questions, numeric_candidate,
)


DOCUMENT = dict(stock_code="600080", stock_abbr="金花股份", report_year=2023, report_period="FY",
                source_path="data_root/report.pdf", source_sha256="a" * 64, document_id="pdf-" + "a" * 64,
                split="development")


def page(rows, number=5):
    return dict(page=number, text=rows, text_path="data/runtime/pages/p5.txt", text_sha256="b" * 64)


def table(body, unit="元"):
    return "主要会计数据\n单位：" + unit + " 币种：人民币\n主要会计数据        2023年        2022年\n" + body


def test_preserves_numeric_column_boundaries_and_original_raw_precision():
    candidate, _ = numeric_candidate(DOCUMENT, [page(table("营业收入    579,374,501.21 534,036,500.95 8.49"))], "total_operating_revenue")
    assert candidate["raw_value"] == "579,374,501.21"
    assert candidate["value"] == pytest.approx(57937.450121)
    assert candidate["unit_multiplier"] == .0001
    assert "2023年" in candidate["column_header_excerpt"]


@pytest.mark.parametrize("first,second", [
    ("经营活动产生的现", "金流量净额"), ("经营活动产生的现金流", "量净额"),
    ("经营活动产生的", "现金流量净额"), ("经营活动产生的现金流量净", "额"),
])
def test_wrapped_cashflow_label_does_not_join_numeric_columns(first, second):
    candidate, _ = numeric_candidate(DOCUMENT, [page(table(first + "      -12,345.67  98,765.43\n" + second))], "operating_cf_net_amount")
    assert candidate["raw_value"] == "-12,345.67"
    assert candidate["value"] == pytest.approx(-1.234567)


def test_percent_unit_wrapped_under_row_is_preserved():
    candidate, _ = numeric_candidate(DOCUMENT, [page(table("主要财务指标\n加权平均净资产收益率    13.42  12.72\n（%）"))], "roe")
    assert candidate["value"] == 13.42 and candidate["raw_unit"] == "%"


def test_previous_page_header_can_support_continued_table():
    pages = [page(table("主要财务指标    2023年   2022年")), page("2023年年度报告\n基本每股收益（元／股）  1.217  1.041", 6)]
    candidate, _ = numeric_candidate(DOCUMENT, pages, "eps")
    assert candidate["value"] == 1.217 and candidate["page"] == 6


@pytest.mark.parametrize("body", [
    "第一季度  第二季度\n营业收入      100.00 200.00",
    "分季度主要财务数据\n营业收入      100.00 200.00",
    "母公司主要会计数据\n营业收入      100.00 200.00",
])
def test_quarter_or_parent_company_table_is_not_an_annual_candidate(body):
    candidate, _ = numeric_candidate(DOCUMENT, [page(table(body))], "total_operating_revenue")
    assert candidate is None


def test_multiple_matching_rows_remain_unannotated():
    candidate, reason = numeric_candidate(DOCUMENT, [page(table("营业收入    100.00  200.00\n营业收入    300.00  400.00"))], "total_operating_revenue")
    assert candidate is None and "Multiple" in reason


def test_missing_amount_unit_is_not_assumed_yuan():
    candidate, _ = numeric_candidate(DOCUMENT, [page("主要会计数据\n2023年 2022年\n营业收入   100 200")], "total_operating_revenue")
    assert candidate is None


def test_citation_draft_preserves_a_complete_original_sentence():
    opening = "本报告期公司持续优化生产经营，提升产品质量并加强研发投入。" * 4
    text = "一、经营情况讨论与分析\n" + opening + "\n" + "公司下一段尚未结束的"
    candidate, _ = citation_candidate(DOCUMENT, [page(text)])
    assert candidate["excerpt"] == opening
    assert candidate["human_verified"] is False


def test_table_of_contents_is_not_operating_discussion_evidence():
    candidate, _ = citation_candidate(DOCUMENT, [page("一、经营情况讨论与分析........8\n二、主要产品和业务........9")])
    assert candidate is None


def test_questions_are_frozen_without_answers_and_missing_parse_stays_in_denominator(tmp_path):
    documents = [{**DOCUMENT, "report_year": year, "report_period": period, "document_id": f"pdf-{year}-{period}"}
                 for year, period in [(2022, "FY"), (2023, "FY"), (2023, "HY")]]
    manifest = tmp_path / "report_manifest.json"
    manifest.write_text(json.dumps({"documents": documents}), encoding="utf-8")
    plan = build_plan(documents)
    freeze_questions(tmp_path, plan, manifest)
    questions = [json.loads(line) for line in (tmp_path / "questions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(questions) == 15
    assert all(set(question) == {"id", "question", "category", "split"} for question in questions)
    labels = build_labels(plan, {}, {})
    assert len(labels) == len(questions)
    assert sum(label["label_status"] == "annotation_missing" for label in labels) == 14
    assert all(label["human_verified"] is False for label in labels)
    original_hash = digest(tmp_path / "questions.jsonl")
    freeze_questions(tmp_path, plan, manifest)
    assert digest(tmp_path / "questions.jsonl") == original_hash
    manifest.write_text(json.dumps({"documents": documents, "changed": True}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="manifest changed"):
        freeze_questions(tmp_path, plan, manifest)
