[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'backups'),
    [string]$MySqlBin = ''
)

$ErrorActionPreference = 'Stop'
$envPath = Join-Path $PSScriptRoot 'backend/.env'
$runtimeDir = Join-Path $PSScriptRoot '.runtime/production'

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
    if ($MySqlBin) {
        $candidate = Join-Path $MySqlBin $Name
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
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

if (-not (Test-Path -LiteralPath $envPath)) { throw '缺少 backend/.env。' }
$database = Get-EnvValue 'MYSQL_DATABASE'
$user = Get-EnvValue 'MYSQL_MIGRATION_USER'
$password = Get-EnvValue 'MYSQL_MIGRATION_PASSWORD'
$hostName = Get-EnvValue 'MYSQL_HOST'
$port = Get-EnvValue 'MYSQL_PORT'
if (-not $database -or -not $user -or -not $password) { throw '备份需要 MYSQL_DATABASE、MYSQL_MIGRATION_USER 和 MYSQL_MIGRATION_PASSWORD。' }
if (-not $hostName) { $hostName = '127.0.0.1' }
if (-not $port) { $port = '3306' }
$dumpExe = Find-MySqlTool 'mysqldump.exe'

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$outputDir = (Resolve-Path -LiteralPath $OutputDirectory).Path
$backupPath = Join-Path $outputDir ("warehouse_{0}.sql" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
if (Test-Path -LiteralPath $backupPath) { throw "备份文件已存在，不会覆盖：$backupPath" }
$defaultsPath = Join-Path $runtimeDir ("mysql-backup-{0}.cnf" -f [guid]::NewGuid().ToString('N'))
try {
    @('[client]', "host=`"$(Escape-OptionValue $hostName)`"", "port=$port", "user=`"$(Escape-OptionValue $user)`"", "password=`"$(Escape-OptionValue $password)`"", 'default-character-set=utf8mb4') |
        Set-Content -LiteralPath $defaultsPath -Encoding ASCII
    Protect-SecretFile $defaultsPath
    $arguments = @("--defaults-extra-file=$defaultsPath", '--single-transaction', '--quick', '--triggers', '--events',
                   '--hex-blob', '--set-gtid-purged=OFF', '--no-tablespaces', "--result-file=$backupPath", $database)
    & $dumpExe @arguments
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $backupPath) -or (Get-Item -LiteralPath $backupPath).Length -eq 0) {
        Remove-Item -LiteralPath $backupPath -ErrorAction SilentlyContinue
        throw 'mysqldump 失败，未保留不完整备份。'
    }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $backupPath).Hash
    Set-Content -LiteralPath "$backupPath.sha256" -Encoding ASCII -Value "$hash  $([IO.Path]::GetFileName($backupPath))"
    Write-Host "数据库备份完成：$backupPath"
    Write-Host "SHA256：$hash"
} finally {
    Remove-Item -LiteralPath $defaultsPath -Force -ErrorAction SilentlyContinue
}
