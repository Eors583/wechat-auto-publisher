$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$python = if (Test-Path $venvPython) { $venvPython } else { 'python' }

Set-Location $projectRoot
$env:PYTHONPATH = $projectRoot
if (-not $env:DATABASE_URL) {
    $env:DATABASE_URL = 'postgresql://wechat_publisher:wechat_dev_password@127.0.0.1:15432/wechat_publisher'
}
if (-not $env:CREDENTIAL_ENCRYPTION_KEY) {
    # Development-only key matching compose.yaml.
    $env:CREDENTIAL_ENCRYPTION_KEY = 'local-docker-credential-key-change-before-production'
}

& $python -m app.ui.server
