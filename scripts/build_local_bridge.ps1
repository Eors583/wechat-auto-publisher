param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$buildDir = Join-Path $projectRoot 'build\local-bridge'
$distDir = Join-Path $projectRoot 'dist\local-bridge'
$installerDir = Join-Path $projectRoot 'dist\installers'
$readme = Join-Path $projectRoot 'packaging\README-Cockpit-Bridge.txt'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}

$version = [string](& $python -c 'import app; print(app.__version__)')
if ($LASTEXITCODE -ne 0 -or -not $version.Trim()) {
    throw 'Unable to read application version.'
}
$version = $version.Trim()
$exeName = "BlueBloodLab-Cockpit-Bridge-$version"
$zipPath = Join-Path $installerDir "BlueBloodLab-Cockpit-Bridge-Portable-$version.zip"

foreach ($target in @($buildDir, $distDir)) {
    $absolute = [System.IO.Path]::GetFullPath($target)
    $prefix = $projectRoot.TrimEnd('\') + '\'
    if (-not $absolute.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the workspace: $absolute"
    }
    if (Test-Path -LiteralPath $absolute) {
        Remove-Item -LiteralPath $absolute -Recurse -Force
    }
}

$pythonBase = [string](& $python -c 'import sys; print(sys.base_prefix)')
$runtimeDllDir = Join-Path $pythonBase.Trim() 'Library\bin'
if (Test-Path -LiteralPath $runtimeDllDir) {
    $env:Path = "$runtimeDllDir;$env:Path"
}

New-Item -ItemType Directory -Force -Path $buildDir, $distDir, $installerDir | Out-Null
Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --clean --onefile --console `
        --name $exeName --workpath $buildDir --specpath $buildDir `
        --distpath $distDir app\local_model_cors_bridge.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$exe = Join-Path $distDir "$exeName.exe"
$selfTest = Start-Process -FilePath $exe -ArgumentList '--self-test' -Wait -PassThru
if ($selfTest.ExitCode -ne 0) {
    throw "Portable bridge self-test failed with exit code $($selfTest.ExitCode)"
}

$readmeDestination = Join-Path $distDir 'README-Cockpit-Bridge.txt'
[System.IO.File]::Copy($readme, $readmeDestination, $true)
if (-not (Test-Path -LiteralPath $readmeDestination)) {
    throw 'Portable bridge README was not copied.'
}
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $distDir '*') -DestinationPath $zipPath -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
Set-Content -LiteralPath "$zipPath.sha256" -Value "$hash  $([System.IO.Path]::GetFileName($zipPath))" -Encoding ascii

Write-Output "Portable bridge package: $zipPath"
Write-Output "SHA256: $hash"
