"""作品说明：通过 PyMySQL 将随附 SQL 导入隔离演示数据库。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pymysql
from pymysql.constants import CLIENT

from prepare_demo_sql import DEFAULT_SOURCE, render_demo_sql


COMPANY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS company (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    stock_code VARCHAR(20) NOT NULL,
    abbr VARCHAR(50) NOT NULL,
    full_name VARCHAR(120) NOT NULL DEFAULT '',
    en_name VARCHAR(200) NOT NULL DEFAULT '',
    exchange VARCHAR(20) NOT NULL DEFAULT '',
    board VARCHAR(40) NOT NULL DEFAULT '',
    industry VARCHAR(80) NOT NULL DEFAULT '',
    region VARCHAR(60) NOT NULL DEFAULT '',
    reg_capital VARCHAR(40) NOT NULL DEFAULT '',
    employees INT NOT NULL DEFAULT 0,
    dataset_tag VARCHAR(20) NOT NULL DEFAULT '',
    data_status VARCHAR(16) NOT NULL DEFAULT 'pending',
    first_year INT NULL,
    last_year INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_stock_code (stock_code),
    KEY idx_data_status (data_status),
    KEY idx_industry (industry)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

COMPANY_SEED_SQL = """
INSERT INTO company
    (stock_code, abbr, full_name, exchange, dataset_tag, data_status, first_year, last_year)
SELECT
    stock_code,
    MAX(stock_abbr),
    MAX(stock_abbr),
    CASE WHEN LEFT(stock_code, 1) IN ('5', '6', '9') THEN '上交所' ELSE '深交所' END,
    '合成演示',
    'imported',
    MIN(report_year),
    MAX(report_year)
FROM income_sheet
GROUP BY stock_code
ON DUPLICATE KEY UPDATE
    abbr=VALUES(abbr),
    full_name=VALUES(full_name),
    exchange=VALUES(exchange),
    dataset_tag=VALUES(dataset_tag),
    data_status=VALUES(data_status),
    first_year=VALUES(first_year),
    last_year=VALUES(last_year)
"""


def seed_database(source: Path, database: str) -> Dict[str, Any]:
    sql = render_demo_sql(source, database)
    connection = pymysql.connect(
        host=os.getenv("DB_HOST") or os.getenv("MYSQL_HOST") or "127.0.0.1",
        port=int(os.getenv("DB_PORT") or os.getenv("MYSQL_PORT") or "3306"),
        user=os.getenv("DB_USER") or os.getenv("MYSQL_USER") or "root",
        password=os.getenv("DB_PASSWORD") or os.getenv("MYSQL_PASSWORD") or "",
        charset=os.getenv("DB_CHARSET") or "utf8mb4",
        autocommit=True,
        client_flag=CLIENT.MULTI_STATEMENTS,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            while cursor.nextset():
                pass
            cursor.execute(f"USE `{database}`")
            cursor.execute(COMPANY_TABLE_SQL)
            cursor.execute(COMPANY_SEED_SQL)
            # 作品说明：保留原有演示公司行，但替换样例后不能让 Agent 继续宣称过期真实公司可查询。
            cursor.execute(
                "UPDATE company c SET c.data_status='pending' "
                "WHERE NOT EXISTS (SELECT 1 FROM income_sheet i WHERE i.stock_code=c.stock_code)"
            )
            cursor.execute(f"SELECT COUNT(*) FROM `{database}`.`income_sheet`")
            row_count = int(cursor.fetchone()[0])
            cursor.execute(f"SELECT COUNT(*) FROM `{database}`.`company` WHERE data_status='imported'")
            company_count = int(cursor.fetchone()[0])
    finally:
        connection.close()
    if row_count <= 0:
        raise RuntimeError("Demo database verification returned no income_sheet rows")
    if company_count <= 0:
        raise RuntimeError("Demo database verification returned no imported companies")
    return {"database": database, "income_sheet_rows": row_count, "companies": company_count}


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the isolated FinSight demo database")
    parser.add_argument("--database", default="finsight_demo")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    try:
        result = seed_database(args.source, args.database)
    except Exception as exc:
        print(f"Demo database seed failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
