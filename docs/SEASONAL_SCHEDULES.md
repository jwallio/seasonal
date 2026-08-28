# Seasonal model release schedules

The scheduled workflows use UTC because that is how GitHub Actions interprets
cron expressions. Each run starts after the provider's normal release window,
leaving a small buffer for source indexing and mirror propagation. Every
workflow remains manually runnable from **Actions > Run workflow** when a
provider is late or a historical cycle needs to be regenerated.

| Workflow | Provider release window | Automatic run (UTC) |
| --- | --- | --- |
| CFSv2 | New model cycles every 6 hours | Twice daily at 10:35 and 22:35, after the 06Z and 18Z cycles; readiness-gated with a two-hour readiness retry |
| CanSIPS v3 | ECCC monthly refresh on the 1st | 2nd of each month at 16:30 |
| CMA CPSv3 | WMO GPC Beijing exchange window on the 15th-20th | 21st of each month at 18:30 |
| NOAA NMME | CPC public products update on the 9th | 9th of each month at 15:30 |
| ECMWF SEAS5 | ECMWF 7-month forecast on the 5th at 12:00 | 5th of each month at 15:30 |
| C3S multi-system | Non-ECMWF systems on the 10th at 12:00 | 10th of each month at 15:30 |
| JMA / MRI-CPS4 | JMA is a non-ECMWF C3S component | 10th of each month at 15:30 |
| APCC MME | APCC seasonal MME around the middle of the month | 20th of each month at 16:30 |
| NASA GEOS-S2S-3 | Public NCCS numerical APCN archives during the first week | 6th of each month at 16:30 |
| Deduplicated super ensemble | After APCC, CMA, and the other component source windows | 22nd of each month at 20:30 |
| 500-mb pattern analogs and top-analog maps | After each successful CFSv2 or super-ensemble release | Source-triggered, with scheduled reconciliation at 02:35 and 14:35 |

Scheduled C3S, JMA, and SEAS5 runs generate the full advertised anomaly suite:
500-mb height, 850-mb temperature, 2-m temperature, precipitation,
sea-surface temperature, and mean sea-level pressure. Manual dispatches remain
single-product so a failed or late field can be repaired without rebuilding the
entire suite. The scheduled super ensemble likewise uses its `all` product mode.

C3S and SEAS5 also publish CONUS snowfall water-equivalent departures when the
native snowfall field is available: monthly totals and the configured DJF
three-month sum. Other model rows remain explicitly not applicable because
their current adapters publish precipitation or snowpack rather than snowfall.

The analog workflow also generates source-backed products for the current rank-1
analog: PSL NCEP CFSR 500-mb height and 2-metre temperature anomalies, plus MRCC
station-interpolated snowfall departure for the NWS Eastern Region (`ER`). A
monthly analog uses that calendar month; a DJF analog uses December through
February. Snowfall uses ER stations plus adjacent Great Lakes and Southeast
stations so the rendered frame covers the eastern Great Lakes through the
Southeast, while the fill is masked to those selected U.S. states. A source
outage retains the last good image and records the stale status in
`seasonal/analog_products_manifest.json`.
The MRCC generator is given a ten-minute per-map wait window; quick HTTP 5xx
responses are retried, while a true timeout is marked unavailable only after
that full window. The analog job allows 120 minutes for this slow provider.
WRIT analog height and temperature products are requested as NetCDF data from
NCEP/CFSR using its native 1981-2010 climatology; pre-1979 analog dates use
WRIT 20CRv3, which has the same native climatology period. Both are re-rendered
through the shared seasonal Lambert Conformal Conic renderer. The 500-mb maps
retain the North America frame; 2-metre temperature uses the shared CONUS
frame. Both retain the provider image/data URLs in the product manifest.
Analog ranking remains ordered by centered pattern correlation, with a separate
cosine-latitude-weighted RMS anomaly amplitude similarity. The top five ranked
analogs are combined into PSL 500-mb, 2-metre, and snowfall-departure maps using
inverse similarity-distance weights (80% pattern, 20% amplitude). Snowfall
composites use MRCC/ACIS monthly station departures, sum the three monthly
departures for DJF, then linearly interpolate the stations to a 0.25° eastern
U.S. grid with nearest-neighbor edge filling; if SciPy is unavailable or cannot
load, the manifest records a NumPy inverse-distance fallback. The snowfall
composite uses a centered, domain-fitted eastern Lambert Conformal Conic frame
(33°/45° standard parallels) through the Great Lakes and Southeast, fitting the
projected window to the selected-state land mask so the states reach and can be
clipped by the frame like the source regional product. Its high-contrast signed
departure palette runs
from brown/dark red/red/orange/yellow through a white zero interval to
light-blue/blue/purple/pink/cyan; only the selected state outlines are drawn so
excluded land does not look like missing data. The rendered MRCC map remains
available as the rank-1 reference. Composite source
failures retain the previous good image and mark it stale.

The CFSv2 readiness check retries the newest listed cycle for two hours before
falling back to the newest complete prior cycle. That prevents the normal
NOMADS directory-versus-file publication gap from turning a newly listed cycle
into an unnecessarily old analog input. The analog workflow also runs a
scheduled reconciliation after each CFSv2 window; it compares the current
Pages source manifests with the analog manifest and only rebuilds when a
source run is newer or changed. This covers delayed workflow events and Pages
propagation without repeatedly regenerating unchanged MRCC maps.

The central `publish-pages.yml` workflow already serializes Pages updates with
`cancel-in-progress: false`, so simultaneous model releases do not overwrite
one another. A scheduled GitHub event can still be delayed during platform
load; the times above are release-aligned targets, not provider SLAs.

Each Pages validation also writes a per-model/per-parameter health report to
`seasonal/catalog.json` and the GitHub job summary. It distinguishes healthy,
aging, stale, partial, failed, missing, non-comparable, and intentional
not-applicable surfaces without blocking publication solely because a provider
is late; strict publication failures remain driven by validation errors such as
invalid manifests or missing rendered assets.

## Official timing references

- [ECMWF dissemination schedule](https://confluence.ecmwf.int/pages/viewpage.action?pageId=685248329)
- [C3S announcements and publication timing](https://confluence.ecmwf.int/spaces/CKB/pages/135565670/Announcements)
- [ECCC CanSIPS global forecast service](https://eccc-scenarios.collab.science.gc.ca/?page=cansips-global)
- [WMO GPC Beijing system configuration](https://www.wmolc.org/contents2/index/Beijing)
- [WMO seasonal direct data exchange](https://www.wmolc.org/seasonDownload/direct)
- [NOAA CPC NMME User's Guide](https://www.cpc.ncep.noaa.gov/products/NMME/users_guide.html)
- [NOAA NOMADS](https://nomads.ncep.noaa.gov/)
- [APCC CLIK API](https://apcc21.org/clik/clikapi?lang=en)
- [NASA GEOS-S2S-3 numerical forecast archive](https://portal.nccs.nasa.gov/datashare/gmao/geos-s2s-3/NRT/APCN/)


