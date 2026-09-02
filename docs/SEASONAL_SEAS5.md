# Seasonal ECMWF SEAS5 products

WN2 includes a standalone SEAS5 adapter at
[`scripts/seas5_seasonal.py`](../scripts/seas5_seasonal.py).
It uses the current ECMWF/System 51 products distributed by the official
Copernicus Climate Data Store (CDS) and publishes a separate viewer at
[`/seasonal/seas5/`](https://jwallio.github.io/seasonal/seas5/).

## Source and provenance

The adapter requests only the selected initialization month, lead month, and
North American area from the CDS API:

- [`seasonal-postprocessed-pressure-levels`](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels)
  supplies the 500-mb geopotential anomaly.
- [`seasonal-postprocessed-single-levels`](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-single-levels)
  supplies the surface anomalies.
- [`seasonal-monthly-pressure-levels`](https://cds.climate.copernicus.eu/datasets/seasonal-monthly-pressure-levels)
  supplies raw 500-mb geopotential for the contour overlay and the optional
  absolute-field smoke output.

The request is filtered to originating centre `ecmwf`, system `51`, and the
`ensemble_mean` product. ECMWF's direct dissemination schedule releases the
7-month SEAS5 forecast on the 5th at 12 UTC, but this adapter consumes the C3S
Climate Data Store rather than direct ECMWF member dissemination. The official
C3S availability table places ECMWF data in CDS on the 6th at 12 UTC. The
nominal initialization date is the first day of the released month, matching
the C3S seasonal data convention.

The workflow requires a repository secret named `CDS_API_KEY`. Local runs can
use the same token through `CDS_API_KEY` or the official `~/.cdsapirc` file.
The current SEAS5 dataset terms must be accepted once in the user's CDS
account before the first download. The acceptance page is the dataset's
Download tab: [manage CDS licences](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels?tab=download#manage-licences).

## Products

| Product | CDS variable | Display | Reduction |
| --- | --- | --- | --- |
| `500mb_height_anomaly` | `geopotential_anomaly` at 500 hPa | height anomaly in m; contours in dam | monthly/seasonal mean |
| `2m_temperature_anomaly` | `2m_temperature_anomaly` | anomaly in °C | monthly/seasonal mean |
| `850mb_temperature_anomaly` | `temperature_anomaly` at 850 hPa | anomaly in °C | monthly/seasonal mean |
| `precipitation_anomaly` | `total_precipitation_anomalous_rate_of_accumulation` | CONUS total anomaly in inches | monthly/seasonal total |
| `snowfall_anomaly` | `snowfall_anomalous_rate_of_accumulation` | CONUS liquid-water-equivalent anomaly in inches | monthly/seasonal total |
| `snow_depth_anomaly` | `snow_depth_anomaly` | CONUS snow-depth water-equivalent anomaly in inches | monthly/seasonal mean |
| `mslp_anomaly` | `mean_sea_level_pressure_anomaly` | anomaly in hPa | monthly/seasonal mean |

The CDS anomaly datasets are already bias-adjusted monthly ensemble-mean
anomalies. WN2 therefore does not subtract a second local hindcast
climatology. Geopotential is divided by standard gravity (`9.80665 m s⁻²`);
precipitation and snowfall anomaly rates are multiplied by the actual seconds
in the target month and converted from metres to inches.

The 500-mb anomaly fill uses the shared seasonal -100 to +100 m range with
10 m intervals, matching the other verified 500-mb model maps and preserving
more contrast for the relatively small seasonal signal.

For the unified dashboard's Compare tab, the 500-mb product also supports a
shared reference mode. The workflow reads the CanSIPS v3 1991-2020 hindcast
mean grid published under `seasonal/common_reference/1991-2020/`, regrids it
to the SEAS5 axes, and subtracts it from the raw SEAS5 500-mb height field.
Those images are labeled `Common 1991-2020 reference (CanSIPS v3 hindcast)`.
The native C3S postprocessed anomaly remains the default fallback when a
common-reference image is unavailable; this does not change the native SEAS5
anomaly methodology.

## Local usage

Install the repository requirements and configure CDS credentials. Then render
the default DJF-style lead window:

```powershell
.\scripts\render_seas5.ps1 `
  -Init "latest" `
  -LeadMonths "4,5,6" `
  -SeasonalWindow "4,5,6"
```

Render a different parameter:

```powershell
.\scripts\render_seas5.ps1 `
  -Product "precipitation_anomaly" `
  -Init "latest" `
  -LeadMonths "4,5,6" `
  -SeasonalWindow "4,5,6"
```

Use `-DecodeOnly` to validate CDS retrieval and GRIB decoding without building
images. Use `-NoBorders` for a source-only smoke test. The workflow and local
wrapper retain the current run plus three prior runs per parameter in the
manifest.

## Release checker, workflow, and viewer

`.github/workflows/seasonal-release-check.yml` polls the three relevant CDS
catalogue constraints documents from 12 UTC on the 6th. Once ECMWF/System 51,
all required fields, and leads 4-6 are listed, it compares the target month
against the live `seas5_manifest.json` and dispatches the full suite only when
needed. Polling continues hourly through the 9th if CDS indexing is late, and
the shared daily catch-up continues through month-end for an incomplete suite.

The rendering worker is `.github/workflows/seas5.yml`. It restores the CDS
GRIB cache, retrieves the previous Pages manifest, renders the selected
parameter or explicit `all` suite, and uploads a scoped Pages payload. The central
`.github/workflows/publish-pages.yml` workflow serializes successful WN2,
CFSv2, and SEAS5 payloads, merges each payload into the existing `gh-pages`
tree, and performs the only GitHub Pages publish.

The unified seasonal dashboard at `/seasonal/` provides one model and
parameter control surface for WeatherNext 2, CFSv2, and SEAS5. The direct
SEAS5 viewer remains available at `/seasonal/seas5/` so model, source,
initialization, and anomaly methodology can still be reviewed without the
cross-model controls.

## Source notes

- [C3S seasonal forecast monthly pressure-level data](https://cds.climate.copernicus.eu/datasets/seasonal-monthly-pressure-levels)
- [C3S seasonal forecast pressure-level anomalies](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels)
- [C3S seasonal forecast single-level anomalies](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-single-levels)
- [CDS API setup](https://cds.climate.copernicus.eu/how-to-api)
- [ECMWF C3S seasonal forecast service](https://www.ecmwf.int/en/forecasts/datasets/c3s-seasonal-forecasts)
- [C3S data availability summary](https://confluence.ecmwf.int/pages/viewpage.action?navigatingVersions=true&pageId=638830872)
