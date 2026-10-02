"""作品说明：FinSight 发布前静态检查：必需文件、大文件、密钥与可选身份词扫描。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Sequence


ROOT_DIR = Path(__file__).resolve().parents[1]
REQUIRED_PATHS = (
    ".env.demo.example",
    ".github/workflows/ci.yml",
    "LICENSE",
    "NOTICE",
    "README.md",
    "docs/DEMO.md",
    "docs/THIRD_PARTY.md",
    "scripts/build_demo_kb.py",
    "scripts/bootstrap_demo_v3.py",
    "demo/v3/generate.py",
    "demo/v3/spec.json",
    "scripts/demo-seed.ps1",
    "scripts/demo-seed.sh",
    "scripts/ensure_ollama_model.py",
    "scripts/prepare_demo_sql.py",
    "scripts/seed_demo_database.py",
    "scripts/verify_demo.py",
    "demo/sources.json",
    "demo/financial_facts.json",
    "demo/financial_report.sql",
    "eval/oracle.py",
    "eval/experiment.py",
)
TEXT_SUFFIXES = {
    ".c", ".conf", ".cpp", ".css", ".csv", ".env", ".go", ".h", ".html",
    ".ini", ".java", ".js", ".json", ".jsonl", ".jsx", ".md", ".properties",
    ".ps1", ".py", ".rb", ".rs", ".sh", ".sql", ".toml", ".ts", ".tsx",
    ".txt", ".vue", ".xml", ".yaml", ".yml",
}
SENSITIVE_FILENAMES = {".env", "id_rsa", "id_ed25519"}
SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
MAX_FILE_BYTES = 10 * 1024 * 1024
SECRET_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"(?:github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,})")),
    ("OpenAI-style token", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
)


def repository_files(root: Path = ROOT_DIR) -> List[Path]:
    """作品说明：获取已跟踪及未忽略文件，不遍历被忽略的数据集。"""
    if not (root / '.git').exists():
        manifest=root/'RELEASE_MANIFEST.json'
        if not manifest.is_file():
            raise ValueError('No Git tree or release manifest; cannot determine release files')
        import json
        names=json.loads(manifest.read_text(encoding='utf-8'))['files']
        paths=[root/item for item in names]
        if any(not p.resolve().is_relative_to(root.resolve()) for p in paths):
            raise ValueError('Release manifest contains an unsafe path')
        return paths
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]


def _is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name.startswith(".env")


def check_release(root: Path = ROOT_DIR, files: Sequence[Path] | None = None) -> List[str]:
    errors: List[str] = []
    for relative in REQUIRED_PATHS:
        if not (root / relative).is_file():
            errors.append(f"缺少发布必需文件: {relative}")

    candidates = list(files) if files is not None else repository_files(root)
    forbidden_terms = [
        term.strip()
        for term in os.getenv("RELEASE_FORBIDDEN_TERMS", "").split(",")
        if term.strip()
    ]
    for path in candidates:
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith((".local_archive/", "data_root/", "data/runtime/", "data/demo_chroma_db/", "data/chroma_db/")) or (relative.startswith("data/") and path.suffix.lower() in {".sqlite", ".sqlite3", ".db", ".bin"}):
            errors.append(f"运行数据或未审核原件不应发布: {relative}")
        if (path.name.startswith('.env') and path.name not in {'.env.example','.env.demo.example'}) or path.name in SENSITIVE_FILENAMES or path.suffix.lower() in SENSITIVE_SUFFIXES:
            errors.append(f"疑似私密文件不应发布: {relative}")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            errors.append(f"文件超过 10 MiB，请改用外部存储: {relative} ({size} bytes)")
        if not _is_text(path) or size > 2 * 1024 * 1024:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if relative == "database/financial_report.sql" and re.search(r"\bINSERT\s+INTO\b", content, re.I):
            errors.append("旧财务 dump 未通过公开来源审核；使用 demo/financial_report.sql")
        if relative.startswith("eval/") and path.suffix == ".jsonl" and ('data_root/' in content or 'data_root\\\\' in content):
            errors.append(f"评测明细包含未登记的私有语料路径/摘录: {relative}")
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                errors.append(f"疑似 {label}: {relative}")
        for term in forbidden_terms:
            if term.casefold() in content.casefold():
                errors.append(f"命中禁止发布的身份词 {term!r}: {relative}")
    return sorted(set(errors))


def main() -> int:
    try:
        errors = check_release()
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"release check could not run: {exc}", file=sys.stderr)
        return 2
    if errors:
        print("Release check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Release check passed: required files, secrets, identity terms, and large files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
