# Seasonal CanSIPS v3 products

WN2 includes a standalone CanSIPS v3 adapter at
[`scripts/cansips_seasonal.py`](/d:/weather-projects/wn2/scripts/cansips_seasonal.py).
It publishes a separate viewer at
[`/seasonal/cansips/`](https://jwallio.github.io/wn2/seasonal/cansips/) and
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
| `precipitation_anomaly` | `PrecipRate` at `Sfc` | precipitation anomaly in inches | calendar-month total; seasonal mean |
| `mslp_anomaly` | `Pressure` at `MSL` | mean sea-level pressure anomaly in hPa | monthly mean; seasonal mean |
| `sst_anomaly` | `WaterTemp` at `Sfc` | sea-surface temperature anomaly in °C | monthly mean; seasonal mean |
| `sea_surface_height_anomaly` | `SeaSfcHeight-Geoid` | sea-surface height anomaly in metres | monthly mean; seasonal mean |

The Datamart provides precipitation as a rate in kg m-2 s-1; the adapter
converts each monthly field to a calendar-month accumulation in millimetres
and then to inches before calculating the anomaly. Pressure is converted from
Pa to hPa. Temperature anomalies are reported in °C because a temperature
difference has the same numerical magnitude in kelvin and Celsius.

CanSIPS uses `P00M` through `P11M`. Lead 0 is the initialization month. For
example, an August 2026 initialization uses leads 4, 5, and 6 for December
2026, January 2027, and February 2027; the seasonal aggregate is labelled
`DJF 2027`.

The maps use the shared operational 1080x1080 North American renderer. The
500-mb product uses the blue-neutral-red scale from -100 to +100 metres with
10-metre intervals;
850-mb and 2-m temperature products use the shared ±7 °C scale, sea-surface
temperature uses ±3 °C, MSLP uses ±10 hPa, precipitation uses the operational
brown/green ±8-inch scale, and SSH uses a ±0.50-metre scale with two-decimal
labels.

When the production climatology window is `1991-2020`, the 500-mb hindcast
means are also written as compact reference grids under
`public/seasonal/common_reference/1991-2020/`. The unified Compare tab uses
these CanSIPS v3 grids as its shared 1991-2020 reference for CFSv2 and SEAS5;
the CanSIPS native anomaly image already uses the same hindcast mean. This
keeps the common mode scientifically explicit while preserving each model's
native reference option.

## Local usage

Install the repository requirements and make `wgrib2` available. Then render
the default DJF-style window:

```powershell
python scripts/cansips_seasonal.py `
  --init latest `
  --lead-months 4,5,6 `
  --seasonal-window 4,5,6 `
  --cache-dir .cache/cansips `
  --output-dir public/seasonal/cansips `
  --manifest public/seasonal/cansips_manifest.json
```

Use `--decode-only` to validate Datamart access, member inventory, ensemble
processing, and hindcast climatology without rendering. Use `--climo-start`
and `--climo-end` only for a deliberate smoke test; production maps use the
full published 1991-2020 hindcast period.

## Workflow and viewer

The scheduled/manual workflow is `.github/workflows/cansips.yml`. It restores
the decoded-grid cache, retrieves the previous Pages manifest, renders the
selected product (all scalar products by default), and uploads a scoped
CanSIPS Pages payload. Retention is applied independently per product, so the
default `--retain-runs 4` keeps the current run plus three prior runs for each
parameter. The central
`.github/workflows/publish-pages.yml` workflow merges that payload with WN2,
CFSv2, and SEAS5 before publishing GitHub Pages.

No CanSIPS credential is required for the public ECCC Datamart source.
