# Seasonal CFSv2 products

The repository now has a standalone CFSv2 seasonal adapter at
[`scripts/cfsv2_seasonal.py`](/d:/weather-projects/wn2/scripts/cfsv2_seasonal.py).
It is deliberately separate from `main.py`: WeatherNext products are
Earth-Engine forecast frames, while these products are NOAA CFSv2 monthly
forecast averages with calendar-month lead metadata rather than the
WeatherNext `forecast_hour` schema.

## What the adapter does

For each requested lead month, the adapter:

1. Builds the official NOMADS monthly URL for the selected product and target
   month. Height products use `pgbf`; precipitation and snow-water-equivalent
   products use the matching `flxf` monthly file.
2. Caches the raw GRIB2 file and runs a product-specific `wgrib2` field filter.
3. Validates the decoded global grid and averages the selected
   `monthly_grib_01` through `monthly_grib_04` member streams.
4. Subtracts a caller-supplied or official month-matched CFSv2/reforecast
   baseline.
5. Renders the selected operational map on a 1080×1080 social canvas and
   updates `public/seasonal/cfsv2_manifest.json`.

The supported production products are:

| Product | Source field | Display units | Seasonal reduction |
| --- | --- | --- | --- |
| `500mb_height_anomaly` | `HGT:500 mb` from `pgbf` | m | 3-month mean |
| `precipitation_anomaly` | `PRATE:surface` from `flxf` | in | 3-month total |
| `snow_water_equivalent_anomaly` | `WEASD:surface` from `flxf` | in | 3-month mean |

For precipitation, the source rate (`kg m-2 s-1`) is multiplied by the actual
number of seconds in each target calendar month. Since `1 kg m-2 = 1 mm` of
water, the result is converted from millimetres to inches. Individual maps and
seasonal totals are therefore shown in inches. Precipitation graphics use a
CONUS-only projection with brown negative anomalies and green positive
anomalies; the scale runs from -8 to +8 inches with a labeled tick at every
inch. The precipitation FLXF files use the native 384×190 Gaussian grid; the
renderer interpolates that grid directly so the entire CONUS projection is
filled.

Snow-water-equivalent (`WEASD`) is a snowpack state, not a snowfall rate or
accumulation. The source is reported in `kg m-2`, equivalent to millimetres of
liquid water, and is divided by 25.4 for display in inches. Individual maps and
seasonal maps use the mean snow-water-equivalent state across the selected
months and the matching mean calibration baseline. SWE graphics use the full
North America/Greenland projection with brown negative anomalies and blue
positive anomalies; the scale runs from -8 to +8 inches with a labeled tick at
every inch.

The map uses a centered ECMWF-style Lambert Conformal Conic frame. The
projected window includes Alaska, Canada, the United States, Mexico, and all
of Greenland; the anomaly field remains continuous in the rectangular frame,
while border drawing is limited to the North America/Greenland window so
South America and unrelated eastern-Atlantic outlines do not appear.

`--lead-months "1,2,3"` writes individual target-month maps. Adding
`--seasonal-window "1,2,3"` also writes a seasonal map. Height uses the mean
of the selected forecast months and corresponding mean baseline; precipitation
uses the total across the selected months and corresponding total baseline;
snow-water equivalent uses the mean state across the selected months and
corresponding mean baseline.

The adapter also supports the CPC-style lagged initial-condition window with
`--rolling-days 10`. CFSv2 is run four times per day, so this produces 40
six-hourly initial-condition members using `monthly_grib_01` from each cycle.
The current anchor cycle is included and the oldest cycle is 39 six-hourly
steps earlier. This follows CPC's description of 40 members from a 10-day
initial-condition period, while remaining explicit that this is a model-based
product and not CPC's official outlook.

The NOMADS real-time archive rotates after seven days. Each rolling run writes
the decoded product grids to `--rolling-state-dir` so a scheduled daily job can
carry the older members forward. A full 40-member product therefore requires
the state cache to be retained between runs; `--allow-partial-rolling` is
available only for clearly marked incomplete smoke products.

The original single-cycle mode remains available when `--rolling-days` is zero
and records its scope as `single_initial_condition_cycle`.

## Comparison-tab reference

The unified dashboard's Compare tab can request a common 500-mb reference in
addition to each model's native anomaly. CanSIPS publishes its absolute
1991-2020 hindcast mean as compact grids under
`public/seasonal/common_reference/1991-2020/`; the CFSv2 workflow downloads
those grids, regrids them to the CFSv2 axes, and subtracts them from the
absolute CFSv2 forecast before rendering the comparison image. The manifest
labels this explicitly as `Common 1991-2020 reference (CanSIPS v3 hindcast)`.

This is a shared comparison baseline, not a relabeling of the native CFSv2
1982-2010 calibration. The native CFSv2 image remains available in the
selector, and the common image is marked unavailable rather than silently
substituted if the reference grid has not yet been published.

The manifest records the source URL, initialization time, target month, lead,
members, decoder, aggregation, baseline, cache paths, image path, and a
per-target status (`planned`, `decoded`, `rendered`, `partial`, or `failed`).
That keeps a failed or stale seasonal run visible instead of silently
presenting an incomplete forecast as current.

The scheduled workflow downloads the previously published manifest before each
render and retains the current run plus three prior runs. It uploads a scoped
Pages payload; `.github/workflows/publish-pages.yml` serializes that payload
with WN2 and SEAS5 output before the single Pages publish. The static CFSv2
viewer exposes those retained runs through Parameter and Run history selectors;
the central publisher keeps the referenced historical image paths available.
The unified seasonal dashboard at `/seasonal/` adds CFSv2 to the same model and
parameter control surface while preserving this direct viewer for focused
source and run-history review.

## Baseline rule

An anomaly image is only produced when `--baseline-file`, `--baseline-dir`, or
`--ncei-calibration` is provided. A baseline may be a CSV in the adapter's
decoded-grid format or a GRIB2 file containing the selected source field. For
`500mb_height_anomaly`, preferred month-specific files in
`--baseline-dir` are `z500_YYYYMM.csv`, `z500_YYYYMM.grb2`, or
`z500_YYYYMM.grib2`. For precipitation, use `prate_YYYYMM.csv` or the
corresponding GRIB2 name; CSV precipitation baselines must already be monthly
totals in inches. For snow-water equivalent, use `weasd_YYYYMM.csv` or the
corresponding GRIB2 name; CSV SWE baselines must already be in inches.

The `--ncei-calibration` option downloads the matching official NCEI CFS
reforecast calibration file for the initialization month/day/cycle and lead.
Height uses the pressure-level `pgbf` calibration; precipitation and
snow-water equivalent use the official `flxf` flux calibration. Both published
calibrations cover
`1982-2010` and are recorded in the manifest; this is separate from the
current CPC display convention of `1991-2020` for the public monthly/seasonal
anomaly pages.

In rolling mode, NCEI calibration uses the anchor initialization's matching
lead to avoid downloading a separate calibration file for every lagged member.
The manifest records this as `baseline.rolling_policy =
anchor_initialization`. A custom target-month baseline directory can be used
when a month-specific 1991-2020 climatology is preferred.

For a custom baseline, identify it in the command with `--baseline-label` and,
when known, `--baseline-years`; `--ncei-calibration` supplies those