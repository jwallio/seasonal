# Seasonal product audit — 2026-08-30

## Executive assessment

The Seasonal product has a strong data-provenance foundation: ten model
families, retained run history, explicit failed/partial states, a canonical
product registry, a serialized Pages publisher, and a useful Compare workflow.
The highest-value work is now reliability and consistency rather than adding
another top-level model row.

The most important defect was monthly release timing. ECMWF disseminates its
SEAS5 forecast on the 5th at 12 UTC, but this repository retrieves SEAS5 from
the C3S Climate Data Store. Official C3S availability is ECMWF on the 6th at
12 UTC and the remaining systems on the 10th at 12 UTC. The old SEAS5 action
ran on the 5th and could therefore select the prior month, with no automatic
run when the current CDS month actually appeared.

This change introduces a catalogue-driven checker. It does not guess that a
release is ready and does not repeatedly submit expensive data jobs. It reads
the current CDS constraints inventory, verifies every required centre/system,
field, pressure level, and lead, checks the live manifest, and dispatches a
full worker only when the source is ready and the live suite is incomplete.

## Current live product health

The 2026-08-30 live catalogue reported:

- 10 of 10 configured model families online;
- 54 supported model/parameter surfaces;
- 49 available surfaces and one partial surface;
- SEAS5 missing 500-mb height;
- C3S multi-system missing precipitation;
- standalone JMA missing precipitation, MSLP, and SST;
- the super-ensemble 500-mb surface partial;
- one APCC precipitation surface present but non-comparable because the live
  artifact predates the current canonical units contract.

The release checker independently reproduced the SEAS5, C3S, and JMA gaps from
their live manifests while confirming that the August CDS source inventory is
complete. A manual forced checker run after deployment can repair those current
gaps; subsequent months are handled automatically.

## Monthly source timing and automation

| Product | Official/source window | Automation decision |
| --- | --- | --- |
| ECMWF SEAS5 direct dissemination | 5th at 12 UTC | Informational only; this repository does not use direct member dissemination |
| ECMWF SEAS5 in C3S/CDS | 6th at 12 UTC | Check every 15 minutes from 12:00-18:59, then hourly through the 9th |
| C3S non-ECMWF systems | 10th at 12 UTC | Check every 15 minutes from 12:00-18:59, then hourly through the 12th |
| JMA/MRI-CPS4 in C3S | 10th at 12 UTC | Evaluate and dispatch independently from the all-centre C3S blend |

A daily 12:17 UTC catch-up continues from the 13th through month-end for any
suite that remains incomplete after the primary polling windows.

The current August constraints objects were last modified at approximately
12:05 UTC on the 10th, showing why inventory detection is preferable to a
large fixed buffer. The checker follows the current constraints link exposed
by each collection rather than hard-coding the versioned object-store URL.

Operational safeguards:

- the checker runs on GitHub-hosted Ubuntu and does not depend on the local
  analog runner or a CDS credential;
- all three workers accept an explicit `all` suite and exact `YYYYMM` target;
- each worker has `cancel-in-progress: false` concurrency;
- an active run suppresses duplicate dispatch;
- a completed full-suite run has a 45-minute retry cooldown while the Pages
  publisher catches up;
- source-ready, live-complete, and dispatch-needed are separate reported
  states;
- a catalogue or manifest read failure fails closed instead of launching a
  possibly duplicate retrieval.

Official references:

