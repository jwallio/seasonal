param(
    [string]$Products = "nh_z500a,na_z500a,conus_t2m_anom",
    [string]$HoursCsv = "6",
    [string]$MaxDim = "900",
    [ValidateSet("era5", "merra2")]
    [string]$Climo = "era5",
    [ValidateSet("first", "mean", "median", "member")]
    [string]$EnsembleMode = "first",
    [string]$EnsembleMember = "",
    [ValidateSet("default", "classic")]
    [string]$Z500Style = "default",
    [string]$EeProject = "snowcast-1"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    & "$PSScriptRoot\run_custom.ps1" `
        -Products $Products `
        -HoursCsv $HoursCsv `
        -MaxDim $MaxDim `
        -Climo $Climo `
        -EnsembleMode $EnsembleMode `
        -EnsembleMember $EnsembleMember `
        -Z500Style $Z500Style `
        -EeProject $EeProject

    $env:WN2_MAX_DIMENSION = $MaxDim
    python tests/smoke_outputs.py
}
finally {
    Pop-Location
}
