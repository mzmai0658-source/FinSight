param(
    [int]$Port = 8080,
    [string]$AgentBaseUrl = "",
    [string]$EnvFile = "",
    [switch]$OverrideEnv
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

. (Join-Path $PSScriptRoot "Load-Env.ps1") -EnvFile $EnvFile -Override:$OverrideEnv
if ($AgentBaseUrl) { $env:AGENT_BASE_URL = $AgentBaseUrl }

if ($Port -gt 0) {
    $env:SERVER_PORT = [string]$Port
}

$dbHost = [Environment]::GetEnvironmentVariable("MYSQL_HOST", "Process")
$dbPort = [Environment]::GetEnvironmentVariable("MYSQL_PORT", "Process")
$dbName = [Environment]::GetEnvironmentVariable("MYSQL_DATABASE", "Process")
$dbUser = [Environment]::GetEnvironmentVariable("MYSQL_USER", "Process")

if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable("MYSQL_PASSWORD", "Process"))) {
    Write-Warning "MYSQL_PASSWORD is empty. If MySQL root has a password, set DB_PASSWORD or MYSQL_PASSWORD in .env."
}

Write-Host "Starting FinSight Java API on http://localhost:$Port"
Write-Host "MySQL: $dbUser@$dbHost`:$dbPort/$dbName"

Push-Location (Join-Path $Root "backend-java")
try {
    & mvn spring-boot:run
} finally {
    Pop-Location
}
