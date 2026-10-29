"""Acquire new Chinese A-share full reports from official sources only.

The acquisition boundary is deliberately independent of OCR, credentials, databases,
and embeddings.  A source announcement is evidence of discovery, while ``verified``
requires actual PDF bytes, a digest, a page count, and matching original text.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from pypdf import PdfReader

CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC = "https://static.cninfo.com.cn/"
PERIOD_LABELS = {"FY": "年度报告", "HY": "半年度报告", "Q1": "第?一季度报告", "Q3": "第?三季度报告"}
OFFICIAL_DOMAINS = ("cninfo.com.cn", "sse.com.cn", "szse.cn", "midea.com", "byd.com", "moutaichina.com", "cypc.com.cn", "cscec.com", "luxshare-ict.com", "catl.com", "yili.com", "csec.com", "shenhuachina.com", "sany.com.cn")
INELIGIBLE = ("摘要", "英文", "english", "更正前", "更新前", "修订前", "撤销", "取消", "作废", "h股", "境外", "ifrs", "国际财务", "国际会计", "更正公告", "修订公告", "更正说明", "问询", "提示性公告")


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _compact(text):
    return re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]+>", "", str(text)))).replace("（", "(").replace("）", ")")


def _period_pattern(year, period):
    return rf"(?<!\d){int(year)}年?{PERIOD_LABELS[period]}"


def validate_report_title(company, year, period, title):
    """Match a full-report title, excluding summaries, foreign standards and notices.

    Company binding is checked separately against the provider's security code; an
    official announcement often has a title containing only the year and period.
    """
    if period not in PERIOD_LABELS:
        return False
    compact = _compact(title)
    if any(marker in compact.lower() for marker in INELIGIBLE):
        return False
    match = re.search(_period_pattern(year, period), compact)
    if not match:
        return False
    prefix, suffix = compact[:match.start()], compact[match.end():]
    company_name = company.get("company", "") if isinstance(company, dict) else str(company)
    legal_name = company.get("legal_name", "") if isinstance(company, dict) else ""
    if prefix and not (prefix.rstrip(":：") in (company_name, company_name + "股份有限公司", company_name + "有限公司", _compact(legal_name))):
        return False
    return re.fullmatch(r"(?:全文)?(?:\((?:A股|修订(?:后|版|稿)?|更正后|更新后|更新版|中文版|中文)\))*", suffix, flags=re.I) is not None


def _official_url(url):
    parsed = urlparse(str(url))
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("official HTTPS source URL required")
    if not any(hostname == domain or hostname.endswith("." + domain) for domain in OFFICIAL_DOMAINS):
        raise ValueError("source URL is outside approved official domains")
    return str(url)


def load_cohort(path):
    cohort = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = cohort["reports"]
    expected = int(cohort["expected_reports"])
    identities = {(r["stock_code"], int(r["year"]), r["period"]) for r in rows}
    if len(rows) != expected or len(identities) != expected:
        raise ValueError("cohort must have one explicit slot per expected report identity")
    companies = {company["stock_code"]: company for company in cohort["companies"]}
    for row in rows:
        report_id = f"{row['stock_code']}_{int(row['year'])}_{row['period']}"
        if row.get("report_id") != report_id or not re.fullmatch(r"\d{6}_20\d{2}_(FY|HY|Q1|Q3)", report_id):
            raise ValueError("unsafe or inconsistent cohort report identity")
        if row["stock_code"] not in companies or row["company"] != companies[row["stock_code"]]["company"]:
            raise ValueError("report company is outside the approved cohort")
    return cohort


def _update_counts(manifest):
    rows = manifest["reports"]
    manifest["counts"] = {
        "expected": len(rows),
        "discovered": sum(bool(row.get("source_url")) for row in rows),
        "verified": sum(row.get("status") == "verified" for row in rows),
        "missing": sum(not row.get("source_url") for row in rows),
        "error": sum(row.get("status") == "error" for row in rows),
    }
    manifest["complete"] = manifest["counts"]["verified"] == len(rows)
    manifest["updated_at"] = _utc_now()
    return manifest


def initialize_manifest(cohort):
    rows = []
    for report in cohort["reports"]:
        row = {**copy.deepcopy(report), "status": "missing", "source_url": None, "title": None, "sha256": None, "page_count": None, "local_path": None}
        sources = report.get("sources", [])
        if sources:
            seed = sources[0]
            row.update({key: value for key, value in seed.items() if key not in ("page_count", "sha256", "status")})
            row["status"] = "discovered"
            row["source_basis"] = seed.get("source_basis", "official_pdf_seed")
        rows.append(row)
    return _update_counts({"schema_version": 1, "cohort_id": cohort["cohort_id"], "expected_reports": len(rows), "reports": rows, "metadata": {"provider": "cninfo", "companies": {}, "network_blocked": False}, "acquisition_only": True, "ocr_cache_reused": False})


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _manifest(cohort, path):
    fresh = initialize_manifest(cohort)
    if Path(path).exists():
        existing = json.loads(Path(path).read_text(encoding="utf-8"))
        if existing.get("cohort_id") != cohort["cohort_id"]:
            raise ValueError("source manifest belongs to a different cohort")
        previous = {r["report_id"]: r for r in existing.get("reports", [])}
        for row in fresh["reports"]:
            old = previous.get(row["report_id"])
            if old:
                immutable = {key: row[key] for key in ("report_id", "stock_code", "company", "legal_name", "year", "period", "split") if key in row}
                row.update(old)
                row.update(immutable)
        fresh["metadata"].update(existing.get("metadata", {}))
    return _update_counts(fresh)


def select_announcement(report, announcements):
    candidates = []
    for announcement in announcements:
        title = announcement.get("announcementTitle", "")
        if str(announcement.get("secCode", "")) != report["stock_code"]:
            continue
        if not validate_report_title(report, report["year"], report["period"], title):
            continue
        if str(announcement.get("adjunctType", "PDF")).upper() != "PDF":
            continue
        if str(announcement.get("announcementStatus", "")).lower() in ("cancelled", "canceled", "deleted", "withdrawn") or announcement.get("isCancel") in (True, "true", "1"):
            continue
        url = urljoin(CNINFO_STATIC, announcement.get("adjunctUrl", ""))
        if not urlparse(url).path.lower().endswith(".pdf"):
            continue
        try:
            _official_url(url)
        except ValueError:
            continue
        revised = any(marker in title for marker in ("更正后", "更新后", "修订", "更新版"))
        candidates.append((int(announcement.get("announcementTime") or 0), revised, str(announcement.get("announcementId", "")), announcement, url))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[:3], reverse=True)
    timestamp, revised, announcement_id, announcement, url = candidates[0]
    return {"source_url": url, "title": html.unescape(re.sub(r"<[^>]+>", "", announcement["announcementTitle"])), "announcement_id": announcement_id, "published_at": datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat() if timestamp else None, "version": "revised" if revised else "original", "version_basis": "latest_eligible_official_announcement", "source_basis": "cninfo_metadata", "metadata_url": CNINFO_QUERY, "superseded_announcement_ids": [row[2] for row in candidates[1:]]}


def _session(session):
    session = session or requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; FinancialReportAcquisition/1.0)", "Referer": "https://www.cninfo.com.cn/"})
    return session


def discover_cninfo_sources(cohort, manifest_path, *, session=None, max_metadata_probes=2, refresh=False):
    """Resume official metadata discovery and stop after bounded connectivity failures.

    ``max_metadata_probes`` limits consecutive failed requests, not successful
    company queries.  A user can rerun this command from another network. Successful
    company metadata is retained, and no PDF is called verified by discovery.
    """
    manifest = _manifest(cohort, manifest_path)
    session = _session(session)
    metadata = manifest["metadata"]
    metadata["network_blocked"] = False
    failures = 0
    for company in cohort["companies"]:
        code = company["stock_code"]
        if not refresh and metadata["companies"].get(code, {}).get("status") == "complete":
            continue
        try:
            announcements, page, total_pages = [], 1, 1
            while page <= total_pages:
                data = {"pageNum": page, "pageSize": 100, "column": company.get("exchange", "szse"), "tabName": "fulltext", "stock": f"{code},{company['org_id']}", "searchkey": "", "secid": "", "plate": "", "category": "category_ndbg_szsh;category_bndbg_szsh;category_yjdbg_szsh;category_sjdbg_szsh;", "trade": "", "seDate": f"{min(r['year'] for r in cohort['reports'])}-01-01~{cohort['collection_as_of']}", "sortName": "time", "sortType": "desc", "isHLtitle": "true"}
                response = session.post(CNINFO_QUERY, data=data, timeout=(8, 25))
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or "announcements" not in payload:
                    raise ValueError("unexpected cninfo metadata response")
                page_rows = payload.get("announcements") or []
                announcements.extend(page_rows)
                if page == 1:
                    # The live provider can cap pageSize and truncate totalpages.
                    # Derive the actual page count from its total and first page.
                    effective_page_size = len(page_rows) or 100
                    total_rows = int(payload.get("totalAnnouncement") or payload.get("totalRecordNum") or len(page_rows))
                    total_pages = max(1, int(payload.get("totalpages") or 1), math.ceil(total_rows / effective_page_size))
                if total_pages > 20:
                    raise ValueError("metadata pagination exceeds bounded company scope")
                page += 1
            for row in manifest["reports"]:
                if row["stock_code"] != code:
                    continue
                selected = select_announcement(row, announcements)
                if selected:
                    if row.get("source_url") != selected["source_url"]:
                        for key in ("sha256", "document_version", "page_count", "local_path", "identity", "download_url"):
                            row.pop(key, None)
                        row["status"] = "discovered"
                    row.update(selected)
                    if row["status"] != "verified":
                        row["status"] = "discovered"
                    row.pop("error", None)
            metadata["companies"][code] = {"status": "complete", "announcement_count": len(announcements), "pages": total_pages, "queried_at": _utc_now()}
            failures = 0
        except (requests.RequestException, OSError, ValueError, RuntimeError) as exc:
            failures += 1
            metadata["companies"][code] = {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:500]}", "queried_at": _utc_now()}
            if failures >= max(1, int(max_metadata_probes)):
                metadata["network_blocked"] = True
                _atomic_json(manifest_path, _update_counts(manifest))
                break
        _atomic_json(manifest_path, _update_counts(manifest))
    _atomic_json(manifest_path, _update_counts(manifest))
    return manifest


def validate_pdf(path, expected):
    """Validate actual original PDF bytes and front matter; never consult OCR caches."""
    path = Path(path)
    with path.open("rb") as stream:
        if not stream.read(5).startswith(b"%PDF-"):
            raise ValueError("source response is not a PDF")
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted or not len(reader.pages):
            raise ValueError("encrypted or empty PDF is ineligible")
        front = "\n".join(page.extract_text() or "" for page in reader.pages[:10])
    except Exception as exc:
        raise ValueError(f"PDF cannot be parsed: {exc}") from exc
    compact = _compact(front)
    code = expected["stock_code"]
    codes = re.findall(r"(?:证券|股票|公司)代码[:：]?(\d{6})", compact)
    # Exchange annual reports may put headers and their stock-code values in
    # separate table cells.  Require a literal code near the stock-code heading.
    if not codes:
        for match in re.finditer(r"(?:股票|证券|公司)代码", compact):
            codes.extend(re.findall(r"(?<!\d)\d{6}(?!\d)", compact[match.end():match.end() + 160]))
    company_match = code in codes if codes else any(name and _compact(name) in compact for name in (expected.get("legal_name"), expected.get("company")))
    # Designed annual/interim covers can put the label before the year. Choose
    # the first original title in either direction, never a later reference.
    title_text=_compact(re.sub(r'(?<!\d)(?=20\d{2})','|',front))
    primary = re.search(r"(?<!\d)(?P<year>20\d{2})年?(?P<label>半年度报告|第?一季度报告|第?三季度报告|年度报告)(?P<summary>摘要)?|(?P<reverse_label>半年度报告|年度报告)(?P<reverse_summary>摘要)?\|?(?P<reverse_year>20\d{2})", title_text)
    primary_period = None
    primary_year = None
    if primary:
        label = primary["label"] or primary["reverse_label"]
        primary_year = int(primary["year"] or primary["reverse_year"])
        primary_period = "HY" if label == "半年度报告" else "Q1" if "一季度" in label else "Q3" if "三季度" in label else "FY"
    period_match = bool(primary and primary_year == int(expected["year"]) and primary_period == expected["period"])
    summary = bool(primary and (primary["summary"] or primary["reverse_summary"]))
    if not company_match or not period_match or summary:
        raise ValueError(f"PDF identity mismatch: company={company_match}, period={period_match}, summary={summary}, codes={sorted(set(codes))}")
    return {"sha256": digest, "document_version": digest, "page_count": len(reader.pages), "size_bytes": path.stat().st_size, "identity": {"company": True, "period": True, "stock_code": code, "year": int(expected["year"]), "report_period": expected["period"], "original_period_title": primary[0], "codes_found": sorted(set(codes)), "basis": "original_pdf_text", "full_report": True}, "validated_at": _utc_now()}


def _download(session, row, target):
    url = _official_url(row["source_url"])
    temporary = target.with_suffix(target.suffix + ".part")
    partial_meta = temporary.with_name(temporary.name + ".meta.json")
    saved = json.loads(partial_meta.read_text(encoding="utf-8")) if partial_meta.exists() else {}
    resume = temporary.stat().st_size if temporary.exists() and saved.get("source_url") == url else 0
    headers = {"Accept-Encoding": "identity"}
    if resume:
        headers["Range"] = f"bytes={resume}-"
        if saved.get("etag") or saved.get("last_modified"):
            headers["If-Range"] = saved.get("etag") or saved["last_modified"]
    request_url = url
    for _ in range(5):
        response = session.get(request_url, headers=headers, stream=True, allow_redirects=False, timeout=(10, 45))
        if response.status_code not in (301, 302, 303, 307, 308):
            break
        try:
            request_url = _official_url(urljoin(request_url, response.headers.get("Location", "")))
        finally:
            response.close()
    else:
        raise ValueError("official PDF exceeded redirect limit")
    try:
        if response.status_code == 416 and resume:
            # A server may reject a saved partial after its object changed or
            # completed just before a disconnect. Reset once, then request all.
            response.close()
            temporary.unlink(missing_ok=True)
            partial_meta.unlink(missing_ok=True)
            return _download(session, row, target)
        response.raise_for_status()
        final_url = _official_url(response.url or url)
        if response.status_code == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
            if not resume or not match or int(match[1]) != resume:
                raise ValueError("invalid partial response Content-Range")
            if saved.get("etag") and response.headers.get("ETag") and saved["etag"] != response.headers["ETag"]:
                raise ValueError("partial response belongs to another PDF version")
            expected_size = int(match[3])
            mode = "ab"
        elif response.status_code == 200:
            mode, resume = "wb", 0
            expected_size = int(response.headers["Content-Length"]) if response.headers.get("Content-Length") else None
        else:
            raise ValueError(f"unexpected PDF HTTP status {response.status_code}")
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(partial_meta, {"source_url": url, "etag": response.headers.get("ETag"), "last_modified": response.headers.get("Last-Modified"), "updated_at": _utc_now()})
        with temporary.open(mode) as stream:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if expected_size is not None and temporary.stat().st_size != expected_size:
            raise ValueError("downloaded PDF byte length differs from provider response")
        try:
            proof = validate_pdf(temporary, row)
        except ValueError:
            temporary.unlink(missing_ok=True)
            partial_meta.unlink(missing_ok=True)
            raise
        temporary.replace(target)
        partial_meta.unlink(missing_ok=True)
        return {**proof, "download_url": final_url, "downloaded_from": url, "downloaded_at": _utc_now()}
    finally:
        response.close()


def acquire_reports(cohort, destination, manifest_path, *, session=None, limit=None):
    """Download selected originals atomically; resume partials and recheck saved hashes.

    ``limit`` bounds newly attempted transfers. All identity slots remain in the
    returned/written manifest, including missing sources and failed downloads.
    Existing PDFs are skipped only when bound to the selected URL and stored hash.
    """
    manifest = _manifest(cohort, manifest_path)
    destination = Path(destination).resolve()
    session = _session(session)
    attempts = 0
    for row in manifest["reports"]:
        if not row.get("source_url"):
            continue
        target = destination / row["stock_code"] / (row["report_id"] + ".pdf")
        if not target.resolve().is_relative_to(destination):
            raise ValueError("report path escapes acquisition destination")
        try:
            _official_url(row["source_url"])
            if not validate_report_title(row, row["year"], row["period"], row.get("title", "")):
                raise ValueError("source announcement title is not an eligible report")
            if target.exists() and row.get("downloaded_from", row.get("download_url")) == row["source_url"] and row.get("sha256"):
                try:
                    proof = validate_pdf(target, row)
                except (ValueError, OSError):
                    proof = None
                if proof and proof["sha256"] == row["sha256"]:
                    row.update(proof, status="verified", local_path=str(target))
                    row.pop("error", None)
                    continue
                row.update(status="error", error="saved original failed identity/hash validation; download required")
            if limit is not None and attempts >= int(limit):
                continue
            attempts += 1
            proof = _download(session, row, target)
            row.update(proof, status="verified", local_path=str(target))
            row.pop("error", None)
        except (requests.RequestException, OSError, ValueError, RuntimeError) as exc:
            row["status"] = "error"
            row["error"] = f"{type(exc).__name__}: {str(exc)[:700]}"
        _atomic_json(manifest_path, _update_counts(manifest))
    _atomic_json(manifest_path, _update_counts(manifest))
    return manifest
