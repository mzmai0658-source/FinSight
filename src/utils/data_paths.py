"""作品说明：统一数据根目录解析。

布局（D:\\data_discovery\\data_root，可用环境变量 DATA_ROOT 覆盖）：
data_root/
financial_reports/reports-上交所|reports-深交所   财报 PDF + OCR JSON
research_reports/个股研报|行业研报                 研报 PDF + OCR JSON
research_reports/metadata/医药|中药                研报信息元数据 xlsx
registry/                                          公司基本信息 xlsx
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import os


ROOT_DIR = Path(__file__).resolve().parents[2]

DATA_ROOT_DIR_NAME = "data_root"

FINANCIAL_REPORTS_DIR_NAME = "financial_reports"
RESEARCH_REPORTS_DIR_NAME = "research_reports"
REGISTRY_DIR_NAME = "registry"

# 作品说明：当前数据库导入的是医药行业数据，注册表解析优先匹配该行业。
PREFERRED_DATASET_TAG = "\u533b\u836f"  # 作品说明：医药

REPORT_SUBDIR_NAMES = ("reports-\u4e0a\u4ea4\u6240", "reports-\u6df1\u4ea4\u6240")  # 作品说明：上交所/深交所
RESEARCH_SUBDIR_NAMES = ("\u4e2a\u80a1\u7814\u62a5", "\u884c\u4e1a\u7814\u62a5")  # 作品说明：个股/行业研报


def get_preferred_data_root() -> Path:
    """作品说明：数据根目录：环境变量 DATA_ROOT/DATA_DIR 优先，缺省用仓库内 data_root。"""
    env_root = os.getenv("DATA_ROOT") or os.getenv("DATA_DIR")
    if env_root:
        path = Path(env_root)
        if path.exists():
            return path
    return ROOT_DIR / DATA_ROOT_DIR_NAME


def _prefer_dataset(paths: List[Path]) -> Optional[Path]:
    """作品说明：同名候选中优先当前已导入行业（医药），其余按路径稳定排序。"""
    candidates = sorted({p.resolve() for p in paths if p.exists()}, key=str)
    if not candidates:
        return None
    preferred = [p for p in candidates if PREFERRED_DATASET_TAG in str(p)]
    return (preferred or candidates)[0]


def find_financial_reports_root() -> Optional[Path]:
    path = get_preferred_data_root() / FINANCIAL_REPORTS_DIR_NAME
    return path if path.is_dir() else None


def find_research_reports_root() -> Optional[Path]:
    path = get_preferred_data_root() / RESEARCH_REPORTS_DIR_NAME
    return path if path.is_dir() else None


def find_report_dirs() -> List[Path]:
    root = find_financial_reports_root()
    if not root:
        return []
    return [root / name for name in REPORT_SUBDIR_NAMES if (root / name).is_dir()]


def find_research_dirs() -> List[Path]:
    root = find_research_reports_root()
    if not root:
        return []
    return [root / name for name in RESEARCH_SUBDIR_NAMES if (root / name).is_dir()]


def find_research_metadata_dir(dataset_tag: Optional[str] = None) -> Optional[Path]:
    """作品说明：研报元数据目录；dataset_tag 形如 \"医药\"/\"中药\"，缺省返回 metadata 根。"""
    root = find_research_reports_root()
    if not root:
        return None
    base = root / "metadata"
    if dataset_tag:
        path = base / dataset_tag
        return path if path.is_dir() else None
    return base if base.is_dir() else None


def find_company_registry_path() -> Optional[Path]:
    registry_dir = get_preferred_data_root() / REGISTRY_DIR_NAME
    if not registry_dir.is_dir():
        return None
    candidates = [p for p in registry_dir.glob("*.xlsx") if not p.name.startswith("~$")]
    return _prefer_dataset(candidates)
