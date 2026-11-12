param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("news", "trading")]
    [string]$Service
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$EnvFile = Join-Path $PSScriptRoot "signal-desk.env"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Python virtual environment not found: $Python. Run the setup steps in deploy/README.md first."
}
if (-not (Test-Path $EnvFile)) {
    throw "Missing $EnvFile. Copy signal-desk.env.example to signal-desk.env and fill in your secrets."
}

# Load KEY=VALUE entries into this process only; values are never printed.
Get-Content -LiteralPath $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            $name = $matches[1]
            $value = $matches[2].Trim()
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

$env:PYTHONUNBUFFERED = "1"
$env:SIGNAL_DESK_SHARED_DATA = "1"
Set-Location $ProjectRoot

if ($Service -eq "news") {
    & $Python -m engine.intelligence --daemon
} else {
    $env:SIGNAL_DESK_SUPERVISOR = "1"
    & $Python -m engine.supervisor --daemon
}

exit $LASTEXITCODE
