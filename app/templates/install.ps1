# Linux Fleet Manager — try-it installer for Windows (PowerShell).
#
#   $env:FLEET_TOKEN="<your token from the dashboard>"; irm __API__/install.ps1 | iex
#
# What it does: makes a temporary folder with its own Python environment, downloads the
# agent from __API__/agent.py, and runs it in this window in SAFE MODE (it can only report
# stats and run built-in read-only actions — never shell commands). Nothing is installed
# system-wide. Press Ctrl+C to stop; the folder is deleted then.

function Start-FleetDemoAgent {
    $Api = "__API__"
    $Token = $env:FLEET_TOKEN
    if (-not $Token) {
        Write-Host "Missing token. Copy the full command from the dashboard (it sets FLEET_TOKEN first)." -ForegroundColor Red
        return
    }
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

    # 1. Find Python 3.9+ (the "py" launcher first; "python" may be a Microsoft Store shortcut)
    $py = $null
    foreach ($candidate in @(@("py", "-3"), @("python"), @("python3"))) {
        $exe = $candidate[0]; $pre = @($candidate | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            & $exe @pre -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { $py = $candidate; break }
        } catch {}
    }
    if (-not $py) {
        Write-Host "Python 3.9+ is needed. Install it from https://www.python.org/downloads/" -ForegroundColor Red
        Write-Host "(tick 'Add python.exe to PATH'), or run:  winget install Python.Python.3.12" -ForegroundColor Red
        Write-Host "Then open a new PowerShell window and run the command again." -ForegroundColor Red
        return
    }
    $exe = $py[0]; $pre = @($py | Select-Object -Skip 1)

    # 2. Temporary environment
    $dir = Join-Path ([IO.Path]::GetTempPath()) ("fleet-agent-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $dir | Out-Null
    $onWindows = ($PSVersionTable.PSEdition -eq "Desktop") -or $IsWindows
    $vpy = if ($onWindows) { Join-Path $dir "venv\Scripts\python.exe" } else { Join-Path $dir "venv/bin/python" }
    try {
        Write-Host ""
        Write-Host "Linux Fleet Manager - adding this computer to the live demo" -ForegroundColor Cyan
        Write-Host "==> Setting up a temporary Python environment (about 20-40 seconds)..."
        & $exe @pre -m venv (Join-Path $dir "venv")
        if ($LASTEXITCODE -ne 0) { throw "couldn't create a Python environment" }
        & $vpy -m pip install --quiet --disable-pip-version-check "httpx>=0.24" "websockets>=13" "psutil>=5.9"
        if ($LASTEXITCODE -ne 0) { throw "couldn't install the agent's packages - check your internet connection" }

        # 3. Download and start the agent
        $agent = Join-Path $dir "agent.py"
        Invoke-WebRequest -UseBasicParsing -Uri "$Api/agent.py" -OutFile $agent
        Write-Host "==> Starting the agent. Your computer appears on the dashboard within a few seconds."
        Write-Host "==> Keep this window open. Press Ctrl+C to stop - the temporary folder is removed automatically."
        Write-Host ""
        $env:FLEET_AGENT_HOME = Join-Path $dir "state"
        & $vpy $agent --server $Api --token $Token --safe
    }
    catch {
        Write-Host "Error: $_" -ForegroundColor Red
    }
    finally {
        Remove-Item Env:FLEET_AGENT_HOME -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        Write-Host ""
        Write-Host "==> Stopped. Nothing was left on this computer. Thanks for trying Linux Fleet Manager!"
    }
}

Start-FleetDemoAgent
