[CmdletBinding()]
param(
    [string]$Python = 'python',
    [Parameter(Mandatory = $true)]
    [string]$OpenHandsSourceRoot
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$requirements = Join-Path $projectRoot 'requirements-offline.txt'
$venvPath = if ($env:BANK_AGENT_VENV_PATH) {
    $env:BANK_AGENT_VENV_PATH
} else {
    Join-Path $projectRoot '.venv'
}

if (!(Test-Path -LiteralPath $requirements -PathType Leaf)) {
    throw "Pinned requirements file was not found: $requirements"
}

$sourceRoot = (Resolve-Path -LiteralPath $OpenHandsSourceRoot -ErrorAction Stop).Path
$sdkSource = Join-Path $sourceRoot 'openhands-sdk'
$toolsSource = Join-Path $sourceRoot 'openhands-tools'
foreach ($path in @($sdkSource, $toolsSource)) {
    if (!(Test-Path -LiteralPath (Join-Path $path 'pyproject.toml') -PathType Leaf)) {
        throw "OpenHands package source was not found: $path"
    }
}

$pythonCommand = Get-Command $Python -ErrorAction Stop
$version = & $pythonCommand.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$version -lt [version]'3.12') {
    throw "Python 3.12 or later is required; found Python $version"
}

if (!(Test-Path -LiteralPath (Join-Path $venvPath 'Scripts\\python.exe') -PathType Leaf)) {
    & $pythonCommand.Source -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$venvPython = Join-Path $venvPath 'Scripts\\python.exe'

# The bank-managed pip configuration supplies the approved Artifactory index.
& $venvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $venvPython -m pip install --requirement $requirements
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

# Install the reviewed SDK source from dcmg-open-hands, then Bank Agent itself.
& $venvPython -m pip install `
    --no-deps `
    --no-build-isolation `
    --editable $sdkSource `
    --editable $toolsSource `
    --editable $projectRoot
exit $LASTEXITCODE
