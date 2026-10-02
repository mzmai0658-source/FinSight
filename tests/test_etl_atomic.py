"""作品说明：以真实事务型 SQLite 验证财务导入完整性。此处是持久化故障测试，不计为模型或财务泛化成绩，不依赖运行中的数据库、OCR 或模型服务。"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pandas.errors import DatabaseError
from pypdf import PdfWriter
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from src.etl import etl_worker, provenance_store
from src.etl.etl_worker import ETLWorker
from src.etl.save_result import BatchSaveError, RecordNotImportable
from src.init_db import Base
from src.utils import provenance


TABLES = (
    "income_sheet", "balance_sheet", "cash_flow_sheet",
    "core_performance_indicators_sheet",
)
IDENTITY = {"stock_code": "990001", "report_year": 2024, "report_period": "FY"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + (tmp_path / "atomic.sqlite").as_posix())
    Base.metadata.create_all(engine)
    provenance_store.metadata.create_all(engine)
    monkeypatch.setattr(etl_worker, "get_code_to_name", lambda: {"990001": "fixture", "990002": "fixture2"})
    monkeypatch.setattr(etl_worker, "resolve_stock_abbr", lambda code: "fixture")
    monkeypatch.setattr(provenance_store, "ROOT", tmp_path)
    monkeypatch.setattr(provenance, "ROOT", tmp_path)
    monkeypatch.delenv("FINANCIAL_FACTS_MANIFEST", raising=False)
    pdf = tmp_path / "data_root" / "report.pdf"
    pdf.parent.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with pdf.open("wb") as stream:
        writer.write(stream)
    worker = ETLWorker(object())
    worker._db_engine = engine
    yield worker, engine, pdf
    engine.dispose()


def record(pdf=None, **changes):
    data = {
        **IDENTITY, "stock_abbr": "fixture", "net_profit": 100.25,
        "asset_total_assets": 1500.50, "operating_cf_net_amount": 75.25,
        "eps": 1.1234,
    }
    if pdf:
        data["_source_document"] = provenance_store.describe_pdf(pdf)
        data["_field_sources"] = {
            name: {"page_start": 1, "page_end": 1, "raw_value": value * 10000,
                   "unit_multiplier": 0.0001, "table_name": "Fixture statement",
                   "statement_scope": "consolidated", "extraction_mode": "rule"}
            for name, value in data.items() if name in {"net_profit", "asset_total_assets", "operating_cf_net_amount", "eps"}
        }
    data.update(changes)
    return data


def database_state(engine):
    with engine.connect() as conn:
        return {
            table: [dict(row) for row in conn.execute(text(f"SELECT * FROM {table} ORDER BY 1")).mappings()]
            for table in (*TABLES, "financial_report_provenance", "financial_report_versions")
        }


def facts_for(engine, identity=None):
    with engine.connect() as conn:
        return provenance_store.read_facts(conn, [identity or IDENTITY])


def test_second_table_database_failure_restores_all_previous_rows_and_versions(store):
    worker, engine, pdf = store
    worker._save_to_db(record(pdf))
    before = database_state(engine)
    # 作品说明：在利润表插入后由数据库触发失败，验证真实事务回滚而非模拟返回值。
    with engine.begin() as conn:
        conn.execute(text("CREATE TRIGGER reject_balance BEFORE INSERT ON balance_sheet "
                          "WHEN NEW.asset_total_assets = -999 "
                          "BEGIN SELECT RAISE(ABORT, 'injected balance write failure'); END"))
    with pytest.raises((IntegrityError, DatabaseError), match="injected balance"):
        worker._save_to_db(record(pdf, net_profit=999, asset_total_assets=-999))
    assert database_state(engine) == before


@pytest.mark.parametrize("source_table", ["financial_report_versions", "financial_report_provenance"])
def test_source_database_failure_rolls_back_all_financial_rows(store, source_table):
    worker, engine, pdf = store
    worker._save_to_db(record(pdf))
    before = database_state(engine)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TRIGGER reject_source BEFORE INSERT ON {source_table} "
                          "BEGIN SELECT RAISE(ABORT, 'injected source write failure'); END"))
    with pytest.raises(IntegrityError, match="injected source"):
        worker._save_to_db(record(pdf, net_profit=501.25))
    assert database_state(engine) == before


def test_retry_same_snapshot_does_not_duplicate_rows_or_source_versions(store):
    worker, engine, pdf = store
    original = record(pdf)
    worker._save_to_db(deepcopy(original))
    before = database_state(engine)
    worker._save_to_db(deepcopy(original))
    assert database_state(engine) == before
    assert all(len(before[table]) == 1 for table in TABLES)
    assert len(before["financial_report_versions"]) == 1


def test_complete_replacement_removes_fields_from_previous_source_snapshot(store):
    worker, engine, pdf = store
    worker._save_to_db(record(pdf))
    worker._save_to_db({**IDENTITY, "net_profit": 202.50})
    state = database_state(engine)
    assert state["income_sheet"][0]["net_profit"] == 202.50
    assert all(state[table] == [] for table in TABLES[1:])
    assert {(fact["table"], fact["field"], fact["value"]) for fact in facts_for(engine)} == {
        ("income_sheet", "net_profit", 202.50),
    }
    assert len(state["financial_report_versions"]) == 2


@pytest.mark.parametrize("fields", [{}, {"net_profit": None}, {"net_profit": float("nan")}, {"eps": None, "asset_total_assets": None}])
def test_no_usable_financial_value_cannot_replace_a_saved_report(store, fields):
    worker, engine, pdf = store
    worker._save_to_db(record(pdf))
    before = database_state(engine)
    with pytest.raises(RecordNotImportable):
        worker._save_to_db({**IDENTITY, **fields})
    assert database_state(engine) == before


def test_zero_is_a_valid_financial_value(store):
    worker, engine, _ = store
    result = worker._save_to_db({**IDENTITY, "net_profit": 0})
    assert result["status"] == "committed"
    assert database_state(engine)["income_sheet"][0]["net_profit"] == 0


@pytest.mark.parametrize("bad_identity", [
    {"report_year": None}, {"report_year": 2024.5},
    {"report_period": ""}, {"report_period": "UNKNOWN"},
])
def test_missing_or_invalid_period_identity_never_creates_a_snapshot(store, bad_identity):
    worker, engine, _ = store
    with pytest.raises(RecordNotImportable):
        worker._save_to_db({**IDENTITY, "net_profit": 50, **bad_identity})
    assert not any(database_state(engine).values())


def test_summary_cannot_create_empty_report_identity(store):
    worker, engine, _ = store
    with pytest.raises(RecordNotImportable):
        worker._save_to_db(record(source_is_summary=True))
    assert not any(database_state(engine).values())


def test_batch_failure_counts_match_durable_commits_and_failed_record_status(store):
    worker, engine, _ = store
    with engine.begin() as conn:
        conn.execute(text("CREATE TRIGGER reject_second_company BEFORE INSERT ON balance_sheet "
                          "WHEN NEW.stock_code = '990002' "
                          "BEGIN SELECT RAISE(ABORT, 'company failure'); END"))
    rows = [
        {"status": "success", "data": record()},
        {"status": "success", "data": record(stock_code="990002")},
        {"status": "error", "message": "extraction failed"},
        {"status": "success", "data": record(_pre_save_review_status="block")},
    ]
    with pytest.raises(BatchSaveError) as caught:
        worker.save_records_to_db(rows)
    report = caught.value.report
    assert (report["committed"], report["failed"], report["skipped"]) == (1, 1, 2)
    assert [row["status"] for row in report["records"]] == ["committed", "failed", "skipped", "skipped"]
    assert rows[0]["status"] == "success"
    assert all(row["status"] == "error" for row in rows[1:])
    assert rows[1]["db_save"]["status"] == "failed"
    assert rows[3]["db_save"]["status"] == "skipped"
    state = database_state(engine)
    assert all([row["stock_code"] for row in state[table]] == ["990001"] for table in TABLES)
    assert len(state["financial_report_provenance"]) == 1


def test_summary_provenance_uses_accepted_values_and_does_not_invent_skipped_table(store):
    worker, engine, pdf = store
    # 作品说明：旧导入可能有合法数值却缺少来源位置。
    worker._save_to_db({**IDENTITY, "net_profit": 100.25})
    summary = record(pdf, source_is_summary=True, net_profit=999)
    worker._save_to_db(summary)
    state = database_state(engine)
    assert state["income_sheet"][0]["net_profit"] == 100.25
    assert all(state[table] == [] for table in TABLES[1:])
    facts = facts_for(engine)
    assert {(fact["table"], fact["field"]) for fact in facts} == {("income_sheet", "net_profit")}
    assert facts[0]["value"] == 100.25
    assert facts[0]["status"] == "source_unlocated"


def test_warn_quality_is_persisted_and_summary_fill_only_keeps_it(store):
    worker, engine, pdf = store
    worker._save_to_db(record(
        pdf,
        _pre_save_review_status="warn",
        _pre_save_review_warnings=["cashflow_incomplete(2/4)"],
        _pre_save_review_blockers=[],
        _missing_required_fields=["investing_cf_net_amount"],
        _optional_llm_missing_fields=["operating_profit"],
        anomaly_flags=["balance_equation_mismatch"],
        _requires_manual_review=True,
    ))

    with engine.connect() as conn:
        before = conn.execute(
            provenance_store.current.select().where(
                provenance_store.identity_clause(IDENTITY)
            )
        ).mappings().one()["payload"]
    assert before["quality"] == {
        "review_status": "warn",
        "warnings": ["cashflow_incomplete(2/4)"],
        "blockers": [],
        "missing_required_fields": ["investing_cf_net_amount"],
        "optional_missing_fields": ["operating_profit"],
        "anomaly_flags": ["balance_equation_mismatch"],
        "requires_manual_review": True,
    }

    # 作品说明：后续摘要可以补齐缺项，但证据弱于完整报告，不能将已发布快照重新标记为完全通过。
    worker._save_to_db(record(
        pdf,
        source_is_summary=True,
        _pre_save_review_status="pass",
        _pre_save_review_warnings=[],
        _missing_required_fields=[],
    ))
    with engine.connect() as conn:
        after = conn.execute(
            provenance_store.current.select().where(
                provenance_store.identity_clause(IDENTITY)
            )
        ).mappings().one()["payload"]
    assert after["quality"] == before["quality"]


def test_committed_source_requires_current_value_and_original_hash(store):
    worker, engine, pdf = store
    worker._save_to_db(record(pdf))
    lineage = {"profit": {"table": "income_sheet", "field": "net_profit"}}
    with engine.connect() as conn:
        valid = provenance.attach_provenance([{"profit": 100.25}], lineage, IDENTITY, connection=conn)
        assert valid[0]["_provenance"]["profit"]["source_sha256"] == provenance_store.describe_pdf(pdf)["source_sha256"]
        assert valid[0]["_provenance"]["profit"]["human_verified"] is False
        wrong = provenance.attach_provenance([{"profit": 100.26}], lineage, IDENTITY, connection=conn)
        assert "_provenance" not in wrong[0]
        wrong_year = provenance.attach_provenance([{"profit": 100.25}], lineage, {**IDENTITY, "report_year": 2023}, connection=conn)
        assert "_provenance" not in wrong_year[0]
        pdf.write_bytes(pdf.read_bytes() + b"\n% source edited after import\n")
        changed = provenance.attach_provenance([{"profit": 100.25}], lineage, IDENTITY, connection=conn)
        assert "_provenance" not in changed[0]


@pytest.mark.parametrize("location", [
    {"page_start": None}, {"page_start": 0}, {"page_start": 2},
    {"page_start": 1, "page_end": 2}, {"page_start": 1, "page_end": -1},
])
def test_invalid_pdf_page_is_never_persisted_as_located_source(store, location):
    worker, engine, pdf = store
    original = record(pdf)
    original["_field_sources"]["net_profit"].update(location)
    worker._save_to_db(original)
    profit_fact = next(fact for fact in facts_for(engine) if fact["field"] == "net_profit")
    assert profit_fact["status"] == "source_unlocated"


def test_missing_source_metadata_stays_explicitly_unlocated(store):
    worker, engine, _ = store
    worker._save_to_db(record())
    assert all(fact["status"] == "source_unlocated" for fact in facts_for(engine))
    with engine.connect() as conn:
        result = provenance.attach_provenance(
            [{"net_profit": 100.25}], {"net_profit": {"table": "income_sheet", "field": "net_profit"}},
            IDENTITY, connection=conn,
        )
    assert "_provenance" not in result[0]


@pytest.mark.parametrize("projection", ["net_profit AS profit", "stock_code, report_year, report_period, net_profit AS profit"])
def test_sql_tool_reads_committed_provenance_and_rejects_stale_value(store, monkeypatch, projection):
    from src.agent.sql_tool import SQLTool

    worker, engine, pdf = store
    worker._save_to_db(record(pdf))
    tool = SQLTool()
    monkeypatch.setattr(tool, "_get_engine", lambda: engine)
    query = (f"SELECT {projection} FROM income_sheet "
             "WHERE stock_code = '990001' AND report_year = 2024 AND report_period = 'FY'")
    answer = tool.run(query)
    assert answer["status"] == "success"
    assert answer["rows"][0]["_provenance"]["profit"]["page_start"] == 1
    with engine.begin() as conn:
        conn.execute(text("UPDATE income_sheet SET net_profit = 222 WHERE stock_code = '990001'"))
    changed = tool.run(query)
    assert changed["status"] == "success"
    assert changed["rows"][0]["profit"] == 222
    assert "_provenance" not in changed["rows"][0]


def test_two_concurrent_replacements_leave_one_consistent_snapshot(store):
    worker, engine, pdf = store
    worker._save_to_db(record(pdf))
    start = Barrier(2)

    def write_variant(multiplier):
        candidate = record(pdf)
        for field in ("net_profit", "asset_total_assets", "operating_cf_net_amount", "eps"):
            candidate[field] *= multiplier
            candidate["_field_sources"][field]["raw_value"] *= multiplier
        own_worker = ETLWorker(object())
        own_worker._db_engine = engine
        start.wait(timeout=5)
        return own_worker._save_to_db(candidate)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write_variant, (2, 3)))
    assert all(result["status"] == "committed" for result in results)
    state = database_state(engine)
    assert all(len(state[table]) == 1 for table in TABLES)
    multiplier = state["income_sheet"][0]["net_profit"] / 100.25
    assert multiplier in (2, 3)
    assert state["balance_sheet"][0]["asset_total_assets"] == pytest.approx(1500.50 * multiplier)
    assert state["cash_flow_sheet"][0]["operating_cf_net_amount"] == pytest.approx(75.25 * multiplier)
    assert state["core_performance_indicators_sheet"][0]["eps"] == pytest.approx(1.1234 * multiplier)
    for fact in facts_for(engine):
        assert float(fact["value"]) == pytest.approx(state[fact["table"]][0][fact["field"]])
    assert len(state["financial_report_versions"]) == 3
