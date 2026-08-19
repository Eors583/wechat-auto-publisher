param(
    [switch]$PublicRelease
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$buildDir = Join-Path $projectRoot 'build\local-bridge'
$distDir = Join-Path $projectRoot 'dist\local-bridge'
$installerDir = Join-Path $projectRoot 'dist\installers'
$readme = Join-Path $projectRoot 'packaging\README-Cockpit-Bridge.txt'
$signTool = $null
$signingThumbprint = [string]$env:WECHAT_PUBLISHER_SIGNING_THUMBPRINT

if ($PublicRelease) {
    $signToolCommand = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($signToolCommand) {
        $signTool = $signToolCommand.Source
    } else {
        $kitsRoot = 'C:\Program Files (x86)\Windows Kits\10\bin'
        if (Test-Path -LiteralPath $kitsRoot) {
            $signTool = Get-ChildItem -LiteralPath $kitsRoot -Filter signtool.exe -Recurse |
                Sort-Object FullName -Descending |
                Select-Object -ExpandProperty FullName -First 1
        }
    }
    if (-not $signTool -or -not $signingThumbprint) {
        throw 'Public bridge release requires signtool.exe and WECHAT_PUBLISHER_SIGNING_THUMBPRINT.'
    }
    if (-not (Test-Path -LiteralPath "Cert:\CurrentUser\My\$signingThumbprint")) {
        throw "The requested CurrentUser code-signing certificate was not found: $signingThumbprint"
    }
}

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
if ($PublicRelease) {
    & $signTool sign /sha1 $signingThumbprint /fd SHA256 /tr 'http://timestamp.digicert.com' /td SHA256 $exe
    if ($LASTEXITCODE -ne 0) {
        throw "Code signing failed: $exe"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $exe
    if ($signature.Status -ne 'Valid') {
        throw "Authenticode verification failed for ${exe}: $($signature.Status)"
    }
}
$selfTest = Start-Process -FilePath $exe -ArgumentList '--self-test' -Wait -PassThru
if ($selfTest.ExitCode -ne 0) {
    throw "Portable bridge self-test failed with exit code $($selfTest.ExitCode)"
}

$downloadExe = Join-Path $installerDir "$exeName.exe"
[System.IO.File]::Copy($exe, $downloadExe, $true)

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
