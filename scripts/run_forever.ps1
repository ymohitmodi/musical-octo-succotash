# OS-level watchdog: restarts the fund process if it ever dies, with backoff.
# The scheduler also writes data\heartbeat.json; if the heartbeat goes stale
# (hung process), the watchdog kills and restarts it.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
$python = ".\.venv\Scripts\python.exe"
$stallSeconds = 1800   # restart if heartbeat older than 30 min

while ($true) {
    Write-Host "$(Get-Date -Format s) starting hedgefund..."
    $proc = Start-Process -FilePath $python -ArgumentList "-m", "hedgefund.main", "run" `
        -NoNewWindow -PassThru

    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 60
        $hb = "data\heartbeat.json"
        if (Test-Path $hb) {
            $age = (Get-Date) - (Get-Item $hb).LastWriteTime
            if ($age.TotalSeconds -gt $stallSeconds) {
                Write-Host "$(Get-Date -Format s) heartbeat stale ($([int]$age.TotalSeconds)s) - restarting"
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                break
            }
        }
    }
    Write-Host "$(Get-Date -Format s) process exited (code $($proc.ExitCode)); restart in 30s"
    Start-Sleep -Seconds 30
}
