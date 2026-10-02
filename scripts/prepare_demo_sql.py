"""作品说明：把固定 SQL dump 重定向到隔离的演示数据库后写到标准输出。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT_DIR / "demo" / "financial_report.sql"
DATABASE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def render_demo_sql(source: Path, database: str) -> str:
    if not DATABASE_RE.fullmatch(database):
        raise ValueError("数据库名只能包含字母、数字和下划线，且必须以字母开头")
    if database != "finsight_demo" and not database.startswith("finsight_demo_"):
        raise ValueError("演示导入只允许 finsight_demo 或 finsight_demo_ 前缀的隔离库")
    sql = source.read_text(encoding="utf-8")
    marker = "`finsight_demo`"
    if marker not in sql:
        raise ValueError(f"SQL dump 中未找到预期数据库标记 {marker}")
    return sql.replace(marker, f"`{database}`")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成隔离数据库名的 FinSight demo SQL")
    parser.add_argument("--database", default="finsight_demo")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    sys.stdout.write(render_demo_sql(args.source, args.database))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
