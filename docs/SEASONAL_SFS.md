# NOAA SFS beta2 seasonal model

The SFS adapter publishes NOAA's experimental beta2 Seasonal Forecast System
from the [SFS development archive](https://noaa-oar-sfsdev-pds.s3.amazonaws.com/index.html).
It is a standalone forecast family in the Seasonal dashboard; it is not added to
the deduplicated super ensemble automatically.

## Source and calibration

The adapter reads the consolidated `atm_monthly.zarr` forecast and reforecast
stores under the selected beta2 release. The forecast store contains 31 members,
12 monthly leads, and a 0.5-degree global grid. The reforecast store is selected
by the release month and contains the same calendar-month initialization with 11
members and at least 30 historical initializations.

For each requested lead and field, the published anomaly is:

```text
31-member forecast mean - same-calendar-month reforecast mean
```

The source schema is checked before decoding: variable name, declared units,
dimension order, member counts, coordinates, monthly leads, and pressure level
metadata must all agree. The historical baseline years are recorded in every
target manifest entry.

The adapter currently publishes 500-mb height, 850-mb temperature, 2-metre
temperature, precipitation, native snowfall, and mean-sea-level pressure. The
default September release leads 3, 4, and 5 map to December, January, and
February, and the three-month target is labelled DJF across the calendar year.

## Snowfall

SFS supplies `tsnowpsfc` / `TSNOWP_surface`, total snow precipitation in
`kg m-2`, which is liquid-water equivalent. In the `atm_monthly` archive the
values behave as monthly mean daily accumulation, so the adapter multiplies
each value by the target calendar month's number of days before converting to
LWE inches and then once to estimated snow-depth inches at a fixed 10:1 ratio.
The published `.snow.csv.gz` grid is the snow-inch product; the `.lwe.csv.gz`
sidecar remains for auditability. The adapter does not treat `swe` /
snow-water equivalent as snowfall.

## Map rendering

All anomaly products use the shared fixed palettes and `BoundaryNorm` interval
boundaries. The maps therefore contain discrete color bands rather than a
smooth interpolated gradient. Temperature and height products use the common
cross-model scales; precipitation, snowfall, and pressure use their canonical
units and boundaries.

The 500-mb product keeps the North American frame and the other products use
the shared CONUS frame. The horizontal frame includes an explicit margin so the
full eastern Maine border remains inside the rendered image. Snowfall is masked
to the selected U.S. states before the map is drawn.

## Operations

The worker is [`.github/workflows/sfs.yml`](../.github/workflows/sfs.yml). It
checks the archive after the 9th of each month at 18:30 UTC, runs on source-code
pushes for immediate validation, and remains manually dispatchable for a
specific release or product. A successful worker uploads a scoped Pages payload
and hands it to the single-writer publisher. The worker retains the current
release and three prior cycles in `seasonal/sfs_manifest.json`.

Because this is a development archive, a missing or schema-incompatible source
fails closed and records the error in the manifest instead of silently mixing a
different field or baseline.
