param(
    [string]$TaskName = "MoneyMore Local Service"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$NodeSource = "C:\Users\cloud\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$NodeTargetDir = Join-Path $ProjectRoot ".runtime\node"
$NodeTarget = Join-Path $NodeTargetDir "node.exe"
$ServiceScript = Join-Path $ProjectRoot "scripts\local_service.ps1"
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
Stop-LegacyMoneyMoreListener 8788
Stop-LegacyMoneyMoreListener 3000

$PowerShell = (Get-Command powershell.exe).Source
$Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ServiceScript`" -Action Start"
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650)
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Principal $Principal -Description "MoneyMore API, dashboard and daily scheduler" -Force | Out-Null

& $ServiceScript -Action Start
Write-Output "Installed Windows auto-start task: $TaskName"
Write-Output "MoneyMore will start automatically after Windows login, without Codex."
