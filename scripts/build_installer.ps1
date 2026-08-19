param(
    [switch]$PublicRelease
)

$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$packagingDir = Join-Path $projectRoot 'packaging'
$spec = (Get-ChildItem -LiteralPath $packagingDir -Filter '*.spec' | Select-Object -First 1).FullName
$iss = (Get-ChildItem -LiteralPath $packagingDir -Filter '*.iss' | Select-Object -First 1).FullName
$buildTarget = Join-Path $projectRoot 'build'
$distRoot = Join-Path $projectRoot 'dist'
$installerDir = Join-Path $projectRoot 'dist\installers'
$productionRemoteUrl = 'https://api.bluebloodlab.cn/publisher/'
$remoteUrl = if ($env:WECHAT_PUBLISHER_REMOTE_URL) {
    $env:WECHAT_PUBLISHER_REMOTE_URL
} else {
    $productionRemoteUrl
}
$isccCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
)
$iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$signTool = $null
$signingThumbprint = [string]$env:WECHAT_PUBLISHER_SIGNING_THUMBPRINT

if ($PublicRelease) {
    if ($remoteUrl -ne $productionRemoteUrl) {
        throw "Public release remote URL must be exactly $productionRemoteUrl"
    }
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
        throw 'Public release requires signtool.exe and WECHAT_PUBLISHER_SIGNING_THUMBPRINT. Build without -PublicRelease for a controlled test package.'
    }
    if (-not (Test-Path -LiteralPath "Cert:\CurrentUser\My\$signingThumbprint")) {
        throw "The requested CurrentUser code-signing certificate was not found: $signingThumbprint"
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}
$pythonBase = [string](& $python -c 'import sys; print(sys.base_prefix)')
if ($LASTEXITCODE -ne 0 -or -not $pythonBase.Trim()) {
    throw 'Unable to locate the base Python runtime.'
}
$runtimeDllDir = Join-Path $pythonBase.Trim() 'Library\bin'
if (Test-Path -LiteralPath $runtimeDllDir) {
    $env:Path = "$runtimeDllDir;$env:Path"
}
if (-not $iscc) {
    throw 'Inno Setup 6 is not installed. Install JRSoftware.InnoSetup first.'
}

function Assert-WorkspaceTarget {
    param([Parameter(Mandatory = $true)][string]$Path)
    $absolute = [System.IO.Path]::GetFullPath($Path)
    $prefix = $projectRoot.TrimEnd('\') + '\'
    if (-not $absolute.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the workspace: $absolute"
    }
    return $absolute
}

function Invoke-CodeSign {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not $PublicRelease) {
        return
    }
    & $signTool sign /sha1 $signingThumbprint /fd SHA256 /tr 'http://timestamp.digicert.com' /td SHA256 $Path
    if ($LASTEXITCODE -ne 0) {
        throw "Code signing failed: $Path"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid') {
        throw "Authenticode verification failed for ${Path}: $($signature.Status)"
    }
}

$verifiedBuild = Assert-WorkspaceTarget -Path $buildTarget
if (Test-Path -LiteralPath $verifiedBuild) {
    Remove-Item -LiteralPath $verifiedBuild -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $installerDir | Out-Null
Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --clean $spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    $distTarget = Get-ChildItem -LiteralPath $distRoot -Directory |
        Where-Object { $_.FullName -ne $installerDir } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $distTarget) {
        throw 'PyInstaller output directory was not found.'
    }
    $distTarget = $distTarget.FullName
    Copy-Item -LiteralPath (Join-Path $projectRoot 'config.example.yaml') -Destination (Join-Path $distTarget 'config.example.yaml')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'config.example.yaml') -Destination (Join-Path $distTarget 'config.yaml')
    Get-ChildItem -LiteralPath $packagingDir -Filter '*.txt' |
        Copy-Item -Destination $distTarget
    $portableData = Join-Path $distTarget 'data'
    New-Item -ItemType Directory -Force -Path $portableData | Out-Null
    foreach ($name in @('hot_topics.json', 'keywords.txt', 'peer_topics.json')) {
        Copy-Item -LiteralPath (Join-Path $projectRoot "data\$name") -Destination $portableData
    }

    $appExe = (Get-ChildItem -LiteralPath $distTarget -Filter '*.exe' | Select-Object -First 1).FullName
    $selfTest = Start-Process -FilePath $appExe -ArgumentList @('--self-test', '--remote-url', $remoteUrl) -WorkingDirectory $distTarget -Wait -PassThru
    if ($selfTest.ExitCode -ne 0) {
        $reportPath = Join-Path $portableData 'logs\package-self-test.json'
        throw "Frozen self-test failed. See $reportPath"
    }

    Invoke-CodeSign -Path $appExe

    $isccArgs = @("/DMyRemoteUrl=$remoteUrl")
    if ($PublicRelease) {
        $outputName = '公众号改写助手-生产环境安装包-1.4.1-20260819'
        $innoSignCommand = '"' + $signTool + '" sign /sha1 ' + $signingThumbprint + ' /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $f'
        $isccArgs += "/DMyOutputBaseFilename=$outputName"
        $isccArgs += "/DMySignTool=bluesign"
        $isccArgs += "/Sbluesign=$innoSignCommand"
    }
    $isccArgs += $iss
    & $iscc @isccArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE"
    }

    $installer = Get-ChildItem -LiteralPath $installerDir -Filter '*.exe' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $installer) {
        throw 'Installer output was not found.'
    }
    if ($PublicRelease) {
        $signature = Get-AuthenticodeSignature -LiteralPath $installer.FullName
        if ($signature.Status -ne 'Valid') {
            throw "Installer Authenticode verification failed: $($signature.Status)"
        }
    }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer.FullName).Hash
    $hashFile = "$($installer.FullName).sha256.txt"
    Set-Content -LiteralPath $hashFile -Encoding UTF8 -Value "$hash  $($installer.Name)"
    Write-Output "Installer: $($installer.FullName)"
    Write-Output "SHA256: $hash"
}
finally {
    Pop-Location
}
