# C3S seasonal workflow

`scripts/c3s_seasonal.py` uses the official Copernicus Climate Data Store
postprocessed seasonal datasets. It can render the individual C3S centre
means and an equal-weight multi-system blend in one manifest.

Supported centre IDs are `ecmwf`, `ukmo`, `meteo_france`, `dwd`, `cmcc`,
`ncep`, `jma`, `eccc`, and `bom`. The default system IDs are current-season
defaults and can be overridden with `--systems centre=system,...` when the CDS
catalogue rolls to a new system.

The operational UK Met Office entry uses GloSea6-GC5.1 (`system=610`) for
nominal start dates from April 2026 onward.

The source fields are native C3S bias-adjusted anomalies. They are not
subtracted from the CFSv2 or CanSIPS climatologies. The multi-system product
records its actual available component list and count for every target. A
seasonal blend uses only systems that supplied every month in that window, so
the image label and data always describe the same ensemble.
All component maps, including JMA/MRI-CPS4, and the C3S multi-system mean use
the shared seasonal 500-mb scale of −120 to +120 m in 10-metre intervals.

The scheduled suite also renders `snowfall_anomaly` for the C3S centres and
multi-system blend. It is a CONUS monthly total or DJF three-month sum of the
native C3S snowfall anomalous rate, converted to inches of liquid-water
equivalent. The canonical comparison field remains LWE; the renderer applies
the fixed 10:1 ratio once for the displayed estimated snow-depth departure.
Monthly and seasonal C3S maps use 20 discrete one-inch snow-depth bands from
−10 to +10 inches, with a white near-zero band from −1 to +1 inches. A centre that does not return the field is retained as a
failed or partial component rather than being silently substituted with
snowpack.
Every monthly and seasonal field passes finite-coverage and physical-range QC
before it can be rendered or published.

The release monitor is `.github/workflows/seasonal-release-check.yml`. Starting
at the official 10th/12 UTC C3S window, it polls the CDS catalogue constraints
until all configured centre/system pairs and the full core field suite are
queryable. It also requires native snowfall for the six centres that currently
contribute to the snowfall blend. It dispatches the complete worker only when
the live multi-system manifest is missing a current product.

The rendering worker is `.github/workflows/c3s.yml`. It requires the existing
`CDS_API_KEY` repository secret and publishes `c3s_manifest.json` plus images
under `seasonal/c3s/`. Manual runs can select one parameter or the explicit
`all` suite. The full suite uses a four-wide product matrix and one merge/publish
step, while compact decoded grids are cached for the later super-ensemble run.
CDS client retries are bounded so a transient 5xx response cannot stall a
worker for hours. Accept the current CDS dataset licence before running the
workflow.

Official sources:

- [C3S seasonal forecasts](https://climate.copernicus.eu/seasonal-forecasts)
- [C3S pressure-level postprocessed dataset](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels)
- [C3S single-level postprocessed dataset](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-single-levels)
- [C3S data availability summary](https://confluence.ecmwf.int/pages/viewpage.action?navigatingVersions=true&pageId=638830872)
