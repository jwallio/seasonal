# NASA GEOS-S2S-3 seasonal graphics

`geos_s2s3_seasonal.py` reads NASA's public NCCS numerical GEOS-S2S-3 APCN
archives. It forms the available-member monthly mean, subtracts NASA's
initialization- and lead-matched drift climatology, and renders the result with
the same 1080-by-1080 operational layout as the other seasonal models.

The public package contains 40 lag/burst member files. Forty members cover
release offsets 0 and 1; ten selected members continue through offset 8. The
manifest records the exact initialization dates, member files, member count,
archive URL, drift URL, and drift years for every target. A DJF window at
offsets 4, 5, and 6 therefore uses the consistent ten-member long-range set.

Validated numerical products are:

- 850-mb temperature anomaly;
- 2-m temperature anomaly;
- CONUS precipitation anomaly in inches;
- mean sea-level pressure anomaly in hPa;
- sea-surface temperature anomaly, with land cells masked.

The NASA archive named `z500` is not scheduled. Its current long-range files
declare a 200-hPa pressure coordinate, and the adapter refuses to label or
publish them as 500-mb data. A 500-mb product can be enabled only after the
source passes the explicit 500-hPa coordinate check. Until then NASA
GEOS-S2S-3 is intentionally absent from the dashboard's
500-mb comparison tab.
If a future source passes that guard, its 500-mb anomaly map is configured to
use the shared seasonal -100 to +100 m scale with 10 m intervals.

The workflow runs during the first week of each month, caches the public
NetCDF archives, and retains four release cycles in the manifest. No repository
secret is required. The default run renders December, January, February, and
DJF for every validated product.

For the deduplicated super ensemble, the standalone GEOS-S2S-3 numerical mean
receives one NASA-family vote for supported products. The older NMME
`NASA_GEOS5v2` field is excluded so NASA is not counted twice.

Official references:

- [NASA GEOS-S2S-3 numerical data share](https://portal.nccs.nasa.gov/datashare/gmao/geos-s2s-3/)
- [NASA GEOS-S2S-3 primer](https://portal.nccs.nasa.gov/datashare/gmao/geos-s2s-3/GEOS-S2S-3-primer.pdf)
- [NASA APCN near-real-time archives](https://portal.nccs.nasa.gov/datashare/gmao/geos-s2s-3/NRT/APCN/)
- [NASA APCN drift climatologies](https://portal.nccs.nasa.gov/datashare/gmao/geos-s2s-3/Drift/for_APCN/)
