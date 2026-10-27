param(
    [ValidateSet('focused28','followup36')][string]$SuiteProfile = 'focused28',
    [string]$EnvFile = '',
    [string]$Output = '',
    [string]$Python = '',
    [int]$AgentPort = 19300,
    [int]$JavaPort = 19380,
    [int]$RedisDatabase = 13,
    [string]$RabbitCtl = '',
    [switch]$UseExisting,
    [switch]$Resume,
    [switch]$CheckOnly,
    [switch]$SkipCancelProbe
)

$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'Use PowerShell 7 (pwsh).' }
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $Python) { $Python = Join-Path $taskRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Set -Python to the installed Python 3.11 environment.' }
if (-not $Output) { $Output = Join-Path $taskRoot ('.local_runtime\cloud-focused-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$Output = [IO.Path]::GetFullPath($Output, $taskRoot)
$taskRunner = Join-Path $taskRoot 'eval\cloud_focused.py'
$taskMarker = Join-Path $Output 'stack.json'
$taskAgentBase = "http://127.0.0.1:$AgentPort"
$taskJavaBase = "http://127.0.0.1:$JavaPort"
$taskArgs = @('-X','utf8',$taskRunner,'--output',$Output,'--base',$taskJavaBase,'--agent-base',$taskAgentBase,'--suite-profile',$SuiteProfile)
if ($CheckOnly) {
    & $Python @taskArgs --check-only
    exit $LASTEXITCODE
}
if (($AgentPort -eq $JavaPort) -or $AgentPort -lt 1024 -or $JavaPort -lt 1024 -or $AgentPort -gt 65535 -or $JavaPort -gt 65535) {
    throw 'Use two different explicit test ports.'
}
if (@(8000,8080,18000,18080,19000,19100) -contains $AgentPort -or @(8000,8080,18000,18080,19000,19100) -contains $JavaPort) {
    throw 'Daily and historical service ports cannot be used by this harness.'
}
if (-not $Resume -and (Test-Path -LiteralPath (Join-Path $Output 'freeze.json'))) { throw 'Existing output requires -Resume.' }
if ($Resume -and -not (Test-Path -LiteralPath (Join-Path $Output 'freeze.json'))) { throw 'Cannot resume an unidentified output.' }

# Read private settings into this process only. API keys are never written to output.
if (-not $EnvFile) {
    $taskRealEnv = Join-Path $taskRoot '.local_runtime\Real Rebuild Room\FinSight\.env'
    $EnvFile = if (Test-Path -LiteralPath $taskRealEnv) { $taskRealEnv } else { Join-Path $taskRoot '.env' }
}
$EnvFile = [IO.Path]::GetFullPath($EnvFile, $taskRoot)
if (-not (Test-Path -LiteralPath $EnvFile)) { throw 'The selected private finance environment file does not exist.' }
. (Join-Path $PSScriptRoot 'Load-Env.ps1') -EnvFile $EnvFile -Override -SkipCredentialPreparation
$env:PYTHON_DOTENV_DISABLED = '1'
$taskDailyRedis = if ($env:REDIS_DATABASE) { [int]$env:REDIS_DATABASE } else { 0 }
if ($RedisDatabase -lt 1 -or $RedisDatabase -gt 15 -or $RedisDatabase -eq $taskDailyRedis) {
    throw 'Select -RedisDatabase in 1..15 different from the daily .env index.'
}
$taskFinanceName = if ($env:SQL_DB_NAME) { $env:SQL_DB_NAME } else { $env:DB_NAME }
if ($taskFinanceName -ne 'finsight_real_rebuild_20261002') { throw 'The accepted real read-only finance database was not selected.' }
if ($env:EMBEDDING_MODEL -and $env:EMBEDDING_MODEL -ne 'BAAI/bge-small-zh-v1.5') { throw 'Preserve the accepted BGE model identity.' }
if ($env:EMBEDDING_PROVIDER -and $env:EMBEDDING_PROVIDER -ne 'bge_local') { throw 'Preserve local BGE.' }
if (-not (Test-Path Env:LLM_API_KEY) -and $env:DEEPSEEK_API_KEY) { $env:LLM_API_KEY = $env:DEEPSEEK_API_KEY }
if (-not $env:LLM_API_KEY) {
    $taskSecureKey = Read-Host 'Private DeepSeek API key (not logged)' -AsSecureString
    $env:LLM_API_KEY = [Net.NetworkCredential]::new('', $taskSecureKey).Password
    $taskSecureKey = $null
}
if (-not $env:LLM_API_KEY) { throw 'A private cloud credential is required.' }
$env:LLM_PROVIDER = 'openai_compat'
$env:LLM_BASE_URL = 'https://api.deepseek.com'
$env:LLM_MODEL = 'deepseek-flash'
$env:LLM_TIMEOUT_SECONDS = '60'
$env:LLM_MAX_RETRIES = '0'
$env:LLM_MAX_TOKENS = '3072'
$env:EMBEDDING_PROVIDER = 'bge_local'
$env:EMBEDDING_MODEL = 'BAAI/bge-small-zh-v1.5'
$env:SQL_DB_NAME = $taskFinanceName
$env:DB_NAME = 'finsight_cloud_alignment_20261020'
$env:MYSQL_DATABASE = $env:DB_NAME
$env:REDIS_DATABASE = [string]$RedisDatabase
$env:RABBITMQ_VHOST = '/finsight-cloud-alignment-20261020'
$env:FINSIGHT_TASK_DB = Join-Path $Output 'tasks.sqlite3'
$env:FINSIGHT_MODEL_AUDIT_PATH = Join-Path $Output 'model-audit.jsonl'
$env:AGENT_BASE_URL = $taskAgentBase
$env:SERVER_PORT = [string]$JavaPort
$env:CHAT_QUOTA_PER_DAY = '1000'
New-Item -ItemType Directory -Force -Path $Output | Out-Null
$taskPrivate = Join-Path $Output '.private'
New-Item -ItemType Directory -Force -Path $taskPrivate | Out-Null
$taskSecrets = Join-Path $taskPrivate 'runtime-secrets.json'
if (-not (Test-Path -LiteralPath $taskSecrets)) {
    @{internal_token=[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(48));
      jwt_secret=[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(48))} |
        ConvertTo-Json | Set-Content -LiteralPath $taskSecrets -Encoding utf8
}
$taskPrivateSettings = Get-Content -LiteralPath $taskSecrets -Raw | ConvertFrom-Json
$env:INTERNAL_API_TOKEN = $taskPrivateSettings.internal_token
$env:JWT_SECRET = $taskPrivateSettings.jwt_secret
$taskPrivateSettings = $null
$taskNamespaces = @{chat_db=$env:DB_NAME;finance_db=$env:SQL_DB_NAME;redis_database=$RedisDatabase;
    rabbitmq_vhost=$env:RABBITMQ_VHOST;task_db=$env:FINSIGHT_TASK_DB}

function ConvertTo-TaskLiteral([string]$Value) { return "'" + $Value.Replace("'", "''") + "'" }
function Start-TaskHidden([string]$Name, [string]$Command) {
    $taskEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))
    return Start-Process -FilePath 'pwsh.exe' -ArgumentList @('-NoProfile','-EncodedCommand',$taskEncoded) -WindowStyle Hidden `
        -WorkingDirectory $taskRoot -RedirectStandardOutput (Join-Path $Output ($Name+'.stdout.log')) `
        -RedirectStandardError (Join-Path $Output ($Name+'.stderr.log')) -PassThru
}
function Test-TaskDescendant([int]$ChildId, [int]$LauncherId) {
    $taskWalk = $ChildId
    for ($taskDepth = 0; $taskDepth -lt 12 -and $taskWalk -gt 0; $taskDepth++) {
        if ($taskWalk -eq $LauncherId) { return $true }
        $taskInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$taskWalk" -ErrorAction Stop
        if (-not $taskInfo) { break }
        $taskWalk = [int]$taskInfo.ParentProcessId
    }
    return $false
}
function Wait-TaskListener([int]$Port, [Diagnostics.Process]$Launcher) {
    $taskUntil = (Get-Date).AddSeconds(150)
    while ((Get-Date) -lt $taskUntil) {
        $Launcher.Refresh()
        if ($Launcher.HasExited) { throw "Isolated launcher exited; inspect logs in $Output." }
        $taskOwners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
        if ($taskOwners.Count -eq 1) {
            if (-not (Test-TaskDescendant -ChildId $taskOwners[0] -LauncherId $Launcher.Id)) { throw 'An unknown process occupied the requested test port.' }
            $taskProcess = Get-Process -Id $taskOwners[0] -ErrorAction Stop
            return @{pid=$taskProcess.Id;started_ticks=$taskProcess.StartTime.ToUniversalTime().Ticks.ToString()}
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Isolated startup timed out; inspect logs in $Output. Existing processes were not stopped."
}
function Wait-TaskHttp([string]$Url, [hashtable]$Headers = @{}) {
    $taskUntil = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $taskUntil) {
        try {
            $taskResponse = Invoke-WebRequest -Uri $Url -Headers $Headers -TimeoutSec 3 -SkipHttpErrorCheck
            if ($taskResponse.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw "Isolated HTTP startup did not finish; inspect logs in $Output. No daily service was stopped."
}

if ($UseExisting) {
    if (-not (Test-Path -LiteralPath $taskMarker)) { throw 'An existing stack must have its original checked stack.json marker.' }
} else {
    foreach ($taskPort in @($AgentPort,$JavaPort)) {
        if (Get-NetTCPConnection -State Listen -LocalPort $taskPort -ErrorAction SilentlyContinue) {
            throw "Port $taskPort is occupied. No process was stopped; use the checked original stack or free test ports."
        }
    }
    # Create only the dedicated business database; no finance import, index rebuild or schema mutation.
    Write-Host 'Preparing isolated chat database and message namespace; finance data and index are read-only.'
    & $Python @taskArgs --prepare-chat-db
    if ($LASTEXITCODE -ne 0) { throw 'Dedicated chat database preparation failed.' }
    if (-not $RabbitCtl) {
        $taskRabbit = Get-Command rabbitmqctl.bat,rabbitmqctl -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($taskRabbit) { $RabbitCtl = $taskRabbit.Source }
    }
    if (-not $RabbitCtl) {
        $taskNativeRabbit = Join-Path $taskRoot '.local_runtime\native\rabbitmq'
        if (Test-Path -LiteralPath $taskNativeRabbit) {
            $taskRabbitCandidates = @(Get-ChildItem -LiteralPath $taskNativeRabbit -Directory |
                ForEach-Object { Join-Path $_.FullName 'sbin\rabbitmqctl.bat' } |
                Where-Object { Test-Path -LiteralPath $_ })
            if ($taskRabbitCandidates.Count -eq 1) { $RabbitCtl = $taskRabbitCandidates[0] }
        }
    }
    if (-not $RabbitCtl) { throw 'Set -RabbitCtl to the local rabbitmqctl.bat so the isolated vhost can be prepared.' }
    $taskNativeBase = Join-Path $taskRoot '.local_runtime\native\rabbitmq'
    if ([IO.Path]::GetFullPath($RabbitCtl).StartsWith($taskNativeBase + '\', [StringComparison]::OrdinalIgnoreCase)) {
        $taskCookieFile = Join-Path $taskRoot '.local_runtime\rabbitmq\node.cookie'
        $taskErlang = Join-Path $taskRoot '.local_runtime\native\erlang'
        if (-not (Test-Path -LiteralPath $taskCookieFile) -or -not (Test-Path -LiteralPath (Join-Path $taskErlang 'bin\erl.exe'))) {
            throw 'The native RabbitMQ node was not prepared. Prepare the existing dependencies before this test.'
        }
        $env:ERLANG_HOME = $taskErlang
        $env:RABBITMQ_BASE = Join-Path $taskRoot '.local_runtime\rabbitmq'
        $env:RABBITMQ_NODENAME = 'finsight_v3@localhost'
        $env:RABBITMQ_ERLANG_COOKIE = (Get-Content -LiteralPath $taskCookieFile -Raw).Trim()
        $env:ERL_FLAGS = '-setcookie ' + $env:RABBITMQ_ERLANG_COOKIE
    }
    $taskVhosts = @(& $RabbitCtl list_vhosts -q)
    if ($LASTEXITCODE -ne 0) { throw 'RabbitMQ vhost listing failed.' }
    if ($taskVhosts -notcontains $env:RABBITMQ_VHOST) {
        & $RabbitCtl add_vhost $env:RABBITMQ_VHOST | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Isolated RabbitMQ vhost creation failed.' }
    }
    & $RabbitCtl set_permissions -p $env:RABBITMQ_VHOST $env:RABBITMQ_USERNAME '.*' '.*' '.*' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Isolated RabbitMQ permissions failed.' }
    $taskMaven = (Get-Command mvn.cmd,mvn -ErrorAction Stop | Select-Object -First 1).Source
    $taskAgentCommand = 'Set-Location ' + (ConvertTo-TaskLiteral $taskRoot) + '; & ' + (ConvertTo-TaskLiteral $Python) +
        ' -X utf8 -m uvicorn src.api.main:app --host 127.0.0.1 --port ' + $AgentPort
    $taskJavaCommand = 'Set-Location ' + (ConvertTo-TaskLiteral (Join-Path $taskRoot 'backend-java')) + '; & ' +
        (ConvertTo-TaskLiteral $taskMaven) + ' spring-boot:run'
    $taskSourceDigest = & $Python @taskArgs --source-digest
    if ($LASTEXITCODE -ne 0 -or $taskSourceDigest -notmatch '^[a-f0-9]{64}$') { throw 'Launch source identity could not be frozen.' }
    $taskAgentLauncher = Start-TaskHidden -Name 'agent' -Command $taskAgentCommand
    Write-Host "Waiting for the isolated Python service on $AgentPort..."
    $taskAgentIdentity = Wait-TaskListener -Port $AgentPort -Launcher $taskAgentLauncher
    $taskJavaLauncher = Start-TaskHidden -Name 'java' -Command $taskJavaCommand
    Write-Host "Waiting for the isolated Java service on $JavaPort; startup logs are in $Output."
    $taskJavaIdentity = Wait-TaskListener -Port $JavaPort -Launcher $taskJavaLauncher
    @{version=1;root=$taskRoot;java_base=$taskJavaBase;agent_base=$taskAgentBase;namespaces=$taskNamespaces;source_sha256=$taskSourceDigest;
      listeners=@{agent=$taskAgentIdentity;java=$taskJavaIdentity}} |
        ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $taskMarker -Encoding utf8
}

Wait-TaskHttp -Url ($taskAgentBase + '/internal/agent-version') -Headers @{'X-Internal-Token'=$env:INTERNAL_API_TOKEN}
Wait-TaskHttp -Url ($taskJavaBase + '/actuator/health')

# Runtime verifies listener PID/start identity, read-only release, and the frozen suite before generation.
Write-Host "Starting $SuiteProfile. First answers will be saved once; automatic checks still need final answer review."
if ($Resume) { $taskArgs += '--resume' }
if ($SkipCancelProbe) { $taskArgs += '--skip-cancel-probe' }
& $Python @taskArgs --stack $taskMarker
$taskExit = $LASTEXITCODE
Write-Host "Results and API lifecycle checks: $Output"
Write-Host 'The isolated test processes remain available for inspection/resume. No daily process was stopped.'
exit $taskExit
