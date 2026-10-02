import json
from decimal import Decimal

from scripts.audit_financial_qa_rollout import (
    TABLES,
    audit_database_identity,
    audit_extraction_detail,
    compare_legacy,
    raw_value_appears,
    read_journal,
)


def _manifest():
    return {
        "stock_code": "000423",
        "company": "东阿阿胶",
        "report_year": 2023,
        "report_period": "FY",
        "source_path": "data_root/financial_reports/report.pdf",
        "sha256": "pdf-sha",
        "ocr_sha256": "ocr-sha",
        "source_key": "source-key",
        "page_count": 2,
        "text_page_count": 2,
    }


def _empty_snapshot():
    return {
        "tables": {name: {} for name in TABLES},
        "provenance": {},
        "duplicates": {name: {} for name in (*TABLES, "financial_report_provenance")},
        "row_counts": {name: 0 for name in (*TABLES, "financial_report_provenance")},
    }


def test_raw_value_appearance_handles_financial_number_formats():
    text = "| 净利润 | 5,370,034,362.2700 |\n| 现金流 | (481,075,759.49) |"

    assert raw_value_appears(5370034362.27, text)
    assert raw_value_appears(Decimal("-481075759.490"), text)
    assert not raw_value_appears(481075759.48, text)


def test_detail_audit_checks_identity_hash_page_and_raw_value():
    manifest = _manifest()
    result = {
        "status": "success",
        "data": {
            "stock_code": "000423",
            "report_year": 2023,
            "report_period": "FY",
            "source_is_summary": False,
            "_source_document": {
                "source_sha256": "pdf-sha",
                "source_path": manifest["source_path"],
                "page_count": 2,
            },
            "net_profit": 537003.44,
            "eps": 1.23,
            "roe": 8.2,
            "_accepted_fields": ["source_file", "net_profit", "eps", "roe"],
            "_field_sources": {
                "net_profit": {
                    "page_start": 1,
                    "page_end": 1,
                    "raw_value": 5370034362.27,
                    "normalized_value": 537003.44,
                    "statement_scope": "consolidated",
                },
                "eps": {
                    "page_start": 2,
                    "page_end": 2,
                    "raw_value": 1.23,
                    "normalized_value": 1.23,
                    "statement_scope": "unknown",
                },
                "roe": {
                    "page_start": 3,
                    "page_end": 3,
                    "raw_value": 8.2,
                    "normalized_value": 8.2,
                    "statement_scope": "unknown",
                },
            },
            "_pre_save_review_status": "warn",
            "_pre_save_review_warnings": ["cashflow_incomplete(2/4)"],
            "_pre_save_review_blockers": [],
            "_missing_required_fields": ["financing_cf_net_amount"],
        },
    }
    pages = ["净利润 5,370,034,362.27", "基本每股收益 1.22"]
    prepared = {"sha256": "pdf-sha", "ocr_sha256": "ocr-sha"}

    audit = audit_extraction_detail(result, manifest, pages, prepared)

    assert audit["accepted_fields"] == 3
    assert audit["sources"]["located"] == 2
    assert audit["sources"]["unlocated_fields"] == ["roe"]
    assert [item["field"] for item in audit["sources"]["raw_missing"]] == ["eps"]
    assert "detail_invalid_page_locator" in audit["issues"]
    assert "detail_raw_value_not_on_source_page" in audit["issues"]
    assert audit["warnings"] == ["cashflow_incomplete(2/4)"]
    assert audit["missing_required_fields"] == ["financing_cf_net_amount"]


def test_database_audit_reconciles_rows_facts_and_page_text():
    manifest = _manifest()
    identity = ("000423", 2023, "FY")
    snapshot = _empty_snapshot()
    for name in TABLES:
        snapshot["tables"][name][identity] = {
            "stock_code": "000423", "stock_abbr": "东阿阿胶",
            "report_year": 2023, "report_period": "FY",
        }
    snapshot["tables"]["income_sheet"][identity]["net_profit"] = Decimal("537003.44")
    snapshot["tables"]["core_performance_indicators_sheet"][identity]["eps"] = Decimal("1.2300")
    snapshot["provenance"][identity] = {
        "stock_code": "000423", "report_year": 2023, "report_period": "FY",
        "document": {
            "source_sha256": "pdf-sha", "source_path": manifest["source_path"],
            "page_count": 2,
        },
        "facts": [
            {
                "table": "income_sheet", "field": "net_profit", "value": 537003.44,
                "status": "source_located", "source_sha256": "pdf-sha",
                "page_start": 1, "page_end": 1, "raw_value": 5370034362.27,
                "statement_scope": "consolidated",
            },
            {
                "table": "core_performance_indicators_sheet", "field": "eps", "value": 1.23,
                "status": "source_unlocated",
            },
        ],
    }

    audit = audit_database_identity(
        snapshot, manifest, ["净利润 5,370,034,362.27", "每股收益 1.23"]
    )

    assert audit["source_located"] == 1
    assert audit["source_unlocated"] == 1
    assert audit["raw_checked"] == audit["raw_found"] == 1
    assert audit["missing_facts"] == []
    assert audit["value_mismatches"] == []
    assert audit["statement_scope"] == {"consolidated": 1}


def test_legacy_comparison_reports_missing_changed_and_matching_fields():
    identity = ("000423", 2023, "FY")
    legacy = _empty_snapshot()
    target = _empty_snapshot()
    for name in TABLES:
        legacy["tables"][name][identity] = {
            "stock_code": "000423", "stock_abbr": "东阿阿胶",
            "report_year": 2023, "report_period": "FY",
        }
        target["tables"][name][identity] = dict(legacy["tables"][name][identity])
    legacy["tables"]["income_sheet"][identity].update(
        net_profit=Decimal("100.00"), total_profit=Decimal("120.00")
    )
    target["tables"]["income_sheet"][identity].update(
        net_profit=Decimal("100.00"), total_profit=Decimal("121.00")
    )
    legacy["row_counts"] = {name: 1 for name in TABLES}
    details = {identity: {"net_profit": 100.0}}

    audit = compare_legacy(legacy, target, details)

    fresh = audit["fresh_detail_comparison"]
    serving = audit["serving_database_comparison"]
    assert fresh["overlap_identities"] == 1
    assert fresh["matching_fields"] == 1
    assert fresh["fresh_missing"] == 1
    assert serving["matching_fields"] == 1
    assert serving["value_changed"] == 1


def test_journal_reader_uses_last_record_and_ignores_only_partial_tail(tmp_path):
    path = tmp_path / "extraction.jsonl"
    path.write_text(
        json.dumps({"source_sha256": "a", "status": "failed"}) + "\n"
        + json.dumps({"source_sha256": "a", "status": "extracted"}) + "\n"
        + '{"source_sha256":',
        encoding="utf-8",
    )

    records, latest, ignored = read_journal(path)

    assert len(records) == 2
    assert latest["a"]["status"] == "extracted"
    assert ignored == 1
