# PowerShell 5.1+. Creates a cold copy, then inspects only that copy.
[CmdletBinding()]
param(
    [string]$SourceRoot = 'D:\LightWarehouseWMS',
    [string]$RecoveryRoot = 'D:\LightWarehouseWMS-Recovery',
    [ValidateRange(1024,65535)][int]$TestPort = 33680
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskSource = (Resolve-Path -LiteralPath $SourceRoot).Path.TrimEnd('\')
$taskRecovery = [IO.Path]::GetFullPath($RecoveryRoot).TrimEnd('\')
if ($taskSource -eq [IO.Path]::GetPathRoot($taskSource).TrimEnd('\') -or
    $taskRecovery -eq [IO.Path]::GetPathRoot($taskRecovery).TrimEnd('\') -or
    $taskRecovery.Equals($taskSource,[StringComparison]::OrdinalIgnoreCase) -or
    $taskRecovery.StartsWith($taskSource+'\',[StringComparison]::OrdinalIgnoreCase)) {
    throw '恢复目录必须是安装目录之外的专用目录，不能是磁盘根目录。'
}
foreach ($taskPath in @($taskSource,$taskRecovery)) {
    $taskAncestor = $taskPath
    while ($taskAncestor) {
        if (Test-Path -LiteralPath $taskAncestor) {
            if ((Get-Item -LiteralPath $taskAncestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw '目标路径包含链接，停止。'
            }
        }
        $taskAncestor = Split-Path -Path $taskAncestor -Parent
    }
}
foreach ($taskRequired in @('data\config.json','data\mysql\auto.cnf','python\python.exe','mysql\bin\mysqld.exe','mysql\bin\mysqldump.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $taskSource $taskRequired) -PathType Leaf)) { throw '原安装缺少诊断所需组件，停止。' }
}
$taskConfig = Get-Content -LiteralPath (Join-Path $taskSource 'data\config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($taskConfig.format -ne 1 -or $taskConfig.ready -ne $true -or -not $taskConfig.server_uuid) { throw '原配置状态不符合已初始化实例，停止。' }
if ($TestPort -in @(80,3306,[int]$taskConfig.mysql_port,[int]$taskConfig.web_port)) { throw '测试端口必须与原实例端口不同。' }
function Assert-SourceStopped {
    $taskManagedNames = @('mysqld.exe','python.exe','pythonw.exe','轻仓WMS.exe')
    $taskProcesses = @(Get-CimInstance Win32_Process | Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($taskSource+'\',[StringComparison]::OrdinalIgnoreCase)) -or
        ($_.Name -in $taskManagedNames -and $_.CommandLine -and $_.CommandLine.IndexOf($taskSource,[StringComparison]::OrdinalIgnoreCase) -ge 0 -and $_.ProcessId -ne $PID)
    })
    if ($taskProcesses.Count -gt 0) { throw '发现使用原安装目录的进程，停止；本脚本不会强停进程。' }
    foreach ($taskPort in @([int]$taskConfig.mysql_port,[int]$taskConfig.web_port,$TestPort)) {
        if (@(Get-NetTCPConnection -State Listen -LocalPort $taskPort -ErrorAction SilentlyContinue).Count -gt 0) { throw '原实例或测试端口存在监听，停止。' }
    }
}
function Get-TreeHashes([string]$Directory) {
    $taskMap = @{}
    foreach ($taskEntry in @(Get-ChildItem -LiteralPath $Directory -Force -Recurse)) {
        if ($taskEntry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '文件树包含链接，停止。' }
        if (-not $taskEntry.PSIsContainer) {
            $taskMap[$taskEntry.FullName.Substring($Directory.Length+1)] = (Get-FileHash -LiteralPath $taskEntry.FullName -Algorithm SHA256).Hash
        }
    }
    return $taskMap
}
Assert-SourceStopped
$taskBefore = Get-TreeHashes $taskSource
$taskRun = Join-Path $taskRecovery ((Get-Date -Format 'yyyyMMdd_HHmmss')+'_'+[Guid]::NewGuid().ToString('N').Substring(0,8))
if (Test-Path -LiteralPath $taskRun) { throw '本次目录已存在，停止。' }
New-Item -ItemType Directory -Path $taskRun | Out-Null
$taskSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $taskRun /inheritance:r /grant:r ('*'+$taskSid+':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw '保护诊断目录权限失败，停止。' }
$taskWorking = Join-Path $taskRun 'working'
& robocopy.exe $taskSource $taskWorking /E /COPY:DAT /DCOPY:DAT /R:0 /W:0 /XJ /NP /NFL /NDL ('/LOG:'+ (Join-Path $taskRun 'copy.log')) | Out-Null
if ($LASTEXITCODE -ge 8) { throw '冷副本复制失败，停止；原目录未修改。' }
Assert-SourceStopped
$taskAfter = Get-TreeHashes $taskSource
$taskCopied = Get-TreeHashes $taskWorking
if ($taskBefore.Count -ne $taskAfter.Count -or $taskBefore.Count -ne $taskCopied.Count) { throw '复制期间文件集合变化，停止。' }
foreach ($taskName in $taskBefore.Keys) {
    if ($taskBefore[$taskName] -ne $taskAfter[$taskName] -or $taskBefore[$taskName] -ne $taskCopied[$taskName]) { throw '原目录变化或复制校验不一致，停止。' }
}
@{source_files=$taskBefore.Count;copied_files=$taskCopied.Count;sha256_all_equal=$true;test_port=$TestPort} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRun 'copy-verification.json') -Encoding UTF8
$taskProbe = Join-Path $PSScriptRoot 'Inspect-WmsUpgradeCopy.py'
if (-not (Test-Path -LiteralPath $taskProbe -PathType Leaf)) { throw '缺少配套副本诊断脚本，停止。' }
& (Join-Path $taskWorking 'python\python.exe') -I $taskProbe --copy $taskWorking --source $taskSource --port $TestPort
if ($LASTEXITCODE -ne 0) { throw ('副本检查未通过，停止；保留本次目录：'+$taskRun) }
Assert-SourceStopped
$taskFinal = Get-TreeHashes $taskSource
if ($taskFinal.Count -ne $taskBefore.Count) { throw '原目录文件集合变化，停止。' }
foreach ($taskName in $taskBefore.Keys) {
    if ($taskBefore[$taskName] -ne $taskFinal[$taskName]) { throw '原目录哈希变化，停止。' }
}
Write-Output ('副本诊断完成。原目录逐文件SHA未变；结果：'+(Join-Path $taskRun 'inspection.json'))
Write-Output '本脚本没有恢复正式系统。请先审阅结果，再选择经验证的恢复分支。'
