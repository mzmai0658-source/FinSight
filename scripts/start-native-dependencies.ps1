param([string]$EnvFile = ".env.demo")
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "Load-Env.ps1") -EnvFile $EnvFile -Override
$Runtime = Join-Path $Root ".local_runtime"
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
function Test-Listening([int]$TargetPort) {
    $connection = [System.Net.Sockets.TcpClient]::new()
    try { return $connection.ConnectAsync("127.0.0.1", $TargetPort).Wait(1500) -and $connection.Connected }
    catch { return $false }
    finally { $connection.Dispose() }
}
$redisExecutable = $env:REDIS_EXECUTABLE
if (-not $redisExecutable) {
    $redisCommand = Get-Command redis-server.exe -ErrorAction SilentlyContinue
    if ($redisCommand) { $redisExecutable = $redisCommand.Source }
}
if (-not $redisExecutable -or -not (Test-Path -LiteralPath $redisExecutable)) { throw "Set REDIS_EXECUTABLE to the installed redis-server.exe path or add it to PATH." }
$redisPort = if ($env:REDIS_PORT) { [int]$env:REDIS_PORT } else { 6379 }
$redisConfig = Join-Path $Runtime "redis.conf"
@("bind 127.0.0.1", "port $redisPort", "protected-mode yes", "dir $($Runtime.Replace('\','/'))", "appendonly yes") | Set-Content -LiteralPath $redisConfig -Encoding ascii
if ($env:REDIS_PASSWORD) { Add-Content -LiteralPath $redisConfig -Value "requirepass $env:REDIS_PASSWORD" -Encoding ascii }
if (-not (Test-Listening $redisPort)) {
    Start-Process -FilePath $redisExecutable -ArgumentList $redisConfig -WindowStyle Hidden -WorkingDirectory $Runtime -RedirectStandardOutput (Join-Path $Runtime "redis.stdout.log") -RedirectStandardError (Join-Path $Runtime "redis.stderr.log") | Out-Null
}
$env:ERLANG_HOME = Join-Path $Runtime "native\erlang"
$rabbitDirectory = Get-ChildItem -LiteralPath (Join-Path $Runtime "native\rabbitmq") -Directory | Select-Object -First 1
if (-not $rabbitDirectory -or -not (Test-Path -LiteralPath (Join-Path $env:ERLANG_HOME "bin\erl.exe"))) { throw "Native Erlang and RabbitMQ packages must be prepared first" }
$env:RABBITMQ_BASE = Join-Path $Runtime "rabbitmq"
New-Item -ItemType Directory -Force -Path $env:RABBITMQ_BASE | Out-Null
$env:RABBITMQ_NODENAME = "finsight_v3@localhost"
$env:RABBITMQ_CONFIG_FILE = Join-Path $env:RABBITMQ_BASE "rabbitmq"
$cookieFile = Join-Path $env:RABBITMQ_BASE "node.cookie"
if (-not (Test-Path -LiteralPath $cookieFile)) {
    ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")) | Set-Content -LiteralPath $cookieFile -Encoding ascii
}
$env:RABBITMQ_ERLANG_COOKIE = (Get-Content -LiteralPath $cookieFile -Raw).Trim()
$env:ERL_FLAGS = "-setcookie $env:RABBITMQ_ERLANG_COOKIE"
$env:RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS = "+S 4:4"
$rabbitPort = if ($env:RABBITMQ_PORT) { [int]$env:RABBITMQ_PORT } else { 5672 }
$rabbitUser = if ($env:RABBITMQ_USERNAME) { $env:RABBITMQ_USERNAME } else { "guest" }
$rabbitPassword = if ($env:RABBITMQ_PASSWORD) { $env:RABBITMQ_PASSWORD } else { "guest" }
@("listeners.tcp.1 = 127.0.0.1:$rabbitPort", "default_user = $rabbitUser", "default_pass = $rabbitPassword", "distribution.listener.interface = 127.0.0.1") | Set-Content -LiteralPath ($env:RABBITMQ_CONFIG_FILE + ".conf") -Encoding ascii
if (-not (Test-Listening $rabbitPort)) {
    Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", (Join-Path $rabbitDirectory.FullName "sbin\rabbitmq-server.bat")) -WindowStyle Hidden -WorkingDirectory $env:RABBITMQ_BASE -RedirectStandardOutput (Join-Path $Runtime "rabbit.stdout.log") -RedirectStandardError (Join-Path $Runtime "rabbit.stderr.log") | Out-Null
}
Write-Host "Native Redis and RabbitMQ startup requested. Protocol readiness is checked separately."
