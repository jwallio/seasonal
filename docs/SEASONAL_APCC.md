# APCC MME seasonal graphics

`apcc_seasonal.py` uses the official APCC CLIK API to request a native APCC
multi-model ensemble (MME) seasonal archive. The workflow requests the
selected variables together, safely extracts the returned NetCDF files, and
renders each field with the shared North America map frame.

The API requires the repository secret `APCC_API_KEY`. The workflow fails early
with an actionable message when that secret is not configured; no key is stored
in the repository or in the generated Pages payload.

The default request is `MME_6MONTH`, `6-MON`, 2.5-degree, seasonal mean, SCM
processing. It selects offsets 3–5 from APCC's first target month so the latest
August issue (requested with first target month September) publishes DJF rather
than the near-term SON window. APCC indexes downloads by the first target month,
not by the issue month; the map and manifest use the NetCDF `Issued_Date` for the
cycle label. The returned `MME_Forecast_Info` metadata remains authoritative and
must exactly match the requested season.
The displayed anomalies are the native APCC MME anomaly fields, not a second
climatology subtraction.

APCC precipitation is delivered as a seasonal mean in `mm/day`. The renderer
multiplies it by the number of days in the returned three-month season and
labels the map in seasonal accumulation `mm`. The manifest records the native
units, conversion, source season, grid resolution, and rendered data range.

The six products use parameter-appropriate scales. The 500-mb height product
matches the common seasonal ±100 m scale; temperature uses ±3°C, precipitation ±200 mm, SST
±4°C, and MSLP ±6 hPa. APCC's z500 archive contains anomalies only, so no
absolute-height contour overlay is fabricated.

SST is rendered only over ocean cells. A missing land-mask geometry is treated
as a failed render rather than allowing source fill values to appear over land.

APCC publishes monthly and seasonal variables including `z500`, `t850`,
`t2m`, `prec`, `sst`, and `slp`. The manifest records the API dataset,
resolution, method, source URLs, and APCC acknowledgement text for each run.

Official references:

- [APCC seasonal prediction processing](https://apcc21.org/clik/processing/prediction)
- [APCC MME 3-MON dataset](https://apcc21.org/clik/dataset/mme/3-MON?lang=en)
- [APCC CLIK API](https://apcc21.org/clik/clikapi?lang=en)
