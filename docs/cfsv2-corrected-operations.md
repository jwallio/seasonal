# Automatic corrected snowfall

`CFSv2 Snowfall Graphics` runs at 05:45, 11:45, 17:45, and 23:45 UTC. To start immediately, choose that workflow in Actions, select Run workflow, and use main. The general CFSv2 workflow also delegates manual snowfall requests to it.

Each run selects a mature initialization whose six-hour file listing covers December through March, restores reusable cycle-month bundles, and downloads missing records. It reconstructs forecast snowfall and the identically sampled 2011–2025 reference using APCP and endpoint CSNOW. DJF and JFM are seasonal totals. The display converts snow water equivalent to estimated snowfall inches at 10:1 once.

Sixteen acquisition shards run two at a time, each with two month workers. NOMADS is preferred for current cycles, followed by the NOAA AWS archive and NCEI for the same record. Transient errors retry with bounded exponential delays, honoring Retry-After. Individual missing months do not prevent saving other completed months. Each shard has a five-hour acquisition timeout and saves partial caches and validated month bundles afterward. The next run can reuse overlapping inputs; a changed initialization still requires its newly added historical cycles.

Only the three explicitly verified archive gaps listed in cfsv2_surface_schedule.py are omitted, on both sides of the comparison. Unknown missing data cause acquisition failure. Publication requires all 15 reference years, both products, every winter period, and the matching cycle count. Failure or unavailable forecasts leave the previous corrected maps live. General weather publication preserves corrected snowfall independently.

Artifacts distinguish acquisition (`surface-phase-inputs-*`), assembled reference (`matched-surface-phase-reference`), and checked graphics (`corrected-snowfall-ready`). A successful download is not by itself publication or observational validation. The publish job merges against the current Pages branch and requests a Pages rebuild.
