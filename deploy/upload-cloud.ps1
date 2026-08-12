param(
    [Parameter(Mandatory = $true)][string]$SshTarget,
    [string]$ArchivePath = ".tmp\moneymore-cloud.tar.gz",
    [string]$RemoteRoot = "/opt/moneymore/current"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ResolvedArchive = if ([System.IO.Path]::IsPathRooted($ArchivePath)) {
    $ArchivePath
} else {
    Join-Path $ProjectRoot $ArchivePath
}

if (-not (Test-Path -LiteralPath $ResolvedArchive)) {
    & (Join-Path $PSScriptRoot "package-cloud.ps1") -OutputPath $ResolvedArchive
}

& ssh $SshTarget "sudo mkdir -p '$RemoteRoot' && sudo chown -R `$(id -un):`$(id -gn) /opt/moneymore"
if ($LASTEXITCODE -ne 0) { throw "remote directory preparation failed" }

& scp $ResolvedArchive "${SshTarget}:/tmp/moneymore-cloud.tar.gz"
if ($LASTEXITCODE -ne 0) { throw "archive upload failed" }

& ssh $SshTarget "tar -xzf /tmp/moneymore-cloud.tar.gz -C '$RemoteRoot' && rm -f /tmp/moneymore-cloud.tar.gz"
if ($LASTEXITCODE -ne 0) { throw "remote archive extraction failed" }

Write-Host "Upload complete. On the server, create dashboard credentials and run:"
Write-Host "  sudo apt-get update && sudo apt-get install -y apache2-utils"
Write-Host "  sudo htpasswd -c /etc/nginx/.htpasswd-moneymore <username>"
Write-Host "  sudo bash $RemoteRoot/deploy/bootstrap-ubuntu.sh"
