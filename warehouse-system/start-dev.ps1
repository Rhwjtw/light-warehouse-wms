param([string]$NodePath = '')
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$projectRoot = $PSScriptRoot
$runtimeDir = Join-Path $projectRoot '.runtime'
$backendDir = Join-Path $projectRoot 'backend'
$frontendDir = Join-Path $projectRoot 'frontend'
$pythonExe = Join-Path $backendDir '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw '请先按 README 创建 backend/.venv 并安装依赖。' }
if (-not $NodePath) { $NodePath = (Get-Command node.exe -ErrorAction Stop).Source }
if (-not (Test-Path (Join-Path $frontendDir 'node_modules/vite/bin/vite.js'))) { throw '请先在 frontend 运行 npm install。' }
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
# 启动前检查端口，不误停其他项目的服务。
foreach ($port in @(8000, 5173)) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
    try { $listener.Start() } catch { throw "端口 $port 已占用，请先确认已有服务；本脚本不会自动结束它。" }
    finally { $listener.Stop() }
}
$backendProcess = Start-Process -FilePath $pythonExe -ArgumentList '-m uvicorn app.main:app --host 127.0.0.1 --port 8000' -WorkingDirectory $backendDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir 'backend.log') -RedirectStandardError (Join-Path $runtimeDir 'backend-error.log')
$frontendProcess = Start-Process -FilePath $NodePath -ArgumentList 'node_modules/vite/bin/vite.js --configLoader native --host 127.0.0.1 --port 5173 --strictPort' -WorkingDirectory $frontendDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir 'frontend.log') -RedirectStandardError (Join-Path $runtimeDir 'frontend-error.log')
@{ backend = @{ id = $backendProcess.Id; started = $backendProcess.StartTime.ToUniversalTime().ToString('o') }; frontend = @{ id = $frontendProcess.Id; started = $frontendProcess.StartTime.ToUniversalTime().ToString('o') } } | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $runtimeDir 'processes.json')
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $null = Invoke-RestMethod 'http://127.0.0.1:8000/api/health/db' -TimeoutSec 2
        $null = Invoke-WebRequest 'http://127.0.0.1:5173' -UseBasicParsing -TimeoutSec 2
        $ready = $true
        break
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ready) { throw '服务未完全就绪，请检查 .runtime 中日志。使用 stop-dev.ps1 可停止本次进程。' }
Write-Host '前端：http://127.0.0.1:5173'
Write-Host '后端：http://127.0.0.1:8000/api/health'
Write-Host 'Swagger：http://127.0.0.1:8000/docs'
