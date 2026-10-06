param(
    [Parameter(Mandatory=$true)][string]$InnoCompiler,
    [string]$RunName = ('upgrade-v13-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
)
$ErrorActionPreference = 'Stop'
$taskAppDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
if ($RunName -notmatch '^upgrade-v13-[A-Za-z0-9_-]+$') { throw '升级构建名称无效。' }
$taskOutput = Join-Path $taskAppDir ('.runtime/' + $RunName)
if (Test-Path -LiteralPath $taskOutput) { throw '构建目录已存在，不会覆盖；请使用新名称。' }
$taskPython = Join-Path $taskAppDir 'backend/.venv/Scripts/python.exe'
foreach ($taskFile in @($InnoCompiler, $taskPython)) {
    if (-not (Test-Path -LiteralPath $taskFile -PathType Leaf)) { throw '缺少构建工具。' }
}
$taskGitRoot = Split-Path $taskAppDir -Parent
$taskSafeRoot = $taskGitRoot.Replace('\','/')
$taskBaseline = git -c "safe.directory=$taskSafeRoot" -c core.fsmonitor=false -C $taskGitRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw '无法确定业务源码基线。' }
git -c "safe.directory=$taskSafeRoot" -c core.fsmonitor=false -C $taskGitRoot diff --quiet HEAD -- warehouse-system/backend warehouse-system/frontend
if ($LASTEXITCODE -ne 0) { throw '业务源码有未提交修改，请先完成隔离验证并提交。' }
New-Item -ItemType Directory -Path $taskOutput | Out-Null
Push-Location (Join-Path $taskAppDir 'frontend')
try {
    & 'C:\Program Files\nodejs\npm.cmd' test
    if ($LASTEXITCODE -ne 0) { throw '前端测试失败。' }
    & 'C:\Program Files\nodejs\npm.cmd' run build -- --outDir (Join-Path $taskOutput 'frontend/dist')
    if ($LASTEXITCODE -ne 0) { throw '隔离前端构建失败。' }
} finally { Pop-Location }
& $taskPython (Join-Path $PSScriptRoot 'build_upgrade.py') --output (Join-Path $taskOutput 'payload') `
    --frontend (Join-Path $taskOutput 'frontend/dist') --baseline $taskBaseline
if ($LASTEXITCODE -ne 0) { throw '升级组件打包失败。' }
& $InnoCompiler (('/DPayloadZip=' + (Join-Path $taskOutput 'wms-upgrade.zip'))) `
    (('/DOutput=' + (Join-Path $taskOutput 'delivery'))) (Join-Path $PSScriptRoot 'upgrade.iss')
if ($LASTEXITCODE -ne 0) { throw '升级包编译失败。' }
$taskExe = Join-Path $taskOutput 'delivery/轻仓WMS升级包-V1.3-Windows-x64.exe'
$taskHash = Get-FileHash -LiteralPath $taskExe -Algorithm SHA256
($taskHash.Hash.ToLowerInvariant() + '  ' + [IO.Path]::GetFileName($taskExe)) |
    Set-Content -LiteralPath ($taskExe + '.sha256') -Encoding ASCII
$taskHash
