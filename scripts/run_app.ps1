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
if (-not $env:CREDENTIAL_ENCRYPTION_KEY) {
    # Development-only key matching compose.yaml. Production must inject a
    # separately generated stable secret instead of using this value.
    $env:CREDENTIAL_ENCRYPTION_KEY = 'local-docker-credential-key-change-before-production'
}

# Use the same launcher as the packaged build. It starts the API and the
# Vue/Element Plus frontend as owned child processes, then opens the browser.
& $python -m app.launcher
