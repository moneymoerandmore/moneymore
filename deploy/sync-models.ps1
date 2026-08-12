param(
    [Parameter(Mandatory = $true)][string]$SshTarget,
    [string]$RemoteRoot = "/opt/moneymore/current"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ModelRoot = Join-Path $ProjectRoot "state\qlib-challenger"
$GovernanceRoot = Join-Path $ProjectRoot "state\qlib-governance"

if (-not (Test-Path -LiteralPath $ModelRoot)) {
    throw "Qlib model directory not found: $ModelRoot"
}

& scp -r $ModelRoot "${SshTarget}:$RemoteRoot/state/"
if ($LASTEXITCODE -ne 0) { throw "model upload failed" }
& scp -r $GovernanceRoot "${SshTarget}:$RemoteRoot/state/"
if ($LASTEXITCODE -ne 0) { throw "governance upload failed" }
& ssh $SshTarget "sudo systemctl restart moneymore-api && curl -fsS http://127.0.0.1:8788/api/health"
if ($LASTEXITCODE -ne 0) { throw "remote API restart or health check failed" }
