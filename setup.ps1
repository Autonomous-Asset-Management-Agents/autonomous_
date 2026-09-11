# setup.ps1 -- AAAgents OSS First-Time Setup (Windows PowerShell)
#
# This is a thin wrapper that invokes the Python-based setup script.
# The actual generation of cryptographically secure secrets and file I/O
# happens in setup.py to ensure cross-platform consistency and security.
#
# Any extra arguments (e.g. --non-interactive) are forwarded to setup.py.

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path "setup.py")) {
    Write-Host "[X] setup.py not found." -ForegroundColor Red
    Write-Host "    Make sure you are in the autonomous_ root directory."
    exit 1
}

# Resolve a real Python 3.8+ interpreter
$PythonCmd = $null
foreach ($cmd in @("python3", "python")) {
    try {
        $ver = & $cmd -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" 2>&1
        if ($LASTEXITCODE -eq 0) {
            $PythonCmd = $cmd
            break
        }
    } catch { }
}

if (-not $PythonCmd) {
    Write-Host "[X] Python 3.8+ not found." -ForegroundColor Red
    Write-Host "    Microsoft Store stubs (python3.exe redirector) do not count."
    Write-Host "    Install Python 3 from https://www.python.org/downloads/ and retry."
    exit 1
}

# Delegate to the robust Python setup script.
# Forward all CLI args (e.g. --non-interactive for CI runs).
if ($ExtraArgs) {
    & $PythonCmd setup.py @ExtraArgs
} else {
    & $PythonCmd setup.py
}
exit $LASTEXITCODE
