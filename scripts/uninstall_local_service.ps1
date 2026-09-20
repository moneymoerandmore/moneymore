param(
    [string]$TaskName = "MoneyMore Local Service",
    [string]$WatchdogTaskName = "MoneyMore Service Watchdog"
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $ProjectRoot "scripts\local_service.ps1") -Action Stop
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $WatchdogTaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Output "Removed Windows auto-start task: $TaskName"
Write-Output "Removed Windows watchdog task: $WatchdogTaskName"
