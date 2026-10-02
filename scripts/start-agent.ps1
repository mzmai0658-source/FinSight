param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8000,
    [string]$EnvFile = "",
    [string]$Model = "",
    [switch]$OverrideEnv
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

. (Join-Path $PSScriptRoot "Load-Env.ps1") -EnvFile $EnvFile -Override:$OverrideEnv
if ($Model) { $env:LLM_MODEL = $Model; $env:OLLAMA_MODEL = $Model }

# 作品说明：默认启动保持非思考模式；评估入口可独立设置思考参数。
$env:OLLAMA_REASONING_EFFORT = "none"
Write-Host "OLLAMA_REASONING_EFFORT=none (thinking off)"

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

Write-Host "Starting FinSight Python AI service on http://$HostName`:$Port"
Set-Location $Root
& $python -m uvicorn src.api.main:app --host $HostName --port $Port
