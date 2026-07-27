param(
    [string]$TradeDate = (Get-Date).ToString("yyyyMMdd"),
    [string]$Symbol = "600036.SH",
    [string]$Account = "default"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python"
}

Push-Location $ProjectRoot
try {
    & $Python -m moneymore.cli daily-run `
        --symbol $Symbol `
        --trade-date $TradeDate `
        --account $Account `
        --data-dir data `
        --database state\paper_orders.sqlite3 `
        --signal-dir state\signals `
        --report-dir state\daily-runs
    if ($LASTEXITCODE -ne 0) {
        throw "Daily pipeline failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
