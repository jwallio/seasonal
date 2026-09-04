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
   month. Height, 850-mb temperature, and mean-sea-level pressure products use
   `pgbf`; 2-m temperature and precipitation use the matching `flxf` monthly
   file. Snowfall is an explicitly derived product that reads all three raw
   dependencies.
2. Caches the raw GRIB2 file, reuses compact decoded grids when available, and
   runs a product-specific `wgrib2` field filter only when a decode is needed.
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
| `850mb_temperature_anomaly` | `TMP:850 mb` from `pgbf` | °C | 3-month mean |
| `2m_temperature_anomaly` | `TMP:2 m above ground` from `flxf` | °C | 3-month mean |
| `mslp_anomaly` | `PRES:mean sea level` from `pgbf` | hPa | 3-month mean |
| `precipitation_anomaly` | `PRATE:surface` from `flxf` | in | 3-month total |
| `snowfall_anomaly` | Derived from `TMP:2 m`, `TMP:850 mb`, and `PRATE:surface` | in LWE | 3-month total |
| `snowfall_accumulation` | Derived snowfall LWE multiplied by seasonal CIPS/Baxter SLR climatology | in estimated snow depth | 3-month total |

The 2-m temperature anomaly is a forecast-minus-reforecast difference; the
Kelvin offset cancels, so it is displayed in °C. MSLP is decoded from the
official `PRES:mean sea level` field and converted from Pa to hPa before the
forecast-minus-reforecast difference is calculated. Both products use the
official NCEI CFS reforecast calibration climatology. Fixed scales keep runs
directly comparable while resolving the relatively small rolling-mean signal:
500-mb height uses ±100 m with 10 m intervals, 2-m temperature uses the shared
±7 °C scale with 1 °C intervals, and MSLP uses ±10 hPa with 1 hPa intervals. Values outside
the displayed range use the saturated end color.

For precipitation, the source rate (`kg m-2 s-1`) is multiplied by the actual
number of seconds in each target calendar month. Since `1 kg m-2 = 1 mm` of
water, the result is converted from millimetres to inches. Individual maps and
seasonal totals are therefore shown in inches. Precipitation graphics use a
CONUS-only projection with brown negative anomalies and green positive
anomalies. Monthly maps use ±4 inches with 0.5-inch intervals; three-month
seasonal totals retain the wider ±8-inch scale with 1-inch intervals. The
precipitation FLXF files use the native 384×190 Gaussian grid; the renderer
interpolates that grid directly so the entire CONUS projection is filled.

Derived snowfall is computed independently for every successful member or
rolling cycle. The monthly precipitation rate is first converted to liquid
water-equivalent inches; the warmer of the decoded 2-m and 850-mb absolute
monthly mean temperatures is then passed through the season-appropriate Dai
(2008) land snow-fraction curve. The 850-mb pressure grid is nearest-neighbor
regridded to the native FLXF Gaussian grid before the phase calculation. The
matching 1982-2010 NCEI calibration fields are processed with the same method
and subtracted from the forecast. A single SWE or precipitation baseline is
never accepted as a snowfall substitute.

Estimated snowfall accumulation is a separate product. After the member-level
Dai phase calculation produces monthly snowfall liquid-water equivalent, the
monthly LWE is multiplied by a deterministic spatial SLR field based on the
published Baxter et al. (2005) CIPS 1971-2000 mean contours. December through
February use the published midwinter contour field and March uses the late-winter
field. Representative contour anchors are inverse-distance interpolated with a
175-km distance floor and bounded to the published 8:1-18:1 CONUS range. DJF and
JFM are sums of the already converted monthly snow-depth estimates, so March's
late-winter ratio is applied before JFM is accumulated. The manifest records the
source, years, anchors, interpolation, bounds, and limitations. This is a
climatological snow-depth estimate; monthly CFSv2 cannot resolve event-scale
crystal habit, melting, wind compaction, or settling.

