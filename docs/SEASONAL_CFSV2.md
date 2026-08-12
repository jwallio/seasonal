# Seasonal CFSv2 products

The repository now has a standalone CFSv2 seasonal adapter at
[`scripts/cfsv2_seasonal.py`](/d:/weather-projects/wn2/scripts/cfsv2_seasonal.py).
It is deliberately separate from `main.py`: WeatherNext products are
Earth-Engine forecast frames, while these products are NOAA CFSv2 monthly
forecast averages with calendar-month lead metadata rather than the
WeatherNext `forecast_hour` schema.

## What the adapter does

For each requested lead month, the adapter:

1. Builds the official NOMADS `pgbf.<member>.<init>.<target>.avrg.grib.grb2`
   URL for the requested CFSv2 cycle and target month.
2. Caches the raw GRIB2 file and runs `wgrib2 -match ":HGT:500 mb:" -csv`.
3. Validates the decoded 1-degree global grid and averages the selected
   `monthly_grib_01` through `monthly_grib_04` member streams.
4. Subtracts a caller-supplied month-matched CFSv2/reforecast baseline.
5. Renders a North America blue/white/red 500-mb height-anomaly graphic and
   updates `public/seasonal/cfsv2_manifest.json`.

`--lead-months "1,2,3"` writes individual target-month maps. Adding
`--seasonal-window "1,2,3"` also writes a 3-month mean map, using the mean of
the selected forecast months and the corresponding mean baseline.

The adapter also supports the CPC-style lagged initial-condition window with
`--rolling-days 10`. CFSv2 is run four times per day, so this produces 40
six-hourly initial-condition members using `monthly_grib_01` from each cycle.
The current anchor cycle is included and the oldest cycle is 39 six-hourly
steps earlier. This follows CPC's description of 40 members from a 10-day
initial-condition period, while remaining explicit that this is a model-based
product and not CPC's official outlook.

The NOMADS real-time archive rotates after seven days. Each rolling run writes
the decoded HGT500 grids to `--rolling-state-dir` so a scheduled daily job can
carry the older members forward. A full 40-member product therefore requires
the state cache to be retained between runs; `--allow-partial-rolling` is
available only for clearly marked incomplete smoke products.

The original single-cycle mode remains available when `--rolling-days` is zero
and records its scope as `single_initial_condition_cycle`.

The manifest records the source URL, initialization time, target month, lead,
members, decoder, aggregation, baseline, cache paths, image path, and a
per-target status (`planned`, `decoded`, `rendered`, `partial`, or `failed`).
That keeps a failed or stale seasonal run visible instead of silently
presenting an incomplete forecast as current.

## Baseline rule

An anomaly image is only produced when `--baseline-file`, `--baseline-dir`, or
`--ncei-calibration` is provided. A baseline may be a CSV in the adapter's decoded-grid format or a
GRIB2 file containing `HGT:500 mb:`. With `--baseline-dir`, the preferred
month-specific names are `z500_YYYYMM.csv`, `z500_YYYYMM.grb2`, or
`z500_YYYYMM.grib2`.

The `--ncei-calibration` option downloads the matching official NCEI CFS
reforecast calibration file for the initialization month/day/cycle and lead.
That published pressure-level calibration is `1982-2010` and is recorded in
the manifest; it is separate from the current CPC display convention of
`1991-2020` for the public monthly/seasonal anomaly pages.

In rolling mode, NCEI calibration uses the anchor initialization's matching
lead to avoid downloading a separate calibration file for every lagged member.
The manifest records this as `baseline.rolling_policy =
anchor_initialization`. A custom target-month baseline directory can be used
when a month-specific 1991-2020 climatology is preferred.

For a custom baseline, identify it in the command with `--baseline-label` and,
when known, `--baseline-years`; `--ncei-calibration` supplies those values from
the official file automatically. Do not use the WN2 ERA5/MERRA-2 baseline and
call it a CFSv2 anomaly. The CFSv2 model/reforecast climatology is a separate
scientific input and is intentionally not guessed by the script.

`--absolute` renders raw 500-mb geopotential height for source/decoder smoke
testing only. Its image and manifest say `z500`, not `z500_anomaly`.

## Local usage

The repository requirements already include `requests`, `numpy`, `matplotlib`,
and `Pillow`; `wgrib2` must be installed separately. The adapter honors
`CFSV2_WGRIB2` and auto-detects `C:\wgrib2\wgrib2.exe` on Windows.

