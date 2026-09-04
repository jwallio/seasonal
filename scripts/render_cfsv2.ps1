param(
    [ValidateSet("500mb_height_anomaly", "500mb_height_anomaly_nh", "500mb_height_absolute", "850mb_temperature_anomaly", "2m_temperature_anomaly", "mslp_anomaly", "precipitation_anomaly", "snowfall_anomaly", "snowfall_accumulation")]
    [string]$Product = "500mb_height_anomaly",
    [string]$Init = "latest",
    [string]$LeadMonths = "1,2,3",
    [string]$SeasonalWindow = "",
    [string]$Members = "1,2,3,4",
    [int]$RollingDays = 0,
    [int]$RollingMember = 1,
    [string]$RollingStateDir = ".cache/cfsv2/rolling",
    [switch]$AllowPartialRolling,
    [string]$BaselineFile = "",
    [string]$BaselineDir = "",
    [string]$BaselineLabel = "",
    [string]$BaselineYears = "",
    [switch]$UseNceiCalibration,
    [string]$CacheDir = ".cache/cfsv2",
    [string]$OutputDir = "public/seasonal/cfsv2",
    [string]$Manifest = "public/seasonal/cfsv2_manifest.json",
    [string]$PreviousManifest = "",
    [int]$RetainRuns = 4,
    [string]$Wgrib2 = "",
    [switch]$Absolute,
    [switch]$DecodeOnly,
    [switch]$NoBorders,
    [switch]$ForceDecode
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $arguments = @(
        "scripts/cfsv2_seasonal.py",
        "--product", $Product,
        "--init", $Init,
        "--lead-months", $LeadMonths,
        "--members", $Members,
        "--rolling-days", $RollingDays,
        "--rolling-member", $RollingMember,
        "--rolling-state-dir", $RollingStateDir,
        "--cache-dir", $CacheDir,
        "--output-dir", $OutputDir,
        "--manifest", $Manifest,
        "--retain-runs", $RetainRuns
    )

    if ($SeasonalWindow) { $arguments += @("--seasonal-window", $SeasonalWindow) }
    if ($AllowPartialRolling) { $arguments += "--allow-partial-rolling" }
    if ($BaselineFile) { $arguments += @("--baseline-file", $BaselineFile) }
    if ($BaselineDir) { $arguments += @("--baseline-dir", $BaselineDir) }
    if ($BaselineLabel) { $arguments += @("--baseline-label", $BaselineLabel) }
    if ($BaselineYears) { $arguments += @("--baseline-years", $BaselineYears) }
    if ($PreviousManifest) { $arguments += @("--previous-manifest", $PreviousManifest) }
    if ($UseNceiCalibration) { $arguments += "--ncei-calibration" }
    if ($Wgrib2) { $arguments += @("--wgrib2", $Wgrib2) }
    if ($Absolute) { $arguments += "--absolute" }
    if ($DecodeOnly) { $arguments += "--decode-only" }
    if ($NoBorders) { $arguments += "--no-borders" }
    if ($ForceDecode) { $arguments += "--force-decode" }

    Write-Host "Running CFSv2 seasonal adapter..."
    Write-Host "  PRODUCT=$Product"
    Write-Host "  INIT=$Init"
    Write-Host "  LEAD_MONTHS=$LeadMonths"
    if ($SeasonalWindow) { Write-Host "  SEASONAL_WINDOW=$SeasonalWindow" }
    Write-Host "  MEMBERS=$Members"
    if ($RollingDays -gt 0) {
        Write-Host "  ROLLING_DAYS=$RollingDays (expected $($RollingDays * 4) six-hourly cycles)"
        Write-Host "  ROLLING_MEMBER=$RollingMember"
    }
    if ($BaselineFile) { Write-Host "  BASELINE_FILE=$BaselineFile" }
    if ($BaselineDir) { Write-Host "  BASELINE_DIR=$BaselineDir" }
    if ($UseNceiCalibration) { Write-Host "  BASELINE=NCEI_CFS_REFORECAST_CALIBRATION_1982_2010" }

    python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "CFSv2 adapter exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
