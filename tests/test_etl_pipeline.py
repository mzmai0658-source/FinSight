"""作品说明：通过本机样例与内存库测试导入管线，不依赖 OCR、模型或向量服务。"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.etl.derived import DERIVED_YOY_MAP, recompute_company_derived
from src.etl.ocr_client import OcrNotConfiguredError, ensure_ocr_json
from src.etl.research_metadata import ResearchMeta, lookup_research_meta, normalize_title
from src.utils.ocr_json_parser import (
    OCR_JSON_SUFFIXES,
    PADDLE_OCR_JSON_SUFFIX,
    PADDLE_OCR_JSON_SUFFIX_V16,
    find_json_cache_for_pdf,
)


# 作品说明：OCR 缓存解析

class TestOcrCacheResolution:
    def test_v16_preferred_over_v15(self, tmp_path):
        pdf = tmp_path / "600000_2024年年度报告.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        v15 = Path(str(pdf) + PADDLE_OCR_JSON_SUFFIX)
        v16 = Path(str(pdf) + PADDLE_OCR_JSON_SUFFIX_V16)
        v15.write_text("{}", encoding="utf-8")
        v16.write_text("{}", encoding="utf-8")

        assert find_json_cache_for_pdf(str(pdf)) == str(v16)

    def test_v15_fallback(self, tmp_path):
        pdf = tmp_path / "600000_2024年年度报告.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        v15 = Path(str(pdf) + PADDLE_OCR_JSON_SUFFIX)
        v15.write_text("{}", encoding="utf-8")

        assert find_json_cache_for_pdf(str(pdf)) == str(v15)

    def test_suffix_order(self):
        assert OCR_JSON_SUFFIXES[0] == PADDLE_OCR_JSON_SUFFIX_V16
        assert PADDLE_OCR_JSON_SUFFIX in OCR_JSON_SUFFIXES

    def test_ensure_ocr_json_uses_cache_without_network(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OCR_API_URL", raising=False)
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        from src.etl.ocr_client import write_validated_ocr_cache
        cache = write_validated_ocr_cache(pdf, [{"markdown": {"text": "report content"}}])

        assert ensure_ocr_json(pdf) == cache

    def test_ensure_ocr_json_raises_when_not_configured(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OCR_API_URL", raising=False)
        pdf = tmp_path / "b.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        with pytest.raises(OcrNotConfiguredError):
            ensure_ocr_json(pdf)


# 作品说明：公司级同比重算

def _make_engine():
    engine = create_engine("sqlite://")
    ddl = {
        "income_sheet": "net_profit REAL, total_operating_revenue REAL, "
                        "net_profit_yoy_growth REAL, operating_revenue_yoy_growth REAL",
        "balance_sheet": "asset_total_assets REAL, liability_total_liabilities REAL, "
                         "asset_total_assets_yoy_growth REAL, liability_total_liabilities_yoy_growth REAL",
        "cash_flow_sheet": "net_cash_flow REAL, net_cash_flow_yoy_growth REAL",
        "core_performance_indicators_sheet":
            "total_operating_revenue REAL, net_profit_10k_yuan REAL, net_profit_excl_non_recurring REAL, "
            "operating_revenue_yoy_growth REAL, net_profit_yoy_growth REAL, net_profit_excl_non_recurring_yoy REAL",
    }
    with engine.begin() as conn:
        for table, cols in ddl.items():
            conn.execute(text(
                f"CREATE TABLE {table} (stock_code TEXT, report_year INTEGER, report_period TEXT, {cols})"
            ))
    return engine


class TestRecomputeCompanyDerived:
    def test_fills_missing_yoy_for_adjacent_years(self):
        engine = _make_engine()
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO income_sheet (stock_code, report_year, report_period, net_profit, total_operating_revenue) "
                "VALUES ('600000', 2023, 'FY', 100.0, 1000.0), ('600000', 2024, 'FY', 150.0, 900.0)"
            ))

        updated = recompute_company_derived("600000", engine=engine)

        assert updated["income_sheet"] == 1
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT net_profit_yoy_growth, operating_revenue_yoy_growth FROM income_sheet "
                "WHERE report_year = 2024"
            )).fetchone()
        assert row[0] == pytest.approx(50.0)
        assert row[1] == pytest.approx(-10.0)

    def test_fill_only_keeps_disclosed_values(self):
        engine = _make_engine()
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO income_sheet "
                "(stock_code, report_year, report_period, net_profit, total_operating_revenue, net_profit_yoy_growth) "
                "VALUES ('600000', 2023, 'FY', 100.0, 1000.0, NULL), "
                "       ('600000', 2024, 'FY', 150.0, 1100.0, 42.42)"
            ))

        recompute_company_derived("600000", engine=engine)

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT net_profit_yoy_growth, operating_revenue_yoy_growth FROM income_sheet "
                "WHERE report_year = 2024"
            )).fetchone()
        # 作品说明：财报披露值保留，缺失字段才回填
        assert row[0] == pytest.approx(42.42)
        assert row[1] == pytest.approx(10.0)

    def test_growth_limit_guard_blocks_tiny_base(self):
        engine = _make_engine()
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO cash_flow_sheet (stock_code, report_year, report_period, net_cash_flow) "
                "VALUES ('600000', 2023, 'FY', 0.001), ('600000', 2024, 'FY', 500.0)"
            ))

        recompute_company_derived("600000", engine=engine)

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT net_cash_flow_yoy_growth FROM cash_flow_sheet WHERE report_year = 2024"
            )).fetchone()
        assert row[0] is None

    def test_periods_do_not_cross_match(self):
        engine = _make_engine()
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO income_sheet (stock_code, report_year, report_period, net_profit, total_operating_revenue) "
                "VALUES ('600000', 2023, 'Q1', 10.0, 100.0), ('600000', 2024, 'FY', 50.0, 500.0)"
            ))

        updated = recompute_company_derived("600000", engine=engine)
        assert updated["income_sheet"] == 0

    def test_map_covers_all_four_tables(self):
        assert set(DERIVED_YOY_MAP) == {
            "income_sheet", "balance_sheet", "cash_flow_sheet", "core_performance_indicators_sheet",
        }


# 作品说明：研报元数据索引

class TestResearchMetadata:
    def _index(self):
        meta = ResearchMeta(
            title="重组蛋白专家，科研试剂新星",
            report_type="stock",
            stock_code="301080",
            stock_name="百普赛斯",
            org_sname="太平洋",
            rating="买入",
            publish_date="2025-12-31",
        )
        return {normalize_title(meta.title): meta}

    def test_lookup_by_pdf_filename(self):
        index = self._index()
        hit = lookup_research_meta(index, "重组蛋白专家，科研试剂新星.pdf")
        assert hit is not None and hit.stock_code == "301080"

    def test_lookup_tolerates_fullwidth_punctuation(self):
        index = self._index()
        hit = lookup_research_meta(index, "重组蛋白专家,科研试剂新星")
        assert hit is not None and hit.rating == "买入"

    def test_lookup_miss_returns_none(self):
        assert lookup_research_meta(self._index(), "不存在的研报.pdf") is None


# 作品说明：单文件管线编排（全部打桩）

class TestSingleFilePipeline:
    def _run(self, tmp_path, monkeypatch, *, extract_status="success", save_raises=False):
        from src.etl import pipeline as pl

        pdf = tmp_path / "600000_2024年年度报告.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        monkeypatch.setattr(pl, "ensure_ocr_json", lambda p: Path(str(p) + PADDLE_OCR_JSON_SUFFIX_V16))

        calls = []

        class FakeWorker:
            def __init__(self, llm):
                pass

            def run(self, file_path, save_to_db=True, source_pdf_path=None):
                calls.append("extract")
                return {
                    "status": extract_status,
                    "message": "" if extract_status == "success" else "抽取失败原因",
                    "data": {"stock_code": "600000", "stock_abbr": "测试", "report_year": 2024, "report_period": "FY"},
                }

            def backfill_cross_file_growths(self, records):
                calls.append("backfill")

            def save_records_to_db(self, records):
                calls.append("save")
                if save_raises:
                    raise RuntimeError("db down")
                return {'committed':1}

        import src.etl.etl_worker as worker_mod
        monkeypatch.setattr(worker_mod, "ETLWorker", FakeWorker)
        monkeypatch.setattr(pl, "recompute_company_derived", lambda code: calls.append("derived") or {"t": 1})
        monkeypatch.setattr(pl, "_update_company_master", lambda code, abbr: calls.append("company") or "ok")

        report = pl.run_single_file(pdf, save_to_db=True, ingest_rag=False, llm_client=object())
        return report, calls

    def test_success_flow_runs_all_steps_in_order(self, tmp_path, monkeypatch):
        report, calls = self._run(tmp_path, monkeypatch)

        assert report.status == "success"
        assert report.stock_code == "600000"
        assert calls == ["extract", "backfill", "save", "derived", "company"]
        step_status = {s.name: s.status for s in report.steps}
        assert step_status == {
            "ocr": "ok", "extract": "ok", "save_db": "ok",
            "derived": "ok", "company": "ok", "rag": "skipped",
        }

    def test_extract_failure_stops_pipeline(self, tmp_path, monkeypatch):
        report, calls = self._run(tmp_path, monkeypatch, extract_status="error")

        assert report.status == "failed"
        assert "save" not in calls and "derived" not in calls

    def test_save_failure_stops_before_derived(self, tmp_path, monkeypatch):
        report, calls = self._run(tmp_path, monkeypatch, save_raises=True)

        assert report.status == "failed"
        assert "derived" not in calls

    def test_missing_ocr_marks_failed(self, tmp_path, monkeypatch):
        from src.etl import pipeline as pl

        pdf = tmp_path / "no_cache.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        monkeypatch.delenv("OCR_API_URL", raising=False)

        report = pl.run_single_file(pdf, ingest_rag=False, llm_client=object())

        assert report.status == "failed"
        assert report.steps[0].name == "ocr" and report.steps[0].status == "failed"


@pytest.mark.parametrize('stage', ['derived','company','rag'])
def test_post_save_failure_is_partial_and_retryable(tmp_path, monkeypatch, stage):
    from types import SimpleNamespace
    import src.etl.pipeline as pipeline
    import src.etl.etl_worker as worker_module
    import src.etl.rag_builder as rag
    saved=[]
    monkeypatch.setattr(pipeline,'ensure_ocr_json',lambda path:tmp_path/'ocr.json')
    worker=SimpleNamespace(run=lambda *args,**kw:dict(status='success',data=dict(stock_code='600000',stock_abbr='fixture',report_year=2024,report_period='FY')),
        backfill_cross_file_growths=lambda rows:None,save_records_to_db=lambda rows:saved.append(rows) or {'committed':1})
    monkeypatch.setattr(worker_module,'ETLWorker',lambda llm:worker)
    def fail(*args,**kwargs): raise RuntimeError('injected failure')
    monkeypatch.setattr(pipeline,'recompute_company_derived',fail if stage=='derived' else lambda *args:{})
    monkeypatch.setattr(pipeline,'_update_company_master',fail if stage=='company' else lambda *args:'updated')
    monkeypatch.setattr(rag,'ingest_financial_report_pdf',fail if stage=='rag' else lambda *args,**kwargs:1)
    report=pipeline.run_single_file(tmp_path/'report.pdf',llm_client=object())
    assert len(saved) == 1
    assert report.status == 'partial'
    assert report.to_dict()['retryable_steps'] == [stage]


def test_same_filename_cache_requires_same_original_pdf(tmp_path,monkeypatch):
    from src.etl.ocr_client import _find_workspace_cache
    import src.utils.data_paths as paths
    root=tmp_path/'reports'
    root.mkdir()
    uploaded=tmp_path/'annual.pdf'
    uploaded.write_bytes(b'new report')
    original=root/'annual.pdf'
    original.write_bytes(b'old report')
    cache=Path(str(original)+PADDLE_OCR_JSON_SUFFIX_V16)
    cache.write_text('{}')
    monkeypatch.setattr(paths,'find_financial_reports_root',lambda:root)
    monkeypatch.setattr(paths,'find_research_reports_root',lambda:None)
    assert _find_workspace_cache(uploaded) is None
    original.write_bytes(uploaded.read_bytes())
    from src.etl.ocr_client import write_validated_ocr_cache
    write_validated_ocr_cache(original, [{"markdown": {"text": "matching report"}}])
    assert _find_workspace_cache(uploaded) == cache
