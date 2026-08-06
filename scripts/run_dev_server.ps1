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

$env:WECHAT_PUBLISHER_API_PORT = '18776'
$apiProcess = Start-Process -FilePath $python -ArgumentList @('-m', 'app.api.server') -PassThru -WindowStyle Hidden
try {
    Set-Location (Join-Path $projectRoot 'frontend')
    & pnpm dev
}
finally {
    if ($apiProcess -and -not $apiProcess.HasExited) {
        Stop-Process -Id $apiProcess.Id
    }
}
