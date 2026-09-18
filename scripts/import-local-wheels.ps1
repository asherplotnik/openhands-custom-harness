[CmdletBinding()]
param(
    [string]$InputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'local-wheel-transfer'),
    [string]$OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'local-wheels')
)

$ErrorActionPreference = 'Stop'
$manifestPath = Join-Path $InputDirectory 'manifest.json'
if (!(Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Wheel manifest was not found: $manifestPath"
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json

foreach ($wheel in $manifest) {
    $encodedPath = Join-Path $InputDirectory $wheel.base64File
    $destination = Join-Path $OutputDirectory $wheel.wheel
    $bytes = [System.Convert]::FromBase64String((Get-Content -LiteralPath $encodedPath -Raw).Trim())
    [System.IO.File]::WriteAllBytes($destination, $bytes)

    $actualHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
    if ($actualHash -ne $wheel.sha256) {
        Remove-Item -LiteralPath $destination -Force
        throw "Hash verification failed for $($wheel.wheel)"
    }
}

Write-Host "Restored verified wheels to $OutputDirectory"
