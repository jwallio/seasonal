# C3S seasonal workflow

`scripts/c3s_seasonal.py` uses the official Copernicus Climate Data Store
postprocessed seasonal datasets. It can render the individual C3S centre
means and an equal-weight multi-system blend in one manifest.

Supported centre IDs are `ecmwf`, `ukmo`, `meteo_france`, `dwd`, `cmcc`,
`ncep`, `jma`, `eccc`, and `bom`. The default system IDs are current-season
defaults and can be overridden with `--systems centre=system,...` when the CDS
catalogue rolls to a new system.

The source fields are native C3S bias-adjusted anomalies. They are not
subtracted from the CFSv2 or CanSIPS climatologies. The multi-system product
is explicitly marked in the manifest with its available component list.

The GitHub workflow is `.github/workflows/c3s.yml`. It requires the existing
`CDS_API_KEY` repository secret and publishes `c3s_manifest.json` plus images
under `seasonal/c3s/`. Accept the current CDS dataset licence before running
the workflow.

Official sources:

- [C3S seasonal forecasts](https://climate.copernicus.eu/seasonal-forecasts)
- [C3S pressure-level postprocessed dataset](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels)
- [C3S single-level postprocessed dataset](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-single-levels)
