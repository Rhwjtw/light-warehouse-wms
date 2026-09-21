#requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$TaskName = 'WarehouseSystemV1',
    [switch]$StartNow
)

$ErrorActionPreference = 'Stop'
$startScript = Join-Path $PSScriptRoot 'start-prod.ps1'
if (-not (Test-Path -LiteralPath $startScript)) { throw '未找到 start-prod.ps1。' }
$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`" -Foreground -TaskName `"$TaskName`""
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT30S'
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 `
                                          -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
                       -Description '轻仓 WMS V1.0 生产服务；开机启动，失败最多重试三次。' -Force | Out-Null
Write-Host "开机任务 $TaskName 已安装；服务器启动约 30 秒后运行。"
if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host '已请求立即启动，可用 Get-ScheduledTaskInfo 查看结果。'
}
