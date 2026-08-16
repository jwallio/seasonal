# WN2

WN2 is a WeatherNext 2 forecast-graphics pipeline built around Google Earth Engine and a static GitHub Pages viewer.

It renders forecast images into `public/`, writes a run manifest, and publishes the latest output through GitHub Actions.

## What This Repo Does

WN2 generates image sequences for these products:

- `nh_z500a`: Northern Hemisphere 500 hPa height anomaly
- `na_z500a`: North America 500 hPa height anomaly
- `conus_mslp_ptype`: CONUS mean sea-level pressure plus precipitation type
- `ne_mslp_ptype`: Northeast mean sea-level pressure plus precipitation type
- `conus_vort500`: CONUS 500 hPa relative vorticity plus height contours
- `conus_t2m`: CONUS 2 m temperature
- `conus_t2m_anom`: CONUS 2 m temperature anomaly
- `conus_wind10`: 10 m wind speed derived from WeatherNext u/v wind components
- `conus_t850`: 850 hPa temperature
- `conus_t500`: 500 hPa temperature
- `conus_omega500`: 500 hPa vertical velocity
- `conus_sst`: sea-surface temperature
- `conus_snow_accum`: CONUS snowfall accumulation
- `ne_snow_accum`: Northeast snowfall accumulation
- `ne_zoom_snow_accum`: New England zoom snowfall accumulation
- `mi_wi_snow_accum`: Michigan/Wisconsin snowfall accumulation
- `carolinas_snow_accum`: Carolinas snowfall accumulation

Snow products support configurable snow ratios. The current snow scale extends to `40"` and the regional snow graphics use the reworked layout and styling.

The WeatherNext parameter products use the same forecast-hour and map export
pipeline as the existing graphics. They are opt-in for a plain local
`python main.py` run so the existing local output set remains stable; the
`weather_parameters` workflow preset enables all five.

The standalone CFSv2 seasonal adapter is documented in [`docs/SEASONAL_CFSV2.md`](/d:/weather-projects/wn2/docs/SEASONAL_CFSV2.md). It downloads official NOAA NOMADS monthly `pgbf` GRIB2 files, extracts 500-mb height with `wgrib2`, computes an ensemble mean, and writes calendar-month products under `public/seasonal/cfsv2/`. Production anomaly images require an explicitly selected CFSv2/reforecast baseline (custom or official NCEI calibration); the adapter never substitutes the WN2 ERA5/MERRA-2 baselines. Use `-RollingDays 10` to build the CPC-style 40-cycle lagged initial-condition blend; the rolling state directory must persist between runs because NOMADS retains only seven days. The scheduled [`cfsv2.yml`](/d:/weather-projects/wn2/.github/workflows/cfsv2.yml) workflow uploads a CFSv2 Pages payload; the central [`publish-pages.yml`](/d:/weather-projects/wn2/.github/workflows/publish-pages.yml) workflow merges it with WN2 and SEAS5 outputs before publishing. The Earth Engine CFSv2 collection remains a separate surface-data option.

The ECMWF SEAS5 seasonal adapter is documented in [`docs/SEASONAL_SEAS5.md`](/d:/weather-projects/wn2/docs/SEASONAL_SEAS5.md). It reads current ECMWF/System 51 monthly anomaly fields from the official Copernicus CDS API and publishes parameter-selectable seasonal maps under `public/seasonal/seas5/`. The scheduled [`seas5.yml`](/d:/weather-projects/wn2/.github/workflows/seas5.yml) workflow requires the `CDS_API_KEY` repository secret, uploads a SEAS5 Pages payload, and keeps SEAS5 provenance separate from CFSv2 while retaining the current run plus three prior runs.

The CanSIPS v3 seasonal adapter is documented in [`docs/SEASONAL_CANSIPS.md`](/d:/weather-projects/wn2/docs/SEASONAL_CANSIPS.md). It reads the official ECCC MSC Datamart 40-member GRIB2 files, computes matching 1991-2020 hindcast anomalies for 500-mb height, 850-mb and 2-metre temperature, precipitation, MSLP, sea-surface temperature, and sea-surface height, and publishes DJF-default North American maps under `public/seasonal/cansips/`. The scheduled [`cansips.yml`](/d:/weather-projects/wn2/.github/workflows/cansips.yml) workflow requires no model secret, caches decoded ensemble means, and retains the current run plus three prior runs independently for each parameter.

