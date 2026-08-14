# Seasonal ECMWF SEAS5 products

WN2 now includes a standalone SEAS5 adapter at
[`scripts/seas5_seasonal.py`](/d:/weather-projects/wn2/scripts/seas5_seasonal.py).
It reads the public Planette/AWS cloud-native copy of the Copernicus Climate
Change Service archive and publishes a separate viewer at
[`/seasonal/seas5/`](https://jwallio.github.io/wn2/seasonal/seas5/).

## Source and provenance

The source is the public
[`planette-c3s-seasonal-forecasts`](https://registry.opendata.aws/planette_c3s_seasonal_forecast_data/)
S3 bucket. It contains ECMWF SEAS5 daily ensemble fields at 1° global
resolution. SEAS5 hindcasts cover 1981–2016 and real-time forecast stores are
organized by initialization year. Access is anonymous and does not require AWS
credentials.

Each yearly variable store is an Icechunk-backed Zarr dataset with
`init_time`, `number`, `lead`, `valid_time`, `lat`, and `lon` dimensions. The
adapter averages all available forecast members and daily values in the
selected calendar month. It does not treat the archive as CFSv2 or use the
WN2 Earth Engine climatology.

## Products

| Product | Archive variable | Display | Reduction |
| --- | --- | --- | --- |
| `500mb_height_anomaly` | `z500` | geopotential-height anomaly in m; height contours in dam | monthly/seasonal mean |
| `2m_temperature_anomaly` | `t2m` | anomaly in °C | monthly/seasonal mean |
| `precipitation_anomaly` | `pr` | CONUS total anomaly in inches | monthly/seasonal total |
| `snowfall_anomaly` | `sf` | CONUS liquid-water-equivalent anomaly in inches | monthly/seasonal total |
| `sst_anomaly` | `sst` | sea-surface-temperature anomaly in °C | monthly/seasonal mean |
| `mslp_anomaly` | `slp` | mean-sea-level-pressure anomaly in hPa | monthly/seasonal mean |

The `z500` archive field is geopotential in `m² s⁻²`; it is divided by
standard gravity (`9.80665 m s⁻²`) before plotting. The precipitation and
snowfall variables are rates; each is multiplied by the actual number of
seconds in its target month and converted from liquid-water millimetres to
inches before aggregation.

## Anomaly baseline

Anomalies use a matched SEAS5 hindcast climatology. For every target month,
the adapter selects hindcasts with the same initialization month and averages
the same target calendar month across the requested hindcast years, ensemble
members, and daily leads. The default baseline is the full available
`1981-2016` hindcast period. Baseline grids are cached under `.cache/seas5`
and the manifest records the years actually used.

This is a model climatology, not an ERA5 or CPC observed climatology. The
adapter records the source and baseline explicitly in both the image header
and `public/seasonal/seas5_manifest.json`.

## Local usage

Install the repository requirements, including `xarray`, `zarr`, `icechunk`,
and `dask[array]`. Then render the default 500-mb DJF-style lead window:

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

Use `-DecodeOnly` to validate public source access and forecast fields without
building climatologies or images. Use `-NoBorders` for a source-only smoke
test. The workflow and local wrapper retain the current run plus three prior
runs in the manifest.

## Workflow and viewer

The scheduled/manual workflow is
`.github/workflows/seas5.yml`. It restores cached climatology grids, retrieves
the previous Pages manifest, renders the selected parameter, and uploads a
scoped Pages payload. The central `.github/workflows/publish-pages.yml`
workflow serializes successful WN2, CFSv2, and SEAS5 payloads, merges each
payload into the existing `gh-pages` tree, and performs the only GitHub Pages
publish. The viewer is intentionally separate from CFSv2 so model, source,
initialization, and baseline metadata cannot be confused.

## Source notes

- [Planette C3S Seasonal Forecast Data registry](https://registry.opendata.aws/planette_c3s_seasonal_forecast_data/)
- [Planette archive documentation](https://github.com/PlanetteAI/planette_c3s_archive/blob/main/README.md)
- [ECMWF seasonal forecasts](https://www.ecmwf.int/en/forecasts/documentation-and-support/seasonal)
- [C3S seasonal forecasts](https://climate.copernicus.eu/seasonal-forecasts)
