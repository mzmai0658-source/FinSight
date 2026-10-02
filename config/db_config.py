"""作品说明：数据库配置模块"""

import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv is optional at import time
    load_dotenv = None

if load_dotenv is not None:
    # 作品说明：仅加载当前项目配置，干净副本不能向父目录寻找私人环境。
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@dataclass
class DatabaseConfig:
    """作品说明：数据库配置"""
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "financial_report"
    charset: str = "utf8mb4"

    @property
    def connection_string(self) -> str:
        """作品说明：获取 SQLAlchemy 连接字符串"""
        user = quote_plus(self.user)
        password = quote_plus(self.password)
        return f"mysql+pymysql://{user}:{password}@{self.host}:{self.port}/{self.database}?charset={self.charset}"

    @property
    def connection_string_no_db(self) -> str:
        """作品说明：获取不包含数据库名的连接字符串（用于创建数据库）"""
        user = quote_plus(self.user)
        password = quote_plus(self.password)
        return f"mysql+pymysql://{user}:{password}@{self.host}:{self.port}?charset={self.charset}"


def get_db_config() -> DatabaseConfig:
    """作品说明：从环境变量创建 DatabaseConfig 数据库配置对象。"""
    return DatabaseConfig(
        host=os.getenv("DB_HOST") or os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("DB_PORT") or os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("DB_USER") or os.getenv("MYSQL_USER", "root"),
        password=os.getenv("DB_PASSWORD") or os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "financial_report"),
        charset=os.getenv("DB_CHARSET") or os.getenv("MYSQL_CHARSET", "utf8mb4")
    )


# 作品说明：默认配置
DEFAULT_CONFIG = DatabaseConfig()


def get_readonly_db_config() -> DatabaseConfig:
    """作品说明：问答查询账号不能默默继承数据导入或管理员账号。"""
    config = get_db_config()
    user = os.getenv("SQL_DB_USER", "").strip()
    password = os.getenv("SQL_DB_PASSWORD", "")
    if not user or not password:
        raise RuntimeError("请配置独立只读账号 SQL_DB_USER / SQL_DB_PASSWORD")
    if user.lower() == "root" or user == config.user:
        raise RuntimeError("SQL_DB_USER 必须与 DB_USER 管理/ETL 账号分离")
    return DatabaseConfig(
        host=os.getenv("SQL_DB_HOST") or config.host,
        port=int(os.getenv("SQL_DB_PORT") or config.port),
        user=user, password=password,
        database=os.getenv("SQL_DB_NAME") or config.database,
        charset=config.charset,
    )
