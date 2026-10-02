import json
import pytest
from pathlib import Path

from scripts.populate_financial_qa import (
    filter_documents_by_stock_codes,
    load_stock_codes_file,
    publication_decision,
    select_documents,
)


ROOT = Path(__file__).resolve().parents[1]


def _document(
    file_name,
    *,
    report_kind="full",
    page_count=100,
    sha256="sha",
    ocr_sha256="ocr",
    stock_code="000001",
    report_year=2023,
    report_period="FY",
):
    return {
        "stock_code": stock_code,
        "report_year": report_year,
        "report_period": report_period,
        "report_kind": report_kind,
        "page_count": page_count,
        "sha256": sha256,
        "ocr_sha256": ocr_sha256,
        "file_name": file_name,
        "source_path": f"data_root/financial_reports/{file_name}",
    }


def test_explicit_long_full_report_overrides_false_summary_classification():
    manifest = [
        _document(
            "盘龙药业：2023年半年度报告.pdf",
            report_kind="summary",
            page_count=170,
            sha256="full",
            stock_code="002864",
            report_period="HY",
        ),
        _document(
            "盘龙药业：2023年半年度报告摘要.pdf",
            report_kind="summary",
            page_count=4,
            sha256="summary",
            stock_code="002864",
            report_period="HY",
        ),
    ]

    selected, excluded = select_documents(manifest)

    assert [row["sha256"] for row in selected] == ["full"]
    assert excluded == []


def test_true_summary_only_identity_stays_excluded():
    manifest = [
        _document(
            "600566_20240410_9Y58.pdf",
            report_kind="summary",
            page_count=9,
            sha256="summary-only",
            stock_code="600566",
        )
    ]

    selected, excluded = select_documents(manifest)

    assert selected == []
    assert excluded[0]["reason"] == "no_full_report"


def test_conflicting_corrected_versions_are_not_resolved_by_ocr_availability():
    manifest = [
        _document(
            "新光药业：2022年年度报告全文（更正后） (1).pdf",
            sha256="older-corrected",
            ocr_sha256="available-ocr",
            stock_code="300519",
            report_year=2022,
        ),
        _document(
            "新光药业：2022年年度报告全文（更正后）.pdf",
            sha256="newer-corrected",
            ocr_sha256="",
            stock_code="300519",
            report_year=2022,
        ),
    ]

    selected, excluded = select_documents(manifest)

    assert selected == []
    assert excluded[0]["reason"] == "conflicting_full_versions"
    assert set(excluded[0]["files"]) == {
        "新光药业：2022年年度报告全文（更正后） (1).pdf",
        "新光药业：2022年年度报告全文（更正后）.pdf",
    }


@pytest.mark.skipif(not (ROOT / "data/runtime/real_validation/financial_library_1417/manifest.json").is_file(), reason="本机私有真实语料覆盖审计，不属于公开安装依赖")
def test_current_manifest_has_only_two_intentional_exclusions():
    manifest = json.loads(
        (
            ROOT
            / "data/runtime/real_validation/financial_library_1417/manifest.json"
        ).read_text(encoding="utf-8")
    )

    selected, excluded = select_documents(manifest)

    assert len(selected) == 942
    assert {
        (tuple(row["identity"]), row["reason"])
        for row in excluded
    } == {
        (("300519", 2022, "FY"), "conflicting_full_versions"),
        (("600566", 2023, "FY"), "no_full_report"),
    }


def test_publication_allows_only_audited_absence_warnings():
    rollout = {"audit_status": "warning", "extraction": {"issues": []}}
    allowed, reason = publication_decision(
        {
            "review": "warn",
            "warnings": [
                "llm_extraction_disabled_skipped(core_metrics)",
                "cashflow_critical_missing",
                "cashflow_incomplete(2/4)",
            ],
        },
        rollout,
        publish_warnings=True,
    )
    assert allowed is True
    assert reason == "safe_partial"


def test_publication_blocks_anomalies_even_with_publish_warnings():
    rollout = {"audit_status": "warning", "extraction": {"issues": []}}
    allowed, reason = publication_decision(
        {
            "review": "warn",
            "warnings": ["cashflow_reconciliation_broken: net(10) vs components(20)"],
        },
        rollout,
        publish_warnings=True,
    )
    assert allowed is False
    assert reason.startswith("Unsafe review warnings:")


def test_publication_blocks_failed_source_evidence_audit():
    allowed, reason = publication_decision(
        {"review": "pass", "warnings": []},
        {
            "audit_status": "blocked",
            "extraction": {"issues": ["detail_raw_value_not_on_source_page"]},
        },
        publish_warnings=True,
    )
    assert allowed is False
    assert "blocked" in reason.lower()


def test_demo_company_allowlist_keeps_all_periods_in_requested_order_independent():
    documents = [
        _document("a.pdf", stock_code="000001", report_period="FY"),
        _document("b.pdf", stock_code="000002", report_period="HY"),
        _document("c.pdf", stock_code="000001", report_period="Q1"),
    ]
    selected, requested = filter_documents_by_stock_codes(documents, "000002, 000001,000002")

    assert requested == ["000002", "000001"]
    assert [(row["stock_code"], row["report_period"]) for row in selected] == [
        ("000001", "FY"),
        ("000002", "HY"),
        ("000001", "Q1"),
    ]


def test_demo_company_allowlist_rejects_unknown_or_malformed_codes():
    documents = [_document("a.pdf", stock_code="000001")]

    try:
        filter_documents_by_stock_codes(documents, "000001,ABC")
        assert False, "expected malformed code rejection"
    except ValueError as error:
        assert "Invalid stock code" in str(error)

    try:
        filter_documents_by_stock_codes(documents, "000002")
        assert False, "expected missing company rejection"
    except ValueError as error:
        assert "no selected full reports" in str(error)


def test_demo_company_file_reads_named_entries(tmp_path):
    path = tmp_path / "companies.json"
    path.write_text(json.dumps({
        "companies": [
            {"stock_code": "002082", "stock_abbr": "万邦德"},
            {"stock_code": "002390", "stock_abbr": "信邦制药"},
        ]
    }, ensure_ascii=False), encoding="utf-8")

    assert load_stock_codes_file(path) == "002082,002390"