The GitHub Actions workflow includes a **CFSv2 product** dropdown. The current
adapter supports `500mb_height_anomaly` (the production output, selected by
default) and `500mb_height_absolute` (a clearly labelled decoder/source smoke
output). CFSv2 parameter choices such as 850-mb temperature or precipitation
are not shown until their data decoding and baseline logic are implemented.

Decode one target without requiring a baseline or rendering dependencies:

```powershell
.\scripts\render_cfsv2.ps1 `
  -Init "2026081000" `
  -LeadMonths "1" `
  -Members "1" `
  -DecodeOnly `
  -NoBorders
```

Render three monthly leads using four selected member streams:

```powershell
.\scripts\render_cfsv2.ps1 `
  -Init "2026081000" `
  -LeadMonths "1,2,3" `
  -SeasonalWindow "1,2,3" `
  -Members "1,2,3,4" `
  -BaselineDir "baselines/cfsv2" `
  -BaselineLabel "CFSv2 reforecast climatology" `
  -BaselineYears "1991-2020"
```

Use the official NCEI calibration automatically:

```powershell
.\scripts\render_cfsv2.ps1 `
  -Init "2026081000" `
  -LeadMonths "1" `
  -Members "1,2,3,4" `
  -UseNceiCalibration
```

Build the CPC-style rolling 40-member blend. Persist `RollingStateDir` between
daily runs using a GitHub Actions cache, private artifact store, or equivalent
local directory:

```powershell
.\scripts\render_cfsv2.ps1 `
  -Init "latest" `
  -LeadMonths "1,2,3" `
  -SeasonalWindow "1,2,3" `
  -RollingDays 10 `
  -RollingMember 1 `
  -UseNceiCalibration
```

For an initial source smoke output before the baseline is available:

```powershell
.\scripts\render_cfsv2.ps1 `
  -Init "2026081000" `
  -LeadMonths "1" `
  -Members "1" `
  -Absolute
```

The output is intentionally labelled as an absolute field and must not be
used as an anomaly forecast.

## Normalized seasonal product contract

Each target entry uses these fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable CFSv2 target identifier |
| `source` / `source_url` | NOAA CFSv2 NOMADS provenance |
| `model` | `CFSv2` |
| `init_utc` | Forecast initialization time |
| `valid_start_utc` / `valid_end_utc` | Calendar month represented |
| `lead_month` / `target_month` | CFSv2 lead and `YYYYMM` target |
| `aggregation` | Monthly forecast average |
| `field` | `z500_anomaly` for production or `z500` for smoke output |
| `units` | Metres |
| `baseline` | Baseline file, label, and years when applicable |
| `statistic` | `ensemble_mean` |
| `ensemble_scope` | The selected streams' initialization-cycle scope |
| `ensemble_members` | Number of usable members in the mean |
| `ensemble_expected_members` | Requested member count, including an incomplete rolling window |
| `rolling_window` | Cycle interval, dates, member stream, and expected count when rolling |
| `status` | Decode/render outcome |
| `image` | Relative rendered image path when successful |

## Source notes

- [NOAA CFSv2 downloads](https://cfs.ncep.noaa.gov/cfsv2/downloads.html)
- [NOAA NOMADS CFSv2 operational directory](https://nomads.ncep.noaa.gov/pub/data/nccf/com/cfs/prod/)
- [NOMADS CFS pressure-level filter](https://nomads.ncep.noaa.gov/cgi-bin/filter_cfs_pgb.pl)
- [NOAA CFSv2 model page](https://www.ncei.noaa.gov/products/weather-climate-models/climate-forecast-system)
- [NCEI CFS reforecast pressure-level calibration catalog](https://www.ncei.noaa.gov/thredds/catalog/model-cfs_refor_calclim_mm_9m_pgbf/catalog.html)
- [NCEP CPC CFSv2 seasonal forecasts](https://www.cpc.ncep.noaa.gov/products/CFSv2/CFSv2seasonal.shtml)
- [CFSv2 forecast-file metadata notes](https://www.cpc.ncep.noaa.gov/products/tools/wgrib2/fix_CFSv2_fcst.html)
- [Earth Engine CFSv2 collection](https://developers.google.com/earth-engine/datasets/catalog/NOAA_CFSV2_FOR6H_HARMONIZED)

The Earth Engine collection is useful for its available 6-hourly surface
fields, but it is not the pressure-level `HGT:500 mb` source used here.
