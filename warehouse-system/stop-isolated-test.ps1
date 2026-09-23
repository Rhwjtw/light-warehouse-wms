param(
    [Parameter(Mandatory=$true)][string]$RunName,
    [switch]$RemoveData
)
$ErrorActionPreference = 'Stop'
if ($RunName -notmatch '^e2e-[A-Za-z0-9_-]+$') { throw '隔离运行名称无效。' }
$runtime = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '.runtime')).Path
$runRoot = (Resolve-Path -LiteralPath (Join-Path $runtime $RunName)).Path
if ((Split-Path $runRoot -Parent) -ne $runtime -or
    -not $runRoot.StartsWith(($runtime.TrimEnd('\') + '\'), [System.StringComparison]::OrdinalIgnoreCase) -or
    (Get-Item -LiteralPath $runRoot).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
    throw '目标目录不在指定的 .runtime 隔离目录内。'
}
$identity = Get-Content -LiteralPath (Join-Path $runRoot 'identity.json') -Raw | ConvertFrom-Json
$expectedData = Join-Path $runRoot 'mysql-data'
if ((Resolve-Path -LiteralPath $identity.datadir).Path -ne (Resolve-Path -LiteralPath $expectedData).Path -or
    $identity.database -ne 'warehouse_system' -or [int]$identity.port -eq 3306) {
    throw '隔离实例身份文件不匹配。'
}
$state = Get-Content -LiteralPath (Join-Path $runRoot 'process.json') -Raw | ConvertFrom-Json
foreach ($name in @('api', 'mysql')) {
    $entry = $state.$name
    if (-not $entry) { continue }
    $process = Get-Process -Id ([int]$entry.id) -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    if ($process.Path -ne [string]$entry.executable -or
        $process.StartTime.ToUniversalTime().Ticks -ne [long]$entry.started_utc_ticks) {
        throw "$name 进程身份不符；不会停止。"
    }
    if ($name -eq 'mysql') {
        $port = [int]$identity.port
        $line = netstat -ano -p tcp | Where-Object { $_ -match "127\.0\.0\.1:$port\s+0\.0\.0\.0:0\s+LISTENING\s+(\d+)" } | Select-Object -First 1
        if ($line -notmatch 'LISTENING\s+(\d+)' -or [int]$Matches[1] -ne [int]$entry.id) {
            throw '隔离数据库监听进程身份不符；不会停止。'
        }
    }
    Stop-Process -Id ([int]$entry.id) -ErrorAction Stop
}
if ($RemoveData) {
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $line = netstat -ano -p tcp | Where-Object { $_ -match "127\.0\.0\.1:$([int]$identity.port)\s+0\.0\.0\.0:0\s+LISTENING" } | Select-Object -First 1
        if (-not $line) { break }
        Start-Sleep -Milliseconds 500
    }
    if ($line) { throw '测试数据库仍在监听，拒绝删除目录。' }
    Remove-Item -LiteralPath $runRoot -Recurse -Force
    Write-Output '隔离测试实例及其全部测试数据已移除。'
} else {
    Write-Output '隔离测试进程已停止；数据目录仍保留。'
}
