# Seasonal model release schedules

The scheduled workflows use UTC because that is how GitHub Actions interprets
cron expressions. Each run starts after the provider's normal release window,
leaving a small buffer for source indexing and mirror propagation. Every
workflow remains manually runnable from **Actions > Run workflow** when a
provider is late or a historical cycle needs to be regenerated.

| Workflow | Provider release window | Automatic run (UTC) |
| --- | --- | --- |
| CFSv2 | New model cycles every 6 hours | Twice daily at 10:35 and 22:35, after the 06Z and 18Z cycles |
| CanSIPS v3 | ECCC monthly refresh on the 1st | 2nd of each month at 16:30 |
| NOAA NMME | CPC public products update on the 9th | 9th of each month at 15:30 |
| ECMWF SEAS5 | ECMWF 7-month forecast on the 5th at 12:00 | 5th of each month at 15:30 |
| C3S multi-system | Non-ECMWF systems on the 10th at 12:00 | 10th of each month at 15:30 |
| JMA / MRI-CPS4 | JMA is a non-ECMWF C3S component | 10th of each month at 15:30 |
| APCC MME | APCC seasonal MME around the middle of the month | 16th of each month at 16:30 |
| NASA GEOS-S2S-3 | Public seasonal chart inventory during the first week | 6th of each month at 16:30 |

The central `publish-pages.yml` workflow already serializes Pages updates with
`cancel-in-progress: false`, so simultaneous model releases do not overwrite
one another. A scheduled GitHub event can still be delayed during platform
load; the dates above are release-aligned targets, not provider SLAs.

## Official timing references

- [ECMWF dissemination schedule](https://confluence.ecmwf.int/pages/viewpage.action?pageId=685248329)
- [C3S announcements and publication timing](https://confluence.ecmwf.int/spaces/CKB/pages/135565670/Announcements)
- [ECCC CanSIPS global forecast service](https://eccc-scenarios.collab.science.gc.ca/?page=cansips-global)
- [NOAA CPC NMME User's Guide](https://www.cpc.ncep.noaa.gov/products/NMME/users_guide.html)
- [NOAA NOMADS](https://nomads.ncep.noaa.gov/)
- [APCC CLIK API](https://apcc21.org/clik/clikapi?lang=en)
- [NASA GEOS-S2S-3 seasonal forecasts](https://gmao.gsfc.nasa.gov/seasonal-forecasts/seasonal-decadal-analysis-prediction-v3/)
