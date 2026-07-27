$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $ProjectRoot "state\runtime"

foreach ($service in @("api", "web")) {
    $pidFile = Join-Path $RuntimeDir "$service.pid"
    if (-not (Test-Path -LiteralPath $pidFile)) {
        continue
    }
    $processId = [int](Get-Content -LiteralPath $pidFile)
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $processId
        Write-Output "Stopped MoneyMore $service PID=$processId"
    }
}
