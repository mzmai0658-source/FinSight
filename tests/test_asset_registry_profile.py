"""A selected data profile keeps its asset registry inside its own workspace."""
from __future__ import annotations

import hashlib
import pytest

from src.api import assets


@pytest.fixture
def registry_workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(assets, "ROOT_DIR", root)
    monkeypatch.setattr(assets, "REGISTRY_PATH", root / "data/runtime/assets.sqlite3")
    monkeypatch.delenv("FINSIGHT_ASSET_DB", raising=False)
    return root


def test_unconfigured_registry_keeps_original_default(registry_workspace):
    with assets._connect() as connection:
        connection.execute("INSERT INTO assets VALUES (?, ?, ?)", ("old", "old.pdf", "old-hash"))
    assert assets.REGISTRY_PATH.is_file()
    with assets._connect() as connection:
        assert connection.execute("SELECT id FROM assets").fetchall() == [("old",)]


@pytest.mark.parametrize("absolute", [False, True])
def test_profile_registry_leaves_existing_default_bytes_unchanged(
    registry_workspace, monkeypatch, absolute
):
    with assets._connect() as connection:
        connection.execute("INSERT INTO assets VALUES (?, ?, ?)", ("old", "old.pdf", "old-hash"))
    before = hashlib.sha256(assets.REGISTRY_PATH.read_bytes()).hexdigest()
    selected = registry_workspace / "data/runtime/cloud118/assets.sqlite3"
    monkeypatch.setenv("FINSIGHT_ASSET_DB", str(selected) if absolute else "data/runtime/cloud118/assets.sqlite3")
    with assets._connect() as connection:
        connection.execute("INSERT INTO assets VALUES (?, ?, ?)", ("new", "new.pdf", "new-hash"))
    assert selected.is_file()
    with assets._connect() as connection:
        assert connection.execute("SELECT id FROM assets").fetchall() == [("new",)]
    assert hashlib.sha256(assets.REGISTRY_PATH.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("selection", ["../escape.sqlite3", "absolute-outside", "."])
def test_profile_registry_rejects_paths_outside_workspace_or_workspace_root(
    registry_workspace, monkeypatch, selection
):
    if selection == "absolute-outside":
        selection = str(registry_workspace.parent / "escape.sqlite3")
    monkeypatch.setenv("FINSIGHT_ASSET_DB", selection)
    with pytest.raises(ValueError, match="workspace"):
        assets._connect()
    assert not (registry_workspace.parent / "escape.sqlite3").exists()


def test_profile_registry_rejects_existing_directory(registry_workspace, monkeypatch):
    selected = registry_workspace / "data/runtime/cloud118"
    selected.mkdir(parents=True)
    monkeypatch.setenv("FINSIGHT_ASSET_DB", str(selected))
    with pytest.raises(ValueError, match="file"):
        assets._connect()
