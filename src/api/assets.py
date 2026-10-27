"""按内容登记持久化资产，不接受请求直接指定文件路径。"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
from contextlib import closing
from src.utils.original_archive import retain_original,retained_original

ROOT_DIR = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT_DIR / "data" / "runtime" / "assets.sqlite3"
ASSET_ROOTS = (
    ROOT_DIR / "data" / "runtime" / "results",
    ROOT_DIR / "data_root",
    ROOT_DIR / "demo" / "knowledge",
)
ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".md"}


def _approved_path(value: str) -> Path:
    path = Path(value)
    path = (path if path.is_absolute() else ROOT_DIR / path).resolve()
    if not any(path.is_relative_to(root.resolve()) for root in ASSET_ROOTS):
        raise ValueError("Asset directory is not allowed")
    if path.suffix.lower() not in ALLOWED_SUFFIXES or not path.is_file():
        raise ValueError("Asset type is not allowed or file is missing")
    return path


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _connect():
    configured = os.getenv("FINSIGHT_ASSET_DB", "").strip()
    registry = REGISTRY_PATH
    if configured:
        registry = Path(configured)
        registry = (registry if registry.is_absolute() else ROOT_DIR / registry).resolve()
        root = ROOT_DIR.resolve()
        if not registry.is_relative_to(root) or registry == root:
            raise ValueError("Asset registry must stay within this workspace")
        if registry.exists() and not registry.is_file():
            raise ValueError("Asset registry must be a file")
    registry.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(registry, timeout=10)
    connection.execute("CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, UNIQUE(path, sha256))")
    return connection


def register_asset(value: str | None) -> dict:
    if not value:
        return {}
    try:
        path = _approved_path(str(value))
        digest = _digest(path)
        if path.suffix.lower()=='.pdf':retain_original(path,digest)
        with closing(_connect()) as connection, connection:
            connection.execute("INSERT OR IGNORE INTO assets VALUES (?, ?, ?)", (secrets.token_urlsafe(24), str(path), digest))
            asset_id = connection.execute("SELECT id FROM assets WHERE path=? AND sha256=?", (str(path), digest)).fetchone()[0]
        return {"asset_id": asset_id, "source_url": f"/api/assets/{asset_id}", "sha256": digest}
    except (OSError, ValueError):
        return {}


def resolve_asset(asset_id: str) -> Path:
    # 通过参数化登记标识查询资产，不能把标识当文件路径。
    with closing(_connect()) as connection, connection:
        row = connection.execute("SELECT path, sha256 FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not row:
        raise FileNotFoundError("Asset not registered")
    if Path(row[0]).suffix.lower()=='.pdf':
        # PDF 原始字节保持不可变，使历史对话在上传删除或替换后仍可打开原件。
        return retained_original(row[1])
    path = _approved_path(row[0])
    if _digest(path) != row[1]:raise FileNotFoundError("Asset version changed; retrieve fresh evidence")
    return path
