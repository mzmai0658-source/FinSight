"""Official-source acquisition checks use generated PDFs; no OCR/cache is read."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas

_MODULE_PATH = Path(__file__).resolve().parents[1] / "src/etl/report_acquisition.py"
_SPEC = importlib.util.spec_from_file_location("acquisition_under_test", _MODULE_PATH)
acquisition = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(acquisition)


def report_pdf(company="美的集团股份有限公司", code="000333", year=2025, period="Q1", reference=None, reverse=False):
    output = io.BytesIO()
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    canvas = Canvas(output, invariant=1)
    canvas.setFont("STSong-Light", 12)
    canvas.drawString(50, 780, company)
    labels = {"FY": "年度报告", "HY": "半年度报告", "Q1": "第一季度报告", "Q3": "第三季度报告"}
    canvas.drawString(50, 750, f"{labels[period]} {year}" if reverse else f"{year}年{labels[period]}")
    canvas.drawString(50, 720, f"证券代码：{code}")
    if reference:
        canvas.drawString(50, 690, reference)
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def cohort_one():
    return {
        "schema_version": 1,
        "cohort_id": "test-new-only",
        "expected_reports": 1,
        "collection_as_of": "2026-10-21",
        "companies": [{"stock_code": "000333", "company": "美的集团", "legal_name": "美的集团股份有限公司", "org_id": "9900005965", "exchange": "szse", "split": "development"}],
        "reports": [{"report_id": "000333_2025_Q1", "stock_code": "000333", "company": "美的集团", "legal_name": "美的集团股份有限公司", "year": 2025, "period": "Q1", "split": "development", "sources": []}],
    }


def seed_manifest(path, *, url="https://static.cninfo.com.cn/finalpage/2025-04-30/1223412432.PDF"):
    manifest = acquisition.initialize_manifest(cohort_one())
    manifest["reports"][0].update(source_url=url, title="2025年一季度报告", status="discovered", announcement_id="1223412432", version="original")
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest


class Response:
    def __init__(self, body=b"", *, status=200, headers=None, url=None, payload=None, fail_after=None):
        self.body, self.status_code = body, status
        self.headers = headers or {}
        self.url = url or "https://static.cninfo.com.cn/finalpage/2025-04-30/1223412432.PDF"
        self.payload, self.fail_after = payload, fail_after

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        if self.fail_after:
            yield self.body[:self.fail_after]
            raise OSError("connection interrupted")
        yield self.body

    def close(self):
        pass


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.parametrize("title", ["2025年第一季度报告", "2025年一季度报告", "美的集团：2025年第一季度报告（更正后）"])
def test_title_accepts_only_requested_full_report(title):
    assert acquisition.validate_report_title("美的集团", 2025, "Q1", title)


@pytest.mark.parametrize("title", ["2025年第一季度报告摘要", "2025年第一季度报告（英文版）", "2025年第一季度报告（更正前）", "2025年第一季度报告的更正公告", "关于2025年第一季度报告的问询函", "2025年第一季度报告（H股）", "2025年第一季度报告（撤销）", "2024年第一季度报告", "2025年第三季度报告", "2025年第一季度报告（IFRS）"])
def test_title_rejects_wrong_period_and_ineligible_variants(title):
    assert not acquisition.validate_report_title("美的集团", 2025, "Q1", title)


def test_title_accepts_exact_registered_legal_name_from_cohort():
    company = {"company": "伊利股份", "legal_name": "内蒙古伊利实业集团股份有限公司"}
    assert acquisition.validate_report_title(company, 2025, "Q1", "内蒙古伊利实业集团股份有限公司2025年第一季度报告")
    assert not acquisition.validate_report_title(company, 2025, "Q1", "另一家公司2025年第一季度报告")


def test_load_cohort_has_exact_120_new_company_identity_slots():
    root = Path(__file__).resolve().parents[1]
    cohort = acquisition.load_cohort(root / "config/cloud_upload120_cohort.json")
    reports = cohort["reports"]
    assert len(reports) == len({row["report_id"] for row in reports}) == 120
    assert len({row["stock_code"] for row in reports}) == 10
    assert {row["year"] for row in reports} == {2023, 2024, 2025}
    assert {row["period"] for row in reports} == {"FY", "HY", "Q1", "Q3"}
    assert sum(row["split"] == "development" for row in reports) == 72
    assert sum(row["split"] == "holdout" for row in reports) == 48


def test_selection_rejects_summary_english_wrong_stock_and_prefers_latest_revision():
    rows = [
        {"secCode": "000333", "announcementTitle": "2025年一季度报告", "announcementTime": 1, "announcementId": "1", "adjunctUrl": "finalpage/2025-04-30/1.PDF"},
        {"secCode": "000333", "announcementTitle": "2025年一季度报告（更正后）", "announcementTime": 2, "announcementId": "2", "adjunctUrl": "finalpage/2025-05-01/2.PDF"},
        {"secCode": "000333", "announcementTitle": "2025年一季度报告摘要", "announcementTime": 3, "announcementId": "3", "adjunctUrl": "finalpage/2025-05-02/3.PDF"},
        {"secCode": "000333", "announcementTitle": "2025年一季度报告（英文版）", "announcementTime": 4, "announcementId": "4", "adjunctUrl": "finalpage/2025-05-03/4.PDF"},
        {"secCode": "002594", "announcementTitle": "2025年一季度报告", "announcementTime": 5, "announcementId": "5", "adjunctUrl": "finalpage/2025-05-04/5.PDF"},
    ]
    result = acquisition.select_announcement(cohort_one()["reports"][0], rows)
    assert result["announcement_id"] == "2"
    assert result["version"] == "revised"
    assert result["source_url"] == "https://static.cninfo.com.cn/finalpage/2025-05-01/2.PDF"
    assert result["superseded_announcement_ids"] == ["1"]


def test_validate_pdf_checks_original_bytes_identity_pages_and_hash(tmp_path):
    path = tmp_path / "report.pdf"
    content = report_pdf()
    path.write_bytes(content)
    result = acquisition.validate_pdf(path, cohort_one()["reports"][0])
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert result["page_count"] == 1
    assert result["identity"]["company"] and result["identity"]["period"]
    path.write_bytes(report_pdf(code="002594", company="比亚迪股份有限公司"))
    with pytest.raises(ValueError, match="identity"):
        acquisition.validate_pdf(path, cohort_one()["reports"][0])
    path.write_text("<html>access denied</html>", encoding="utf-8")
    with pytest.raises(ValueError, match="PDF"):
        acquisition.validate_pdf(path, cohort_one()["reports"][0])


def test_image_cover_can_be_confirmed_by_later_original_front_matter(tmp_path):
    from pypdf import PdfWriter,PdfReader
    path=tmp_path/'designed-cover.pdf'
    writer=PdfWriter()
    for _ in range(5):writer.add_blank_page(600,800)
    writer.add_page(PdfReader(io.BytesIO(report_pdf())).pages[0])
    writer.write(path)
    result=acquisition.validate_pdf(path,cohort_one()['reports'][0])
    assert result['identity']['period'] is True and result['page_count']==6


def test_page_number_before_year_is_not_part_of_the_report_year(tmp_path):
    content=report_pdf(year=2025,period='HY')
    path=tmp_path/'numbered.pdf';path.write_bytes(content)
    original=acquisition._compact
    # 模拟页面提取顺序中的页码，不能把3与2025合成32025。
    from unittest.mock import patch
    from pypdf import PdfReader
    reader=PdfReader(path)
    text=reader.pages[0].extract_text()
    with patch('pypdf._page.PageObject.extract_text',return_value='3\n'+text):
        assert acquisition.validate_pdf(path,{**cohort_one()['reports'][0],'period':'HY'})['identity']['period']


def test_historical_report_reference_does_not_override_original_cover_period(tmp_path):
    path = tmp_path / "wrong_year.pdf"
    path.write_bytes(report_pdf(year=2024, reference="后续公告参考2025年第一季度报告"))
    with pytest.raises(ValueError, match="identity"):
        acquisition.validate_pdf(path, cohort_one()["reports"][0])


@pytest.mark.parametrize("period", ["FY", "HY"])
def test_designed_cover_with_year_after_report_label_keeps_exact_period(tmp_path, period):
    path = tmp_path / "reverse_cover.pdf"
    expected = {**cohort_one()["reports"][0], "period": period}
    path.write_bytes(report_pdf(period=period, reverse=True))
    result = acquisition.validate_pdf(path, expected)
    assert result["identity"]["year"] == 2025
    assert result["identity"]["report_period"] == period
    path.write_bytes(report_pdf(year=2024, period=period, reverse=True, reference=f"下一年度2025年{'年度报告' if period == 'FY' else '半年度报告'}"))
    with pytest.raises(ValueError, match="identity"):
        acquisition.validate_pdf(path, expected)


def test_discovery_requests_all_period_categories_and_persists_metadata(tmp_path):
    path = tmp_path / "sources.json"
    row = {"secCode": "000333", "announcementTitle": "2025年一季度报告", "announcementTime": 1745942400000, "announcementId": "1223412432", "adjunctUrl": "finalpage/2025-04-30/1223412432.PDF"}
    session = Session([Response(payload={"announcements": [row], "totalpages": 1})])
    result = acquisition.discover_cninfo_sources(cohort_one(), path, session=session)
    assert result["reports"][0]["status"] == "discovered"
    assert result["counts"]["missing"] == 0
    assert result["counts"]["verified"] == 0
    assert "category_ndbg_szsh" in session.calls[0][2]["data"]["category"]
    assert "category_bndbg_szsh" in session.calls[0][2]["data"]["category"]
    assert "category_yjdbg_szsh" in session.calls[0][2]["data"]["category"]
    assert "category_sjdbg_szsh" in session.calls[0][2]["data"]["category"]
    assert json.loads(path.read_text(encoding="utf-8"))["reports"][0]["announcement_id"] == "1223412432"


def test_metadata_failure_circuit_breaker_does_not_query_all_companies(tmp_path):
    cohort = cohort_one()
    cohort["companies"] = cohort["companies"] * 10
    session = Session([OSError("reset"), OSError("reset")])
    result = acquisition.discover_cninfo_sources(cohort, tmp_path / "sources.json", session=session, max_metadata_probes=2)
    assert len(session.calls) == 2
    assert result["metadata"]["network_blocked"] is True
    assert result["counts"]["missing"] == 1


def test_metadata_paginates_when_provider_totalpages_is_truncated(tmp_path):
    row = {"secCode": "000333", "announcementTitle": "2025年一季度报告", "announcementTime": 1, "announcementId": "1", "adjunctUrl": "finalpage/2025-04-30/1.PDF"}
    session = Session([Response(payload={"announcements": [{**row, "announcementTitle": "2025年一季度报告摘要"}], "totalpages": 0, "totalAnnouncement": 2}), Response(payload={"announcements": [row], "totalpages": 0, "totalAnnouncement": 2})])
    result = acquisition.discover_cninfo_sources(cohort_one(), tmp_path / "sources.json", session=session)
    assert len(session.calls) == 2
    assert result["counts"]["discovered"] == 1
    assert session.calls[1][2]["data"]["pageNum"] == 2


def test_atomic_download_and_resume_skips_verified_bytes(tmp_path):
    path = tmp_path / "sources.json"
    seed_manifest(path)
    content = report_pdf()
    session = Session([Response(content, headers={"Content-Length": str(len(content)), "Content-Type": "application/pdf"})])
    result = acquisition.acquire_reports(cohort_one(), tmp_path / "pdfs", path, session=session)
    report = result["reports"][0]
    assert report["status"] == "verified"
    assert Path(report["local_path"]).read_bytes() == content
    assert report["document_version"] == report["sha256"]
    assert report["download_url"] == report["source_url"]
    assert not list(tmp_path.rglob("*.part"))
    no_network = Session([])
    result = acquisition.acquire_reports(cohort_one(), tmp_path / "pdfs", path, session=no_network)
    assert result["counts"]["verified"] == 1
    assert not no_network.calls


def test_corrupted_saved_original_is_redownloaded(tmp_path):
    path = tmp_path / "sources.json"
    seed_manifest(path)
    content = report_pdf()
    first = acquisition.acquire_reports(cohort_one(), tmp_path / "pdfs", path, session=Session([Response(content)]))
    target = Path(first["reports"][0]["local_path"])
    target.write_bytes(b"corrupted local copy")
    session = Session([Response(content)])
    repaired = acquisition.acquire_reports(cohort_one(), tmp_path / "pdfs", path, session=session)
    assert repaired["counts"]["verified"] == 1
    assert target.read_bytes() == content
    assert len(session.calls) == 1


def test_interrupted_transfer_keeps_part_and_uses_validated_range_resume(tmp_path):
    path = tmp_path / "sources.json"
    seed_manifest(path)
    content = report_pdf()
    first = Session([Response(content, headers={"Content-Length": str(len(content)), "ETag": '"same"'}, fail_after=100)])
    result = acquisition.acquire_reports(cohort_one(), tmp_path / "pdfs", path, session=first)
    assert result["reports"][0]["status"] == "error"
    assert not list((tmp_path / "pdfs").rglob("*.pdf"))
    second = Session([Response(content[100:], status=206, headers={"Content-Range": f"bytes 100-{len(content)-1}/{len(content)}", "Content-Length": str(len(content)-100), "ETag": '"same"'})])
    result = acquisition.acquire_reports(cohort_one(), tmp_path / "pdfs", path, session=second)
    assert result["counts"]["verified"] == 1
    assert second.calls[0][2]["headers"]["Range"] == "bytes=100-"
    assert second.calls[0][2]["headers"]["If-Range"] == '"same"'
    assert result["reports"][0]["sha256"] == hashlib.sha256(content).hexdigest()


def test_finished_partial_rejected_by_range_server_restarts_once(tmp_path):
    path = tmp_path / "sources.json"
    seed_manifest(path)
    content = report_pdf()
    acquisition.acquire_reports(cohort_one(), tmp_path / "pdfs", path, session=Session([Response(content, fail_after=100)]))
    session = Session([Response(status=416), Response(content)])
    result = acquisition.acquire_reports(cohort_one(), tmp_path / "pdfs", path, session=session)
    assert result["counts"]["verified"] == 1
    assert len(session.calls) == 2
    assert "Range" not in session.calls[1][2]["headers"]


def test_unofficial_url_or_redirect_is_rejected_before_publication(tmp_path):
    path = tmp_path / "sources.json"
    seed_manifest(path, url="https://not-cninfo.example/report.pdf")
    session = Session([])
    result = acquisition.acquire_reports(cohort_one(), tmp_path / "pdfs", path, session=session)
    assert result["counts"]["verified"] == 0
    assert result["reports"][0]["status"] == "error"
    assert not session.calls
    seed_manifest(path)
    session = Session([Response(report_pdf(), url="https://untrusted.example/file.pdf")])
    result = acquisition.acquire_reports(cohort_one(), tmp_path / "pdfs", path, session=session)
    assert result["counts"]["verified"] == 0
    assert not list((tmp_path / "pdfs").rglob("*.pdf"))


def test_redirect_target_is_checked_before_any_download_request(tmp_path):
    path = tmp_path / "sources.json"
    seed_manifest(path)
    session = Session([Response(status=302, headers={"Location": "https://untrusted.example/file.pdf"})])
    result = acquisition.acquire_reports(cohort_one(), tmp_path / "pdfs", path, session=session)
    assert result["counts"]["verified"] == 0
    assert len(session.calls) == 1
    assert session.calls[0][2]["allow_redirects"] is False


def test_wrong_company_download_is_never_marked_verified(tmp_path):
    path = tmp_path / "sources.json"
    seed_manifest(path)
    session = Session([Response(report_pdf(company="比亚迪股份有限公司", code="002594"))])
    result = acquisition.acquire_reports(cohort_one(), tmp_path / "pdfs", path, session=session)
    assert result["counts"]["verified"] == 0
    assert "identity" in result["reports"][0]["error"]
    assert not list((tmp_path / "pdfs").rglob("*.pdf"))


def test_missing_sources_are_explicit_and_limit_is_resumable(tmp_path):
    path = tmp_path / "sources.json"
    result = acquisition.acquire_reports(cohort_one(), tmp_path / "pdfs", path, session=Session([]))
    assert result["counts"] == {"expected": 1, "discovered": 0, "verified": 0, "missing": 1, "error": 0}
    assert result["reports"][0]["source_url"] is None
    assert result["complete"] is False


def test_user_run_cli_status_requires_no_network_or_credentials(tmp_path):
    root = Path(__file__).resolve().parents[1]
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(json.dumps(cohort_one(), ensure_ascii=False), encoding="utf-8")
    result = subprocess.run([sys.executable, str(root / "scripts/collect_financial_sources.py"), "--stage", "status", "--cohort", str(cohort_path), "--manifest", str(tmp_path / "missing.json")], cwd=root, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["counts"]["missing"] == 1
    assert payload["complete"] is False


def test_standalone_cli_does_not_import_existing_etl_or_private_configuration(tmp_path):
    root = Path(__file__).resolve().parents[1]
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(json.dumps(cohort_one(), ensure_ascii=False), encoding="utf-8")
    bootstrap = '''import builtins,runpy,sys
original_import=builtins.__import__
def guarded_import(name,*args,**kwargs):
    if name.startswith(("src.","config.")):
        raise RuntimeError("standalone acquisition tried to import private ETL configuration")
    return original_import(name,*args,**kwargs)
builtins.__import__=guarded_import
script=sys.argv.pop(1)
runpy.run_path(script,run_name="__main__")
'''
    result = subprocess.run([sys.executable, "-c", bootstrap, str(root / "scripts/collect_financial_sources.py"), "--stage", "status", "--cohort", str(cohort_path), "--manifest", str(tmp_path / "missing.json")], cwd=root, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["counts"]["missing"] == 1
