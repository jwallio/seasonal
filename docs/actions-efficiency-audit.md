# Actions efficiency audit — 2026-09-09

Reviewed all 24 workflow files on main. 21 remain active; three obsolete/research launchers are preserved under docs/archived-workflows as .disabled files. GitHub can retain historical workflow names/run history after removal. No historical runs or artifacts were deleted. Producer names used by workflow_run consumers are unchanged.

## Changes and expected impact

- Four source caches now save updated data under unique run/attempt keys; prior prefixes remain reusable. Fixed keys could restore old data forever without saving new downloads. See [GitHub cache behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching).
- A repeated scheduled snowfall plan skips all 16 acquisition jobs and rendering/publication when the full published plan and rendering signature match. Missing/unreadable state rebuilds normally; manual/push runs always rebuild. The acquisition lock now cancels stale progress, rejects failed/partial source artifacts, uses four workers, and bounds each download command. This is not a shortcut for the first new-method reference rebuild.
- Raw-data concurrency stays bounded; product-scoped producer locks keep 500-mb, 850-mb, and 2-m temperature publications from canceling one another. No scientific checks, historical years, cycle membership, or snowfall formula changed.
- The shared temperature palette refreshes automatically when its contract changes, with four-way provider fan-out, bounded producer handoffs, five-attempt dispatch retries, and a drained serialized Pages queue. The shared 500-mb style refresh uses the same safeguards for both North America and Northern Hemisphere views. Corrected snowfall now drains the same queue before dispatching its validated artifact to the shared publisher, so a pending Pages run cannot silently drop the snowfall update. The repair workflow is manual-only so one edit cannot launch duplicate fan-outs; operational schedules remain unchanged.

## Workflow inventory

| Workflow | Review / action |
|---|---|
| `apcc.yml` | Fixed immutable source cache key to save new downloads per run/attempt. |
| `c3s.yml` | Retained product matrix, per-run cache, and explicit publisher dispatch. |
| `cansips.yml` | Retained render-only mode, per-attempt cache, and release schedules. |
| `cfsv2-snow-recover.yml` | Manual-only maintenance tool; pip cache and uncompressed artifacts. Uses existing inputs and refuses incompatible method bundles. |
| `cfsv2-snow.yml` | Added exact published-plan skip for schedules, pip cache, uncompressed artifact transfer, stale-lock cancellation, failed-artifact rejection, four-way acquisition, bounded download commands, and queue-safe dispatch to the shared Pages publisher. |
| `cfsv2.yml` | Removed unused native-snow reference cache transfers; retry-specific rolling cache keys. Scheduled products already exclude snowfall, so no duplicate scheduled snow pipeline was found. |
| `cma-cpsv3.yml` | Retained monthly schedule and per-run source cache. |
| `geos-s2s3.yml` | Retained monthly schedule and per-run source cache. |
| `height-style-refresh.yml` | Automatic refresh on shared 500-mb style edits; four-way matrix, bounded waits, retries, and serialized Pages handoff for NA and NH views. |
| `jma.yml` | Fixed immutable source cache key to save new downloads per run/attempt. |
| `nmme.yml` | Fixed immutable source cache key to save new downloads per run/attempt. |
| `publish-pages.yml` | Added an explicit corrected-snowfall artifact input and merge path; added 30-minute timeout. Shared publish serialization preserved. |
| `runner.yml` | Removed public/** trigger: dashboard changes no longer download/render WeatherNext forecasts. |
| `seas5.yml` | Fixed immutable source cache key to save new downloads per run/attempt. |
| `seasonal-analog-health.yml` | Retained hosted half-hour health checks: these detect offline home runner, not forecast downloads. |
| `seasonal-analogs.yml` | Retained event-driven refresh plus freshness-gated scheduled reconciliation; Windows archive dependency remains. |
| `seasonal-contracts.yml` | Retained push/PR contract gates and superseded-test cancellation. |
| `seasonal-release-check.yml` | Retained readiness/published/active-worker gates and release-window polling; avoids blindly starting unavailable CDS jobs. |
| `superensemble.yml` | Retained bounded four-product matrix, component cache fallbacks and shared wgrib2 artifact. |
| `temperature-style-refresh.yml` | Automatic shared temperature-style refresh on palette edits; four-way provider matrix, product-scoped producers, bounded waits, dispatch retries, and serialized Pages handoff. |
| `update.yml` | Retained renderer sharding and pip cache; reusable WeatherNext engine, not a second automatic schedule. |

## Archived launchers

- cfsv2-native-refresh.yml: redundant alias for cfsv2-snow.yml.
- snowfall-refresh-202608.yml: fixed August 2026 one-off refresh.
- cfsv2-march-pilot.yml: research-only March pilot, preserved for restoration; not operational.

## Remaining limitations

No measured end-to-end wall-time speedup is claimed yet. Initial six-hour reference reconstruction still dominates; caches can be evicted and missing records still need retrieval. The shared publisher lock remains necessary to avoid overwriting other models, so maintenance waits for the queue to drain before handing off a payload. Long-running existing jobs use their original workflow revision and are not retroactively changed. Analog reconciliation can still upload unchanged payloads; its upstream freshness test is retained rather than rewritten during this pass.

## Validation

All active YAML files parsed; local reusable workflow targets exist. Nine schedule tests, seasonal Actions contracts, and CFSv2 contracts passed.
