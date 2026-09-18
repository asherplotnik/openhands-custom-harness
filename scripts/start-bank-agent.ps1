[CmdletBinding(PositionalBinding = $false)]
param(
    [ValidateSet('gemini', 'groq')]
    [string]$Provider,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AgentArgs
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$bankAgentExe = Join-Path $projectRoot '.venv\\Scripts\\bank-agent.exe'
if (!(Test-Path -LiteralPath $bankAgentExe -PathType Leaf)) {
    throw "Bank Agent is not installed at $bankAgentExe. Run scripts\\setup-artifactory.ps1 first."
}
# Keep OpenHands profiles and runtime state in this prototype rather than in
# the user's global ~/.openhands directory, which may be locked by policy.
$env:OH_PERSISTENCE_DIR = Join-Path $projectRoot '.openhands'
if (!$env:OPENHANDS_SUPPRESS_BANNER) {
    $env:OPENHANDS_SUPPRESS_BANNER = '1'
}
if (!$env:LOG_LEVEL) {
    $env:LOG_LEVEL = 'ERROR'
}
$logPath = Join-Path $projectRoot 'bank-agent-startup.log'
$transcriptStarted = $false

try {
    Start-Transcript -Path $logPath -Append -Force | Out-Null
    $transcriptStarted = $true
}
catch {
    Write-Warning "Could not start the local startup log at $logPath"
}

trap {
    Write-Error $_
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
    exit 1
}

function Import-EnvFile([string]$Path) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if (!$line -or $line.StartsWith('#')) {
            continue
        }
        if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            throw "Invalid .env entry in $Path"
        }
        $name = $Matches[1]
        $value = $Matches[2].Trim()
        if ($value.Length -ge 2 -and $value[0] -eq $value[$value.Length - 1] -and ($value[0] -eq '"' -or $value[0] -eq "'")) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($null -eq [Environment]::GetEnvironmentVariable($name, 'Process')) {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}

Import-EnvFile (Join-Path $projectRoot '.env')
if (!$Provider) {
    $Provider = if ($env:BANK_AGENT_PROVIDER) { $env:BANK_AGENT_PROVIDER } else { 'gemini' }
}
$Provider = $Provider.ToLowerInvariant()
$env:BANK_AGENT_PROVIDER = $Provider

if ($Provider -eq 'groq' -and !$env:BANK_AGENT_GROQ_API_KEY -and !$env:GROQ_API_KEY) {
    throw 'A Groq API key is required in bank-agent\.env.'
}

try {
    & $bankAgentExe @AgentArgs
    exit $LASTEXITCODE
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
}
