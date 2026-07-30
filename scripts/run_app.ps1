$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$python = if (Test-Path $venvPython) { $venvPython } else { 'python' }
$logDir = Join-Path $projectRoot 'data\logs'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Set-Location $projectRoot
$env:PYTHONPATH = $projectRoot
if (-not $env:DATABASE_URL) {
    # Local desktop development shares the same PostgreSQL database as the
    # standalone merchant backend. Production installers can override this
    # value with their managed service connection.
    $env:DATABASE_URL = 'postgresql://wechat_publisher:wechat_dev_password@127.0.0.1:15432/wechat_publisher'
}

# Use the same launcher as the frozen desktop build.  The launcher retains the
# exact Process handle it created, so the Feishu settings page can safely
# restart that API/long-connection child without looking up or killing a
# process merely because it happens to occupy port 18766.
& $python -m app.launcher
