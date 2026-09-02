# JMA/MRI-CPS4 seasonal graphics

The JMA workflow renders the JMA component of the Copernicus Climate Change
Service (C3S) seasonal forecast archive. It uses the official C3S
post-processed anomaly datasets rather than scraping the JMA web graphics.

## Current system

- Producing centre: Japan Meteorological Agency (JMA)
- Model: JMA/MRI-CPS4
- C3S system: `4`
- C3S hindcast period: `1991-2020`
- C3S qualifying forecast subset: 55 members for CPS4
- Initial workflow target: leads 4, 5, and 6, plus the matching seasonal mean

The system and hindcast metadata follow the [C3S multi-system description](https://confluence.ecmwf.int/spaces/CKB/pages/77213502/Description%2Bof%2Bthe%2BC3S%2Bseasonal%2Bmulti-system), the [C3S data availability summary](https://confluence.ecmwf.int/spaces/CKB/pages/638830872/Summary%2Bof%2Bavailable%2Bdata), and the [JMA CPS4 contribution notes](https://confluence.ecmwf.int/spaces/CKB/pages/639220545/Description%2Bof%2Bcps4-v20260101%2BC3S%2Bcontribution).

## Workflow

`.github/workflows/seasonal-release-check.yml` begins checking JMA/System 4 at
the 10th/12 UTC C3S release window. It dispatches `.github/workflows/jma.yml`
as soon as every JMA field and lead used by the suite appears in the CDS
catalogue and the live JMA manifest is incomplete. JMA is evaluated separately
from the full C3S blend, so another late centre does not hold it back. The
worker can also be started manually from Actions to choose:

- 500-mb height, 850-mb temperature, 2-m temperature, precipitation, or MSLP anomaly
- initialization month or `latest`
- target lead months
- seasonal lead-month window
- an optional `jma=system` override for historical troubleshooting
- the explicit `all` suite for a complete repair

The workflow calls `scripts/c3s_seasonal.py --centres jma --no-blend`, so the
standalone JMA page contains only the JMA component. The shared C3S workflow
continues to publish all configured centres and its multi-system mean.
Its 500-mb height-anomaly maps use the shared seasonal -100 to +100 m scale
with 10 m intervals.

## Outputs and retention

The renderer writes `public/seasonal/jma/` and
`public/seasonal/jma_manifest.json`. The manifest keeps the current cycle and
three prior initialization cycles. Failed or unavailable targets remain
visible in the manifest instead of being silently replaced with empty maps.

The source is the C3S [seasonal forecast service](https://climate.copernicus.eu/seasonal-forecasts),
using the [pressure-level](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels)
and [single-level](https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-single-levels)
datasets. A valid `CDS_API_KEY` repository secret and acceptance of the current
dataset terms are required.
