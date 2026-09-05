# Native snowfall snapshot release

Publishes the approved Style A maps at `/seasonal/cfsv2/native-snowfall/`, linked
from the dashboard and CFSv2 viewer. This is a frozen experimental September 4,
2026 12Z run, with January, February, March and JFM 2027 products. It is not
an automatic-pipeline replacement. Existing model manifests and forecasting
logic are unchanged. Excessive totals and departure-reference work remain open.

## Scope and provenance

Source baseline: main `88ca4765cf1e8661a25e395759533875e857cbd2`. Work used an
isolated checkout on `codex/native-snow-site-20260905`; prior untracked preview
and historical-reference work was preserved in its original checkout.

Retained SRWEQ fields use 24 identical cycles for every month, August 29 18Z
through September 4 12Z. Native rate is integrated over actual month lengths;
water equivalent is multiplied by 97 verified CIPS CWA mean ratios. Nineteen
CONUS CWAs remain unavailable. The source registry, exact source records,
retrieval time, rendering time, boundary provenance and 12 asset hashes ship
in the snapshot's provenance.json. Publication and source initialization are
distinct; the page explicitly states it does not refresh automatically.

Maps preserve the original discrete palette, bins, rectangular legend, light
hatching and full-CONUS frame. No added contours or smoothing. The only image
change from the approved preview is replacing UNPUBLISHED PREVIEW with
EXPERIMENTAL ESTIMATE. Numerical downloads are byte-identical. Display pixels
use existing bilinear LWE sampling and exact CWA assignment; downloads retain
the original grid. This distinction is explained on the page.

## Reproduction and validation

Offline rendering uses `scripts/build_native_snow_snapshot.py --retained-inputs
<retained-preview-root> --output <new-snapshot-directory>`. It requires the
existing map environment with numpy, matplotlib, shapely and pyshp. No new
dependencies were installed or added to production requirements. The renderer
is intentionally restricted to the reviewed frozen run and makes no downloads.

Executed with the existing Conda Python:

- Offline builder: complete-cycle provenance and approved display/grid hashes passed.
- `tests/test_native_snow_snapshot.py`: 12 hashes, four PNG decodes, eight grid
  geometries, nonnegative finite native LWE, JFM sums and missingness passed.
- `tests/test_seasonal_actions_contract.py`: passed.
- `tests/test_seasonal_dashboard_contract.py`: passed.
- Pixel comparison against the accepted four maps: identical outside the
  status line, including all map, title, legend and footer pixels.
- `git -c core.autocrlf=false -c core.whitespace=cr-at-eol diff --check`: passed.
- In-app Chromium: all periods, 390/768/1440 widths, no horizontal overflow,
  matching graphic/download/grid paths, and back/forward/reload passed.
  No physical iPhone or WebKit check was performed.

An initial local render exposed a Windows default-encoding problem. It was
rejected before publication and regenerated using explicit UTF-8. Working-tree
line endings were preserved when staging existing mixed-ending files.

## Publishing and rollback

The existing serialized `publish-pages.yml` gains a manual static-page option:
Native Snowfall Estimate Page, with source_run_id 0. No model workflow is
dispatched. The snapshot overlay happens after model payloads, and a read-only
asset/numerical validator blocks publication if it fails. Existing catalog,
share-image and thumbnail checks still run. Subsequent model releases retain
this page through the same source overlay.

The source commit uses `[skip ci]` to avoid the unrelated WeatherNext full
render triggered by any public-directory change. Repository contracts are
dispatched explicitly, followed by the central publisher. No schedules,
permissions, runners or repository visibility are changed.

For rollback, remove the two navigation links and restore the prior public
page through the central publisher, retaining the dated assets for existing
links. Do not delete model products or rewrite gh-pages history. A future
scientific correction must get a new versioned directory and updated
provenance; it must not overwrite this frozen run in place.

## Next: excessive native totals

Investigate the retained native LWE and precipitation first, separating native
snow amount from ratio amplification. Recheck record identity, averaging and
month integration, then report how much each contributes at representative
grid cells. Do not use a visual cap, arbitrary ratio reduction, or annual normal
as a monthly/seasonal correction. Historical-reference work stays paused;
any correction requires a separately documented reference and method.
