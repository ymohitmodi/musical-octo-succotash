# ============================================================================
# ONE-SHOT SETUP for Windows 11 — installs and configures EVERYTHING.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1 `
#       -Email you@example.com [-FredKey XXXX] [-PullModels] [-Full]
#
#   -Email      your real email (REQUIRED by SEC for EDGAR access)
#   -FredKey    optional free FRED API key (macro data)
#   -PullModels pull the default Ollama model chain after install
#   -Full       everything above + register the 24/7 Task Scheduler task
#               (needs an elevated/Administrator PowerShell)
#
# Idempotent: safe to re-run; it skips what is already done.
# ============================================================================
param(
    [string]$Email = "",
    [string]$FredKey = "",
    [switch]$PullModels,
    [switch]$Full
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$repo = Get-Location

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }

Step "AI Hedge Fund: one-shot setup"

# ---- 1. Python (auto-install via winget if missing) -------------------------
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Step "Python not found - installing via winget"
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { Write-Host "Python installed - open a NEW terminal and re-run this script." -ForegroundColor Yellow; exit 1 }
}
Write-Host "Python: $(python --version)"

# ---- 2. Ollama (auto-install via winget if missing) -------------------------
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Step "Ollama not found - installing via winget"
    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}
Write-Host "Ollama: $((Get-Command ollama -ErrorAction SilentlyContinue) -ne $null)"

# ---- 3. venv + dependencies --------------------------------------------------
Step "Python environment"
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
Write-Host "Dependencies installed."

# ---- 4. .env (fully written, no manual editing needed) -----------------------
Step ".env configuration"
if (-not $Email) {
    $Email = Read-Host "Your email (required by SEC for EDGAR; used in User-Agent only)"
}
if (-not $Email -or $Email -notmatch "@") {
    Write-Host "A real email is required (SEC fair-access rule). Re-run with -Email you@example.com" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
$envText = Get-Content ".env" -Raw
$envText = $envText -replace "EDGAR_USER_AGENT=.*", "EDGAR_USER_AGENT=DeepValueFund research bot $Email"
if ($FredKey) { $envText = $envText -replace "FRED_API_KEY=.*", "FRED_API_KEY=$FredKey" }
Set-Content ".env" $envText -NoNewline
Write-Host ".env written (EDGAR contact: $Email)."

# ---- 5. offline test suite ----------------------------------------------------
Step "Running offline test suite (40 tests)"
& .\.venv\Scripts\python.exe -m unittest discover tests
if ($LASTEXITCODE -ne 0) { Write-Host "Tests failed - aborting." -ForegroundColor Red; exit 1 }

# ---- 6. models -----------------------------------------------------------------
if ($PullModels -or $Full) {
    Step "Pulling Ollama models (cloud models need 'ollama signin' first)"
    $models = (Select-String -Path ".env" -Pattern "^OLLAMA_MODELS=(.*)").Matches[0].Groups[1].Value -split ","
    foreach ($m in $models) {
        $m = $m.Trim()
        if ($m) { Write-Host "  ollama pull $m"; ollama pull $m }
    }
}

# ---- 7. keep-awake + live validation ------------------------------------------
Step "Power plan: never sleep on AC"
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null

Step "Live integration validation (SEC EDGAR, Yahoo, Stooq, FRED, RSS)"
& .\.venv\Scripts\python.exe -m hedgefund.main validate
$valOk = ($LASTEXITCODE -eq 0)

Step "Doctor preflight (Ollama + runtime)"
& .\.venv\Scripts\python.exe -m hedgefund.main doctor
$docOk = ($LASTEXITCODE -eq 0)

# ---- 8. 24/7 registration --------------------------------------------------------
if ($Full) {
    if (-not $valOk -or -not $docOk) {
        Write-Host "`nSkipping 24/7 registration: fix the FAIL items above first, then run scripts\register_task.ps1" -ForegroundColor Yellow
    } else {
        Step "Registering 24/7 Task Scheduler task"
        & powershell -ExecutionPolicy Bypass -File "$repo\scripts\register_task.ps1"
    }
}

Step "Setup complete"
Write-Host "Next:  .\.venv\Scripts\python.exe -m hedgefund.main once        (first research cycle)"
Write-Host "       .\.venv\Scripts\python.exe -m hedgefund.main dashboard   (http://127.0.0.1:8787)"
if (-not $Full) {
    Write-Host "       powershell -File scripts\register_task.ps1             (go 24/7, as Admin)"
}
