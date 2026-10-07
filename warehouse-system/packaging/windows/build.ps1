param(
    [Parameter(Mandatory=$true)][string]$InnoCompiler,
    [Parameter(Mandatory=$true)][string]$VCRedist,
    [string]$MySqlBase = 'C:\Program Files\MySQL\MySQL Server 8.4',
    [string]$RunName = ('standalone-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
)
$ErrorActionPreference = 'Stop'
$appDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
if ($RunName -notmatch '^standalone-[A-Za-z0-9_-]+$') { throw '打包名称无效。' }
$outputRoot = Join-Path $appDir ('.runtime/' + $RunName)
if (Test-Path -LiteralPath $outputRoot) { throw '打包目录已存在，不会覆盖。' }
foreach ($file in @($InnoCompiler, $VCRedist, (Join-Path $MySqlBase 'bin/mysqld.exe'))) {
    if (-not (Test-Path -LiteralPath $file)) { throw '缺少打包工具或运行组件。' }
}
$signature = Get-AuthenticodeSignature -LiteralPath $VCRedist
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
    throw '微软运行库签名核对未通过。'
}
$version = & (Join-Path $MySqlBase 'bin/mysqld.exe') --no-defaults --version
if ($version -notmatch 'Ver 8\.4\.9 ') { throw '本打包流程固定使用已验证的 MySQL 8.4.9。' }
$pythonExe = Join-Path $appDir 'backend/.venv/Scripts/python.exe'
$gitRoot = Split-Path $appDir -Parent
$baseline = git -c "safe.directory=$($gitRoot.Replace('\','/'))" -C $gitRoot rev-parse HEAD
git -c "safe.directory=$($gitRoot.Replace('\','/'))" -C $gitRoot diff --quiet HEAD -- warehouse-system/backend warehouse-system/frontend
if ($LASTEXITCODE -ne 0) { throw '业务源码有未提交修改；先核对版本，避免混入未验证代码。' }
New-Item -ItemType Directory -Path $outputRoot | Out-Null
Push-Location (Join-Path $appDir 'frontend')
try {
    & 'C:\Program Files\nodejs\npm.cmd' test
    if ($LASTEXITCODE -ne 0) { throw '前端测试失败。' }
    & 'C:\Program Files\nodejs\npm.cmd' run build -- --outDir (Join-Path $outputRoot 'frontend/dist')
    if ($LASTEXITCODE -ne 0) { throw '隔离前端构建失败。' }
} finally { Pop-Location }
& $pythonExe (Join-Path $PSScriptRoot 'build_package.py') --output (Join-Path $outputRoot 'payload') `
    --frontend (Join-Path $outputRoot 'frontend/dist') --mysql $MySqlBase --redist $VCRedist --baseline $baseline
if ($LASTEXITCODE -ne 0) { throw '运行组件打包失败。' }
& $pythonExe (Join-Path $PSScriptRoot 'build_upgrade.py') --output (Join-Path $outputRoot 'upgrade-payload') --from-package (Join-Path $outputRoot 'payload') --baseline $baseline
if ($LASTEXITCODE -ne 0) { throw '升级组件打包失败。' }
& $InnoCompiler (('/DPayload=' + (Join-Path $outputRoot 'payload'))) (('/DPayloadZip=' + (Join-Path $outputRoot 'wms-upgrade.zip'))) (('/DOutput=' + (Join-Path $outputRoot 'output'))) (Join-Path $PSScriptRoot 'installer.iss')
if ($LASTEXITCODE -ne 0) { throw '安装包编译失败。' }
Get-FileHash -LiteralPath (Join-Path $outputRoot 'output/轻仓WMS安装包-V1.3-安装升级通用-Windows-x64.exe') -Algorithm SHA256
