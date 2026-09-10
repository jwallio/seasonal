param([switch]$Install)
$ErrorActionPreference = 'Stop'
$repo = 'jwallio/seasonal'
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'Install GitHub CLI first: winget install --id GitHub.cli'
}
gh auth status
if ($LASTEXITCODE -ne 0) { throw 'Run gh auth login first.' }
if ($Install) {
    $dir = Join-Path $env:LOCALAPPDATA 'WallCloud'
    New-Item -ItemType Directory -Force $dir | Out-Null
    $dest = Join-Path $dir 'cfsv2-watchdog.ps1'
    if ($PSCommandPath -ne $dest) { Copy-Item $PSCommandPath $dest -Force }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -File ""$dest"""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName 'WallCloud CFS availability backup' -Action $action -Trigger $trigger -Settings $settings -Description 'Trigger the GitHub availability probe when its schedule falls behind.' -Force | Out-Null
    Write-Output 'Installed. The PC must be awake and your Windows account logged in.'
    exit
}
$raw = gh api "repos/$repo/actions/workflows/cfsv2-availability.yml/runs?branch=main&per_page=20"
if ($LASTEXITCODE -ne 0) { throw 'Could not check GitHub runs; no dispatch attempted.' }
$runs = ($raw | ConvertFrom-Json).workflow_runs
$active = @($runs | Where-Object { $_.status -in @('queued','in_progress','waiting','pending','requested') })
if ($active.Count -gt 0) { Write-Output 'Availability probe already active.'; exit }
$recent = @($runs | Where-Object {
    $_.conclusion -eq 'success' -and
    [DateTimeOffset]::Parse($_.updated_at) -gt [DateTimeOffset]::UtcNow.AddMinutes(-30)
})
if ($recent.Count -gt 0) { Write-Output 'GitHub availability checks are current.'; exit }
gh workflow run cfsv2-availability.yml --repo $repo --ref main
if ($LASTEXITCODE -ne 0) { throw 'Backup dispatch failed.' }
Write-Output 'Dispatched availability check; its source checks and deduplication decide whether to build.'
