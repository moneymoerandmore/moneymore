$ErrorActionPreference = "Stop"
$AuKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
$WuKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate"
$LogPath = Join-Path (Split-Path -Parent $PSScriptRoot) "logs\windows-update-policy.log"

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null
New-Item -ItemType Directory -Force -Path $WuKey | Out-Null
New-ItemProperty -Path $WuKey -Name SetActiveHours -PropertyType DWord -Value 1 -Force | Out-Null
New-ItemProperty -Path $WuKey -Name ActiveHoursStart -PropertyType DWord -Value 6 -Force | Out-Null
New-ItemProperty -Path $WuKey -Name ActiveHoursEnd -PropertyType DWord -Value 0 -Force | Out-Null
New-Item -ItemType Directory -Force -Path $AuKey | Out-Null
New-ItemProperty -Path $AuKey -Name AUOptions -PropertyType DWord -Value 3 -Force | Out-Null
New-ItemProperty -Path $AuKey -Name NoAutoRebootWithLoggedOnUsers -PropertyType DWord -Value 1 -Force | Out-Null
& gpupdate.exe /target:computer /force | Out-Null

[pscustomobject]@{
    applied_at = [DateTimeOffset]::Now.ToString("o")
    AUOptions = (Get-ItemProperty -Path $AuKey).AUOptions
    NoAutoRebootWithLoggedOnUsers = (Get-ItemProperty -Path $AuKey).NoAutoRebootWithLoggedOnUsers
    SetActiveHours = (Get-ItemProperty -Path $WuKey).SetActiveHours
    ActiveHoursStart = (Get-ItemProperty -Path $WuKey).ActiveHoursStart
    ActiveHoursEnd = (Get-ItemProperty -Path $WuKey).ActiveHoursEnd
} | ConvertTo-Json | Set-Content -LiteralPath $LogPath -Encoding UTF8
