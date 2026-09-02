param(
    [ValidateSet("500mb_height_anomaly", "500mb_height_anomaly_nh", "2m_temperature_anomaly", "850mb_temperature_anomaly", "precipitation_anomaly", "snowfall_anomaly", "snow_depth_anomaly", "mslp_anomaly")]
    [string]$Product = "500mb_height_anomaly",
    [string]$Init = "latest",
    [string]$LeadMonths = "4,5,6",
    [string]$SeasonalWindow = "4,5,6",
    [string]$ClimoYears = "1981-2016",
    [string]$CacheDir = ".cache/seas5",
    [string]$OutputDir = "public/seasonal/seas5",
    [string]$Manifest = "public/seasonal/seas5_manifest.json",
    [string]$PreviousManifest = "",
    [int]$RetainRuns = 4,
    [switch]$NoBorders,
    [switch]$DecodeOnly,
    [switch]$Absolute
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $arguments = @(
        "scripts/seas5_seasonal.py",
        "--product", $Product,
        "--init", $Init,
        "--lead-months", $LeadMonths,
        "--seasonal-window", $SeasonalWindow,
        "--climo-years", $ClimoYears,
        "--cache-dir", $CacheDir,
        "--output-dir", $OutputDir,
        "--manifest", $Manifest,
        "--retain-runs", $RetainRuns
    )
    if ($PreviousManifest) { $arguments += @("--previous-manifest", $PreviousManifest) }
    if ($NoBorders) { $arguments += "--no-borders" }
    if ($DecodeOnly) { $arguments += "--decode-only" }
    if ($Absolute) { $arguments += "--absolute" }

    Write-Host "Running SEAS5 seasonal adapter..."
    Write-Host "  PRODUCT=$Product"
    Write-Host "  INIT=$Init"
    Write-Host "  LEAD_MONTHS=$LeadMonths"
    Write-Host "  SEASONAL_WINDOW=$SeasonalWindow"
    Write-Host "  CLIMO_YEARS=$ClimoYears"
    python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "SEAS5 adapter exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
