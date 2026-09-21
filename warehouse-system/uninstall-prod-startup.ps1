#requires -RunAsAdministrator
param([string]$TaskName = 'WarehouseSystemV1', [switch]$KeepServiceRunning)
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) { Write-Host "未找到开机任务 $TaskName。"; exit 0 }
if (-not $KeepServiceRunning) {
    if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName }
    & (Join-Path $PSScriptRoot 'stop-prod.ps1')
}
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "开机任务 $TaskName 已删除。"
