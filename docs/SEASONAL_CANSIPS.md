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
| `snowfall_anomaly` | derived from `AirTemp` at `AGL-2m` + `AirTemp` at `ISBL-0850` + `PrecipRate` at `Sfc` | CanSIPS-derived snowfall liquid-water-equivalent departure in inches over the CONUS | member-level monthly estimate; seasonal total |
| `mslp_anomaly` | `Pressure` at `MSL` | mean sea-level pressure anomaly in hPa | monthly mean; seasonal mean |
| `sea_surface_height_anomaly` | `SeaSfcHeight-Geoid` | sea-surface height anomaly in metres | monthly mean; seasonal mean |

The Datamart provides precipitation as a rate in kg m-2 s-1; the adapter
converts each monthly field to a calendar-month accumulation in millimetres
and then to inches before calculating the anomaly. Pressure is converted from
Pa to hPa. Temperature anomalies are reported in °C because a temperature
difference has the same numerical magnitude in kelvin and Celsius.

## Derived snowfall estimate

CanSIPS v3 does not publish a native snowfall field in the raw Datamart
bundle. The `snowfall_anomaly` product therefore derives a monthly liquid-water
equivalent estimate member by member from the 2-m temperature, 850-hPa
temperature, and surface precipitation-rate files:

```text
precipitation rate × calendar-month seconds ÷ 25.4 × snow fraction
```

The snow fraction uses the season-appropriate land hyperbolic-tangent fit from
Dai (2008)—the DJF fit for December through February—and applies it to the
warmer of the monthly mean 2-m and 850-hPa temperatures. Using the warmer level
is a conservative warm-layer gate: it keeps the surface temperature signal in
ordinary winter profiles while reducing an all-snow bias when a warm layer is
present aloft. The source files provide monthly means, so
this remains a monthly phase estimate rather than an event-by-event sounding
diagnostic; it does not distinguish sleet from freezing rain or reconstruct a
full vertical temperature profile. At high terrain, 850 hPa can be below the
surface, which is another reason to treat this as a transparent estimate rather
than a native snowfall analysis.

For the DJF implementation, the fitted percentage is evaluated as
`clip(-48.2372 * (tanh(0.7449 * (T - 1.0919)) - 1.0209) / 100, 0, 1)`,
where `T` is the warmer temperature in °C. This is the complete fitted curve,
not the earlier piecewise -1/+2 °C approximation. MAM, JJA, and SON monthly
requests use the corresponding land coefficients from Dai's seasonal table.

The 40 member estimates are averaged before the matching 1991-2020 hindcast
climatology is subtracted. Seasonal values sum the monthly departures, so the
result is liquid-water equivalent in inches, not snow depth and not a
snow-to-liquid-ratio product. The map uses the same tight CONUS crop and
lower-48 land mask as the site's other snowfall maps. Monthly maps use
nonlinear bins from -2.0 to +2.0 inches; seasonal/DJF maps use -4.0 to +4.0
inches, with endpoint values clipped at the active range. The labelled
breakpoints are 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, and
4.0 inches on either side of zero; monthly maps stop at 2.0 inches.

The phase relationship is based on [Dai (2008), “Temperature and pressure
dependences of the rain-snow phase transition over land and ocean”](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2008GL033295).
That study describes the land transition as a smooth function of surface
temperature rather than a hard -1/+2 °C cutoff; the 850-hPa field is an
additional warm-layer safeguard used here because CanSIPS publishes it for the
same members and target months.

The manifest records all three input URLs, decoded variable names,
calendar-month conversion, Dai parameters, phase-level choice, member
completeness, and the retained derived-grid path. The raw three-file GRIB2
inputs are intermediate files and are removed after successful decoding.

CanSIPS uses `P00M` through `P11M`. Lead 0 is the initialization month. For
example, an August 2026 initialization uses leads 4, 5, and 6 for December
2026, January 2027, and February 2027; the seasonal aggregate is labelled
`DJF 2027`.

The maps use the shared operational renderer at 1080 pixels wide. Snowfall
sources are tightly cropped below the legend before the optional branding
footer is added. The
500-mb product uses the blue-neutral-red scale from -100 to +100 metres with
10-metre intervals;
850-mb and 2-m temperature products use the shared ±7 °C scale, sea-surface
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
CanSIPS Pages payload. The scheduled bundle includes the derived snowfall
product and installs the GRIB2/xarray decoding dependencies. Retention is
applied independently per product, so the
default `--retain-runs 4` keeps the current run plus three prior runs for each
parameter. The central
`.github/workflows/publish-pages.yml` workflow merges that payload with the
other model payloads before publishing GitHub Pages.

No CanSIPS credential is required for the public ECCC Datamart source.
