param(
    [int]$Port = 33312,
    [string]$RunName = ('e2e-' + (Get-Date -Format 'yyyyMMdd-HHmmss')),
    [ValidateSet('head', '0011')][string]$TargetRevision = 'head',
    [string]$MySqlBase = 'C:\Program Files\MySQL\MySQL Server 8.4'
)
$ErrorActionPreference = 'Stop'
if ($RunName -notmatch '^e2e-[A-Za-z0-9_-]+$' -or $Port -lt 1024 -or $Port -eq 3306 -or $Port -gt 65535) {
    throw '隔离运行名称或端口无效。'
}
$root = $PSScriptRoot
$runtime = Join-Path $root '.runtime'
$runRoot = Join-Path $runtime $RunName
if (Test-Path -LiteralPath $runRoot) { throw '隔离运行目录已存在；不会覆盖。' }
$mysqld = Join-Path $MySqlBase 'bin/mysqld.exe'
$python = Join-Path $root 'backend/.venv/Scripts/python.exe'
$npm = 'C:\Program Files\nodejs\npm.cmd'
foreach ($file in @($mysqld, $python, $npm)) {
    if (-not (Test-Path -LiteralPath $file)) { throw "缺少所需程序：$file" }
}
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $listener.Start() } finally { $listener.Stop() }

$source = Join-Path $runRoot 'source'
$data = Join-Path $runRoot 'mysql-data'
New-Item -ItemType Directory -Force -Path $source, $data | Out-Null
$archive = Join-Path $runRoot 'source.tar'
Push-Location (Split-Path $root -Parent)
try {
    $safeDirectory = (Split-Path $root -Parent).Replace('\', '/')
    git -c "safe.directory=$safeDirectory" archive --format=tar --output $archive HEAD warehouse-system
    if ($LASTEXITCODE -ne 0) { throw '无法导出已提交源码。' }
} finally { Pop-Location }
tar -xf $archive -C $source --strip-components=1
if ($LASTEXITCODE -ne 0) { throw '无法解压隔离源码。' }
# 当前阶段新增的测试保护文件在提交前也复制进去；业务文件始终来自 HEAD。
Copy-Item -LiteralPath (Join-Path $root 'backend/scripts/check_test_target.py') -Destination (Join-Path $source 'backend/scripts/check_test_target.py')
Copy-Item -LiteralPath (Join-Path $root 'backend/tests/conftest.py') -Destination (Join-Path $source 'backend/tests/conftest.py')
if (Test-Path -LiteralPath (Join-Path $source 'backend/.env')) { throw '隔离源码意外包含 .env；拒绝继续。' }

& $mysqld --no-defaults --initialize-insecure "--basedir=$MySqlBase" "--datadir=$data" "--log-error=$(Join-Path $runRoot 'mysql-init.log')"
if ($LASTEXITCODE -ne 0) { throw '隔离 MySQL 初始化失败。' }
$arguments = @('--no-defaults', ('--basedir="' + $MySqlBase + '"'), ('--datadir="' + $data + '"'),
               "--port=$Port", '--bind-address=127.0.0.1', '--mysqlx=0',
               ('--log-error="' + (Join-Path $runRoot 'mysql.log') + '"'))
Start-Process -FilePath $mysqld -ArgumentList $arguments -WindowStyle Hidden | Out-Null
$serverPid = $null
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    $line = netstat -ano -p tcp | Where-Object { $_ -match "127\.0\.0\.1:$Port\s+0\.0\.0\.0:0\s+LISTENING\s+(\d+)" } | Select-Object -First 1
    if ($line -match 'LISTENING\s+(\d+)') { $serverPid = [int]$Matches[1]; break }
}
if (-not $serverPid) { throw '隔离 MySQL 未在指定端口启动。' }
$process = Get-Process -Id $serverPid -ErrorAction Stop
if ($process.ProcessName -ne 'mysqld' -or $process.Path -ne $mysqld) { throw '监听端口的进程不是指定 MySQL。' }
@{ mysql = @{ id = $serverPid; started_utc_ticks = $process.StartTime.ToUniversalTime().Ticks;
              executable = $mysqld; port = $Port } } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runRoot 'process.json')

$helper = Join-Path $root 'backend/scripts/setup_isolated_test.py'
& $python $helper create $runRoot $Port
if ($LASTEXITCODE -ne 0) { throw '隔离库账号创建失败。' }
& $python $helper preflight $runRoot $Port
if ($LASTEXITCODE -ne 0) { throw '隔离目标校验失败。' }
Push-Location (Join-Path $source 'backend')
try {
    & $python -m alembic upgrade $TargetRevision
    if ($LASTEXITCODE -ne 0) { throw '隔离迁移失败。' }
} finally { Pop-Location }
& $python $helper grants $runRoot $Port
if ($LASTEXITCODE -ne 0) { throw '隔离应用授权失败。' }
Push-Location (Join-Path $root 'frontend')
try {
    & $npm run build -- --outDir (Join-Path $source 'frontend/dist')
    if ($LASTEXITCODE -ne 0) { throw '隔离前端构建失败。' }
} finally { Pop-Location }
Write-Output "隔离实例已就绪：$runRoot"
Write-Output "测试库端口：$Port；运行 run-isolated-tests.ps1 -IsolatedBackend '$($source)/backend'"
