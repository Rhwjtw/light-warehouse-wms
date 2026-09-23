param([Parameter(Mandatory=$true)][string]$IsolatedBackend)
$ErrorActionPreference = 'Stop'

$runtime = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '.runtime')).Path
$backend = (Resolve-Path -LiteralPath $IsolatedBackend).Path
$prefix = $runtime.TrimEnd('\') + '\'
if (-not $backend.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw '测试后端必须位于本项目 .runtime 隔离目录内。'
}
if (-not (Test-Path -LiteralPath (Join-Path $backend 'tests/conftest.py')) -or
    -not (Test-Path -LiteralPath (Join-Path $backend 'scripts/check_test_target.py'))) {
    throw '隔离副本缺少测试目标保护文件。'
}
$runRoot = Split-Path (Split-Path $backend -Parent) -Parent
$identityPath = Join-Path $runRoot 'identity.json'
$identity = Get-Content -LiteralPath $identityPath -Raw | ConvertFrom-Json
$data = (Resolve-Path -LiteralPath $identity.datadir).Path
if (-not $data.StartsWith(($runRoot.TrimEnd('\') + '\'), [System.StringComparison]::OrdinalIgnoreCase) -or
    $identity.database -ne 'warehouse_system' -or [int]$identity.port -eq 3306) {
    throw '隔离实例身份文件无效。'
}
$python = Join-Path $PSScriptRoot 'backend/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '缺少项目 Python 虚拟环境。' }

$env:WMS_TEST_SERVER_UUID = [string]$identity.server_uuid
$env:WMS_TEST_DATADIR = $data
$env:WMS_TEST_PORT = [string]$identity.port
try {
    Push-Location $backend
    try {
        & $python -m scripts.check_test_target
        if ($LASTEXITCODE -ne 0) { throw '测试目标检查失败。' }
        & $python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw '后端测试失败。' }
        & $python -m alembic current
        if ($LASTEXITCODE -ne 0) { throw 'Alembic current 失败。' }
        & $python -m alembic heads
        if ($LASTEXITCODE -ne 0) { throw 'Alembic heads 失败。' }
        & $python -m alembic check
        if ($LASTEXITCODE -ne 0) { throw 'Alembic check 失败。' }
    } finally { Pop-Location }
} finally {
    Remove-Item Env:WMS_TEST_SERVER_UUID -ErrorAction SilentlyContinue
    Remove-Item Env:WMS_TEST_DATADIR -ErrorAction SilentlyContinue
    Remove-Item Env:WMS_TEST_PORT -ErrorAction SilentlyContinue
}
