from pathlib import Path
import hashlib
import json

import pytest

from scripts.build_demo_kb import load_demo_documents
from scripts.check_release import REQUIRED_PATHS, check_release
from scripts.prepare_demo_sql import render_demo_sql
from src.agent.tool_shared import ROOT_DIR, resolve_chroma_db_path


def test_demo_sql_rewrites_only_the_database_target(tmp_path: Path):
    source = tmp_path / "seed.sql"
    source.write_text("CREATE DATABASE `finsight_demo`; USE `finsight_demo`;", encoding="utf-8")

    rendered = render_demo_sql(source, "finsight_demo_test")

    assert "`finsight_demo`" not in rendered
    assert rendered.count("`finsight_demo_test`") == 2


@pytest.mark.parametrize("database", ["", "9demo", "demo-name", "demo;DROP DATABASE x", "financial_report", "production"])
def test_demo_sql_rejects_unsafe_database_names(tmp_path: Path, database: str):
    source = tmp_path / "seed.sql"
    source.write_text("USE `financial_report`;", encoding="utf-8")

    with pytest.raises(ValueError):
        render_demo_sql(source, database)


def test_demo_knowledge_source_is_small_and_nonempty():
    documents = load_demo_documents(ROOT_DIR / "demo" / "knowledge")

    assert len(documents) == 15
    assert all(chunks for _, _, chunks in documents)


def test_relative_chroma_path_is_rooted_at_repository(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHROMA_DB_PATH", "data/demo_chroma_db")

    assert resolve_chroma_db_path() == (ROOT_DIR / "data" / "demo_chroma_db").resolve()


def test_release_manifest_and_scanner_pass_current_tree():
    for relative in REQUIRED_PATHS:
        assert (ROOT_DIR / relative).is_file(), relative

    assert check_release() == []


def test_release_scanner_detects_secret_and_large_file(tmp_path: Path):
    required = [tmp_path / relative for relative in REQUIRED_PATHS]
    for path in required:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder", encoding="utf-8")
    secret = tmp_path / "config.txt"
    secret.write_text("token=github_pat_" + "a" * 40, encoding="utf-8")
    large = tmp_path / "asset.bin"
    large.write_bytes(b"x" * (10 * 1024 * 1024 + 1))

    errors = check_release(tmp_path, files=[secret, large])

    assert any("疑似 GitHub token" in error for error in errors)
    assert any("超过 10 MiB" in error for error in errors)


def test_public_fixture_sources_have_stable_identity_and_matching_hashes():
    sources = json.loads((ROOT_DIR / "demo/sources.json").read_text(encoding="utf-8"))["documents"]
    facts = json.loads((ROOT_DIR / "demo/financial_facts.json").read_text(encoding="utf-8"))["facts"]
    by_id = {source["document_id"]: source for source in sources}
    assert len(by_id) == 15
    assert len({source["chunk_id"] for source in sources}) == 15
    assert {s["stock_code"] for s in sources} == {"990001", "990002", "990003", "990004", "990005"}
    for source in sources:
        path = ROOT_DIR / source["source_path"]
        assert source["synthetic"] and source["license"] == "Apache-2.0"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["source_sha256"] == source["document_version"]
        assert source["rationale"] in path.read_text(encoding="utf-8")
    for fact in facts:
        source = by_id[fact["document_id"]]
        assert all(fact[key] == source[key] for key in ("stock_code", "report_year", "report_period", "source_sha256"))
        assert fact["page_start"] == 1


def test_release_scanner_rejects_small_runtime_index_and_private_archive(tmp_path):
    index = tmp_path / "data/demo_chroma_db/chroma.sqlite3"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"small runtime database")
    archive = tmp_path / ".local_archive/report.txt"
    archive.parent.mkdir()
    archive.write_text("private original", encoding="utf-8")
    errors = check_release(tmp_path, files=[index, archive])
    assert any("运行数据" in error and "sqlite3" in error for error in errors)
    assert any("未审核原件" in error and ".local_archive" in error for error in errors)
