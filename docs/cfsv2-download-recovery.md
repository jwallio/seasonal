# Download execution and recovery

The February test workflow succeeded, including all inputs and rendering. GitHub Actions is the primary execution environment; the home-PC runner is a fallback for compute/network access, not a replacement for absent archive records.

## Recovery policy

- Reuse checksummed complete bundles and raw message checkpoints first.
- For 2019 onward, try NOAA's AWS CFS archive, then the exact same initialization, member, and valid time at NOAA NCEI. Older records use NCEI. Exhausted transient AWS errors now permit NCEI fallback, as do 404s.
- Connect timeout: 10 seconds; read timeout: 40 seconds. Retry 408/429/500/502/503/504 and transport errors up to eight attempts, with 60-second exponential backoff capped at 15 minutes. Honor longer Retry-After values.
- Do not retry 404 repeatedly. Do not bypass 401/403 or network access controls. Report missing/denied records.
- Acquire at most two cycle-months concurrently by default, with no unconditional pause. Different months of the same initialization are serialized to protect shared endpoint checkpoints. Continue other tasks after a failed cycle; keep an explicit incomplete status.
- Validate GRIB identity, valid time, units, grid, categories, and hashes on both providers. Never substitute reanalysis, a different forecast cycle, or zero precipitation.
- A missing record does not become available on a home PC. Exclusions must be explicit and applied to both forecast and reference across seasonal months.

## GitHub

Use CFSv2 Surface Phase Reference Backfill for one historical year and target month at a time. Re-run with identical inputs to restore saved checkpoints. The job has a 300-minute download budget inside a 350-minute job, leaving time to upload results. Archive artifacts locally before expiration; GitHub caches can be evicted. Green acquisition is not proof that all production references or graphics are ready.

## Home PC (Windows + WSL)

Use an Ubuntu WSL checkout of branch `codex/cfsv2-surface-phase`. From that checkout:

```bash
bash scripts/run-cfsv2-download-home.sh --years 2019 --targets 202612,202701,202702,202703
```

The checkpoint directory is `~/.local/share/seasonal-cfsv2-downloads`. The campaign processes year/month tasks serially, with two download workers within each task, and allows four hours per year/month task, logs each task, and continues after timeouts. Re-run the same command to reuse saved records. Stop with Ctrl-C; retain the directory. Keep the PC awake and WSL running.

For the full planned reference plus forecast, omit `--years`. Run with `--plan` first to inspect the scope. Use the same init, targets, cycle exclusions, and rolling window everywhere. This script downloads only: it does not publish or change scientific reference requirements.

To continue on another machine, copy the entire checkpoint directory, including raw, months, and JSON provenance. Completed month bundles from GitHub artifacts can be copied into its `months/` directory. Local execution does not need a GitHub token or self-hosted Actions registration. This avoids letting public pull requests execute on the home PC.

## Remaining publication gates

The reference builder currently requires 2011–2025; smaller fixed year sets require explicit implementation and labeling. Seasonal target months must share the same year/cycle sample. Build and verify forecasts and matching references, then connect them to graphics generation. None of the recovery paths publishes incomplete departure maps.

## Further speed improvements to evaluate

1. Unify preview/backfill input caches, or import completed artifacts once. Cache identity should follow initialization, target, member, and method rather than the workflow name. This avoids rebuilding the eight preview years when production requests the same cycles.
2. Keep completed compact month bundles in durable versioned storage; use Actions cache primarily for partial raw inputs. This avoids cache eviction forcing completed reconstructions to be downloaded again.
3. Measure download, decode, and cache-validation time separately. Reuse an endpoint already decoded in the adjacent interval; avoid repeated hash checks for unchanged files within a single process. Preserve verification when loading data from a new run.
4. Use resumable year/month shards with a campaign-level limit. Do not launch unrestricted combinations of job and worker concurrency; two jobs with two workers would create four simultaneous requests.
5. Preflight calendar coverage from provider indexes/listings to identify missing entire cycles before spending time on them. Absence of an index alone is not proof that GRIB data are absent.
6. Once references are complete, operational runs need only new forecast cycles, not historical reconstruction.

These are follow-up opportunities, not implemented performance claims. Reducing historical years, changing precipitation-type fields, or dropping cycles changes the scientific product and is not a performance-only optimization.
