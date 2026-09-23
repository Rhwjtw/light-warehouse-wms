param(
    [Parameter(Mandatory=$true)][string]$RunName,
    [int]$ApiPort = 18080
)
$ErrorActionPreference = 'Stop'
if ($RunName -notmatch '^e2e-[A-Za-z0-9_-]+$' -or
    $ApiPort -lt 1024 -or $ApiPort -in @(3306, 8000, 80) -or $ApiPort -gt 65535) {
    throw '隔离运行名称或 API 端口无效。'
}
$runtime = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '.runtime')).Path
$runRoot = (Resolve-Path -LiteralPath (Join-Path $runtime $RunName)).Path
if ((Split-Path $runRoot -Parent) -ne $runtime -or
    (Get-Item -LiteralPath $runRoot).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
    throw '隔离运行目录身份无效。'
}
$backend = Join-Path $runRoot 'source/backend'
$identity = Get-Content -LiteralPath (Join-Path $runRoot 'identity.json') -Raw | ConvertFrom-Json
$expectedData = Join-Path $runRoot 'mysql-data'
if ((Resolve-Path -LiteralPath $identity.datadir).Path -ne (Resolve-Path -LiteralPath $expectedData).Path -or
    $identity.database -ne 'warehouse_system' -or [int]$identity.port -eq 3306) {
    throw '隔离数据库身份文件无效。'
}
$statePath = Join-Path $runRoot 'process.json'
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
if ($state.api) { throw '隔离 API 已有进程记录；先核对并停止。' }
$python = Join-Path $PSScriptRoot 'backend/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python) -or
    -not (Test-Path -LiteralPath (Join-Path $backend 'scripts/check_test_target.py'))) {
    throw '隔离副本或 Python 虚拟环境不完整。'
}
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $ApiPort)
try { $listener.Start() } finally { $listener.Stop() }
$env:WMS_TEST_SERVER_UUID = [string]$identity.server_uuid
$env:WMS_TEST_DATADIR = (Resolve-Path -LiteralPath $expectedData).Path
$env:WMS_TEST_PORT = [string]$identity.port
try {
    Push-Location $backend
    try {
        & $python -m scripts.check_test_target
        if ($LASTEXITCODE -ne 0) { throw '隔离数据库校验失败。' }
        & $python scripts/seed_phase2.py
        if ($LASTEXITCODE -ne 0) { throw '隔离演示账号初始化失败。' }
    } finally { Pop-Location }
    $api = Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'app.main:app',
        '--host', '127.0.0.1', '--port', [string]$ApiPort) -WorkingDirectory $backend -WindowStyle Hidden -PassThru
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/health/db" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) {
        Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
        throw '隔离 API 未在指定端口就绪。'
    }
    $line = netstat -ano -p tcp | Where-Object { $_ -match "127\.0\.0\.1:$ApiPort\s+0\.0\.0\.0:0\s+LISTENING\s+(\d+)" } | Select-Object -First 1
    if ($line -notmatch 'LISTENING\s+(\d+)') {
        Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
        throw '隔离 API 监听进程身份不符。'
    }
    $server = Get-Process -Id ([int]$Matches[1]) -ErrorAction Stop
    $venvConfig = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'backend/.venv/pyvenv.cfg')
    $baseLine = $venvConfig | Where-Object { $_ -match '^executable\s*=\s*(.+)$' } | Select-Object -First 1
    if ($baseLine -notmatch '^executable\s*=\s*(.+)$') {
        Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
        throw '无法识别虚拟环境的 Python 进程。'
    }
    $basePython = $Matches[1].Trim()
    if ($server.Path -notin @($python, $basePython) -or
        $server.StartTime -lt $api.StartTime.AddSeconds(-2) -or $api.Path -ne $python) {
        Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
        throw '隔离 API 监听进程身份不符。'
    }
    $state | Add-Member -NotePropertyName api -NotePropertyValue @{
        id = $server.Id; started_utc_ticks = $server.StartTime.ToUniversalTime().Ticks;
        executable = $server.Path; port = $ApiPort
    }
    $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath
    Write-Output "隔离 API 已启动：http://127.0.0.1:$ApiPort"
    Write-Output "测试账号初始密码仅存于：$runRoot/source/.runtime/phase-2-accounts.txt"
} finally {
    Remove-Item Env:WMS_TEST_SERVER_UUID -ErrorAction SilentlyContinue
    Remove-Item Env:WMS_TEST_DATADIR -ErrorAction SilentlyContinue
    Remove-Item Env:WMS_TEST_PORT -ErrorAction SilentlyContinue
}
