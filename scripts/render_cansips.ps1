param(
    [ValidateSet("500mb_height_anomaly")]
    [string]$Product = "500mb_height_anomaly",
    [string]$Init = "latest",
    [string]$LeadMonths = "4,5,6",
    [string]$SeasonalWindow = "4,5,6",
    [int]$ClimoStart = 1991,
    [int]$ClimoEnd = 2020,
    [string]$CacheDir = ".cache/cansips",
    [string]$OutputDir = "public/seasonal/cansips",
    [string]$Manifest = "public/seasonal/cansips_manifest.json",
    [string]$PreviousManifest = "",
    [int]$RetainRuns = 4,
    [string]$Wgrib2 = "",
    [switch]$NoBorders,
    [switch]$DecodeOnly,
    [switch]$ForceDecode
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $arguments = @(
        "scripts/cansips_seasonal.py",
        "--product", $Product,
        "--init", $Init,
        "--lead-months", $LeadMonths,
        "--seasonal-window", $SeasonalWindow,
        "--climo-start", $ClimoStart,
        "--climo-end", $ClimoEnd,
        "--cache-dir", $CacheDir,
        "--output-dir", $OutputDir,
        "--manifest", $Manifest,
        "--retain-runs", $RetainRuns
    )
    if ($PreviousManifest) { $arguments += @("--previous-manifest", $PreviousManifest) }
    if ($Wgrib2) { $arguments += @("--wgrib2", $Wgrib2) }
    if ($NoBorders) { $arguments += "--no-borders" }
    if ($DecodeOnly) { $arguments += "--decode-only" }
    if ($ForceDecode) { $arguments += "--force-decode" }

    Write-Host "Running CanSIPS v3 seasonal adapter..."
    Write-Host "  PRODUCT=$Product"
    Write-Host "  INIT=$Init"
    Write-Host "  LEAD_MONTHS=$LeadMonths"
    Write-Host "  SEASONAL_WINDOW=$SeasonalWindow"
    Write-Host "  CLIMO_YEARS=$ClimoStart-$ClimoEnd"
    python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "CanSIPS adapter exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
