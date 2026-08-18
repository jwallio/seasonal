# Deduplicated seasonal super ensemble

The super-ensemble package combines numeric anomaly grids from the seasonal
systems already supported by this repository. It does not average JPEGs and it
does not treat a provider multi-model mean as an additional independent model.

## Membership rule

Each canonical, non-overlapping forecast-family source receives one equal
weight after that source has formed its own ensemble mean. The current core is:

- eight separable C3S systems: ECMWF, UKMO, Météo-France, DWD, CMCC, NCEP,
  JMA, and BOM;
- one ECCC CanSIPS v3 family mean, representing its GEM5.2-NEMO and CanESM5
  members once;
- for 2-m temperature and precipitation only, the three unique NMME component
  fields: NASA GEOS5v2, NCAR CCSM4, and NCAR CESM1.

Standalone SEAS5, CFSv2, and JMA are excluded because the same systems are
already supplied by C3S. C3S ECCC and the ECCC components in NMME are excluded
because CanSIPS represents that family. C3S, NMME, and APCC aggregate means are
not nested into the result. APCC is recorded as unavailable for numerical
inclusion because the current package exposes an overlapping aggregate rather
than separable component grids. The GEOS-S2S-3 package is recorded but excluded
because the current adapter has pre-rendered charts, not numeric fields.

The complete included, missing, and excluded membership is written into
`superensemble_manifest.json` for every product and target.

## Baselines and seasonal windows

The blend uses each source's native anomaly baseline. It is therefore labelled
as a native-model-baseline anomaly blend, not as a common-climatology product.
For a multi-month map, a source must be available in every constituent month.
This intersection rule prevents its weight from silently changing inside DJF.

The workflow renders a partial product only when at least six canonical source
families are available. It never replaces a missing source with an image,
aggregate mean, or duplicate standalone package.

## Run the package

Use **Actions > Deduplicated Seasonal Super Ensemble > Run workflow**. The
scheduled run generates 500-mb height anomalies after the monthly source
release windows. Other shared parameters can be selected manually. The current
cycle plus three older cycles are retained on the seasonal dashboard.

An API-free visual check is available locally:

```powershell
python scripts/superensemble_seasonal.py --synthetic-preview --init 202608 --product 500mb_height_anomaly --output-dir .preview/superensemble --manifest .preview/superensemble_manifest.json
```

Synthetic previews are visibly labelled and must not be published as forecast
guidance.
