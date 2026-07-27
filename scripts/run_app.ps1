$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$python = if (Test-Path $venvPython) { $venvPython } else { 'python' }
$logDir = Join-Path $projectRoot 'data\logs'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Set-Location $projectRoot
$env:PYTHONPATH = $projectRoot

# The web interface and the Feishu long-connection service use separate ports.
# Start the API/bot service for this desktop session. When this script started
# it, stop it with the desktop window so reopening the app reloads new settings.
$apiListener = Get-NetTCPConnection -State Listen -LocalPort 18766 -ErrorAction SilentlyContinue
$apiProcess = $null
if (-not $apiListener) {
    $apiProcess = Start-Process `
        -FilePath $python `
        -ArgumentList @('-m', 'app.api') `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir 'feishu-api.out.log') `
        -RedirectStandardError (Join-Path $logDir 'feishu-api.err.log') `
        -PassThru
}

try {
    & $python -m app.ui
}
finally {
    if ($apiProcess -and -not $apiProcess.HasExited) {
        Stop-Process -Id $apiProcess.Id -Force -ErrorAction SilentlyContinue
        $apiProcess.WaitForExit(5000)
    }
}
