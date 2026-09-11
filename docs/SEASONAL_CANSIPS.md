# Seasonal CanSIPS v3 products

The seasonal repository includes a standalone CanSIPS v3 adapter at
[`scripts/cansips_seasonal.py`](../scripts/cansips_seasonal.py).
It publishes a separate viewer at
[`/seasonal/cansips/`](https://jwallio.github.io/seasonal/cansips/) and
also registers the model in the unified seasonal dashboard.

## Source and provenance

The adapter uses the official [ECCC MSC Open Data CanSIPS v3 Datamart
documentation](https://eccc-msc.github.io/open-data/msc-data/nwp_cansips/readme_cansips-datamart_en/)
and its HTTPS forecast and hindcast directories. CanSIPS v3 provides a global
1-degree grid with 40 members: members 1-20 are GEM5.2-NEMO and members 21-40
are CanESM5. The published hindcast period is 1991-2020.

Each monthly target is calculated as:

```text
40-member forecast mean - matching-initialization-month/lead hindcast climatology
```

The manifest records the forecast URL, hindcast years, initialization month,
lead, member count, member-model groups, grid, cache path, and anomaly method.
Raw GRIB2 files are used only as intermediate inputs; the persistent cache
keeps decoded ensemble-mean grids so the monthly workflow does not repeatedly
download the same hindcasts.

## Product and lead mapping

The default workflow renders the complete scalar anomaly bundle. A single
product can still be selected with `--product` when a faster or targeted run
is needed:

| Product | Source field | Display | Reduction |
| --- | --- | --- | --- |
| `500mb_height_anomaly` | `GeopotentialHeight` at `ISBL-0500` | 500-mb height anomaly in metres with height contours in dam | monthly mean; seasonal mean |
| `850mb_temperature_anomaly` | `AirTemp` at `ISBL-0850` | 850-mb temperature anomaly in °C | monthly mean; seasonal mean |
| `2m_temperature_anomaly` | `AirTemp` at `AGL-2m` | 2-m temperature anomaly in °C | monthly mean; seasonal mean |
| `precipitation_anomaly` | `PrecipRate` at `Sfc` | precipitation anomaly in inches | calendar-month total; seasonal total |
| `snowfall_anomaly` | native snowfall anomalies, ECCC C3S systems 4 + 5 | native LWE departure; displayed at 10:1 in inches | equal component mean; seasonal sum |
| `mslp_anomaly` | `Pressure` at `MSL` | mean sea-level pressure anomaly in hPa | monthly mean; seasonal mean |
| `sea_surface_height_anomaly` | `SeaSfcHeight-Geoid` | sea-surface height anomaly in metres | monthly mean; seasonal mean |

The Datamart provides precipitation as a rate in kg m-2 s-1; the adapter
converts each monthly field to a calendar-month accumulation in millimetres
and then to inches before calculating the anomaly. Pressure is converted from
Pa to hPa. Temperature anomalies are reported in °C because a temperature
difference has the same numerical magnitude in kelvin and Celsius.

## Native snowfall (September 2026 replacement)

The snowfall product now uses `snowfall_anomalous_rate_of_accumulation` from
[C3S seasonal postprocessed single levels](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-single-levels).
Both Canadian systems are required: ECCC system 4 (CanESM5.1p1bc) and system 5
(GEM5.2-NEMO), with 20 members each and equal component weights. Their native
snowfall includes the model's own precipitation-phase physics. No AirTemp /
PrecipRate phase estimate is used in the standalone or super-ensemble path.

For each system and target month:

```text
LWE departure (in) = native snowfall anomalous rate (m/s) × month seconds / 0.0254
Canadian departure = (system 4 departure + system 5 departure) / 2
DJF departure = December departure + January departure + February departure
Displayed snowfall departure (in) = LWE departure × 10
```

C3S has already subtracted the matching system/initialization/lead hindcast
climatology using its common **1993–2016** reference. Do not subtract the old
1991–2020 derived climatology a second time. The `--climo-start/end` settings
continue to govern Datamart products; they do not change the native C3S baseline.

The 10:1 ratio remains an explicit depth estimate, not an observed accumulation
or snowpack depth. The maps use the shared nonlinear snow-depth bands from −100 to +100 inches,
with white between −1 and +1 inches, and report the actual displayed clipped
fraction. Data-integrity QC is
not a claim of observed seasonal snowfall forecast skill.

C3S forecast months 1–6 correspond to CanSIPS leads 0–5. September leads 3,4,5
are DJF. The native monthly archive cannot provide February from an August
initialization. It does not substitute a different run or a single Canadian
component when either requested component is unavailable. Canadian releases
normally arrive on the 10th at 12 UTC; the workflow checks on the 10th–14th.

On 2026-09-06 the live catalogue constraints confirmed both systems' native
snowfall, postprocessed snowfall anomalies, and 1993–2016 hindcast snowfall;
the newest forecast initialization was August 2026. September remains pending.
The workflow includes a small real-data check against the newest available
initialization before first publication of the migration.

Legacy monthly-temperature derivation functions and cached files remain for
historical reproducibility. Their old maps are excluded from current manifest
history. `--render-only` uses only the separate native cache. Legacy helpers
include `derived_snowfall_lwe`, `snowfall_fraction_from_temperature_c`,
`load_snowfall_estimate`, `snowfall_hindcast_climatology`, and the
`SNOWFALL_DAI_LAND_DJF_PARAMS` constants; none feed the new operational path.

Sources:
- [C3S Canadian system identities and members](https://confluence.ecmwf.int/spaces/CKB/pages/77213502/Description+of+the+C3S+seasonal+multi-system)
- [Provider confirmation of the anomaly reference period](https://forum.ecmwf.int/t/c3s-seasonal-models-anomaly/1149)
- [Release schedule and availability](https://confluence.ecmwf.int/spaces/CKB/pages/104239050/Summary+of+available+data)

CanSIPS uses `P00M` through `P11M`. Lead 0 is the initialization month. For
example, an August 2026 initialization uses leads 4, 5, and 6 for December
2026, January 2027, and February 2027; the seasonal aggregate is labelled
`DJF 2027`.

The maps use the shared operational renderer at 1080 pixels wide. Snowfall
sources are tightly cropped below the legend before the optional branding
footer is added. The
500-mb product uses the blue–white–yellow–orange–red scale from −120 to +120 metres with
nonuniform reference-image intervals;
850-mb and 2-m temperature products use the shared ±6 °C scale in 0.5 °C bands, sea-surface
temperature uses ±3 °C, MSLP uses ±10 hPa, precipitation uses the operational
brown/green ±8-inch scale, snowfall uses nonlinear blue/brown ±2.0-inch
monthly or ±4.0-inch seasonal scales with the documented breakpoints, and SSH
uses a ±0.50-metre scale with two-decimal labels.

When the production climatology window is `1991-2020`, the 500-mb hindcast
means are also written as compact reference grids under
`public/seasonal/common_reference/1991-2020/`. The unified Compare tab uses
these CanSIPS v3 grids as its shared 1991-2020 reference for CFSv2 and SEAS5;
the CanSIPS native anomaly image already uses the same hindcast mean. This
keeps the common mode scientifically explicit while preserving each model's
native reference option.

## Local usage

Install the repository requirements and make `wgrib2` available for the raw
scalar products. The derived snowfall product uses `xarray`, `cfgrib`, and
`eccodes`, all included in `requirements.txt`. Then render the default
DJF-style window:

```powershell
python scripts/cansips_seasonal.py `
  --init latest `
  --lead-months 4,5,6 `
  --seasonal-window 4,5,6 `
  --cache-dir .cache/cansips `
  --output-dir public/seasonal/cansips `
  --manifest public/seasonal/cansips_manifest.json
```

To render only the derived CanSIPS snowfall maps, use
`--product snowfall_anomaly`; the workflow does not require a native CanSIPS
snowfall field.

Use `--decode-only` to validate Datamart access, member inventory, ensemble
processing, and hindcast climatology without rendering. Use `--climo-start`
and `--climo-end` only for a deliberate smoke test; production maps use the
full published 1991-2020 hindcast period.

## Workflow and viewer

The scheduled/manual workflow is `.github/workflows/cansips.yml`. It restores
the decoded-grid cache, retrieves the previous Pages manifest, renders the
selected product (all scalar products by default), and uploads a scoped
CanSIPS Pages payload. The scheduled bundle includes native snowfall
product and installs the GRIB2/xarray decoding dependencies. Retention is
applied independently per product, so the
default `--retain-runs 4` keeps the current run plus three prior runs for each
parameter. The central
`.github/workflows/publish-pages.yml` workflow merges that payload with the
other model payloads before publishing GitHub Pages.

No credential is required for the public ECCC Datamart products. Native snowfall
uses the existing repository `CDS_API_KEY` secret and accepted C3S/non-European
data terms. Locally set `CDS_API_KEY` or configure `~/.cdsapirc`.


## Estimated snow-depth departure images

Standalone monthly and seasonal snowfall images convert signed LWE departures
to estimated snow depth using a fixed 10:1 snow-to-liquid ratio. The title and
subtitle identify estimated snowfall inches and the ratio. This is not a
calibration: forecast and hindcast reference implicitly use the same fixed ratio.
The scale has one-inch steps from -10 to +10 inches, with white between -1
and +1 inch. Endpoint labels indicate saturation; larger numeric values are
retained. Numeric grids and multi-model comparisons retain LWE units.
Run metadata records image units, ratio, white band, and scale separately.
Existing maps need regeneration to pick up this display change.


## Faster repeat runs

Run normal mode once to populate versioned, checksummed monthly render grids
and finished climatologies. Subsequent normal runs reuse climatologies. Select
`render_only` in Actions (or pass `--render-only`) for styling-only reruns;
missing or damaged grids stop the run rather than triggering model downloads.
The same selected months and baseline years must have been prepared first.
Borders and the published history manifest may still require small downloads.
Datamart products retain these caches. Native snowfall uses a separate GRIB
cache with metadata validation and does not reuse legacy temperature-derived grids.

Actions uses `--decode-workers 2`: a persistent pair of worker processes decodes
legacy temperature/precipitation inputs, while downloads retain their
existing sequential pacing. Use 1 on memory-constrained local machines.
Each successful Actions run saves a fresh cache key and restores the latest
matching prefix, so additional months no longer disappear behind an immutable
cache key. Derived caches use an explicit science version; bump it whenever
the derivation or baseline semantics change. No measured speedup is claimed
until a production run is timed.

## Winter snowfall presentation

Native snowfall always presents DJF and JFM first, followed by December–March monthly maps. These calendar periods override the generic lead/window inputs for snowfall only. For January–March initializations, the winter begins in the preceding December; otherwise it begins in the initialization year. A season is the ensemble-mean **three-month total departure**, not a monthly average. No division by three is applied.

All three months must come from the same initialization and both Canadian models. Months outside CDS leads 1–6 appear as unavailable with an explanation. August supports December and January only; September covers DJF; October covers both DJF and JFM. November is not substituted. The map title is “CanSIPS v3 Snowfall Departure (in)”; the subtitle retains “10:1 snow-depth estimate.”

D+J is also available as an explicitly labeled December–January two-month total departure. It uses the same initialization and both Canadian components, sums the two monthly LWE departures, and applies 10:1 once for display. It is not labeled DJF and is not scaled to stand in for February.
