[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$statePath = Join-Path $PSScriptRoot '.runtime/production/process.json'
if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Host '没有 start-prod.ps1 启动的生产进程记录；未停止任何其他进程。'
    exit 0
}

$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
if ($state.task_name) {
    $task = Get-ScheduledTask -TaskName $state.task_name -ErrorAction SilentlyContinue
    if ($task -and $task.State -eq 'Running') { Stop-ScheduledTask -TaskName $state.task_name }
}

$process = Get-Process -Id $state.backend.id -ErrorAction SilentlyContinue
if ($state.backend.started_utc_ticks) {
    $recordedStart = [datetime]::new([long]$state.backend.started_utc_ticks, [datetimekind]::Utc)
} elseif ($state.backend.started -is [datetime]) {
    $recordedStart = $state.backend.started.ToUniversalTime()
} else {
    $recordedStart = [datetimeoffset]::Parse([string]$state.backend.started).UtcDateTime
}
$startMatches = $process -and [math]::Abs(($process.StartTime.ToUniversalTime() - $recordedStart).TotalSeconds) -lt 2
$nameMatches = $process -and $process.ProcessName -ieq 'python'
Write-Verbose ("Recorded PID={0}; processFound={1}; startMatches={2}; nameMatches={3}" -f $state.backend.id, [bool]$process, $startMatches, $nameMatches)
if ($process -and $startMatches -and $nameMatches) {
    # 只按脚本记录且启动时间一致的 PID 停止整棵 Uvicorn 进程树，避免 PID 复用误杀。
    & taskkill.exe /PID $state.backend.id /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "无法停止本项目生产进程 $($state.backend.id)。请用启动服务的同一账号，或在管理员 PowerShell 中重试。"
    }
    Write-Host "已停止本项目生产服务（PID $($state.backend.id)）。"
} else {
    Write-Host '记录中的生产进程已不存在或 PID 已复用，未停止任何进程。'
}
Remove-Item -LiteralPath $statePath
