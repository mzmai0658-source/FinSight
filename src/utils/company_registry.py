"""作品说明：公司信息注册表 —— DB company 表优先加载（仅已入库公司），registry xlsx 兜底"""

import os
import re
from typing import Dict, Optional
from loguru import logger

import pandas as pd
from src.utils.data_paths import find_company_registry_path

_CODE_TO_NAME: Dict[str, str] = {}   # 作品说明："600080" -> "金花股份"
_NAME_TO_CODE: Dict[str, str] = {}   # 作品说明："金花股份" -> "600080"
_CODE_TO_EXCHANGE: Dict[str, str] = {}  # 作品说明："600080" -> "上交所"
_LOADED = False

# 作品说明：全量公司映射（含未入库 pending 公司）：ETL 导入新公司时用，与对话侧（仅 imported）隔离
_ALL_CODE_TO_NAME: Dict[str, str] = {}
_ALL_NAME_TO_CODE: Dict[str, str] = {}
_ALL_LOADED = False


def _detect_csv_path() -> Optional[str]:
    detected = find_company_registry_path()
    return str(detected) if detected else None


def _register(code: str, abbr: str, full_name: str = "", exchange: str = "") -> None:
    _CODE_TO_NAME[code] = abbr
    _NAME_TO_CODE[abbr] = code
    if full_name:
        _NAME_TO_CODE[full_name] = code
    if exchange:
        _CODE_TO_EXCHANGE[code] = exchange


