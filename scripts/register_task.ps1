# Registers the fund watchdog as a Windows Task Scheduler task that starts at
# boot and restarts on failure — true 24/7 lights-out operation.
# Run as Administrator:  powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
$taskName = "AIHedgeFund"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$repo\scripts\run_forever.ps1`"" `
    -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -RunLevel Highest -Force

Start-ScheduledTask -TaskName $taskName
Write-Host "Task '$taskName' registered and started. Manage it in Task Scheduler." -ForegroundColor Green
Write-Host "Logs: $repo\logs\hedgefund.log   Reports: $repo\reports\" -ForegroundColor Cyan
