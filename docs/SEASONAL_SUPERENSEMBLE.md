# Deduplicated seasonal super ensemble

The super-ensemble package combines numeric anomaly grids from the seasonal
systems already supported by this repository. It does not average JPEGs and it
does not treat a provider multi-model mean as an additional independent model.

## Membership rule

Each canonical, non-overlapping forecast-family source receives one equal
weight after that source has formed its own ensemble mean. The current core is:

- seven separable C3S systems: ECMWF, UKMO, Météo-France, DWD, CMCC, JMA,
  and BOM;
- one NOAA CFSv2 family mean formed from the standalone six-day, 24-cycle
  rolling initial-condition blend for 500-mb height, 2-m temperature,
  precipitation, snowfall, and MSLP;
- one ECCC CanSIPS v3 family mean, representing its GEM5.2-NEMO and CanESM5
  members once;
- one 21-member CMA CPSv3 family mean from the WMO GPC Beijing package when
  every requested target is within redistributed forecast months 1-3;
- one NASA GEOS-S2S-3 lag/burst family mean for its validated temperature,
  precipitation, and MSLP products;
- for 2-m temperature and precipitation only, the two remaining unique NMME
  component fields: NCAR CCSM4 and NCAR CESM1.

For products supported by the standalone CFSv2 adapter, C3S NCEP System 2 and
NMME CFSv2 are excluded so the rolling blend receives the family's only vote.
For 850-mb temperature, the current standalone adapter has no numeric product,
so C3S NCEP remains the single CFSv2-family source. Standalone SEAS5
and JMA are excluded because those systems are supplied by C3S. C3S ECCC and
the ECCC components in NMME are excluded because CanSIPS represents that
family. The NMME NASA_GEOS5v2 copy is excluded because the standalone
GEOS-S2S-3 numerical adapter supplies the NASA-family vote. C3S, NMME, and APCC aggregate means are not nested into the result.
APCC is recorded as unavailable for numerical inclusion because the current
package exposes an overlapping aggregate rather than separable component
grids. GEOS-S2S-3 remains excluded from 500-mb height because NASA's current
long-range archive named `z500` declares 200 hPa and fails the adapter's
strict pressure-level check.

Snowfall has a seven-family operational roster: five C3S systems (ECMWF,
UKMO, Météo-France, DWD, and CMCC), the standalone rolling CFSv2 derivation,
and the CanSIPS v3 derivation. C3S JMA and BOM are excluded because the
provider returns no postprocessed snowfall-anomaly field for those systems;
they are recorded as unsupported rather than making every valid snowfall
blend appear permanently partial.

CMA CPSv3 is target-aligned rather than assumed to be available at every
horizon. The source system runs seven months, but WMO redistributes only
forecast months 1-3. A request containing any later lead therefore uses the
existing canonical roster and records no CMA vote; a request wholly inside
leads 1-3 includes CMA once. The package never extrapolates CMA or mixes a
changing CMA membership inside one seasonal mean.

The complete included, missing, and excluded membership is written into
`superensemble_manifest.json` for every product and target. Each rendered map
also names the families that actually contributed in a footer; a partial map's
footer therefore omits any unavailable family rather than showing the full
expected roster.

## Baselines and seasonal windows

The blend uses each source's native anomaly baseline. It is therefore labelled
as a native-model-baseline anomaly blend, not as a common-climatology product.
The blended 500-mb map uses the same -100 to +100 m scale and 10 m intervals
as its verified component maps.
The rolling CFSv2 contribution uses its official NCEI CFS reforecast
calibration climatology and records its anchor cycle, available/expected cycle
count, source files, and calibration URL in the manifest. Its `latest` anchor
is aligned to the requested target months, so a calendar-month rollover does
not strand the blend on an obsolete CFSv2 cycle while monthly systems await
their next release. An explicitly supplied anchor remains constrained to the
shared monthly release for reproducible backfills.
The GEOS-S2S-3 contribution uses NASA's lead- and initialization-month-matched
provider drift climatology and records its lagged initialization dates,
available members, archive, and drift source.
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
