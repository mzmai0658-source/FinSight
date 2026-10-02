param()
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Record = Join-Path $Root '.local_runtime/finsight-processes.json'
if (-not (Test-Path -LiteralPath $Record)) { Write-Host 'No owned FinSight launcher record.'; return }
$Jobs = Get-Content -LiteralPath $Record -Raw | ConvertFrom-Json -AsHashtable
function Stop-OwnedTree([int]$ProcessId) {
    foreach ($child in Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId") {
        Stop-OwnedTree ([int]$child.ProcessId)
    }
    $ownedProcess = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($ownedProcess) { $ownedProcess.Kill() }
}
foreach ($job in $Jobs.Values) {
    $process = Get-Process -Id $job.id -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    # 作品说明：PID 可能被复用；同时核对启动时间，只停止此目录登记的服务树。
    $recordedTime = if ($job.startTime -is [DateTime]) { $job.startTime.ToUniversalTime() } else { [DateTime]::Parse($job.startTime).ToUniversalTime() }
    if ($process.StartTime.ToUniversalTime().Ticks -ne $recordedTime.Ticks) { throw 'Launcher PID was reused; refusing to stop unrelated processes.' }
    Stop-OwnedTree ([int]$job.id)
}
Remove-Item -LiteralPath $Record
Write-Host 'FinSight application services stopped; shared MySQL, Ollama, Redis and RabbitMQ retained.'
