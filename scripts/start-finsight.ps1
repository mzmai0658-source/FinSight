param([string]$EnvFile = ".env.demo", [int]$AgentPort = 8000, [int]$JavaPort = 8080, [int]$FrontendPort = 5173)
$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -lt 7) { throw "Unified startup requires PowerShell 7 (pwsh.exe)." }
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Runtime = Join-Path $Root ".local_runtime"
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
foreach ($port in @($AgentPort,$JavaPort,$FrontendPort)) {
    if ($port -lt 1 -or $port -gt 65535) { throw "Invalid service port: $port" }
    $probe = [Net.Sockets.TcpClient]::new()
    try {
        $connect = $probe.ConnectAsync("127.0.0.1",$port)
        if ($connect.Wait(500) -and $probe.Connected) { throw "Port $port is occupied. No FinSight process was started; retain the existing service or select free ports." }
    } catch [System.AggregateException] {
        # 作品说明：本机回环端口拒绝连接时，表示当前未有服务监听。
    } finally { $probe.Dispose() }
}
if (@($AgentPort,$JavaPort,$FrontendPort) | Group-Object | Where-Object Count -gt 1) { throw "FinSight service ports must be distinct." }
& (Join-Path $Root ".venv\Scripts\python.exe") -X utf8 (Join-Path $PSScriptRoot "preflight.py") --env $EnvFile
if ($LASTEXITCODE -ne 0) { throw "Dependency protocol checks failed. Prepare native dependencies before starting FinSight." }
$env:VITE_API_PROXY_TARGET = "http://127.0.0.1:$JavaPort"
$jobs = @(
    @{name="agent";script="start-agent.ps1";args=@("-Port",$AgentPort)},
    @{name="java";script="start-java.ps1";args=@("-Port",$JavaPort,"-AgentBaseUrl","http://127.0.0.1:$AgentPort")},
    @{name="frontend";script="start-frontend.ps1";args=@("-Port",$FrontendPort)}
)
$pids = @{}
$runId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$logRoot = Join-Path $Runtime ("startup-"+$runId)
New-Item -ItemType Directory -Path $logRoot | Out-Null
function ConvertTo-PowerShellLiteral([object]$Value) { return "'" + ([string]$Value).Replace("'", "''") + "'" }
function Wait-ForReady([string]$Url,[Diagnostics.Process]$Process) {
    $until = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $until) {
        $Process.Refresh()
        if ($Process.HasExited) { throw "A FinSight launcher exited before readiness. Inspect $logRoot." }
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 3 -SkipHttpErrorCheck
            if ($response.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw "FinSight readiness timed out for $Url. Inspect $logRoot. Already started processes are recorded and remain available for diagnosis."
}
foreach ($job in $jobs) {
    # 作品说明：进程启动参数直接拼接不能自动保护路径中的空格。
    # 作品说明：对完整 PowerShell 调用编码，保留工作区和环境文件路径的准确身份。
    $command = "& " + (ConvertTo-PowerShellLiteral (Join-Path $PSScriptRoot $job.script)) + " -EnvFile " + (ConvertTo-PowerShellLiteral $EnvFile) + " -OverrideEnv"
    for ($index=0; $index -lt $job.args.Count; $index+=2) {
        $command += " " + $job.args[$index] + " " + (ConvertTo-PowerShellLiteral $job.args[$index+1])
    }
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $arguments = @("-NoProfile","-ExecutionPolicy","Bypass","-EncodedCommand",$encoded)
    $process = Start-Process -FilePath "pwsh.exe" -ArgumentList $arguments -WindowStyle Hidden -WorkingDirectory $Root -RedirectStandardOutput (Join-Path $logRoot ($job.name+".stdout.log")) -RedirectStandardError (Join-Path $logRoot ($job.name+".stderr.log")) -PassThru
    $pids[$job.name] = @{id=$process.Id;startTime=$process.StartTime.ToUniversalTime().ToString('o');logRoot=$logRoot}
    $pids | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $Runtime "finsight-processes.json")
    $ready = switch ($job.name) { "agent" { "http://127.0.0.1:$AgentPort/api/health" }; "java" { "http://127.0.0.1:$JavaPort/actuator/health" }; "frontend" { "http://127.0.0.1:$FrontendPort/" } }
    Wait-ForReady $ready $process
}
Write-Host "FinSight services are ready. Logs: $logRoot. Frontend: http://127.0.0.1:$FrontendPort"
