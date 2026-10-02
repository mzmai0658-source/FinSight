"""作品说明：SQL 查询工具：只读校验 → 执行 → 空结果诊断。

SQL 由 LLM 生成，因此执行前必须经 `sql_guard` 归一为只读语句；查询结果同时用于
回答合成与数字核对，行数据需原样返回而不做二次加工。
"""

import json
import os
import re
from threading import RLock
from typing import Any, Dict

import pandas as pd
from loguru import logger
from sqlalchemy import create_engine, inspect, text

from config.db_config import get_readonly_db_config
from src.agent.domain import get_schema_description, get_table_fields
from src.agent.sql_guard import MAX_LIMIT, normalize_readonly_sql, query_lineage
from src.utils.provenance import attach_provenance


class SQLTool:
    """作品说明：SQL 数据库查询工具：只读校验 → 执行 → 空结果诊断。"""

    name = "sql_tool"

    _engine: Any = None
    _engine_url: str = ""
    _engine_lock: RLock = RLock()

    def _get_engine(self):
        config = get_readonly_db_config()
        url = config.connection_string
        with SQLTool._engine_lock:
            if SQLTool._engine is None or SQLTool._engine_url != url:
                if SQLTool._engine is not None:
                    SQLTool._engine.dispose()
                SQLTool._engine = create_engine(
                    url, pool_pre_ping=True, pool_recycle=1800,
                    connect_args={"connect_timeout": 5, "read_timeout": 15, "write_timeout": 5},
                )
                SQLTool._engine_url = url
            return SQLTool._engine

    def run(self, sql: str) -> Dict[str, Any]:
        readonly_sql, reason = normalize_readonly_sql(sql)
        if readonly_sql is None:
            return {
                "status": "rejected",
                "message": f"SQL 被拒绝：{reason}",
                "schema_hint": get_schema_description(),
            }
        try:
            engine = self._get_engine()
            with engine.connect() as conn:
                if engine.dialect.name == "mysql":
                    timeout_ms = max(100, min(int(os.getenv("SQL_QUERY_TIMEOUT_MS", "5000")), 30000))
                    conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {timeout_ms}"))
                    conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
                df = pd.read_sql_query(text(readonly_sql), conn)
                metadata = query_lineage(readonly_sql)
                rows = json.loads(df.head(MAX_LIMIT).to_json(orient="records", force_ascii=False, date_format="iso"))
                rows = attach_provenance(rows, metadata['column_lineage'], metadata['scope'], connection=conn)

            if df.empty:
                return {
                    "status": "empty",
                    "sql": readonly_sql,
                    "message": "查询结果为空。" + self._build_empty_hint(readonly_sql, engine),
                    "rows": [],
                }

            return {
                "status": "success",
                "sql": readonly_sql,
                "row_count": int(len(df)),
                "columns": df.columns.tolist(),
                "rows": rows,
                **metadata,
            }
        except Exception as e:
            logger.error(f"SQL执行失败: {e}")
            return {
                "status": "error",
                "sql": readonly_sql,
                "message": f"SQL执行出错: {str(e)}。请检查SQL语法或字段名。{self._build_field_hint(readonly_sql)}",
            }

    @staticmethod
    def _build_field_hint(sql: str) -> str:
        """作品说明：报错时附上 SQL 引用表的合法字段清单，帮助 LLM 一次纠正。"""
        try:
            tables = {m.lower() for m in re.findall(r"\b(?:FROM|JOIN)\s+`?(\w+)`?", sql, re.IGNORECASE)}
            table_fields = get_table_fields()
            hints = []
            for table in sorted(tables):
                fields = table_fields.get(table)
                if fields:
                    hints.append(f"表 {table} 的可用字段: {', '.join(fields.keys())}")
            return ("\n" + "\n".join(hints)) if hints else ""
        except Exception:
            return ""

    def _build_empty_hint(self, sql: str, engine) -> str:
        """作品说明：查询数据库现状，给 LLM 提供具体修复建议。"""
        try:
            m = re.search(r"\bFROM\s+(\w+)", sql, re.IGNORECASE)
            if not m:
                return "建议：检查表名是否正确。"
            table_name = m.group(1)

            inspector = inspect(engine)
            if table_name not in inspector.get_table_names():
                return f"表 '{table_name}' 不存在。可用表：{inspector.get_table_names()}"

            with engine.connect() as conn:
                sample = pd.read_sql_query(
                    text(f"SELECT DISTINCT stock_code, report_year, report_period FROM {table_name} LIMIT 20"),
                    conn,
                )
            if sample.empty:
                return f"表 '{table_name}' 中暂无数据，请先运行 ETL 流程导入数据。"
            return (
                f"表 '{table_name}' 中有数据，请检查过滤条件。"
                f"现有记录示例：{sample.to_dict(orient='records')[:5]}"
            )
        except Exception:
            return "建议：检查股票代码、报告年份和报告期是否正确。"
