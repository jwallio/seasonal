# NASA GEOS-S2S-3 seasonal graphics

`geos_s2s3_seasonal.py` uses NASA's public seasonal lookup endpoint to retain
the official North America seasonal panels for 2-m temperature and
precipitation anomalies. The chart is intentionally preserved as a NASA
pre-rendered product because the public GEOS-S2S-3 page does not expose a
500-mb field through its image lookup form.

The workflow runs during the first week of each month and retains four release
cycles in the manifest. No repository secret is required. Each target records
the official chart URL and the product is kept separate from the common 500-mb
comparison set.

Official references:

- [NASA GEOS-S2S-3 overview](https://gmao.gsfc.nasa.gov/seasonal-forecasts/seasonal-decadal-analysis-prediction-v3/)
- [NASA atmospheric anomaly charts](https://gmao.gsfc.nasa.gov/seasonal-forecasts/seasonal-decadal-analysis-prediction-v3/forecast-data_atmospheric-anomalies/)