Source references: [CIPS interactive climatology](https://www.eas.slu.edu/CIPS/SLR/slrmap.htm)
and [Baxter et al. (2005)](https://doi.org/10.1175/WAF856.1).

`snow_water_equivalent_anomaly` is quarantined from production. The available
NCEI flux-calibration `WEASD` field is effectively zero and is not a valid
snowpack-state climatology, so subtracting it makes the result look like an
anomaly while actually showing the absolute forecast. Retained SWE runs and
assets are purged during publication. The decoder and conversion code remain
only to support development of a verified CFS reforecast-derived baseline;
the workflow and site do not expose the product until that baseline exists.

The map uses a centered ECMWF-style Lambert Conformal Conic frame. The
projected window includes Alaska, Canada, the United States, Mexico, and all
of Greenland; the anomaly field remains continuous in the rectangular frame,
while border drawing is limited to the North America/Greenland window so
South America and unrelated eastern-Atlantic outlines do not appear.

`--lead-months "1,2,3"` writes individual target-month maps. Adding
`--seasonal-window "1,2,3"` also writes a seasonal map. Snowfall can request
more than one window in one run with semicolons, such as
`--seasonal-window "3,4,5;4,5,6"`. Height uses the mean of the selected forecast
months and corresponding mean baseline; precipitation uses the total across the
selected months and corresponding total baseline; derived snowfall uses the
accumulated total of the selected monthly departures. A three-month snowfall
departure therefore remains inches of liquid-water-equivalent accumulation,
not an arithmetic mean of monthly inch values.

The scheduled site workflow uses a six-day lagged initial-condition window.
CFSv2 is run four times per day, so this produces 24 six-hourly members using
`monthly_grib_01` from each cycle. Six days leaves practical margin inside the
seven-day NOMADS real-time archive when the latest usable anchor is delayed.
The workflow checks all four daily cycles at 05:45, 11:45, 17:45, and 23:45
UTC, approximately 11 hours 45 minutes after the corresponding 18Z, 00Z, 06Z,
and 12Z initializations. A minimum-age gate ignores the newer cycle directory
whose monthly files are not expected yet, while the existing 30-minute retry
allows for a modest publication delay without silently selecting a partial
anchor.
The workflow still records and retains decoded grids, and explicitly saves the
rolling cache after failed attempts so a transient source problem does not
discard useful state. It probes the required monthly GRIB2 files for every
selected product and lead, chooses the newest ready anchor, runs every
scheduled product, and fails closed without publishing an incomplete blend.
Within one scheduled invocation, raw and decoded cycle files are retained so
the 850-mb and snowfall products reuse the same CFSv2 downloads as the other
fields. A cleanup step removes only the temporary per-cycle source directories
before the durable rolling-state cache is saved.

Scheduled and manual `all` runs resolve the snowfall leads from the selected
anchor initialization instead of treating fixed lead numbers as calendar
months. When the full cold season is inside the 1-9 month forecast horizon,
each CFSv2 refresh publishes six snowfall targets: **December**, **January**,
**February**, **March**, the **DJF** accumulated departure, and the **JFM**
accumulated departure. The same six periods are published as climatology-adjusted
estimated snowfall accumulation. For example, a September initialization maps those
targets to leads `3,4,5,6`, with seasonal windows `3,4,5;4,5,6`; an August
initialization maps them to `4,5,6,7` and `4,5,6;5,6,7`. If a scheduled or
manual all-field cycle cannot contain the entire December-March window within
leads 1-9, the workflow keeps the last complete published snowfall run while
continuing to refresh the other CFSv2 fields.

The Actions `workflow_dispatch` menu also provides an `all` choice. Selecting
it uses the same ready-anchor and six-day rolling window, then renders every
manual menu field in one run: both 500-mb views, absolute 500-mb height,
850-mb temperature, 2-m temperature, MSLP, precipitation, and derived
snowfall. The four-times-daily scheduled suite remains limited to the
operational anomaly products listed above.

The GitHub-hosted Actions menus intentionally lock this window to six days
(24 cycles). Free-form 7- or 10-day requests are not offered because the
oldest required cycles can already be outside NOMADS by the time a delayed
anchor is ready. Reusable workflow calls fail immediately with a clear error
if they request more than six days, instead of downloading for several minutes
and failing on an unavailable boundary cycle. Longer experimental windows
remain available through the local CLI only when a complete external rolling
archive has already been populated.

For a shorter manual menu, **CFSv2 Snowfall Graphics** runs the validated
derived **Snowfall departure** (`snowfall_anomaly`), the separate **Estimated
snowfall accumulation** (`snowfall_accumulation`), or both. Its default
`snowfall_suite` plus `operational-winter` preset produces the same
December-March monthly set plus DJF and JFM for both products. Custom calls can
still supply explicit lead months and one or more
semicolon-separated seasonal windows. The menu always uses the archive-safe
24-cycle window, then uploads the standard CFSv2 payload for the central Pages
publisher.

The adapter also supports the CPC-style `--rolling-days 10` window, which
produces 40 cycles, for callers that maintain a durable rolling-state archive
outside the seven-day NOMADS rotation. It is not the automated site default.
The `--allow-partial-rolling` option remains available only for explicitly
marked incomplete smoke products.

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

The twice-daily scheduled workflow runs at 10:35 and 22:35 UTC, after the
nominal 06Z and 18Z source cycles. It downloads the previously published
manifest before each render and readiness-checks the newest listed monthly
files. If that newest cycle is listed before its files finish propagating, the
check retries it for 30 minutes before selecting the newest complete prior
cycle. Readiness uses a lightweight HEAD request and falls back to a one-byte
ranged GET when the NOMADS edge returns a redirect for HEAD. The workflow
reuses a cached `wgrib2` binary and refreshes the 500-mb
height, 850-mb temperature, 2-m temperature, MSLP, precipitation, and
derived snowfall anomaly suite. It retains the current run plus three prior
runs for each parameter and uploads a scoped Pages payload;
`.github/workflows/publish-pages.yml` serializes that payload with WN2 and
SEAS5 output before the single Pages publish. The static CFSv2 viewer exposes
those retained runs through Parameter and Run history selectors; the central
publisher keeps the referenced historical image paths available.
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
totals in inches. For 2-m temperature, use `tmp2m_YYYYMM.csv` or the
corresponding GRIB2 name; CSV temperature baselines must use the decoded
Kelvin units. For 850-mb
temperature, use `tmp850_YYYYMM.csv` or the corresponding GRIB2 name, also in
Kelvin. For MSLP, use
`mslp_YYYYMM.csv` or the corresponding GRIB2 name; CSV pressure baselines must
already be in hPa, while GRIB2 pressure is converted from Pa automatically.

The `--ncei-calibration` option downloads the matching official NCEI CFS
reforecast calibration file for the initialization month/day/cycle and lead.
Height, 850-mb temperature, and MSLP use the pressure-level `pgbf` calibration;
2-m temperature and precipitation use the official `flxf` flux calibration.
Derived snowfall downloads both calibration families and applies the same
three-field derivation to them. Both published calibrations cover
`1982-2010` and are recorded in the manifest; this is separate from the
current CPC display convention of `1991-2020` for the public monthly/seasonal
anomaly pages.

In rolling mode, NCEI calibration uses the anchor initialization's matching
lead to avoid downloading a separate calibration file for every lagged member.
The manifest records this as `baseline.rolling_policy =
anchor_initialization`. A custom target-month baseline directory can be used
when a month-specific 1991-2020 climatology is preferred.

The scheduled workflow also passes `--allow-stale-calibration`. If NCEI returns
a transient error after bounded retries, the adapter may use a cached matching
cycle from the prior seven days. The image header and manifest identify the
requested and used initialization so the fallback is never presented as an
exact current-cycle calibration.

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
default), `850mb_temperature_anomaly`, `2m_temperature_anomaly`, `mslp_anomaly`,
`precipitation_anomaly`, `snowfall_anomaly`, `snowfall_accumulation`,
and `500mb_height_absolute` (a clearly
labelled decoder/source smoke output). The viewer's product selector displays
each product when its run is present in the retained manifest.
The workflow passes `--previous-manifest` and `--retain-runs 4` so the live
viewer can show the current run and three historical runs independently for
each parameter.

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

Generate precipitation anomaly graphics using the same DJF rolling workflow
as the height maps:

```powershell
.\scripts\render_cfsv2.ps1 `
  -Product "precipitation_anomaly" `
  -Init "2026081000" `
  -LeadMonths "4,5,6" `
  -SeasonalWindow "4,5,6" `
  -RollingDays 6 `
  -RollingMember 1 `
  -UseNceiCalibration
```

Generate 2-m temperature anomalies:

```powershell
.\scripts\render_cfsv2.ps1 `
  -Product "2m_temperature_anomaly" `
  -Init "2026081000" `
  -LeadMonths "4,5,6" `
  -SeasonalWindow "4,5,6" `
  -RollingDays 6 `
  -RollingMember 1 `
  -UseNceiCalibration
```

Generate derived snowfall liquid-water-equivalent departures from the same
2-m/850-mb temperature and precipitation members:

```powershell
.\scripts\render_cfsv2.ps1 `
  -Product "snowfall_anomaly" `
  -Init "2026081000" `
  -LeadMonths "4,5,6,7" `
  -SeasonalWindow "4,5,6;5,6,7" `
  -RollingDays 6 `
  -RollingMember 1 `
  -UseNceiCalibration
```

Generate mean-sea-level pressure anomalies:

```powershell
.\scripts\render_cfsv2.ps1 `
  -Product "mslp_anomaly" `
  -Init "2026081000" `
  -LeadMonths "4,5,6" `
  -SeasonalWindow "4,5,6" `
  -RollingDays 6 `
  -RollingMember 1 `
  -UseNceiCalibration
```

Build the optional CPC-style rolling 40-member blend. This requires
`RollingStateDir` to be persisted between runs using a private artifact store
or equivalent durable local directory because it exceeds the live archive:

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
| `aggregation` | Monthly forecast average, monthly 2-m temperature/sea-level pressure mean, monthly precipitation total, or 3-month seasonal total/mean |
| `field` | `z500_anomaly`, `z500`, `t850_anomaly`, `t2m_anomaly`, `mslp_anomaly`, `precipitation_anomaly`, `snowfall_lwe`, or `snowfall_accumulation` |
| `units` | m for height; °C for 850-mb/2-m temperature; hPa for MSLP; in for precipitation, snowfall LWE, and estimated snow depth |
| `raw_field` / `raw_units` | Source GRIB field and units before any conversion |
| `conversion` | Product-specific unit conversion, including calendar-month PRATE totals |
| `baseline` | Baseline file, label, and years when applicable |
| `statistic` | `ensemble_mean` |
| `ensemble_scope` | The selected streams' initialization-cycle scope |
| `ensemble_members` | Number of usable members in the mean |
| `ensemble_expected_members` | Requested member count, including an incomplete rolling window |
| `rolling_window` | Cycle interval, dates, member stream, and expected count when rolling |
| `status` | Decode/render outcome |
| `image` | Relative rendered image path when successful |

The top-level manifest sets `retention.scope` to `per_product` and includes
`retention.max_runs_per_product` and `retention.history_runs_per_product`; the
production workflow sets these to `4` and `3`. The legacy `max_runs` and
`history_runs` aliases remain for viewer compatibility and have the same
per-product meaning.

## Source notes

- [NOAA CFSv2 operational information](https://cfs.ncep.noaa.gov/cfsv2.info/)
- [NCEP CFSv2 technical paper](https://cfs.ncep.noaa.gov/cfsv2.info/CFSv2_paper.pdf)
- [NOAA CFSv2 downloads](https://cfs.ncep.noaa.gov/cfsv2/downloads.html)
- [NOAA NOMADS CFSv2 operational directory](https://nomads.ncep.noaa.gov/pub/data/nccf/com/cfs/prod/)
- [NOMADS CFS pressure-level filter](https://nomads.ncep.noaa.gov/cgi-bin/filter_cfs_pgb.pl)
- [NOAA CFSv2 model page](https://www.ncei.noaa.gov/products/weather-climate-models/climate-forecast-system)
- [NCEI CFS reforecast pressure-level calibration catalog](https://www.ncei.noaa.gov/thredds/catalog/model-cfs_refor_calclim_mm_9m_pgbf/catalog.html)
- [NCEI CFS reforecast flux calibration catalog](https://www.ncei.noaa.gov/thredds/catalog/model-cfs-allfile-reforecast/calibration-climatologies/flux-1982-2010/catalog.html)
- [NOAA NOMADS CFS flux fields](https://nomads.ncep.noaa.gov/cgi-bin/filter_cfs_flx.pl)
- [NCEP CPC CFSv2 seasonal forecasts](https://www.cpc.ncep.noaa.gov/products/CFSv2/CFSv2seasonal.shtml)
- [CFSv2 forecast-file metadata notes](https://www.cpc.ncep.noaa.gov/products/tools/wgrib2/fix_CFSv2_fcst.html)
- [Earth Engine CFSv2 collection](https://developers.google.com/earth-engine/datasets/catalog/NOAA_CFSV2_FOR6H_HARMONIZED)

The Earth Engine collection is useful for its available 6-hourly surface
fields, but it is not the pressure-level `HGT:500 mb` source used here.
