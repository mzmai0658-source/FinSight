"""作品说明：Agent 工具的公共基础：项目根路径与证据路径归一化。

`normalize_reference_path` 同时服务于 RAG 引用输出与后续的答案校验，两侧必须用
同一套归一规则，否则「引用是否真实存在」这类核对会因路径写法差异而误判。
"""

import os
import sys
from pathlib import Path
from typing import Any

# 作品说明：允许以脚本方式直接运行 src 下的模块时仍能 import config / src 包。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

_OCR_JSON_SUFFIX = "_by_PaddleOCR-VL-1.5.json"


def resolve_chroma_db_path() -> Path:
    """作品说明：解析 Chroma 路径；相对路径始终相对项目根目录。"""
    configured = str(os.getenv("CHROMA_DB_PATH") or "").strip()
    path = Path(configured).expanduser() if configured else ROOT_DIR / "data" / "chroma_db"
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


def normalize_reference_path(path_value: Any) -> str:
    """作品说明：把 OCR 缓存文件名还原为原始 PDF 路径，并统一为正斜杠。"""
    path = str(path_value or "").replace("\\", "/")
    if path.endswith(".pdf" + _OCR_JSON_SUFFIX):
        return path[: -len(_OCR_JSON_SUFFIX)]
    return path
