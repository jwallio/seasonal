# APCC MME seasonal graphics

`apcc_seasonal.py` uses the official APCC CLIK API to request a native APCC
multi-model ensemble (MME) seasonal archive. The workflow requests the
selected variables together, safely extracts the returned NetCDF files, and
renders each field with the shared North America map frame.

The API requires the repository secret `APCC_API_KEY`. The workflow fails early
with an actionable message when that secret is not configured; no key is stored
in the repository or in the generated Pages payload.

The default request is `MME_3MONTH`, `3-MON`, 1-degree, seasonal mean, SCM
processing, and the viewer labels the 4–6 lead window as DJF/MAM/JJA/SON when
the dates match a standard meteorological season. The displayed anomalies are
the native APCC MME anomaly fields, not a second climatology subtraction.

APCC publishes monthly and seasonal variables including `z500`, `t850`,
`t2m`, `prec`, `sst`, and `slp`. The manifest records the API dataset,
resolution, method, source URLs, and APCC acknowledgement text for each run.

Official references:

- [APCC seasonal prediction processing](https://apcc21.org/clik/processing/prediction)
- [APCC MME 3-MON dataset](https://apcc21.org/clik/dataset/mme/3-MON?lang=en)
- [APCC CLIK API](https://apcc21.org/clik/clikapi?lang=en)
