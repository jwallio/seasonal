# CFSv2 surface-phase candidate

This opt-in method estimates snow water equivalent from six-hour APCP multiplied by the average of CSNOW at the interval endpoints. APCP is already an accumulated mass per area; no additional time multiplication is applied. Convert kg/m² to inches of liquid using 25.4, then use the existing 10:1 display conversion once. CSNOW is a category, not a snowfall amount; the map title remains Estimated.

The endpoint approximation cannot resolve all precipitation-type changes within six hours. It is not a validated deterministic seasonal snowfall forecast.

## Initial evaluation

Eight February targets (2012–2019), one September 6 12Z initialization per year, five airport observation series, bilinear sampling at identical station coordinates: 40 complete comparisons. Absolute snowfall MAE fell from 6.62 to 4.65 inches; 28 of 40 cases improved. Average error fell at all five airports, but Atlanta and Raleigh remained too snowy. Leave-one-year-out departure MAE also fell at all five sites; only Chicago beat the corresponding historical-average benchmark. This is exploratory, limited to February and a single cycle, and does not validate the full rolling DJF/JFM products.

## Backfill

Dispatch `cfsv2-surface-phase-backfill.yml` for each historical initialization year 2011–2025 and once with a blank year for the forecast. Keep init, targets, and rolling_days identical across jobs. Re-run an interrupted shard with the same inputs: it restores complete and partial inputs from cache and checks complete bundles before reusing them. Downloads use two workers by default with no unconditional pauses; retry backoff remains enabled. HTTP 429, selected server errors, and transport failures retry with backoff and Retry-After support; persistent failures still fail visibly.

Each job limits acquisition to 300 minutes, leaving time to save checkpoints and upload complete bundles. Cache retention/eviction is controlled by GitHub; download completed artifacts if long-term preservation is needed. Do not launch many shards simultaneously against the archive.

After collecting all matching bundles:

```bash
python scripts/cfsv2_surface_phase_build.py --init 2026090612 --targets 202612,202701,202702,202703 --rolling-days 6 --reference
```

Reference assembly requires all 15 years and the exact forecast cycle window. It rejects incomplete years and incompatible methods. Pass the resulting month directory using `--surface-phase-bundle-dir` and the matching reference directory using the existing snowfall reference option when rendering. Do not combine these forecast estimates with the old SRWEQ reference. Production defaults remain unchanged; this workflow never publishes.

## Explicit paired cycle exclusions

The known September 2019 gap maps to forecast initialization `2026090418` for the September 2026 anchor. Pass `--exclude-cycles 2026090418` to acquisition and reference assembly, and `--surface-phase-exclude-cycles 2026090418` to rendering. The same list must be used for every month in DJF/JFM. Reference loading compares the exact selected forecast-cycle list and rejects a different mask. No interval is zero-filled. The title's ensemble line reports 23/24-cycle matched mean.

The PR backfill now omits that known cycle explicitly and restores previously completed bundles. This only completes the 2019 February shard; other reference years and seasonal months are still required. Missing cycles discovered elsewhere are reported; they are not automatically dropped from a reference.
