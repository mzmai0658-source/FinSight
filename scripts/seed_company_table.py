"""作品说明：公司主数据种子脚本：registry xlsx → MySQL company 表。

- 两份注册表（医药/中药）合并导入，dataset_tag 区分来源
- data_status 按财务表实际入库情况标记（imported/pending），并记录年份覆盖
- 交易所按股票代码前缀推导（修正 xlsx 中\"上市交易所\"列的错漏，如百克生物 688276）
- 幂等：ON DUPLICATE KEY UPDATE，可重复执行

用法：
python -m scripts.seed_company_table
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.db_config import get_db_config  # noqa: E402
from src.utils.data_paths import get_preferred_data_root  # noqa: E402


def normalize_code(raw: object) -> str | None:
    code = re.sub(r"\D", "", str(raw)).zfill(6)
    return code if len(code) == 6 else None


def derive_exchange(code: str) -> str:
    """作品说明：6 开头上交所（含 688 科创板），0/3 开头深交所。"""
    return "上交所" if code.startswith("6") else "深交所"


def load_registry_frame(path: Path, dataset_tag: str) -> list[dict]:
    df = pd.read_excel(path)
    rows: list[dict] = []
    for _, row in df.iterrows():
        code = normalize_code(row.get("股票代码"))
        if not code:
            continue
        rows.append({
            "stock_code": code,
            "abbr": str(row.get("A股简称", "")).strip(),
            "full_name": str(row.get("公司名称", "")).strip(),
            "en_name": str(row.get("英文名称", "")).strip(),
            "exchange": derive_exchange(code),
            "board": str(row.get("证券类别", "")).strip(),
            "industry": str(row.get("所属证监会行业", "")).strip(),
            "region": str(row.get("注册区域", "")).strip(),
            "reg_capital": str(row.get("注册资本", "")).strip(),
            "employees": int(row.get("雇员人数") or 0),
            "dataset_tag": dataset_tag,
        })
    return rows


def main() -> None:
    registry_dir = get_preferred_data_root() / "registry"
    sources: list[tuple[Path, str]] = []
    for path in sorted(registry_dir.glob("*.xlsx")):
        if path.name.startswith("~$"):
            continue
        tag = "医药" if "医药" in path.name else ("中药" if "中药" in path.name else "其他")
        sources.append((path, tag))
    if not sources:
        raise SystemExit(f"注册表目录无 xlsx: {registry_dir}")

    companies: dict[str, dict] = {}
    for path, tag in sources:
        for row in load_registry_frame(path, tag):
            companies[row["stock_code"]] = row
        print(f"读取 {path.name}（{tag}）")

    cfg = get_db_config()
    conn = pymysql.connect(host=cfg.host, port=cfg.port, user=cfg.user,
                           password=cfg.password, database=cfg.database, charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            # 作品说明：财务数据实际覆盖：决定 data_status 与年份范围
            cur.execute("""
                SELECT stock_code, MIN(report_year), MAX(report_year)
                FROM core_performance_indicators_sheet GROUP BY stock_code
            """)
            coverage = {code: (lo, hi) for code, lo, hi in cur.fetchall()}

            sql = """
                INSERT INTO company
                    (stock_code, abbr, full_name, en_name, exchange, board, industry,
                     region, reg_capital, employees, dataset_tag, data_status, first_year, last_year)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    abbr=VALUES(abbr), full_name=VALUES(full_name), en_name=VALUES(en_name),
                    exchange=VALUES(exchange), board=VALUES(board), industry=VALUES(industry),
                    region=VALUES(region), reg_capital=VALUES(reg_capital), employees=VALUES(employees),
                    dataset_tag=VALUES(dataset_tag), data_status=VALUES(data_status),
                    first_year=VALUES(first_year), last_year=VALUES(last_year)
            """
            imported = 0
            for code, row in sorted(companies.items()):
                lo_hi = coverage.get(code)
                status = "imported" if lo_hi else "pending"
                imported += 1 if lo_hi else 0
                cur.execute(sql, (
                    row["stock_code"], row["abbr"], row["full_name"], row["en_name"],
                    row["exchange"], row["board"], row["industry"], row["region"],
                    row["reg_capital"], row["employees"], row["dataset_tag"], status,
                    lo_hi[0] if lo_hi else None, lo_hi[1] if lo_hi else None,
                ))
            # 作品说明：库里有数据但注册表没有的公司也补一条（防数据先于注册表到位）
            for code, (lo, hi) in sorted(coverage.items()):
                if code in companies:
                    continue
                cur.execute("SELECT stock_abbr FROM core_performance_indicators_sheet "
                            "WHERE stock_code=%s LIMIT 1", (code,))
                abbr = (cur.fetchone() or [code])[0]
                cur.execute(sql, (code, abbr, "", "", derive_exchange(code), "", "",
                                  "", "", 0, "其他", "imported", lo, hi))
                imported += 1
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT data_status, COUNT(*) FROM company GROUP BY data_status")
            stats = dict(cur.fetchall())
        print(f"完成: company 共 {sum(stats.values())} 家（imported={stats.get('imported', 0)}, "
              f"pending={stats.get('pending', 0)}）")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
