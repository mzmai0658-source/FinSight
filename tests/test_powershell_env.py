"""作品说明：运行真实 Windows 启动器，检查环境配置优先级。"""
import base64
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell launcher')
def test_explicit_env_values_survive_override_and_default_setup(tmp_path):
    env_file = tmp_path / 'custom.env'
    env_file.write_text('REDIS_PORT=16379\nRABBITMQ_PORT=15672\nAGENT_BASE_URL=http://127.0.0.1:18000\nINTERNAL_API_TOKEN=fixture-only\n', encoding='utf-8')
    loader = Path(__file__).resolve().parents[1] / 'scripts/Load-Env.ps1'
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    command = f"""
$ErrorActionPreference='Stop'
$env:REDIS_PORT='9999'
. {quote(loader)} -EnvFile {quote(env_file)} -Override
if ($env:REDIS_PORT -ne '16379' -or $env:RABBITMQ_PORT -ne '15672' -or $env:AGENT_BASE_URL -ne 'http://127.0.0.1:18000') {{ throw 'Explicit file settings overwritten' }}
$env:REDIS_PORT='8888'
. {quote(loader)} -EnvFile {quote(env_file)}
if ($env:REDIS_PORT -ne '8888') {{ throw 'Inherited setting overwritten without Override' }}
"""
    encoded = base64.b64encode(command.encode('utf-16le')).decode('ascii')
    subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded], check=True, capture_output=True, timeout=30)
