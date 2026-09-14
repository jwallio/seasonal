# Canonical seasonal rendering contract

Public comparison maps resolve their visual style from only three inputs:
canonical product, monthly or seasonal aggregation, and geographic domain.
Provider adapters continue to own source discovery, decoding, units,
climatology, ensemble construction, and scientific aggregation, but they cannot
select a public palette, range, projection, crop, or legend geometry.

`scripts/seasonal_products.py` is the scientific product registry.
`scripts/seasonal_rendering.py` combines that registry with the approved shared
palettes and fixed map layouts. `scripts/cfsv2_seasonal.py` remains the common
renderer. Every provider spec is canonicalized again immediately before
rendering, so a stale or provider-local visual field cannot alter the output.

## Fixed anomaly scales

| Product | Monthly | Seasonal | Domain |
| --- | --- | --- | --- |
| 500-mb geopotential height | −120 to +120 m, 10-m bands | same | North America; separate Northern Hemisphere view |
| 850-mb and 2-m temperature | −6 to +6 °C, 0.5-°C bands | same | CONUS |
| Precipitation accumulation anomaly | −4 to +4 in, 0.5-in bands | −8 to +8 in, 1-in bands | CONUS |
| Estimated snowfall-depth departure | −14 to +14 in; 1-in center bands and wider outer bands | −20 to +20 in, 2-in bands | fitted lower-48 land mask |
| Mean sea-level pressure | −5 to +5 hPa, 0.5-hPa boundaries | same | CONUS |

Monthly snowfall has a white −1 to +1 inch center band. Seasonal snowfall has
a white −2 to +2 inch center band because its approved intervals are two inches
wide. These bands describe estimated snow depth after the single 10:1 display
conversion.

The MSLP interval from −0.5 through +0.5 hPa is one neutral band. Both −0.5
and +0.5 are explicit boundaries and zero remains a labelled midpoint. This
keeps small pressure signals neutral without deleting either adjacent signal
class.

Snowfall comparison math and `.lwe.csv.gz` sidecars remain signed liquid-water
equivalent inches. The renderer applies the established fixed 10:1
liquid-to-snow ratio exactly once; `.snow.csv.gz` and the image are estimated
snow-depth inches. Provider identity cannot select a wider or narrower snowfall
profile. Monthly and seasonal styles differ because their aggregations differ.

Native CFSv2 snowfall accumulation is a distinct positive-only quantity. It
uses the approved nonlinear 0–180-inch monthly and seasonal boundary sets and
a separate `180+` overflow cell; it is never treated as a departure.

## Overflow and exact boundaries

Each anomaly legend has rectangular underflow and overflow cells of the same
width as ordinary bins. They use darker hues that are distinct from the
endpoint-bin colors. Labels use the compact `≤−cap` and `cap+` convention.
Exact cap values remain in the ordinary endpoint bins; only values strictly
outside the range use overflow colors. Exact inclusive internal boundaries,
including +0.5 hPa on MSLP, are assigned deterministically. Forecast arrays and
downloadable numerical grids are never clipped or capped for display.

## Geometry and cache identity

The approved CFSv2 layouts are fixed allocations rather than tight-layout
results. North America, Northern Hemisphere, CONUS, and fitted lower-48 panels
therefore keep identical map and colorbar axes across providers. Core maps are
1080×1080 pixels, snowfall departures are 1080×845, and native accumulation is
1080×882. Provider titles and provenance text may change, but they cannot move
the map, resize the legend, or change the crop.

Source/cache variables may be shared between domain variants, but public
artifact tokens may not. The explicit Northern Hemisphere height product is
always suffixed `-nh`; strict catalog validation rejects any image path shared
by distinct products so a polar render cannot overwrite the North America map.

Every resolved style is serialized as sorted canonical JSON and identified by
a SHA-256 fingerprint. The catalog publishes the complete `render_styles`
registry, while each target records its style key and fingerprint. The
dashboard uses the published pixel dimensions to reserve the correct aspect
ratio before an image loads. Changes to any shared visual contract dispatch one
complete supported-product refresh for every active provider workflow.
