param(
    [string]$EnvFile = "",
    [string]$Database = "",
    [switch]$SkipDatabase,
    [switch]$SkipModelCheck
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $Root ".env.demo"
}
if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Missing $EnvFile. Copy .env.demo.example to .env.demo and set the local MySQL credentials first."
}
 . (Join-Path $PSScriptRoot "Load-Env.ps1") -EnvFile $EnvFile -Override -SkipCredentialPreparation
$configuredDatabase = if ($env:DB_NAME) { $env:DB_NAME } else { "finsight_demo" }
if ($Database -and $Database -ne $configuredDatabase) {
    throw "Database must match DB_NAME in the environment file. Edit the isolated demo configuration first."
}
$Database = $configuredDatabase
if ($Database -notmatch '^finsight_demo(_[a-zA-Z0-9_]+)?$') {
    throw "Public demo bootstrap is restricted to the isolated finsight_demo database."
}

 . (Join-Path $PSScriptRoot "Load-Env.ps1") -EnvFile $EnvFile -Override

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

$env:LLM_PROVIDER = "openai_compat"
$env:EMBEDDING_PROVIDER = "bge_local"
$env:DB_NAME = $Database
$env:MYSQL_DATABASE = $Database
$env:CHROMA_DB_PATH = "data/demo_chroma_db"

if (-not $SkipModelCheck) {
    & $python (Join-Path $PSScriptRoot "check_cloud_model.py") --env $EnvFile
    if ($LASTEXITCODE -ne 0) {
        throw "Cloud configuration or model check failed with exit code $LASTEXITCODE"
    }
}

if (-not $SkipDatabase) {
    Write-Host "Seeding isolated MySQL database $Database ..."
    & $python (Join-Path $PSScriptRoot "bootstrap_demo_v3.py") --env-file $EnvFile
    if ($LASTEXITCODE -ne 0) {
        throw "MySQL demo import failed with exit code $LASTEXITCODE"
    }
}

& $python (Join-Path $PSScriptRoot "ensure_demo_reader.py") --env-file $EnvFile
if ($LASTEXITCODE -ne 0) { throw "Demo read-only account setup failed" }
& $python (Join-Path $PSScriptRoot "runtime_env.py") --env-file $EnvFile
if ($LASTEXITCODE -ne 0) { throw "Local service credential setup failed" }

Write-Host ""
Write-Host "Demo seed completed. Start each service with the same environment file:"
Write-Host "  .\scripts\start-agent.ps1 -EnvFile .env.demo -OverrideEnv"
Write-Host "  .\scripts\start-java.ps1 -EnvFile .env.demo -OverrideEnv"
Write-Host "  .\scripts\start-frontend.ps1 -EnvFile .env.demo -OverrideEnv"
