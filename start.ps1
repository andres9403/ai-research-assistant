# Set up and start the whole app at http://localhost:8000 from one terminal.
# The first run creates backend\.venv, installs the dependencies and builds the
# frontend; later runs reuse them and reinstall only what changed.
# Port 8000 is used if it is free, otherwise the next free one up to 8020; the
# script prints the link to open.
# Usage: .\start.ps1            (.\start.ps1 -Port 8001 to use that port only)
# If PowerShell blocks the script, run it with:
#   powershell -ExecutionPolicy Bypass -File .\start.ps1
param([int]$Port = 8000)

# No global "Stop": Windows PowerShell 5.1 would then treat npm and pip warnings
# on stderr as fatal. Native commands are checked by exit code instead.
Set-Location $PSScriptRoot -ErrorAction Stop

function Fail($message) {
    Write-Host "start.ps1: $message" -ForegroundColor Red
    exit 1
}

# Run a native command and stop if it fails; PowerShell doesn't on its own.
function Invoke-Checked {
    $command, $arguments = $args
    & $command @arguments
    if ($LASTEXITCODE -ne 0) { Fail "'$($args -join ' ')' failed." }
}

function Test-Python($command) {
    try {
        & $command[0] @($command[1..9] + @("-c", "import sys; sys.exit(sys.version_info < (3, 11))")) 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

# Python 3.11+: the py launcher picks a version explicitly; fall back to python.
$python = $null
foreach ($candidate in @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("python"), @("python3"))) {
    if ((Get-Command $candidate[0] -ErrorAction SilentlyContinue) -and (Test-Python $candidate)) {
        $python = $candidate
        break
    }
}
if (-not $python) { Fail "Python 3.11 or newer is required. Install it from https://www.python.org/downloads/ and run this again." }

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Fail "Node.js 18 or newer is required. Install it from https://nodejs.org/ and run this again."
}
$nodeMajor = [int]((node --version).TrimStart("v").Split(".")[0])
if ($nodeMajor -lt 18) { Fail "Node.js 18 or newer is required; found $(node --version)." }

$venvPython = "backend\.venv\Scripts\python.exe"
if ((Test-Path $venvPython) -and -not (Test-Python @($venvPython))) {
    Fail "backend\.venv was made with an older Python. Delete backend\.venv and run this again."
}
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating backend\.venv..."
    Invoke-Checked @python -m venv backend\.venv
}

# Reinstall only when requirements.txt changed since the last successful install.
$stamp = "backend\.venv\requirements.installed"
$requirements = Get-Content backend\requirements.txt -Raw
if (-not (Test-Path $stamp) -or (Get-Content $stamp -Raw) -ne $requirements) {
    Write-Host "Installing backend dependencies..."
    Invoke-Checked $venvPython -m pip install --quiet --upgrade pip
    Invoke-Checked $venvPython -m pip install --quiet -r backend\requirements.txt
    Copy-Item backend\requirements.txt $stamp
}

Push-Location frontend
try {
    if (-not (Test-Path node_modules) -or
        (Get-Item package-lock.json).LastWriteTime -gt (Get-Item node_modules).LastWriteTime) {
        Write-Host "Installing frontend dependencies..."
        Invoke-Checked npm ci --no-audit --no-fund
        (Get-Item node_modules).LastWriteTime = Get-Date
    }
    Write-Host "Building the frontend..."
    Invoke-Checked npm run build --silent
} finally {
    Pop-Location
}

# True if nothing holds the port and Windows hasn't reserved it (port 8000 is
# sometimes reserved for HTTP.sys).
function Test-PortFree([int]$port) {
    try {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $port)
        $listener.Start()
        $listener.Stop()
        return $true
    } catch {
        return $false
    }
}

if ($PSBoundParameters.ContainsKey("Port")) {
    if (-not (Test-PortFree $Port)) {
        Fail "Port $Port is in use. Run .\start.ps1 without -Port to pick a free port, or choose another one."
    }
} else {
    $Port = 8000..8020 | Where-Object { Test-PortFree $_ } | Select-Object -First 1
    if (-not $Port) { Fail "Ports 8000 to 8020 are all in use. Run .\start.ps1 -Port <number> with a free port." }
    if ($Port -ne 8000) { Write-Host "Port 8000 is in use, so the app will use port $Port." }
}

Write-Host ""
Write-Host "Starting the app. Open http://localhost:$Port when you see 'Application startup complete'."
Write-Host "Press Ctrl+C to stop it."
Write-Host ""
Set-Location backend
& .venv\Scripts\python.exe -m uvicorn app.main:app --port $Port
exit $LASTEXITCODE
