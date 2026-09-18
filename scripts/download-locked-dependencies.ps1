[CmdletBinding()]
param(
    # The destination is deliberately separate from local-wheels.  This script
    # never passes local-wheels to pip as a find-links source.
    [string]$Destination = (Join-Path $PSScriptRoot '..\downloaded-wheelhouse'),
    [string]$Python = (Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'),

    # The bank's PyPI-compatible Artifactory "simple" repository URL. When
    # omitted, pip uses its existing configured index.
    [string]$IndexUrl
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$lockPath = Join-Path $projectRoot 'uv.lock'
$projectPath = Join-Path $projectRoot 'pyproject.toml'

if (-not (Test-Path -LiteralPath $lockPath)) {
    throw "uv.lock was not found at $lockPath"
}
if (-not (Test-Path -LiteralPath $projectPath)) {
    throw "pyproject.toml was not found at $projectPath"
}
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = 'python'
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$Destination = (Resolve-Path $Destination).Path

$requirementsPath = Join-Path $Destination 'locked-requirements.txt'
$missingPath = Join-Path $Destination 'packages-pip-could-not-download.txt'
$logPath = Join-Path $Destination 'pip-download.log'

# uv.lock lists the exact resolved dependency graph.  We take registry-backed
# packages from it, while deliberately skipping local/editable packages such as
# bank-agent, openhands-sdk, and openhands-tools: those are supplied as source
# folders alongside this project, not downloaded from PyPI.
$packages = [System.Collections.Generic.List[object]]::new()
$current = $null
function Add-LockedPackage {
    param([object]$Package)
    if ($null -ne $Package -and $Package.Name -and $Package.Version -and $Package.Registry) {
        $packages.Add([pscustomobject]@{
            Name = $Package.Name
            Version = $Package.Version
        })
    }
}

foreach ($line in Get-Content -LiteralPath $lockPath) {
    if ($line -match '^\[\[package\]\]') {
        Add-LockedPackage $current
        $current = [pscustomobject]@{ Name = $null; Version = $null; Registry = $false }
        continue
    }
    if ($null -eq $current) { continue }
    if ($line -match '^name\s*=\s*"([^"]+)"') {
        $current.Name = $Matches[1]
    }
    elseif ($line -match '^version\s*=\s*"([^"]+)"') {
        $current.Version = $Matches[1]
    }
    elseif ($line -match '^source\s*=\s*\{\s*registry\s*=') {
        $current.Registry = $true
    }
}
Add-LockedPackage $current

$requirements = $packages |
    ForEach-Object { "$($_.Name)==$($_.Version)" } |
    Sort-Object -Unique

if ($requirements.Count -eq 0) {
    throw 'No registry-backed packages were found in uv.lock.'
}

$requirements | Set-Content -LiteralPath $requirementsPath -Encoding ascii
Set-Content -LiteralPath $missingPath -Value @(
    '# Exact locked packages pip could not download for this Windows/Python environment.',
    '# Each line can be supplied manually to the security/artifactory team.'
) -Encoding ascii
Set-Content -LiteralPath $logPath -Value "Started: $(Get-Date -Format o)" -Encoding utf8

Write-Host "Reading exact versions from: $lockPath"
Write-Host "Trying $($requirements.Count) registry packages with pip."
Write-Host "Destination: $Destination"
Write-Host 'local-wheels is not used.'
if ($IndexUrl) {
    Write-Host 'Using the supplied Artifactory package index.'
}

$failed = 0
foreach ($requirement in $requirements) {
    Write-Host "Downloading $requirement"
    # --no-deps matters: uv.lock, rather than pip's live resolver, defines the
    # complete package set. --no-cache-dir prevents a prior pip cache from
    # hiding an unavailable registry package.
    # Do not let PowerShell turn pip's stderr/non-zero exit code into a
    # terminating NativeCommandError.  A missing registry route is exactly
    # what this collector is meant to report, then continue past.
    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
        $savedNativeCommandPreference = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }
    $pipArguments = @(
        '-m', 'pip', 'download',
        '--disable-pip-version-check', '--no-deps', '--no-cache-dir',
        '--dest', $Destination
    )
    if ($IndexUrl) {
        $pipArguments += '--index-url'
        $pipArguments += $IndexUrl
    }
    $pipArguments += $requirement
    & $Python @pipArguments *>> $logPath
    $pipExitCode = $LASTEXITCODE
    if (Get-Variable -Name savedNativeCommandPreference -ErrorAction SilentlyContinue) {
        $PSNativeCommandUseErrorActionPreference = $savedNativeCommandPreference
        Remove-Variable -Name savedNativeCommandPreference
    }
    $ErrorActionPreference = $savedErrorActionPreference

    if ($pipExitCode -ne 0) {
        Add-Content -LiteralPath $missingPath -Value $requirement -Encoding ascii
        $failed++
        Write-Warning "Pip could not download $requirement; added it to $missingPath"
    }
}

Write-Host ''
Write-Host "Done. Exact lock list: $requirementsPath"
Write-Host "Pip failures ($failed): $missingPath"
Write-Host "Detailed pip output: $logPath"
exit $(if ($failed -gt 0) { 1 } else { 0 })
