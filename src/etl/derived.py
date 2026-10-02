"""作品说明：公司级衍生字段重算。

新报告期入库后，同公司相邻年份的同比增长字段才可计算（如导入 2025 年报后，
2025 FY 的 yoy 才有 2024 基数）。该模块按公司整体重算，只回填缺失值，
不覆盖财报原文披露的增长率（行为保持）。
"""
from __future__ import annotations

from typing import Dict, Optional

from loguru import logger
from sqlalchemy import text
from sqlalchemy.engine import Engine

# 作品说明：表 → {增长字段: 基数字段}
DERIVED_YOY_MAP: Dict[str, Dict[str, str]] = {
    "income_sheet": {
        "net_profit_yoy_growth": "net_profit",
        "operating_revenue_yoy_growth": "total_operating_revenue",
    },
    "balance_sheet": {
        "asset_total_assets_yoy_growth": "asset_total_assets",
        "liability_total_liabilities_yoy_growth": "liability_total_liabilities",
    },
    "cash_flow_sheet": {
        "net_cash_flow_yoy_growth": "net_cash_flow",
    },
    "core_performance_indicators_sheet": {
        "operating_revenue_yoy_growth": "total_operating_revenue",
        "net_profit_yoy_growth": "net_profit_10k_yuan",
        "net_profit_excl_non_recurring_yoy": "net_profit_excl_non_recurring",
    },
}


def _default_engine() -> Engine:
    from sqlalchemy import create_engine

    from config.db_config import get_db_config

    return create_engine(get_db_config().connection_string, pool_pre_ping=True)


# 作品说明：与 yoy_calculator 同口径的异常增长率上限：基数过小导致的畸形值不回填
GROWTH_LIMIT_PCT = 1000.0


def _yoy(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None:
        return None
    previous = float(previous)
    if abs(previous) <= 0.01:
        return None
    value = round((float(current) - previous) / abs(previous) * 100, 4)
    return value if abs(value) <= GROWTH_LIMIT_PCT else None


def recompute_company_derived(
    stock_code: str,
    engine: Optional[Engine] = None,
    fill_only: bool = True,
) -> Dict[str, int]:
    """作品说明：重算单个公司的同比增长字段，返回每张表的回填行数。
    
    Args:
    fill_only: True 时只回填 NULL（默认，保持财报披露值）；False 时强制重算。
    """
    engine = engine or _default_engine()
    updated: Dict[str, int] = {}

    with engine.begin() as conn:
        for table, field_map in DERIVED_YOY_MAP.items():
            base_fields = sorted(set(field_map.values()))
            growth_fields = sorted(field_map.keys())
            rows = conn.execute(text(
                f"SELECT report_year, report_period, {', '.join(base_fields + growth_fields)} "
                f"FROM {table} WHERE stock_code = :code"
            ), {"code": stock_code}).mappings().all()

            # 作品说明：(period, year) -> 行数据；按报告期分组对齐相邻年份
            by_period: Dict[str, Dict[int, dict]] = {}
            for row in rows:
                by_period.setdefault(str(row["report_period"]), {})[int(row["report_year"])] = dict(row)

            count = 0
            for period, years in by_period.items():
                for year, row in years.items():
                    prev = years.get(year - 1)
                    if prev is None:
                        continue
                    assignments: Dict[str, float] = {}
                    for growth_field, base_field in field_map.items():
                        if fill_only and row.get(growth_field) is not None:
                            continue
                        value = _yoy(row.get(base_field), prev.get(base_field))
                        if value is not None:
                            assignments[growth_field] = value
                    if not assignments:
                        continue
                    set_clause = ", ".join(f"{field} = :{field}" for field in assignments)
                    conn.execute(text(
                        f"UPDATE {table} SET {set_clause} "
                        f"WHERE stock_code = :code AND report_year = :year AND report_period = :period"
                    ), {**assignments, "code": stock_code, "year": year, "period": period})
                    count += 1
            updated[table] = count

    total = sum(updated.values())
    if total:
        logger.info(f"[derived] {stock_code} 同比字段回填 {total} 行: {updated}")
    return updated
