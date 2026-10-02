param(
    [int]$Port = 5173,
    [string]$ApiBaseUrl = "",
    [string]$EnvFile = "",
    [switch]$OverrideEnv
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

. (Join-Path $PSScriptRoot "Load-Env.ps1") -EnvFile $EnvFile -Override:$OverrideEnv

if (-not [string]::IsNullOrWhiteSpace($ApiBaseUrl)) {
    $env:VITE_API_BASE_URL = $ApiBaseUrl.TrimEnd("/")
}

Write-Host "Starting FinSight frontend on http://localhost:$Port"
Push-Location (Join-Path $Root "frontend")
try {
    & npm run dev -- --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}
