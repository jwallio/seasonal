# Native snowfall in the normal automatic product

This supersedes the snapshot-only release: the existing `snowfall_accumulation`
product in Overview/Explore and the CFSv2 viewer now uses native SRWEQ with
verified CIPS CWA means. The normal `cfsv2.yml` scheduled suite already includes
this product; its schedule, runner and permissions do not change. A targeted
manual accumulation run generates December–March and DJF/JFM for this release.
The front-page and model-page snapshot links are removed. Dated snapshot URLs
remain available to avoid breaking previously shared links.

## Data and rendering

- `cfsv2_native_snow.py` retrieves only the exact surface SRWEQ GRIB message
  using checked HTTP 206 ranges, with bounded sizes, timeouts and retries.
- It verifies source initialization, lead, units, surface level and corrected
  calendar-month metadata; integrates kg m-2 s-1 using actual month seconds /25.4.
- Complete identical requested cycles are required. An incomplete native cycle
  fails the product even when the general partial-rolling option is enabled.
  The successful-only payload/publisher path retains the prior release.
- Native decoded states and provenance have a separate versioned cache. Valid
  cached states are used first, with grid and SHA256 checks. No old phase-derived
  cache is substituted. Ratios are applied after the linear native ensemble mean.
- The packaged CWA lookup is derived from the previously verified boundary release
  and 97 chart means. Runtime needs only existing NumPy/matplotlib/requests.
  `build_cfsv2_cwa_lookup.py` documents its offline rebuild; changed model axes fail
  rather than silently using the wrong cells. Full source provenance is in its JSON.
- Nineteen CWAs remain missing. The generic accumulation finite-coverage floor
  changes from 2% to 1% of the global grid because the exact supported mask occupies
  829/72,960 cells (~1.14%). Native inputs must still be globally finite/nonnegative;
  missingness is introduced solely by the verified ratio mask.
- Rendering retains Style A bins/colors, light hatching, full-CONUS framing and
  no additional smoothing/contours. The display samples native LWE before CWA
  assignment; numerical depth and paired native LWE downloads use original grids.
- Monthly/seasonal IDs, filenames and selectors are preserved. Manifests and catalog
  carry native source, method, coverage, download paths and limitations. Full-size
  dashboard images get the same catalog version treatment as thumbnails, so a
  repaired image under an existing run path does not retain an older cached map.

Snowfall departure remains the separate phase-derived LWE product and is not a
reference for the native accumulation. It is not silently subtracted or relabeled.
Excessive native totals remain uncorrected as agreed; no new calibration is added.

## Verification

- Six numerical/contract tests cover year rollover, leap February, wrong levels
  and leads, native routing, missing/negative inputs, incomplete cycles, ratio
  masks, axis changes and seasonal identity.
- Existing CFSv2, catalog, dashboard and Actions contracts pass.
- An isolated execution of the actual `cfsv2_seasonal.py` CLI used 72 retained,
  hash-verified native inputs: all January/February/March/JFM products rendered
  with 24/24 cycles, proper source metadata and numerical downloads. Every depth
  grid matches the accepted preview to 1e-10 inch, including missingness. No
  re-download or hindcast work was needed for that replay.
- A JFM graphic from that actual generator was visually inspected.
- A live targeted operational-winter run and its central publication are required
  after the source push; completion is reported with their run references.

Rollback restores the previous generator/viewer files and republishes the retained
last-good accumulation payload through the central publisher. Do not rewrite Git
history or delete the new native cache. Retain source method labels on old runs.
