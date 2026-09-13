$ErrorActionPreference = 'Stop'
$statePath = Join-Path $PSScriptRoot '.runtime/processes.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Host '没有本脚本启动的进程记录。'; exit }
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
foreach ($entry in @($state.backend, $state.frontend)) {
    $runningProcess = Get-Process -Id $entry.id -ErrorAction SilentlyContinue
    # 校验启动时间，避免 PID 已复用时误停其他程序。
    if ($runningProcess -and $runningProcess.StartTime.ToUniversalTime().Ticks -eq ([datetime]$entry.started).ToUniversalTime().Ticks) {
        # Windows venv 启动器有 Python 子进程，需要一起结束。
        & taskkill.exe /PID $entry.id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "无法停止进程 $($entry.id)。" }
    }
}
Remove-Item -LiteralPath $statePath
Write-Host '本项目开发服务已停止。'
