[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackupFile,
    [Parameter(Mandatory = $true)][string]$ConfirmDatabase,
    [Parameter(Mandatory = $true)][switch]$IUnderstandThisOverwritesData,
    [string]$MySqlBin = ''
)

$ErrorActionPreference = 'Stop'
$envPath = Join-Path $PSScriptRoot 'backend/.env'
$runtimeDir = Join-Path $PSScriptRoot '.runtime/production'
$statePath = Join-Path $runtimeDir 'process.json'

function Get-EnvValue([string]$Name) {
    $processValue = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ($null -ne $processValue) { return $processValue }
    $line = Get-Content -LiteralPath $envPath | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -Last 1
    if (-not $line) { return '' }
    $value = ($line -split '=', 2)[1].Trim()
    if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or ($value[0] -eq "'" -and $value[-1] -eq "'"))) { return $value.Substring(1, $value.Length - 2) }
    return $value
}

function Find-MySqlTool([string]$Name) {
    if ($MySqlBin) { $candidate = Join-Path $MySqlBin $Name; if (Test-Path -LiteralPath $candidate) { return $candidate } }
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidate = Join-Path $env:ProgramFiles "MySQL/MySQL Server 8.4/bin/$Name"
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    throw "未找到 $Name；请安装 MySQL 客户端或用 -MySqlBin 指定 bin 目录。"
}

function Escape-OptionValue([string]$Value) {
    if ($Value.Contains("`r") -or $Value.Contains("`n")) { throw '数据库配置不能包含换行。' }
    return $Value.Replace('\', '\\').Replace('"', '\"')
}

function Protect-SecretFile([string]$Path) {
    $security = New-Object System.Security.AccessControl.FileSecurity
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'Allow')
    $security.SetOwner($identity)
    $security.SetAccessRuleProtection($true, $false)
    $security.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $security
}

if (-not $IUnderstandThisOverwritesData) { throw '恢复会覆盖目标数据库；必须显式传入 -IUnderstandThisOverwritesData。' }
if (Test-Path -LiteralPath $statePath) { throw '检测到生产服务进程记录。请先运行 stop-prod.ps1，停止业务写入后再恢复。' }
if (-not (Test-Path -LiteralPath $envPath)) { throw '缺少 backend/.env。' }
$resolvedBackup = Resolve-Path -LiteralPath $BackupFile -ErrorAction Stop
if ([IO.Path]::GetExtension($resolvedBackup.Path).ToLowerInvariant() -ne '.sql') { throw '恢复文件必须是 .sql 备份。' }
$database = Get-EnvValue 'MYSQL_DATABASE'
if ($ConfirmDatabase -cne $database) { throw "确认数据库名不匹配；目标是 $database。" }
$user = Get-EnvValue 'MYSQL_MIGRATION_USER'
$password = Get-EnvValue 'MYSQL_MIGRATION_PASSWORD'
$hostName = Get-EnvValue 'MYSQL_HOST'
$port = Get-EnvValue 'MYSQL_PORT'
if (-not $database -or -not $user -or -not $password) { throw '恢复需要迁移账号配置。' }
if (-not $hostName) { $hostName = '127.0.0.1' }
if (-not $port) { $port = '3306' }

$checksumPath = "$($resolvedBackup.Path).sha256"
if (Test-Path -LiteralPath $checksumPath) {
    $expectedHash = ((Get-Content -LiteralPath $checksumPath -First 1) -split '\s+')[0].ToUpperInvariant()
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedBackup.Path).Hash
    if ($expectedHash -ne $actualHash) { throw '备份 SHA256 校验失败，禁止恢复。' }
}

$mysqlExe = Find-MySqlTool 'mysql.exe'
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$defaultsPath = Join-Path $runtimeDir ("mysql-restore-{0}.cnf" -f [guid]::NewGuid().ToString('N'))
try {
    @('[client]', "host=`"$(Escape-OptionValue $hostName)`"", "port=$port", "user=`"$(Escape-OptionValue $user)`"", "password=`"$(Escape-OptionValue $password)`"", 'default-character-set=utf8mb4') |
        Set-Content -LiteralPath $defaultsPath -Encoding ASCII
    Protect-SecretFile $defaultsPath
    Write-Warning "即将用 $($resolvedBackup.Path) 覆盖数据库 $database；请确认已保留当前数据库备份。"
    $process = Start-Process -FilePath $mysqlExe -ArgumentList @("--defaults-extra-file=`"$defaultsPath`"", '--binary-mode', "--database=$database") `
                             -RedirectStandardInput $resolvedBackup.Path -NoNewWindow -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "mysql 恢复失败，退出码 $($process.ExitCode)。" }
    Write-Host '恢复命令执行完成。请立即运行 Alembic current/heads/check 和只读库存对账，再启动服务。'
} finally {
    Remove-Item -LiteralPath $defaultsPath -Force -ErrorAction SilentlyContinue
}
