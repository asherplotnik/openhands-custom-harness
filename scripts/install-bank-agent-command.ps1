[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcherPath = Join-Path $projectRoot 'Open Bank Agent.cmd'
if (!(Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
    throw "Could not find the Bank Agent launcher at $launcherPath"
}
if (!$env:LOCALAPPDATA) {
    throw 'LOCALAPPDATA is not available, so the Bank Agent command cannot be installed.'
}

$commandDirectory = Join-Path $env:LOCALAPPDATA 'BankAgent\bin'
$commandPath = Join-Path $commandDirectory 'bank-agent.cmd'
if ((Test-Path -LiteralPath $commandPath -PathType Leaf) -and !$Force) {
    throw "$commandPath already exists. Re-run with -Force to replace it."
}

$commandContents = @"
@echo off
call "$launcherPath" %*
exit /b %ERRORLEVEL%
"@

if ($PSCmdlet.ShouldProcess($commandPath, 'install the Bank Agent command')) {
    New-Item -ItemType Directory -Path $commandDirectory -Force | Out-Null
    Set-Content -LiteralPath $commandPath -Value $commandContents -Encoding OEM
}

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$pathEntries = @($userPath -split ';' | ForEach-Object { $_.Trim().Trim('"') })
$pathAlreadyContainsCommand = $pathEntries | Where-Object {
    [string]::Equals($_, $commandDirectory, [System.StringComparison]::OrdinalIgnoreCase)
}
if (!$pathAlreadyContainsCommand) {
    $updatedUserPath = if ([string]::IsNullOrWhiteSpace($userPath)) {
        $commandDirectory
    } else {
        "$commandDirectory;$userPath"
    }
    if ($PSCmdlet.ShouldProcess('the current user PATH', 'add the Bank Agent command directory')) {
        [Environment]::SetEnvironmentVariable('Path', $updatedUserPath, 'User')
    }
}

if ($PSCmdlet.ShouldProcess('the current PowerShell session', 'add the Bank Agent command directory')) {
    $env:Path = "$commandDirectory;$env:Path"
}

if ($WhatIfPreference) {
    Write-Host 'No changes were made.'
} else {
    Write-Host "Installed: $commandPath"
    Write-Host 'Open a new IDE terminal, then run: bank-agent'
    Write-Host 'After moving this harness, rerun this installer with -Force.'
}