def _load_from_database() -> bool:
    """作品说明：从 company 表加载已入库公司（agent 只应认识有数据的公司）。"""
    try:
        import pymysql
        from config.db_config import get_db_config

        cfg = get_db_config()
        conn = pymysql.connect(host=cfg.host, port=cfg.port, user=cfg.user,
                               password=cfg.password, database=cfg.database,
                               charset="utf8mb4", connect_timeout=3)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT stock_code, abbr, full_name, exchange
                    FROM company WHERE data_status = 'imported'
                """)
                rows = cur.fetchall()
        finally:
            conn.close()
        if not rows:
            return False
        for code, abbr, full_name, exchange in rows:
            _register(str(code), str(abbr or "").strip(),
                      str(full_name or "").strip(), str(exchange or "").strip())
        logger.info(f"公司注册表已加载: {len(_CODE_TO_NAME)} 家公司 from MySQL company 表")
        return True
    except Exception as e:
        logger.warning(f"company 表加载失败，回退 xlsx: {e}")
        return False


def _load_from_xlsx(csv_path: str) -> None:
    df = pd.read_excel(csv_path)

    code_col = next((c for c in df.columns if "股票代码" in c), None)
    abbr_col = next((c for c in df.columns if "A股简称" in c), None)
    exchange_col = next((c for c in df.columns if "交易所" in c), None)
    full_name_col = next((c for c in df.columns if c in ("公司名称", "公司全称")), None)

    if not code_col or not abbr_col:
        logger.error(f"注册表缺少 '股票代码' 或 'A股简称' 列: {df.columns.tolist()}")
        return

    for _, row in df.iterrows():
        code = re.sub(r"\D", "", str(row[code_col]).strip()).zfill(6)
        if len(code) != 6:
            continue
        full_name = ""
        if full_name_col and pd.notna(row.get(full_name_col)):
            full_name = str(row[full_name_col]).strip()
        exchange = ""
        if exchange_col and pd.notna(row.get(exchange_col)):
            ex = str(row[exchange_col]).strip()
            if "上海" in ex or "上交" in ex:
                exchange = "上交所"
            elif "深圳" in ex or "深交" in ex:
                exchange = "深交所"
        _register(code, str(row[abbr_col]).strip(), full_name, exchange)

    logger.info(f"公司注册表已加载: {len(_CODE_TO_NAME)} 家公司 from {csv_path}")


def load_company_registry(csv_path: Optional[str] = None) -> None:
    """作品说明：加载顺序：显式路径/环境变量 → MySQL company 表 → registry xlsx。"""
    global _LOADED

    explicit = csv_path or os.environ.get("COMPANY_CSV_PATH")
    try:
        if explicit:
            if os.path.exists(explicit):
                _load_from_xlsx(explicit)
            else:
                logger.warning(f"指定的公司信息文件不存在({explicit})，使用空映射")
        elif not _load_from_database():
            detected = _detect_csv_path()
            if detected and os.path.exists(detected):
                _load_from_xlsx(detected)
            else:
                logger.warning("公司注册表无可用来源（DB/xlsx 均不可用），使用空映射")
    except Exception as e:
        logger.error(f"加载公司注册表失败: {e}")
    finally:
        _LOADED = True


def _ensure_loaded():
    if not _LOADED:
        load_company_registry()


def get_code_to_name() -> Dict[str, str]:
    _ensure_loaded()
    return dict(_CODE_TO_NAME)


def get_name_to_code() -> Dict[str, str]:
    _ensure_loaded()
    return dict(_NAME_TO_CODE)


def get_code_to_exchange() -> Dict[str, str]:
    _ensure_loaded()
    return dict(_CODE_TO_EXCHANGE)


def resolve_stock_code(text: str) -> Optional[str]:
    _ensure_loaded()
    for name, code in sorted(_NAME_TO_CODE.items(), key=lambda x: -len(x[0])):
        if name in text:
            return code
    return None


# 作品说明：全量映射（ETL 导入侧）


def _load_all_companies() -> None:
    """作品说明：加载 company 全表（含 pending），失败时退化为对话侧映射。"""
    global _ALL_LOADED
    if _ALL_LOADED:
        return
    try:
        import pymysql
        from config.db_config import get_db_config

        cfg = get_db_config()
        conn = pymysql.connect(host=cfg.host, port=cfg.port, user=cfg.user,
                               password=cfg.password, database=cfg.database,
                               charset="utf8mb4", connect_timeout=3)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT stock_code, abbr, full_name FROM company")
                rows = cur.fetchall()
        finally:
            conn.close()
        for code, abbr, full_name in rows:
            code, abbr = str(code), str(abbr or "").strip()
            _ALL_CODE_TO_NAME[code] = abbr
            if abbr:
                _ALL_NAME_TO_CODE[abbr] = code
            full_name = str(full_name or "").strip()
            if full_name:
                _ALL_NAME_TO_CODE[full_name] = code
        logger.info(f"ETL 全量公司映射已加载: {len(_ALL_CODE_TO_NAME)} 家（含未入库）")
    except Exception as e:
        logger.warning(f"company 全表加载失败，ETL 侧退化为已入库映射: {e}")
    _ALL_LOADED = True


def get_all_code_to_name() -> Dict[str, str]:
    """作品说明：ETL 导入用：全量 公司代码→简称（含 pending 公司，允许导入新公司数据）。"""
    _load_all_companies()
    merged = dict(get_code_to_name())
    merged.update(_ALL_CODE_TO_NAME)
    return merged


def get_all_name_to_code() -> Dict[str, str]:
    """作品说明：ETL 导入用：全量 公司名→代码。"""
    _load_all_companies()
    merged = dict(get_name_to_code())
    merged.update(_ALL_NAME_TO_CODE)
    return merged


def register_company_for_etl(
    code: str,
    abbr: str,
    full_name: str = "",
) -> None:
    """作品说明：数据导入阶段仅登记进程内身份；对话可见性需等财务事务提交并标记已导入，避免暴露待处理、孤立或预览公司。"""
    global _ALL_LOADED
    digits = re.sub(r"\D", "", str(code or ""))
    if len(digits) != 6:
        raise ValueError("ETL company code must contain exactly six digits")
    clean_abbr = str(abbr or "").strip()
    if not clean_abbr:
        raise ValueError("ETL company abbreviation must not be empty")
    _load_all_companies()
    _ALL_CODE_TO_NAME[digits] = clean_abbr
    _ALL_NAME_TO_CODE[clean_abbr] = digits
    clean_full_name = str(full_name or "").strip()
    if clean_full_name:
        _ALL_NAME_TO_CODE[clean_full_name] = digits
    _ALL_LOADED = True


def resolve_stock_code_for_etl(text: str) -> Optional[str]:
    """作品说明：ETL 导入用：在全量映射中按公司名最长匹配。"""
    mapping = get_all_name_to_code()
    for name, code in sorted(mapping.items(), key=lambda x: -len(x[0])):
        if name in text:
            return code
    return None


def resolve_stock_abbr(code: str) -> str:
    _ensure_loaded()
    return _CODE_TO_NAME.get(code, code)


def build_company_list_for_prompt() -> str:
    _ensure_loaded()
    if not _CODE_TO_NAME:
        return "（公司列表未加载）"
    lines = [f"{name}={code}" for code, name in sorted(_CODE_TO_NAME.items())]
    return "公司代码映射：" + ", ".join(lines)
