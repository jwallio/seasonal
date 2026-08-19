# CMA CPSv3 seasonal graphics

`scripts/cma_cpsv3_seasonal.py` renders the current GPC Beijing contribution
from the WMO Lead Centre for Seasonal Prediction Multi-Model Ensemble. The
current GPC Beijing system is CMA CPSv3: a 21-member coupled system with a
T266 atmosphere and MOM5 ocean. The WMO system record says this configuration
became operational in January 2025.

The adapter downloads the official NetCDF anomaly bundle from WMO's direct
data exchange, validates the archive member, and preserves the source issue,
variables, units, grid, and hindcast-year attributes in the manifest. It does
not use the obsolete BCC-CGCM1/BCC-CPSv2 download archive.

The WMO server currently omits its RapidSSL intermediate certificate during
the TLS handshake. The adapter supplies the official DigiCert intermediate,
pins its published SHA-256 fingerprint, and keeps hostname and certificate
validation enabled. It never uses an insecure `verify=False` fallback.

## Available forecast horizon

CMA CPSv3 itself runs seven months, but the WMO redistribution contains only
forecast months 1, 2, and 3 for each issue. WN2 therefore publishes those
three monthly fields and their three-month seasonal aggregate. It does not
extrapolate or relabel unavailable months 4-7.

The August 2026 package, for example, contains September, October, and
November anomalies and produces a SON 2026 seasonal map. A CMA field can join
the deduplicated super ensemble only when that requested target window uses
the same WMO-available forecast months 1-3.

## Products and conversions

| Product | WMO variable | Published units | Conversion |
| --- | --- | --- | --- |
| `500mb_height_anomaly` | `h500` | m | gpm anomaly treated as geopotential-height metres |
| `850mb_temperature_anomaly` | `t850` | °C | K anomaly increment equals °C anomaly increment |
| `2m_temperature_anomaly` | `t02m` | °C | K anomaly increment equals °C anomaly increment |
| `precipitation_anomaly` | `prec` | in | rate × calendar-month seconds ÷ 25.4 |
| `sea_surface_temperature_anomaly` | `sst` | °C | K anomaly increment equals °C anomaly increment; land remains masked |
| `mslp_anomaly` | `mslp` | hPa | Pa ÷ 100 |

The WMO data policy defines precipitation as total precipitation rate in
`kg m-2 s-1`. Some converted NetCDF files carry the shorter `kg/m^2` units
attribute, so the manifest records both the source-declared string and the
policy-defined raw units used for conversion.

The source fields are already anomalies relative to the provider's hindcast
climatology. WN2 does not subtract a second climatology. The delivered file's
`hindcast_start_year` and `hindcast_end_year` attributes are authoritative for
each run and are written into the manifest.

## Scheduling and retention

`.github/workflows/cma-cpsv3.yml` runs on the 21st of each month, after the WMO
15th-20th contribution window. It publishes the current cycle plus three prior
cycles. If discovery or download fails, the workflow fails before publication,
leaving the previously published manifest and maps intact.

Run a source decode without rendering:

```powershell
python scripts/cma_cpsv3_seasonal.py `
  --product all `
  --init latest `
  --decode-only `
  --manifest .preview/cma_cpsv3_manifest.json
```

Official references:

- [WMO GPC Beijing system configuration](https://www.wmolc.org/contents2/index/Beijing)
- [WMO seasonal direct download](https://www.wmolc.org/seasonDownload/direct)
- [WMO seasonal data exchange policy](https://www.wmolc.org/contents/index/Data%2BExchange%2BPolicy)
- [DigiCert trusted root and intermediate certificates](https://knowledge.digicert.com/general-information/digicert-trusted-root-authority-certificates)
