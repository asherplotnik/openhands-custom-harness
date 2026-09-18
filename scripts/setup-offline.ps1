[CmdletBinding()]
param(
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$wheelhouse = Join-Path $projectRoot 'wheelhouse'
$requirements = Join-Path $projectRoot 'requirements-offline.txt'
$venvPath = if ($env:BANK_AGENT_VENV_PATH) {
    $env:BANK_AGENT_VENV_PATH
} else {
    Join-Path $projectRoot '.venv'
}

if (!(Test-Path -LiteralPath $wheelhouse -PathType Container)) {
    throw "Offline wheelhouse was not found: $wheelhouse"
}

if (!(Test-Path -LiteralPath $requirements -PathType Leaf)) {
    throw "Offline requirements file was not found: $requirements"
}

$pythonCommand = Get-Command $Python -ErrorAction Stop

$version = & $pythonCommand.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($version -ne '3.13') {
    throw "This wheelhouse was built for CPython 3.13 on Windows x64; found Python $version. Supply a matching wheelhouse before installing."
}

if (!(Test-Path -LiteralPath (Join-Path $venvPath 'Scripts\\python.exe') -PathType Leaf)) {
    & $pythonCommand.Source -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$venvPython = Join-Path $venvPath 'Scripts\\python.exe'

# Build backends for the editable local packages. All artifacts come from the
# supplied wheelhouse; pip never contacts Artifactory or the public internet.
& $venvPython -m pip install `
    --no-index `
    --find-links $wheelhouse `
    --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $venvPython -m pip install `
    --no-index `
    --find-links $wheelhouse `
    --requirement $requirements
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $venvPython -m pip install `
    --no-index `
    --find-links $wheelhouse `
    --no-deps `
    --no-build-isolation `
    --editable (Join-Path $projectRoot 'vendor\\openhands-sdk') `
    --editable (Join-Path $projectRoot 'vendor\\openhands-tools') `
    --editable $projectRoot
exit $LASTEXITCODE