The unified [Seasonal Model Dashboard](/d:/weather-projects/wn2/public/seasonal/index.html) is published at
[`/seasonal/`](https://jwallio.github.io/wn2/seasonal/). It provides one model,
parameter, run, and target control surface for CFSv2, ECMWF SEAS5, and CanSIPS
v3. WeatherNext 2 remains published by its own workflow at the repository root,
but is intentionally not listed as a seasonal model. The model-specific pages remain available at
`/seasonal/cfsv2/`, `/seasonal/seas5/`, and `/seasonal/cansips/` when focused
provenance review is needed. The dashboard's Compare tab places the latest
matching 500-mb height-anomaly map from each seasonal model side by side and
uses one shared monthly or seasonal-period selector. It defaults to each
model's native anomaly; its Reference selector can optionally show a common
1991-2020 reference based on the CanSIPS v3 hindcast mean.

## Repo Layout

- [`main.py`](/d:/weather-projects/wn2/main.py): main render pipeline
- [`.github/workflows/runner.yml`](/d:/weather-projects/wn2/.github/workflows/runner.yml): short default GitHub Actions runner
- [`.github/workflows/update.yml`](/d:/weather-projects/wn2/.github/workflows/update.yml): full custom GitHub Actions workflow
- [`scripts/run_custom.ps1`](/d:/weather-projects/wn2/scripts/run_custom.ps1): local custom render helper
- [`scripts/render_and_test.ps1`](/d:/weather-projects/wn2/scripts/render_and_test.ps1): local render plus smoke test
- [`scripts/render_cfsv2.ps1`](/d:/weather-projects/wn2/scripts/render_cfsv2.ps1): CFSv2 monthly/seasonal 500-mb adapter helper
- [`.github/workflows/cfsv2.yml`](/d:/weather-projects/wn2/.github/workflows/cfsv2.yml): daily rolling CFSv2 workflow
- [`scripts/render_seas5.ps1`](/d:/weather-projects/wn2/scripts/render_seas5.ps1): ECMWF SEAS5 seasonal adapter helper
- [`scripts/seas5_seasonal.py`](/d:/weather-projects/wn2/scripts/seas5_seasonal.py): Copernicus CDS/SEAS5 GRIB adapter
- [`.github/workflows/seas5.yml`](/d:/weather-projects/wn2/.github/workflows/seas5.yml): monthly SEAS5 workflow
- [`scripts/cansips_seasonal.py`](/d:/weather-projects/wn2/scripts/cansips_seasonal.py): ECCC MSC Datamart/CanSIPS v3 GRIB adapter
- [`.github/workflows/cansips.yml`](/d:/weather-projects/wn2/.github/workflows/cansips.yml): monthly CanSIPS v3 workflow
- [`.github/workflows/publish-pages.yml`](/d:/weather-projects/wn2/.github/workflows/publish-pages.yml): single serialized WN2/CFSv2/SEAS5/CanSIPS Pages publisher
- [`public/seasonal/index.html`](/d:/weather-projects/wn2/public/seasonal/index.html): unified seasonal model dashboard shell
- [`scripts/clean.ps1`](/d:/weather-projects/wn2/scripts/clean.ps1): remove root `.tmp_*` files and `*.log`
- [`tests/smoke_outputs.py`](/d:/weather-projects/wn2/tests/smoke_outputs.py): output sanity/smoke test
- [`tests/test_pipeline_contract.py`](/d:/weather-projects/wn2/tests/test_pipeline_contract.py): static pipeline contract check
- [`tests/test_cfsv2_contract.py`](/d:/weather-projects/wn2/tests/test_cfsv2_contract.py): static CFSv2 adapter contract check
- [`tests/test_seas5_contract.py`](/d:/weather-projects/wn2/tests/test_seas5_contract.py): static SEAS5 adapter contract check
- [`public/index.html`](/d:/weather-projects/wn2/public/index.html): generated static viewer
- [`public/runs_manifest.json`](/d:/weather-projects/wn2/public/runs_manifest.json): viewer manifest

## Output Structure

Rendered frames are written under:

```text
public/runs/<run_id>/
```

Examples:

```text
public/runs/2026030812/ne_zoom_snow_accum_r10_240.jpg
public/runs/2026030712/na_z500a_048.jpg
```

The viewer reads:

```text
public/index.html
public/runs_manifest.json
```

## Requirements

- Python 3.11
- Packages from [`requirements.txt`](/d:/weather-projects/wn2/requirements.txt)
- Google Earth Engine access
- An Earth Engine project ID

Install dependencies:

```powershell
pip install -r requirements.txt
```

## Envir