"""作品说明：服务共享凭据优先来自环境变量，其次来自私有本机运行文件。"""
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
TOKEN_FILE = ROOT_DIR / "data" / "runtime" / "internal-api-token"


def internal_api_token() -> str:
    token = os.getenv("INTERNAL_API_TOKEN", "").strip()
    if not token and TOKEN_FILE.is_file():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    return token if len(token) >= 32 else ""
