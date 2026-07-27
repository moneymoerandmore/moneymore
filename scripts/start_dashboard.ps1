param(
    [string]$ApiHost = "127.0.0.1",
    [int]$ApiPort = 8788,
    [int]$WebPort = 3000
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Node = "C:\Users\cloud\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$Vinext = Join-Path $ProjectRoot "apps\dashboard\node_modules\vinext\dist\cli.js"
$RuntimeDir = Join-Path $ProjectRoot "state\runtime"
$LogDir = Join-Path $ProjectRoot "logs"

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$apiHealthy = $false
$webHealthy = $false
try {
    $health = Invoke-RestMethod -Uri "http://$ApiHost`:$ApiPort/api/health" -TimeoutSec 2
    $apiHealthy = $health.status -eq "ok"
}
catch {}
try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1`:$WebPort" -UseBasicParsing -TimeoutSec 2
    $webHealthy = $response.StatusCode -eq 200
}
catch {}

if (-not $apiHealthy) {
    $api = Start-Process -FilePath $Python `
        -ArgumentList @("-m", "uvicorn", "moneymore.web:app", "--host", $ApiHost, "--port", "$ApiPort") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "api.out.log") `
        -RedirectStandardError (Join-Path $LogDir "api.err.log") `
        -PassThru
    Set-Content -LiteralPath (Join-Path $RuntimeDir "api.pid") -Value $api.Id
    Write-Output "MoneyMore API PID=$($api.Id) http://$ApiHost`:$ApiPort"
}
else {
    Write-Output "MoneyMore API already running at http://$ApiHost`:$ApiPort"
}

if (-not $webHealthy) {
    $web = Start-Process -FilePath $Node `
        -ArgumentList @($Vinext, "dev", "--hostname", "127.0.0.1", "--port", "$WebPort") `
        -WorkingDirectory (Join-Path $ProjectRoot "apps\dashboard") `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "web.out.log") `
        -RedirectStandardError (Join-Path $LogDir "web.err.log") `
        -PassThru
    Set-Content -LiteralPath (Join-Path $RuntimeDir "web.pid") -Value $web.Id
    Write-Output "MoneyMore Web PID=$($web.Id) http://127.0.0.1`:$WebPort"
}
else {
    Write-Output "MoneyMore Web already running at http://127.0.0.1`:$WebPort"
}
