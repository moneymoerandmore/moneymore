param(
    [ValidateSet("Run", "Start", "Stop", "Status")]
    [string]$Action = "Status",
    [int]$ApiPort = 8788,
    [int]$WebPort = 3000
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $ProjectRoot "state\runtime"
$LogDir = Join-Path $ProjectRoot "logs"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Node = Join-Path $ProjectRoot ".runtime\node\node.exe"
$Vinext = Join-Path $ProjectRoot "apps\dashboard\node_modules\vinext\dist\cli.js"
$SupervisorPidFile = Join-Path $RuntimeDir "local-service.pid"
$StopFile = Join-Path $RuntimeDir "local-service.stop"

New-Item -ItemType Directory -Force -Path $RuntimeDir, $LogDir | Out-Null

function Get-RecordedProcess([string]$PidFile) {
    if (-not (Test-Path -LiteralPath $PidFile)) { return $null }
    $RecordedPid = [int](Get-Content -LiteralPath $PidFile -Raw)
    return Get-Process -Id $RecordedPid -ErrorAction SilentlyContinue
}

function Test-Endpoint([string]$Uri) {
    try {
        $Response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 3
        return $Response.StatusCode -eq 200
    } catch { return $false }
}

function Stop-RecordedProcess([string]$PidFile) {
    $Process = Get-RecordedProcess $PidFile
    if ($Process) { Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Start-ChildProcess(
    [string]$Name,
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$WorkingDirectory
) {
    # Some launchers inject both Path and PATH. Windows PowerShell's
    # Start-Process builds a case-insensitive environment dictionary and
    # crashes on that duplicate, so normalize it before spawning children.
    $ProcessPath = [System.Environment]::GetEnvironmentVariable("Path", "Process")
    [System.Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [System.Environment]::SetEnvironmentVariable("Path", $ProcessPath, "Process")
    $OutLog = Join-Path $LogDir "$Name.out.log"
    $ErrLog = Join-Path $LogDir "$Name.err.log"
    $Process = Start-Process -FilePath $FilePath -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -PassThru
    Set-Content -LiteralPath (Join-Path $RuntimeDir "$Name.pid") -Value $Process.Id
    return $Process
}

function Assert-Runtime {
    foreach ($Required in @($Python, $Node, $Vinext)) {
        if (-not (Test-Path -LiteralPath $Required)) { throw "Missing runtime: $Required" }
    }
}

function Repair-VinextWindowsStaticPaths {
    & $Node (Join-Path $ProjectRoot "apps\dashboard\scripts\patch-vinext-windows.mjs")
    if ($LASTEXITCODE -ne 0) { throw "Could not prepare the dashboard production server." }
}

switch ($Action) {
    "Start" {
        Assert-Runtime
        Repair-VinextWindowsStaticPaths
        $Existing = Get-RecordedProcess $SupervisorPidFile
        if ($Existing) { Write-Output "MoneyMore local service already running (PID=$($Existing.Id))."; break }
        Remove-Item -LiteralPath $StopFile -Force -ErrorAction SilentlyContinue
        $PowerShell = (Get-Command powershell.exe).Source
        $Process = Start-Process -FilePath $PowerShell -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", $PSCommandPath, "-Action", "Run", "-ApiPort", $ApiPort, "-WebPort", $WebPort
        ) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
        Start-Sleep -Seconds 3
        Write-Output "MoneyMore local service started (PID=$($Process.Id))."
        Write-Output "Dashboard: http://127.0.0.1:$WebPort"
    }
    "Stop" {
        New-Item -ItemType File -Force -Path $StopFile | Out-Null
        Stop-RecordedProcess (Join-Path $RuntimeDir "api.pid")
        Stop-RecordedProcess (Join-Path $RuntimeDir "web.pid")
        Stop-RecordedProcess $SupervisorPidFile
        Write-Output "MoneyMore local service stopped."
    }
    "Status" {
        $Supervisor = Get-RecordedProcess $SupervisorPidFile
        [pscustomobject]@{
            Supervisor = if ($Supervisor) { "RUNNING (PID=$($Supervisor.Id))" } else { "STOPPED" }
            Api = if (Test-Endpoint "http://127.0.0.1:$ApiPort/api/health") { "HEALTHY" } else { "DOWN" }
            Dashboard = if (Test-Endpoint "http://127.0.0.1:$WebPort") { "HEALTHY" } else { "DOWN" }
            Url = "http://127.0.0.1:$WebPort"
        } | Format-List
    }
    "Run" {
        Assert-Runtime
        Repair-VinextWindowsStaticPaths
        Set-Content -LiteralPath $SupervisorPidFile -Value $PID
        Remove-Item -LiteralPath $StopFile -Force -ErrorAction SilentlyContinue
        $Api = $null
        $Web = $null
        try {
            while (-not (Test-Path -LiteralPath $StopFile)) {
                if (-not $Api -or $Api.HasExited) {
                    $Api = Start-ChildProcess "api" $Python @(
                        "-m", "uvicorn", "moneymore.web:app", "--host", "127.0.0.1",
                        "--port", "$ApiPort", "--workers", "1"
                    ) $ProjectRoot
                }
                if (-not $Web -or $Web.HasExited) {
                    $Web = Start-ChildProcess "web" $Node @(
                        $Vinext, "start", "--hostname", "127.0.0.1", "--port", "$WebPort"
                    ) (Join-Path $ProjectRoot "apps\dashboard")
                }
                Start-Sleep -Seconds 10
            }
        } finally {
            Stop-RecordedProcess (Join-Path $RuntimeDir "api.pid")
            Stop-RecordedProcess (Join-Path $RuntimeDir "web.pid")
            Remove-Item -LiteralPath $SupervisorPidFile, $StopFile -Force -ErrorAction SilentlyContinue
        }
    }
}