- [ECMWF dissemination schedule](https://confluence.ecmwf.int/pages/viewpage.action?navigatingVersions=true&pageId=621030722)
- [C3S summary of available data](https://confluence.ecmwf.int/pages/viewpage.action?navigatingVersions=true&pageId=638830872)
- [C3S announcements](https://confluence.ecmwf.int/spaces/CKB/pages/135565670/Announcements)
- [CDS API and catalogue access](https://cds.climate.copernicus.eu/en/how-to-api)

## GitHub Actions assessment

### What is working

- Model jobs produce scoped artifacts rather than directly mutating the live
  branch.
- `publish-pages.yml` serializes publication, rebuilds the catalog and
  thumbnails, validates manifests/assets, and then updates Pages.
- CFSv2 checks source readiness and retries the newest cycle before falling
  back to a complete prior cycle.
- Analog reconciliation detects when CFSv2 or the super ensemble has advanced
  and avoids rebuilding an unchanged source.
- Retained manifests are downloaded through a temporary path and preserved
  when Pages is temporarily unavailable.

### Observed operational friction

- Recent CFSv2 jobs ranged from roughly 25 minutes to more than three hours;
  this is the largest frequent GitHub-hosted workload and needs duration
  telemetry by product and source request.
- Successful analog builds recently completed in roughly 18-24 minutes, but
  historical cancelled runs accumulated for hours. Slow MRCC work should be
  isolated from inexpensive rank/map work so a snowfall provider delay does
  not hold the entire analog artifact.
- The Pages publisher normally completes in about one to two minutes, but each
  successful provider causes a full catalog/publish cycle. Closely spaced
  monthly sources create avoidable repeated deployments.
- Generated image directories on `gh-pages` are overlaid but not pruned from
  manifest reachability. Old CFSv2 assets and a legacy nested analog path can
  continue growing the Pages branch even after run retention removes them from
  the UI.

Recommended action changes, in order:

1. Record per-product download, decode, render, and publish duration in job
   summaries so CFSv2 bottlenecks are measurable.
2. Split analog snowfall enrichment into a follow-up artifact or resumable
   step; publish rank/WRIT products even if MRCC remains pending.
3. Add a safe, manifest-referenced Pages garbage collector with a dry-run
   report before deletion is enabled.
4. Batch monthly provider payloads for a short publish window, or allow the
   serialized publisher to coalesce queued runs, to reduce redundant catalog
   rebuilds.

## Local runner and watchdog assessment

The analog runner was online and idle during this audit with labels
`self-hosted`, `Windows`, `X64`, and `wn2-analogwx`. `Runner.Listener.exe` was
active from `D:\actions-runner-wn2`. Its durable launcher is the Windows
Scheduled Task `GitHubActionsRunner-seasonal`, configured to restart after one
minute, rather than a Windows service.

The main remaining reliability risk is that the task uses an interactive
logon trigger. The runner can remain offline after a reboot until that user
logs in. The next runner hardening phase should:

1. install the already-configured runner as its supported Windows service, or
   change the task to a machine-start trigger under a non-interactive account;
2. retain the one-minute restart policy;
3. add an external heartbeat that checks GitHub's runner API and alerts after
   two consecutive offline checks;
4. monitor free disk, cache size, listener process age, queued analog jobs, and
   the age of the last successful analog manifest;
5. never re-register or run `config.cmd` as part of a watchdog restart.

Monthly C3S/SEAS5/JMA automation is intentionally GitHub-hosted, so those
time-sensitive releases no longer depend on this Windows runner.

## UI/UX assessment

Desktop Compare has clear hierarchy, sensible controls, useful map cards, and
a good default of collapsing advanced options. At a 390×844 mobile viewport,
however, several elements create or expose horizontal overflow:

- the right edge of the primary tabs and overview shortcut can be clipped;
- the analog ranking table clips its amplitude/weight columns;
- long availability and missing-product summaries are difficult to scan;
- the large analog section appears before the selected forecast maps, forcing
  a long scroll before the core comparison;
- the horizontally scrollable coverage matrix gives little indication that
  more model columns are available.

Recommended mobile order and behavior:

1. forecast comparison maps;
2. compact current-selection/status strip;
3. collapsed analog section;
4. advanced controls and detailed availability diagnostics.

Use a single-column control sheet below 640 px, cap all flex/grid children with
`min-width: 0`, wrap or abbreviate chart headers, convert the analog table to
stacked rank cards below 480 px, and add a visible horizontal-scroll affordance
to the coverage matrix. Keep copy/share actions inside an overflow menu on
mobile so they do not consume map width.

## Map and parameter consistency

### Strengths

- A canonical registry now defines names, units, scales, support, and QC.
- 500-mb maps use the shared ±100 m anomaly scale with 10 m intervals.
- 2-m and 850-mb temperature use the shared ±7 °C scale.
- MSLP and SST have common signed scales, and land/ocean masks are explicit.
- Failed and unavailable fields remain visible rather than being silently
  replaced by a different variable.

### Remaining inconsistencies

- C3S, SEAS5, and APCC precipitation renderers currently use a fixed ±8 inch
  range for both individual months and seasonal sums, while the canonical
  contract and CFSv2 use approximately ±4 inches monthly and ±8 inches for
  DJF. Monthly maps therefore lose useful contrast.
- Some CONUS products use slightly different source crops and rendered extents;
  the region should be a named registry contract rather than adapter-local
  coordinates.
- Several live products predate numerical clipping/QC metadata and are flagged
  as legacy. Each provider needs one complete regeneration after automation is
  stable.
- Product-specific titles, source-detail line lengths, and contour density vary
  enough to be noticeable in Compare thumbnails.

Recommended map work:

1. make scale selection period-aware in the canonical registry and remove
   adapter-local precipitation bounds;
2. define shared named domains for North America, CONUS, eastern U.S., and
   ocean-only SST products;
3. add image-regression fixtures for title safe area, projection/extent,
   colorbar bounds, coastline/state density, and footer wrapping;
4. regenerate every current monthly suite once, then reject legacy QC for a
   newly generated cycle;
5. expose C3S component systems through a component selector rather than
   adding nine duplicate top-level model rows.

## Model coverage

The dashboard already includes the major distinct operational families
available from its current public sources: CFSv2, CanSIPS, NMME, SEAS5, all
nine C3S systems, JMA/MRI-CPS4, CMA CPSv3, APCC MME, GEOS-S2S-3, WN2, and a
deduplicated super ensemble. The C3S multi-system manifest already retains
ECMWF, UKMO, Météo-France, DWD, CMCC, NCEP, JMA, ECCC, and BOM components.

Adding separate rows for every C3S component would increase apparent model
count while double-counting families in the UI and super ensemble. The better
near-term improvement is a component drill-down and clearer ensemble-membership
metadata. Revisit additional models only when they add a distinct forecast
system, stable machine-readable source, documented climatology, and fields not
already represented.

## Prioritized roadmap

### P0 — implemented in this change

- inventory-driven SEAS5/C3S/JMA release checker;
- corrected CDS release timing;
- exact target-month full-suite dispatch;
- live-manifest completeness and duplicate-run guards;
- worker concurrency and deterministic tests.

### P1 — next

- run a forced August repair after the checker is merged;
- fix mobile overflow and put forecast maps ahead of analog detail;
- centralize period-aware precipitation scales and named map domains;
- regenerate legacy-QC model suites;
- add runner heartbeat/offline notification.

### P2

- split or resume slow analog snowfall enrichment;
- add manifest-reachability cleanup for Pages assets;
- coalesce closely spaced Pages publishes;
- add a C3S component drill-down and richer provenance panel;
- add image-regression baselines for every canonical map family.

Success should be measured by source-ready-to-live latency, complete current
surface percentage, failed/partial cycle rate, runner online percentage,
median and p95 workflow duration, Pages branch growth, mobile horizontal
overflow, and Compare-map time-to-first-visible-content.
