# One-time setup on Windows 11. Run from the repo root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "== AI Hedge Fund: Windows 11 setup ==" -ForegroundColor Cyan

# 1. Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "Python not found. Install Python 3.11+ from https://www.python.org/downloads/ (check 'Add to PATH') and re-run." -ForegroundColor Red
    exit 1
}
$ver = (python --version) -replace "Python ", ""
Write-Host "Python $ver found."

# 2. Virtual environment + dependencies
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
Write-Host "Dependencies installed." -ForegroundColor Green

# 3. .env
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env created from template. EDIT IT NOW:" -ForegroundColor Yellow
    Write-Host "  - EDGAR_USER_AGENT must contain YOUR email (SEC requirement)" -ForegroundColor Yellow
    Write-Host "  - OLLAMA_MODELS: your Ollama cloud model chain" -ForegroundColor Yellow
    Write-Host "  - FRED_API_KEY: free key from fred.stlouisfed.org (optional)" -ForegroundColor Yellow
}

# 4. Ollama
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Write-Host "Ollama not found. Install from https://ollama.com/download/windows" -ForegroundColor Yellow
} else {
    Write-Host "Ollama found. Sign in for cloud models:  ollama signin" -ForegroundColor Green
    Write-Host "Then pull your models, e.g.:  ollama pull deepseek-v3.1:671b-cloud"
}

# 5. Keep the mini PC awake 24/7 (AC power: never sleep)
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
Write-Host "Power plan set: never sleep on AC." -ForegroundColor Green

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Edit .env"
Write-Host "  2. Smoke test:      .\.venv\Scripts\python.exe -m hedgefund.main screen"
Write-Host "  3. Run 24/7:        powershell -File scripts\register_task.ps1   (auto-start + watchdog)"
