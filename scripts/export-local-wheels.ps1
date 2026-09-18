[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'local-wheel-transfer')
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$wheelhouse = Join-Path $projectRoot 'wheelhouse'

$wheels = @(
    @{ FileName = 'agent_client_protocol-0.10.1-py3-none-any.whl'; Base64Name = 'agent-client-protocol.b64' },
    @{ FileName = 'aiofile-3.12.3-py3-none-any.whl'; Base64Name = 'aiofile.b64' },
    @{ FileName = 'aiofiles-25.1.0-py3-none-any.whl'; Base64Name = 'aiofiles.b64' },
    @{ FileName = 'aiohappyeyeballs-2.7.1-py3-none-any.whl'; Base64Name = 'aiohappyeyeballs.b64' },
    @{ FileName = 'caio-0.12.2-py3-none-any.whl'; Base64Name = 'caio.b64' }
)

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$manifest = @()

foreach ($wheel in $wheels) {
    $source = Join-Path $wheelhouse $wheel.FileName
    if (!(Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required wheel was not found: $source"
    }

    $destination = Join-Path $OutputDirectory $wheel.Base64Name
    $bytes = [System.IO.File]::ReadAllBytes($source)
    [System.IO.File]::WriteAllText(
        $destination,
        [System.Convert]::ToBase64String($bytes),
        [System.Text.UTF8Encoding]::new($false)
    )

    $manifest += [pscustomobject]@{
        wheel = $wheel.FileName
        base64File = $wheel.Base64Name
        sha256 = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        byteLength = $bytes.Length
    }
}

$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $OutputDirectory 'manifest.json') -Encoding utf8NoBOM
Write-Host "Created Base64 wheel files in $OutputDirectory"
