param(
    [string]$TaskName = "MoneyMore Local Service",
    [string]$WatchdogTaskName = "MoneyMore Service Watchdog"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$NodeSource = "C:\Users\cloud\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$NodeTargetDir = Join-Path $ProjectRoot ".runtime\node"
$NodeTarget = Join-Path $NodeTargetDir "node.exe"
$ServiceScript = Join-Path $ProjectRoot "scripts\local_service.ps1"
$HiddenLauncher = Join-Path $ProjectRoot "scripts\run_local_service_hidden.vbs"
$DashboardDir = Join-Path $ProjectRoot "apps\dashboard"

function Stop-LegacyMoneyMoreListener([int]$Port) {
    $ListenerPids = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($ListenerPid in $ListenerPids) {
        $CommandLine = (Get-CimInstance Win32_Process -Filter "ProcessId=$ListenerPid" -ErrorAction SilentlyContinue).CommandLine
        if ($CommandLine -and $CommandLine -like "*$ProjectRoot*" -and
            ($CommandLine -match "uvicorn\s+moneymore\.web:app" -or $CommandLine -match "vinext.*(dev|start)")) {
            Stop-Process -Id $ListenerPid -Force -ErrorAction Stop
        } else {
            throw "Port $Port is occupied by a process that is not recognized as MoneyMore: PID=$ListenerPid"
        }
    }
}

if (-not (Test-Path -LiteralPath $NodeTarget)) {
    if (-not (Test-Path -LiteralPath $NodeSource)) {
        throw "Node runtime is unavailable. Install Node.js 22 or place node.exe at $NodeTarget"
    }
    New-Item -ItemType Directory -Force -Path $NodeTargetDir | Out-Null
    Copy-Item -LiteralPath $NodeSource -Destination $NodeTarget -Force
}

if (-not (Test-Path -LiteralPath (Join-Path $DashboardDir "node_modules"))) {
    throw "Dashboard dependencies are missing: $DashboardDir\node_modules"
}

& (Join-Path $ProjectRoot "scripts\local_service.ps1") -Action Stop
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:8788/api/health" -UseBasicParsing -TimeoutSec 3 | Out-Null
} catch { Stop-LegacyMoneyMoreListener 8788 }
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:3000" -UseBasicParsing -TimeoutSec 3 | Out-Null
} catch { Stop-LegacyMoneyMoreListener 3000 }

$WScript = Join-Path $env:SystemRoot "System32\wscript.exe"
$Arguments = "`"$HiddenLauncher`" Run"
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action = New-ScheduledTaskAction -Execute $WScript -Argument $Arguments -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Principal $Principal -Description "MoneyMore API, dashboard and daily scheduler" -Force | Out-Null

# The long-running supervisor is itself a single process. Task Scheduler does
# not reliably restart it after an external hard kill, so a short independent
# task checks its atomic heartbeat every minute and recreates it when stale.
$WatchdogArguments = "`"$HiddenLauncher`" Ensure"
$WatchdogAction = New-ScheduledTaskAction -Execute $WScript -Argument $WatchdogArguments -WorkingDirectory $ProjectRoot
$WatchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$WatchdogSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $WatchdogTaskName -Action $WatchdogAction -Trigger $WatchdogTrigger `
    -Settings $WatchdogSettings -Principal $Principal `
    -Description "Recreate MoneyMore supervisor when its heartbeat is stale" -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-ScheduledTask -TaskName $WatchdogTaskName
Start-Sleep -Seconds 3
Write-Output "Installed Windows auto-start task: $TaskName"
Write-Output "Installed one-minute watchdog task: $WatchdogTaskName"
Write-Output "MoneyMore supervisor is directly hosted by Task Scheduler and starts after Windows login."
