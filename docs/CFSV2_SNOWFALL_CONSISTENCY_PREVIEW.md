# Unpublished CFSv2 snowfall consistency preview

This preview is isolated from the production renderer and model workflow. It
implements an explicitly approved **monthly-mean input approximation** on both
forecast and reference sides. It does not reconstruct member-level historical
snowfall or calibrate totals against observed snowfall normals.

## Frozen input set

- Initialization: September 4, 2026 12Z; 24 paired cycles.
- Target months: January, February and March 2027; summed for JFM.
- Fields: monthly mean 2-m temperature, 850-hPa temperature and precipitation.
- Exact initialization/lead reference fields: NCEI 1982–2010 calibration.
- Source cache: `cfsv2-rolling-33934463395`, from the successful snowfall
  departure run. The earlier accumulation-only cache did not contain the
  matching reference CSVs; extraction failed closed before artifact upload.
- Successful input extraction: https://github.com/jwallio/seasonal/actions/runs/33938072457
- Artifact: `cfsv2-snow-preview-inputs`, about 11 MB. Provenance contains hashes
  for 216 rolling input files and nine decoded calibration fields.

The workflow restores but does not save or modify the upstream cache. Its
export script reads cached fields and the public source manifest, with no NOAA
downloads, new model runs, historical archive ingestion, or Pages publication.

## Calculation

For each month, average the 24 input grids before applying the existing snow
phase equation. Apply exactly the same operation to the climatological mean
input fields. Convert both resulting LWE fields to snowfall depth with the same
monthly ratio grid, then subtract. Sum January, February and March separately
for total, reference and departure. Missing monthly cells propagate as missing.

The reference is **derived snowfall from mean climate inputs**, not the mean
of historical derived snowfall. Matching the calculation order removes the
previous mean-of-derived versus derived-from-means inconsistency by changing
the forecast estimate. It does not recover the missing historical joint
distribution or solve the physical limitations of using monthly mean weather
to estimate precipitation-event snow phase.

## View and reproduce

The local preview includes 12 maps (total/reference/departure for four periods),
the original cycle-level JFM accumulation reconstructed from the same inputs,
downloadable grids, provenance, and a table of nearest model-grid cells.
All triplet maps use inches of estimated accumulated snowfall depth; these
are not standing snowpack amounts. Total and reference share identical scales.
The original discrete accumulation bands remain intact. Departure uses brown/
blue bands with fine near-zero intervals and explicit signed limits.

```powershell
python -B scripts/preview_cfsv2_snowfall.py `
  --inputs 'PATH\TO\preview-inputs' `
  --output 'NEW\NONPUBLISHED\OUTPUT' `
  --borders 'PATH\TO\us-states.geojson'
```

The renderer refuses an existing output directory or the repository's public
directory. Open `index.html` from the output directory. The supplied preview
is under `D:\weather-projects\_previews\seasonal-snow-consistency-20260904\v1`.

## Verified results

- Seven new offline cases check zero departure for identical inputs, the
  total/reference/departure identity, zero precipitation, incomplete seasonal
  cells, March handling, matched absolute scales and valid signed bins.
- All 26 repository test scripts passed locally.
- Seasonal arithmetic residual: at most 5.7e-14 inches before rounding.
- Reconstructed original JFM min/max/p01/p99 match the published statistics
  within 1e-5 inches. This is not a pixel-by-pixel comparison of original JPEGs.
- Forty-eight Chromium browser combinations passed: 390/768/1440 widths,
  four periods and four view modes. Download selection and expansion passed.
  These are viewport tests, not a physical iPhone or WebKit test.

For the nearest grid cell to State College, JFM previous total is 201.0 inches;
the preview total is 205.0, the reference 156.9, and departure +48.1. Near
Pittsburgh, the preview is 192.1 = 151.2 + 40.8 inches. These are model grid
values, not station forecasts. They demonstrate internal consistency while
exposing the very large synthetic reference. The change must not be presented
as a correction toward observed snowfall amounts.

No production methods, public map assets, schedules or deployment changed.
Preview files are committed only on the isolated preview branch. Further
adoption is a separate decision after reviewing this approximation and its
remaining limitations; rolling back the preview requires no production action.
