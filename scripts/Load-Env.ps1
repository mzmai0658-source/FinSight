param(
    [string]$EnvFile = "",
    [switch]$Override
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $Root ".env"
}

function Set-ProcessEnv {
    param(
        [string]$Name,
        [string]$Value,
        [switch]$Default
    )

    if ([string]::IsNullOrWhiteSpace($Name)) {
        return
    }

    $current = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (($Override -and -not $Default) -or [string]::IsNullOrEmpty($current)) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

function Copy-EnvIfPresent {
    param(
        [string]$Source,
        [string]$Target
    )

    $sourceValue = [Environment]::GetEnvironmentVariable($Source, "Process")
    $targetValue = [Environment]::GetEnvironmentVariable($Target, "Process")
    if (-not [string]::IsNullOrEmpty($sourceValue) -and [string]::IsNullOrEmpty($targetValue)) {
        [Environment]::SetEnvironmentVariable($Target, $sourceValue, "Process")
    }
}

if (Test-Path -LiteralPath $EnvFile) {
    foreach ($rawLine in Get-Content -LiteralPath $EnvFile) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
            continue
        }
        if ($line.StartsWith("export ")) {
            $line = $line.Substring(7).Trim()
        }

        $separator = $line.IndexOf("=")
        if ($separator -le 0) {
            continue
        }

        $name = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        Set-ProcessEnv -Name $name -Value $value
    }
} else {
    Write-Warning "No .env file found at $EnvFile. Defaults from application.yml and Python settings will be used."
}

# 作品说明：本机启动时同步 Java MYSQL_* 与 Python DB_* 配置。
Copy-EnvIfPresent -Source "DB_HOST" -Target "MYSQL_HOST"
Copy-EnvIfPresent -Source "DB_PORT" -Target "MYSQL_PORT"
Copy-EnvIfPresent -Source "DB_USER" -Target "MYSQL_USER"
Copy-EnvIfPresent -Source "DB_PASSWORD" -Target "MYSQL_PASSWORD"
Copy-EnvIfPresent -Source "DB_NAME" -Target "MYSQL_DATABASE"

Copy-EnvIfPresent -Source "MYSQL_HOST" -Target "DB_HOST"
Copy-EnvIfPresent -Source "MYSQL_PORT" -Target "DB_PORT"
Copy-EnvIfPresent -Source "MYSQL_USER" -Target "DB_USER"
Copy-EnvIfPresent -Source "MYSQL_PASSWORD" -Target "DB_PASSWORD"
Copy-EnvIfPresent -Source "MYSQL_DATABASE" -Target "DB_NAME"

Set-ProcessEnv -Name "REDIS_HOST" -Value "127.0.0.1" -Default
Set-ProcessEnv -Name "REDIS_PORT" -Value "6379" -Default
Set-ProcessEnv -Name "RABBITMQ_HOST" -Value "127.0.0.1" -Default
Set-ProcessEnv -Name "RABBITMQ_PORT" -Value "5672" -Default
Set-ProcessEnv -Name "RABBITMQ_USERNAME" -Value "guest" -Default
Set-ProcessEnv -Name "RABBITMQ_PASSWORD" -Value "guest" -Default
Set-ProcessEnv -Name "AGENT_BASE_URL" -Value "http://127.0.0.1:8000" -Default

# 作品说明：两项本机服务共享工作区私有凭据。
if ([string]::IsNullOrWhiteSpace($env:INTERNAL_API_TOKEN)) {
    $taskPython = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython = 'python' }
    & $taskPython (Join-Path $Root 'scripts\runtime_env.py')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to prepare internal service credential.' }
    $env:INTERNAL_API_TOKEN = (Get-Content -LiteralPath (Join-Path $Root 'data\runtime\internal-api-token') -Raw).Trim()
}
