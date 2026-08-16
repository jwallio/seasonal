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

Official sources:

- [NOAA CPC NMME data](https://www.cpc.ncep.noaa.gov/products/NMME/data.html)
- [NOAA NCEI North American Multi-Model Ensemble](https://www.ncei.noaa.gov/products/weather-climate-models/north-american-multi-model)
- [CPC realtime NMME anomaly archive](https://ftp.cpc.ncep.noaa.gov/NMME/realtime_anom/)
- [CPC NMME probability archive](https://ftp.cpc.ncep.noaa.gov/NMME/prob/netcdf/)
