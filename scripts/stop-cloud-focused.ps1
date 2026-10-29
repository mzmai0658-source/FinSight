param([Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskMarkerPath = Join-Path ([IO.Path]::GetFullPath($Output, $taskRoot)) 'stack.json'
$taskMarker = Get-Content -LiteralPath $taskMarkerPath -Raw | ConvertFrom-Json
if ([IO.Path]::GetFullPath($taskMarker.root) -ne $taskRoot) { throw 'This marker belongs to another source root.' }
$taskProcesses = @()
foreach ($taskName in @('agent','java')) {
    $taskSaved = $taskMarker.listeners.$taskName
    $taskProcess = Get-Process -Id $taskSaved.pid -ErrorAction SilentlyContinue
    if (-not $taskProcess) { continue }
    if ($taskProcess.StartTime.ToUniversalTime().Ticks.ToString() -ne $taskSaved.started_ticks) {
        throw 'A PID was reused; no process was stopped.'
    }
    $taskProcesses += $taskProcess
}
# Only exact PID/start identities created by test-cloud-focused.ps1 are eligible.
foreach ($taskProcess in $taskProcesses) { Stop-Process -InputObject $taskProcess -ErrorAction Stop }
Write-Host 'Owned isolated application listeners stopped. Daily services, Redis, RabbitMQ and data were not changed.'
