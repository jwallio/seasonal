# Seasonal Forecast Graphics

This repository builds and publishes the wall.cloud seasonal forecast and
forecast-graphics site: [jwallio.github.io/seasonal](https://jwallio.github.io/seasonal/).
It combines official seasonal-model data, historical analog guidance,
cross-model comparisons, a deduplicated super ensemble, validation, and a
static GitHub Pages viewer.

## What the repository does

### Seasonal model products

The seasonal pipeline downloads or decodes provider data, converts it to
consistent units, computes model-native anomalies, renders monthly and
seasonal maps, writes provenance-rich manifests, and retains recent runs.

Supported model adapters are:

- **CFSv2** — NOAA/NOMADS monthly ensemble fields with selectable custom or
  official NCEI reforecast calibration, including the rolling initial-condition
  blend.
- **ECMWF SEAS5** — official Copernicus CDS seasonal anomalies.
- **CanSIPS v3** — official ECCC MSC Datamart ensemble data and matching
  hindcast anomalies.
- **CMA CPSv3** — official WMO GPC Beijing anomaly data.
- **C3S multi-system** — Copernicus Climate Change Service component systems,
  including JMA/MRI-CPS4 and the other configured centres.
- **APCC MME** — official APCC CLIK multi-model seasonal anomalies.
- **NASA GEOS-S2S-3** — public NASA NCCS numerical and drift-climatology
  archives, subject to strict source and pressure-level checks.

The viewer also includes model-specific pages, a unified Overview, a Compare
view, valid-period controls, blend/family/component filters, run provenance,
coverage status, and quality-control status. Unsupported or incomplete model
products are labelled explicitly rather than silently substituted.

### Seasonal parameters

The common comparison suite includes:

- 500-mb geopotential-height anomalies
- 850-mb temperature anomalies
- 2-metre temperature anomalies
- CONUS precipitation anomalies
- CONUS snowfall liquid-water-equivalent departures
- Mean sea-level pressure anomalies

Additional provider-specific products include snow depth, absolute fields,
sea-surface height, 200-mb fields, probability categories, and other native
parameters where the source supports them.

Shared comparison conventions include fixed cross-provider scales for 500-mb
height and temperature. Precipitation is rendered in accumulated inches.
Snowfall maps from C3S and SEAS5 use native snowfall accumulation rates,
converted to inches of liquid-water equivalent—not snow depth—and provide
CONUS monthly totals and DJF three-month sums. CanSIPS v3 adds a transparent
derived estimate from its paired 2-m temperature and precipitation members;
the super ensemble can include that CanSIPS-derived family vote alongside
native snowfall fields. CanSIPS also uses the paired 850-hPa temperature as a
warm-layer gate with its 2-m temperature. Monthly snowfall maps use nonlinear
bins from -2.0 to +2.0 inches, while seasonal/DJF maps use -4.0 to +4.0
inches—finer near zero and wider in the tails—to reduce clipping while
preserving the smaller LWE signal. Models without a
native or explicitly derived snowfall field remain explicitly not applicable.
CFSv2 refreshes its derived snowfall suite for December through March and
publishes accumulated DJF and JFM departures whenever that complete cold-season
window is inside the model's 1-9 month horizon. It also publishes separate
estimated snow-depth accumulations for the same six periods by multiplying each
month's derived snowfall LWE by a bounded, spatially varying seasonal SLR field
based on the published 1971-2000 CIPS/Baxter climatology. These maps are labelled
as climatological estimates and are not storm-scale ratio forecasts.

The established 500-mb map retains its North America framing, with a separate
Northern Hemisphere 500-mb view available where the source provides 500-mb
heights. Other seasonal map fields use a CONUS frame.

## Historical analog guidance

The analog workflows identify historical matches for current seasonal patterns
and produce both rank-1 reference maps and weighted top-five composites.

They support:

- CFSv2 and deduplicated super-ensemble 500-mb pattern analogs
- PSL NCEP/CFSR 500-mb height and 2-metre temperature anomaly maps
- WRIT 20CRv3 handling for pre-1979 analog dates
- MRCC/ACIS station-interpolated snowfall departures
- Monthly and DJF analog periods
- Centered, domain-fitted eastern-U.S. snowfall maps through the Great Lakes
  and Southeast
- Inverse similarity-distance weighting using pattern and amplitude similarity
- Bicubic display resampling with light sub-grid filtering for coarse WRIT data
- 0.25-degree snowfall interpolation with recorded SciPy/NumPy fallback
- Retained last-good products and stale-source status when a provider fails

The analog products and their source URLs are recorded in
`seasonal/analog_products_manifest.json`.

## Super ensemble and comparison catalog

The super-ensemble creates a transparent, deduplicated blend of eligible model
families. It avoids double-counting duplicate C3S/NMME copies, uses the
standalone rolling CFSv2 blend as the CFSv2-family vote, and applies explicit
availability and support rules.

Before publication, `scripts/build_seasonal_catalog.py --strict` validates:

- timestamps and valid periods
- pressure levels and field identity
- units and aggregation metadata
- probability integrity
- safe asset paths
- image existence
- numerical range and clipping QC
- model/product support and intentional unavailability

The resulting `seasonal/catalog.json` drives the dashboard and its health and
coverage summaries. Missing or invalid metadata fails closed before publishing.

## Automation and publishing

Release-aligned GitHub Actions workflows are provided for CFSv2, SEAS5, C3S,
JMA/MRI-CPS4, CanSIPS, CMA CPSv3, APCC, NASA GEOS-S2S-3, NMME, the
super-ensemble, seasonal analogs, and the central Pages publisher.

Provider schedules and UTC automation timing are documented in
[`docs/SEASONAL_SCHEDULES.md`](docs/SEASONAL_SCHEDULES.md).
CDS-backed SEAS5, C3S, and JMA refreshes are readiness-driven by
[`seasonal-release-check.yml`](.github/workflows/seasonal-release-check.yml),
which checks the live CDS inventory and published manifests before dispatching
a full rendering suite.
The broader UI, map, workflow, and runner review is recorded in the
[`2026-08-30 Seasonal product audit`](docs/SEASONAL_PRODUCT_AUDIT_2026-08-30.md).

Model workflows upload scoped payloads. The serialized
[`publish-pages.yml`](.github/workflows/publish-pages.yml) workflow merges
successful payloads with retained site assets, rebuilds the catalog and
thumbnails, validates the complete tree, and publishes the static site without
allowing one model update to remove another model's products.

## Repository layout

- `main.py` — primary forecast-graphics renderer
- `public/` — static viewer, generated manifests, and published assets
- `public/seasonal/` — seasonal dashboard and model-specific viewers
- `scripts/seasonal_products.py` — canonical product, units, scale, support,
  and QC registry
- `scripts/*_seasonal.py` — provider adapters
- `scripts/build_seasonal_catalog.py` — strict catalog and health builder
- `scripts/build_seasonal_thumbnails.py` — deterministic Compare thumbnails
- `scripts/build_analog_products.py` — analog maps and weighted composites
- `scripts/build_seasonal_analogs.py` and `scripts/seasonal_analogs.py` —
  analog selection and workflow support
- `.github/workflows/` — scheduled model, analog, validation, and publishing
  workflows
- `docs/` — provider methods, data sources, schedules, and limitations
- `tests/` — provider contracts, workflow contracts, catalog validation,
  analog tests, dashboard tests, and smoke tests

Provider documentation:

[`CFSv2`](docs/SEASONAL_CFSV2.md) ·
[`SEAS5`](docs/SEASONAL_SEAS5.md) ·
[`C3S`](docs/SEASONAL_C3S.md) ·
[`CanSIPS`](docs/SEASONAL_CANSIPS.md) ·
[`CMA CPSv3`](docs/SEASONAL_CMA_CPSV3.md) ·
[`JMA/MRI-CPS4`](docs/SEASONAL_JMA.md) ·
[`APCC`](docs/SEASONAL_APCC.md) ·
[`GEOS-S2S-3`](docs/SEASONAL_GEOS_S2S3.md) ·
[`NMME`](docs/SEASONAL_NMME.md) ·
[`Super ensemble`](docs/SEASONAL_SUPERENSEMBLE.md)

## Legacy forecast graphics

The repository also retains the general forecast-graphics renderer in
`main.py`. It can render North America and Northern Hemisphere height fields,
CONUS pressure/precipitation-type, vorticity, temperature, wind, vertical
velocity, SST, temperature-anomaly, and regional snowfall-accumulation
products. Snow ratios, forecast-hour selections, ensemble mode, climatology,
map styling, geography detail, worker count, and output dimensions are
configurable through environment variables and the custom workflows.

## Local setup

The seasonal adapters require Python 3.11 and the packages in
[`requirements.txt`](requirements.txt). Some provider adapters additionally
require their source credentials or optional decoding tools.

```bash
pip install -r requirements.txt
```

Common credentials are supplied through GitHub Actions secrets rather than
committed files, including `CDS_API_KEY`, `APCC_API_KEY`, `EE_PROJECT`, and
`EE_KEY` where applicable.

Examples of seasonal helper commands are available in:

- [`scripts/render_cfsv2.ps1`](scripts/render_cfsv2.ps1)
- [`scripts/render_seas5.ps1`](scripts/render_seas5.ps1)
- [`scripts/render_cansips.ps1`](scripts/render_cansips.ps1)
- [`scripts/run_custom.ps1`](scripts/run_custom.ps1)
- [`scripts/render_and_test.ps1`](scripts/render_and_test.ps1)

## Validation

Run the focused contracts or the full test collection with Python. The
repository includes checks for every provider, the dashboard, schedules,
catalog integrity, analog selection and rendering, thumbnails, and generated
output smoke tests.

```bash
python tests/test_pipeline_contract.py
python tests/test_seasonal_catalog.py
python tests/test_seasonal_dashboard_contract.py
python tests/test_seasonal_share_images.py
python tests/test_seasonal_thumbnails.py
python tests/test_seasonal_release_check.py
python tests/test_seasonal_schedules.py
python tests/smoke_outputs.py
```

## License and data provenance

The repository preserves source URLs, provider labels, run metadata, baseline
definitions, and conversion notes in its manifests and documentation. Source
data remains subject to each provider's terms and licences. Generated maps,
analysis logic, composites, metadata, and site presentation should not be
redistributed or used to create a competing service without permission.

