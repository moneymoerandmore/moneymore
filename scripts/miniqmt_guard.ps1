param(
    [Parameter(Mandatory = $true)]
    [string]$Launcher,
    [int]$PollSeconds = 20,
    [int]$StartupGraceSeconds = 25
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $ProjectRoot "state\runtime"
$LogFile = Join-Path $RuntimeDir "miniqmt-guard.log"
$WorkingDirectory = Join-Path (Split-Path -Parent (Split-Path -Parent $Launcher)) "config\tradingtime"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

function Write-GuardLog([string]$Message) {
    $Line = "{0} {1}" -f [DateTimeOffset]::Now.ToString("o"), $Message
    Add-Content -LiteralPath $LogFile -Value $Line -Encoding UTF8
}

function Get-QmtMainProcess {
    Get-Process -Name "XtMiniQmt" -ErrorAction SilentlyContinue |
        Sort-Object StartTime -Descending |
        Select-Object -First 1
}

function Remove-OrphanQuoteProcesses {
    if (Get-QmtMainProcess) { return }
    $Quotes = @(Get-Process -Name "miniquote" -ErrorAction SilentlyContinue)
    foreach ($Quote in $Quotes) {
        Write-GuardLog "stopping orphan miniquote pid=$($Quote.Id)"
        Stop-Process -Id $Quote.Id -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path -LiteralPath $Launcher)) {
    throw "MiniQMT official launcher not found: $Launcher"
}
if (-not (Test-Path -LiteralPath $WorkingDirectory)) {
    throw "MiniQMT working directory not found: $WorkingDirectory"
}

Write-GuardLog "guard started launcher=$Launcher"
while ($true) {
    try {
        if (-not (Get-QmtMainProcess)) {
            # XtMiniQmt can leave miniquote behind after its main window exits.
            # The orphan holds shared-memory/config files and destabilizes the
            # next login, so remove it only after confirming the main process is gone.
            Remove-OrphanQuoteProcesses
            Write-GuardLog "main process missing; launching official client"
            Start-Process -FilePath $Launcher -WorkingDirectory $WorkingDirectory -WindowStyle Minimized
            Start-Sleep -Seconds $StartupGraceSeconds
            $Started = Get-QmtMainProcess
            if ($Started) {
                Write-GuardLog "main process restored pid=$($Started.Id)"
            } else {
                Write-GuardLog "official launcher returned but XtMiniQmt is still absent"
            }
        }
    } catch {
        Write-GuardLog "guard iteration failed: $($_.Exception.GetType().Name): $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $PollSeconds
}
