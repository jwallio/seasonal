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
is explicitly marked in the manifest with its available component list.
All component maps, including JMA/MRI-CPS4, and the C3S multi-system mean use
the shared seasonal 500-mb scale of -100 to +100 m with 10 m intervals.

The scheduled suite also renders `snowfall_anomaly` for the C3S centres and
multi-system blend. It is a CONUS monthly total or DJF three-month sum of the
native C3S snowfall anomalous rate, converted to inches of liquid-water
equivalent. This is a snowfall water-equivalent departure, not a snow-depth
estimate; a centre that does not return the field is retained as a failed or
partial component rather than being silently substituted with snowpack.

The GitHub workflow is `.github/workflows/c3s.yml`. It requires the existing
`CDS_API_KEY` repository secret and publishes `c3s_manifest.json` plus images
under `seasonal/c3s/`. Accept the current CDS dataset licence before running
the workflow.

Official sources:

- [C3S seasonal forecasts](https://climate.copernicus.eu/seasonal-forecasts)
- [C3S pressure-level postprocessed dataset](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels)
- [C3S single-level postprocessed dataset](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-single-levels)
