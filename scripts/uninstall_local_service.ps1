param([string]$TaskName = "MoneyMore Local Service")

$ProjectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $ProjectRoot "scripts\local_service.ps1") -Action Stop
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Output "Removed Windows auto-start task: $TaskName"
