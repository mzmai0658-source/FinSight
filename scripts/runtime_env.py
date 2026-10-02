"""作品说明：准备私有本机服务令牌，不输出凭据。"""
import argparse
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.runtime_security import TOKEN_FILE


def ensure_token() -> str:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with TOKEN_FILE.open("x", encoding="utf-8") as stream:
            os.chmod(TOKEN_FILE, 0o600)
            stream.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise RuntimeError("Invalid local service token; replace the private runtime file")
    return token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    token = ensure_token()
    if args.env_file:
        from dotenv import dotenv_values, set_key
        existing = dotenv_values(args.env_file).get("INTERNAL_API_TOKEN")
        if not existing:
            set_key(str(args.env_file), "INTERNAL_API_TOKEN", token)
        values=dotenv_values(args.env_file)
        if not values.get('JWT_SECRET') or values.get('JWT_SECRET','').startswith('replace-'):
            set_key(str(args.env_file),'JWT_SECRET',secrets.token_urlsafe(48))
        if not values.get('ADMIN_PASSWORD') or values.get('ADMIN_PASSWORD')=='admin123456':
            set_key(str(args.env_file),'ADMIN_PASSWORD',secrets.token_urlsafe(24))
    print("Local service credential prepared (not displayed).")


if __name__ == "__main__":
    main()
