"""作品说明：检查本机 Ollama 服务，配置模型缺失时下载该模型。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

import requests


def native_base_url(value: str) -> str:
    base = value.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def installed_models(base_url: str) -> List[str]:
    response = requests.get(native_base_url(base_url) + "/api/tags", timeout=10)
    response.raise_for_status()
    payload = response.json()
    return [str(item.get("name") or "") for item in payload.get("models") or []]


def ensure_model(base_url: str, model: str) -> Dict[str, Any]:
    models = installed_models(base_url)
    if model in models:
        return {"model": model, "status": "present", "downloaded": False}

    endpoint = native_base_url(base_url) + "/api/pull"
    with requests.post(endpoint, json={"model": model}, stream=True, timeout=(10, 3600)) as response:
        response.raise_for_status()
        final_status = ""
        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            event = json.loads(raw_line)
            if event.get("error"):
                raise RuntimeError(str(event["error"]))
            final_status = str(event.get("status") or final_status)
            total = int(event.get("total") or 0)
            completed = int(event.get("completed") or 0)
            if total and completed:
                percent = completed * 100 // total
                print(f"\rPulling {model}: {percent:3d}%", end="", flush=True)
        if final_status:
            print()
    if model not in installed_models(base_url):
        raise RuntimeError(f"Ollama pull returned but {model} is still absent")
    return {"model": model, "status": "pulled", "downloaded": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure the FinSight local Ollama model is installed")
    parser.add_argument("--base-url", default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"))
    parser.add_argument("--model", default=os.getenv("OLLAMA_MODEL") or os.getenv("LLM_MODEL") or "qwen3.5:9b-q4_K_M")
    args = parser.parse_args()
    try:
        result = ensure_model(args.base_url, args.model)
    except (requests.RequestException, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Ollama model check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
