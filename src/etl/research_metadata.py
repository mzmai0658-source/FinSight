"""作品说明：研报元数据索引：标题 ↔ 股票代码/机构/评级/发布日期。

数据源：data_root/research_reports/metadata/医药|中药 下的研报信息 xlsx。
研报 PDF 文件名即标题，借此把 RAG 切片与结构化元数据关联起来。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
from loguru import logger

from src.utils.data_paths import find_research_metadata_dir


@dataclass
class ResearchMeta:
    title: str
    report_type: str  # 作品说明：股票代码与行业身份。
    stock_code: str = ""
    stock_name: str = ""
    org_name: str = ""
    org_sname: str = ""
    publish_date: str = ""
    industry_name: str = ""
    rating: str = ""
    researcher: str = ""
    dataset_tag: str = ""
    extra: Dict[str, str] = field(default_factory=dict)


def normalize_title(title: str) -> str:
    """作品说明：文件名/标题归一化：去空白与全半角差异，便于匹配。"""
    text = str(title or "").strip()
    text = re.sub(r"\s+", "", text)
    table = str.maketrans({"：": ":", "，": ",", "（": "(", "）": ")", "！": "!", "？": "?"})
    return text.translate(table)


def _clean(value) -> str:
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "nat", "<na>"} else text


def _normalize_code(value) -> str:
    digits = re.sub(r"\D", "", str(value))
    return digits.zfill(6) if digits else ""


def load_research_metadata(metadata_dir: Optional[Path] = None) -> Dict[str, ResearchMeta]:
    """作品说明：构建 normalized_title → ResearchMeta 索引（个股研报含股票代码）。"""
    base = metadata_dir or find_research_metadata_dir()
    index: Dict[str, ResearchMeta] = {}
    if base is None or not Path(base).is_dir():
        logger.warning("研报元数据目录不存在，跳过元数据关联")
        return index

    for xlsx in sorted(Path(base).rglob("*.xlsx")):
        if xlsx.name.startswith("~$") or "字段说明" in xlsx.name:
            continue
        dataset_tag = xlsx.parent.name  # 作品说明：医药 / 中药
        is_stock = "个股" in xlsx.name
        try:
            df = pd.read_excel(xlsx)
        except Exception as e:
            logger.warning(f"读取研报元数据失败 {xlsx.name}: {e}")
            continue
        for _, row in df.iterrows():
            title = _clean(row.get("title"))
            if not title:
                continue
            meta = ResearchMeta(
                title=title,
                report_type="stock" if is_stock else "industry",
                stock_code=_normalize_code(row.get("stockCode")) if is_stock else "",
                stock_name=_clean(row.get("stockName")) if is_stock else "",
                org_name=_clean(row.get("orgName")),
                org_sname=_clean(row.get("orgSName")),
                publish_date=_clean(row.get("publishDate"))[:10],
                industry_name=_clean(row.get("indvInduName") or row.get("industryName")),
                rating=_clean(row.get("emRatingName") or row.get("sRatingName")),
                researcher=_clean(row.get("researcher")),
                dataset_tag=dataset_tag,
                extra={
                    "last_rating": _clean(row.get("lastEmRatingName")),
                    "predict_this_year_eps": _clean(row.get("predictThisYearEps")),
                    "predict_this_year_pe": _clean(row.get("predictThisYearPe")),
                    "aim_price": _clean(row.get("indvAimPriceT")),
                },
            )
            index.setdefault(normalize_title(title), meta)

    logger.info(f"研报元数据索引构建完成: {len(index)} 条")
    return index


def lookup_research_meta(
    index: Dict[str, ResearchMeta], filename_or_title: str,
) -> Optional[ResearchMeta]:
    """作品说明：按文件名（去扩展名）或标题查找元数据。"""
    stem = Path(str(filename_or_title)).stem
    return index.get(normalize_title(stem))
