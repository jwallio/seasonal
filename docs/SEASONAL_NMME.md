# NOAA NMME seasonal workflow

`scripts/nmme_seasonal.py` reads the public NOAA CPC NMME realtime anomaly
NetCDF archive and the official CPC probability NetCDF archive. It supports
2-m temperature, precipitation, and 200-mb height anomalies, plus:

- official above/near/below-normal probability maps;
- an equal-weight component-model consensus map; and
- optional individual component-model maps.

The realtime feed currently exposes `z200` rather than a preprocessed realtime
`z500` file. The viewer therefore labels the NMME height product as 200 mb and
does not imply that it is a 500-mb product.

The GitHub workflow is `.github/workflows/nmme.yml`. It publishes
`nmme_manifest.json` plus images under `seasonal/nmme/`. The default scheduled
bundle uses 2-m temperature for the probability and consensus products.
Change `base_product` to precipitation or 200-mb height for those derived
products.

Public lead numbers follow the same convention as the other seasonal suites:
lead 4 from an August initialization is December. The decoder maps that to the
CPC NetCDF target coordinate, whose index 0 is the initialization month. Thus
the default 4–6 seasonal window is DJF rather than November–January.

The above-, near-, and below-normal fields are decoded as a triplet and are
published only after their finite masks match, every value lies in 0–100%, and
their pointwise sum is 100% within tolerance. Each category uses its own
sequential color family (red, green, or blue), and probability titles/legends
are labelled in percent rather than temperature units.

Official sources:

- [NOAA CPC NMME data](https://www.cpc.ncep.noaa.gov/products/NMME/data.html)
- [NOAA NCEI North American Multi-Model Ensemble](https://www.ncei.noaa.gov/products/weather-climate-models/north-american-multi-model)
- [CPC realtime NMME anomaly archive](https://ftp.cpc.ncep.noaa.gov/NMME/realtime_anom/)
- [CPC NMME probability archive](https://ftp.cpc.ncep.noaa.gov/NMME/prob/netcdf/)
