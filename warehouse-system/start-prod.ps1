[CmdletBinding()]
param(
    [switch]$Foreground,
    [string]$TaskName = ''
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$projectRoot = $PSScriptRoot
$backendDir = Join-Path $projectRoot 'backend'
$envPath = Join-Path $backendDir '.env'
$pythonExe = Join-Path $backendDir '.venv/Scripts/python.exe'
$runtimeDir = Join-Path $projectRoot '.runtime/production'
$logDir = Join-Path $runtimeDir 'logs'
$statePath = Join-Path $runtimeDir 'process.json'

function Get-EnvValue([string]$Name, [string]$Default = '') {
    $processValue = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ($null -ne $processValue) { return $processValue }
    if (-not (Test-Path -LiteralPath $envPath)) { return $Default }
    $line = Get-Content -LiteralPath $envPath | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -Last 1
    if (-not $line) { return $Default }
    $value = ($line -split '=', 2)[1].Trim()
    if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
        return $value.Substring(1, $value.Length - 2)
    }
    return $value
}

function Test-RecordedProcess($Entry) {
    if (-not $Entry) { return $false }
    $process = Get-Process -Id $Entry.id -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    if ($Entry.started_utc_ticks) {
        $recorded = [datetime]::new([long]$Entry.started_utc_ticks, [datetimekind]::Utc)
    } elseif ($Entry.started -is [datetime]) {
        $recorded = $Entry.started.ToUniversalTime()
    } else {
        $recorded = [datetimeoffset]::Parse([string]$Entry.started).UtcDateTime
    }
    return [math]::Abs(($process.StartTime.ToUniversalTime() - $recorded).TotalSeconds) -lt 2
}

function Stop-RecordedProcess($Entry) {
    if (Test-RecordedProcess $Entry) {
        & taskkill.exe /PID $Entry.id /T /F | Out-Null
    }
}

function Write-AccessAddresses([int]$Port) {
    $suffix = if ($Port -eq 80) { '' } else { ":$Port" }
    Write-Host "服务器本机：http://127.0.0.1$suffix"
    $seen = @{}
    foreach ($network in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
        if ($network.OperationalStatus -ne [System.Net.NetworkInformation.OperationalStatus]::Up) { continue }
        foreach ($address in $network.GetIPProperties().UnicastAddresses) {
            if ($address.Address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { continue }
            $ip = $address.Address.ToString()
            if ($ip -like '127.*' -or $ip -like '169.254.*' -or $seen.ContainsKey($ip)) { continue }
            $seen[$ip] = $true
            $kind = if ($ip -like '100.*') { 'Tailscale IP' } else { '局域网/网络地址' }
            Write-Host ("{0}：http://{1}{2}" -f $kind, $ip, $suffix)
        }
    }
    Write-Host '异地 HTTPS 地址请执行 tailscale serve status 查看。'
}

if (-not (Test-Path -LiteralPath $pythonExe)) { throw '请先按 DEPLOYMENT.md 创建 backend/.venv 并安装锁定依赖。' }
if (-not (Test-Path -LiteralPath $envPath)) { throw '缺少 backend/.env，请从 .env.production.example 复制并填写。' }
if ((Get-EnvValue 'APP_ENV' 'development').ToLowerInvariant() -ne 'production') { throw '生产启动要求 backend/.env 中 APP_ENV=production。' }

$hostAddress = Get-EnvValue 'WEB_HOST' '0.0.0.0'
$port = 0
if (-not [int]::TryParse((Get-EnvValue 'WEB_PORT' '80'), [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
    throw 'WEB_PORT 必须是 1～65535 的端口号。'
}
$workers = 0
if (-not [int]::TryParse((Get-EnvValue 'WEB_WORKERS' '1'), [ref]$workers) -or $workers -lt 1 -or $workers -gt 4) {
    throw 'WEB_WORKERS 必须是 1～4；首版建议保持 1。'
}
$logLevel = (Get-EnvValue 'LOG_LEVEL' 'INFO').ToLowerInvariant()
if ($logLevel -notin @('debug', 'info', 'warning', 'error', 'critical')) { throw 'LOG_LEVEL 配置无效。' }

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
if (Test-Path -LiteralPath $statePath) {
    $oldState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if (Test-RecordedProcess $oldState.backend) {
        Write-Host "生产服务已在运行（PID $($oldState.backend.id)），不会重复启动。"
        Write-AccessAddresses $port
        exit 0
    }
    Remove-Item -LiteralPath $statePath
}

& $pythonExe (Join-Path $backendDir 'scripts/check_production.py')
if ($LASTEXITCODE -ne 0) { throw '生产配置检查失败，请按上方提示修正。' }

$listenAddress = if ($hostAddress -eq '0.0.0.0') { [System.Net.IPAddress]::Any } elseif ($hostAddress -eq '::') { [System.Net.IPAddress]::IPv6Any } else { [System.Net.IPAddress]::Parse($hostAddress) }
$listener = [System.Net.Sockets.TcpListener]::new($listenAddress, $port)
try { $listener.Start() } catch { throw "端口 $port 已占用；本脚本不会停止未知服务。" } finally { $listener.Stop() }

$backendLogPath = Join-Path $logDir 'backend.log'
$arguments = @('-m', 'uvicorn', 'app.main:app', '--host', $hostAddress, '--port', "$port",
               '--log-level', $logLevel, '--log-config', 'logging-production.json', '--no-access-log',
               '--proxy-headers', '--forwarded-allow-ips', '127.0.0.1')
if ($workers -gt 1) { $arguments += @('--workers', "$workers") }
$process = Start-Process -FilePath $pythonExe -ArgumentList $arguments -WorkingDirectory $backendDir -WindowStyle Hidden -PassThru
$state = @{
    backend = @{ id = $process.Id; started = $process.StartTime.ToUniversalTime().ToString('o');
                 started_utc_ticks = $process.StartTime.ToUniversalTime().Ticks; executable = $pythonExe }
    host = $hostAddress
    port = $port
    workers = $workers
    task_name = $TaskName
    foreground = [bool]$Foreground
    log_file = $backendLogPath
}
$state | ConvertTo-Json -Depth 3 | Set-Content -Encoding UTF8 -LiteralPath $statePath
Add-Content -Encoding UTF8 -LiteralPath (Join-Path $logDir 'startup.log') -Value ("{0} started pid={1} host={2} port={3} workers={4}" -f (Get-Date).ToString('o'), $process.Id, $hostAddress, $port, $workers)

$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if (-not (Test-RecordedProcess $state.backend)) { break }
    try {
        $db = Invoke-RestMethod "http://127.0.0.1:$port/api/health/db" -TimeoutSec 2
        $homeResponse = Invoke-WebRequest "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 2
        if ($db.data.status -eq 'ok' -and $homeResponse.StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ready) {
    Stop-RecordedProcess $state.backend
    Remove-Item -LiteralPath $statePath -ErrorAction SilentlyContinue
    throw "生产服务未就绪，请检查日志：$backendLogPath"
}

Write-Host '生产服务已就绪。'
Write-AccessAddresses $port
Write-Host "日志目录：$logDir"

if ($Foreground) {
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    if (Test-Path -LiteralPath $statePath) {
        $currentState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($currentState.backend.id -eq $process.Id) { Remove-Item -LiteralPath $statePath }
    }
    exit $exitCode
}
