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
- `conus_snow_accum`: CONUS snowfall accumulation
- `ne_snow_accum`: Northeast snowfall accumulation
- `ne_zoom_snow_accum`: New England zoom snowfall accumulation
- `mi_wi_snow_accum`: Michigan/Wisconsin snowfall accumulation
- `carolinas_snow_accum`: Carolinas snowfall accumulation

Snow products support configurable snow ratios. The current snow scale extends to `40"` and the regional snow graphics use the reworked layout and styling.

## Repo Layout

- [`main.py`](/d:/weather-projects/wn2/main.py): main render pipeline
- [`.github/workflows/runner.yml`](/d:/weather-projects/wn2/.github/workflows/runner.yml): short default GitHub Actions runner
- [`.github/workflows/update.yml`](/d:/weather-projects/wn2/.github/workflows/update.yml): full custom GitHub Actions workflow
- [`scripts/run_custom.ps1`](/d:/weather-projects/wn2/scripts/run_custom.ps1): local custom render helper
- [`scripts/render_and_test.ps1`](/d:/weather-projects/wn2/scripts/render_and_test.ps1): local render plus smoke test
- [`scripts/clean.ps1`](/d:/weather-projects/wn2/scripts/clean.ps1): remove root `.tmp_*` files and `*.log`
- [`tests/smoke_outputs.py`](/d:/weather-projects/wn2/tests/smoke_outputs.py): output sanity/smoke test
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

## Environment Variables

Minimum local setup:

- `EE_PROJECT`: required Earth Engine project ID
- `EE_KEY`: optional locally, used in Actions as the serialized service-account JSON

Basic local run:

```powershell
$env:EE_PROJECT = "snowcast-1"
python main.py
```

Local run with a service-account JSON file:

```powershell
$env:EE_PROJECT = "snowcast-1"
$env:EE_KEY = Get-Content .\service-account.json -Raw
python main.py
```

Other commonly used runtime controls:

- `WN2_SELECTED_PRODUCTS`
- `WN2_PRODUCT_MODE`
- `HOURS_CSV`
- `HOURS_MAX`
- `RUN_INIT_UTC`
- `SNOW_RATIO_CSV`
- `WN2_MAX_DIMENSION`
- `WN2_CLIMO_SOURCE`
- `WN2_NH_RENDER_MODE`

## GitHub Actions

There are two workflows:

### WeatherNext Runner

Defined in [`.github/workflows/runner.yml`](/d:/weather-projects/wn2/.github/workflows/runner.yml).

This is the short default workflow. It runs:

- all products
- graphics profile `rework` by default
- full forecast completion
- max dimension `900`
- snow ratio `10`
- climatology source `era5`

On `push` to `main`, it runs the same default configuration but limits the run to a preview frame.

### WeatherNext Runner Custom

Defined in [`.github/workflows/update.yml`](/d:/weather-projects/wn2/.github/workflows/update.yml).

This is the full manual/custom workflow. It exposes inputs for:

- product preset selection
- custom product CSV
- graphics profile
- preview one-frame mode
- hour-range preset
- explicit `hours_csv`
- run initialization override
- maximum render dimension
- export worker count
- geography detail mode
- snow ratio CSV
- climatology source
- climatology start/end year
- run history retention

The workflow renders in shards, uploads artifacts, restores recent runs from `gh-pages`, reconciles manifests, and deploys the merged `public/` directory back to Pages.

## Product Presets

Current workflow presets include:

- `all`
- `flagship`
- `winter_weather`
- `conus_core`
- `regional_zoom`
- `z500_anomalies`
- `temperature`
- `snowfall`
- `custom_csv`

## Local Scripts

Run a custom subset:

```powershell
.\scripts\run_custom.ps1 `
  -Products "nh_z500a,na_z500a,conus_t2m_anom" `
  -HoursCsv "6,12,18" `
  -MaxDim "900" `
  -Climo "era5" `
  -EeProject "snowcast-1"
```

Render and then smoke-test:

```powershell
.\scripts\render_and_test.ps1 `
  -Products "nh_z500a,na_z500a,conus_t2m_anom" `
  -HoursCsv "6" `
  -MaxDim "900" `
  -Climo "era5" `
  -EeProject "snowcast-1"
```

Clean temp/log files from the repo root:

```powershell
.\scripts\clean.ps1
```

Preview only:

```powershell
.\scripts\clean.ps1 -WhatIf
```

## Smoke Testing

Smoke testing is handled by [`tests/smoke_outputs.py`](/d:/weather-projects/wn2/tests/smoke_outputs.py).

It checks that:

- a run directory exists
- images are readable
- images are not tiny
- images stay within the configured max-dimension policy

Run it directly:

```powershell
python tests/smoke_outputs.py
```

## Rendering Notes

- The codebase is centered on a single script, [`main.py`](/d:/weather-projects/wn2/main.py).
- Recent runs are retained through the manifest and `public/runs/`.
- Snow maps use dedicated labeling and border overlays.
- Regional snow graphics currently include New England, Michigan/Wisconsin, and the Carolinas.
- Product dimensions are kept consistently landscape-oriented across forecast hours.

## Secrets Needed In GitHub

The GitHub Actions workflows require:

- `EE_PROJECT`
- `EE_KEY`

## Branches And Publishing

- `main` is the source/default branch
- `gh-pages` is the published static site branch

The workflows restore recent `gh-pages` outputs, merge newly rendered frames, rebuild the manifest, and deploy the site again.
