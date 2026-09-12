"""Convert snowfall liquid-water-equivalent departures to snow-depth inches."""
from copy import deepcopy

RATIO = 10.0

# The broad profile is retained for SFS and other products whose seasonal
# mountain departures can exceed ten inches. CFSv2 and C3S explicitly opt into
# the compact profile below so their legends match the readable reference
# product: one-inch bands through ±8, then two-inch bands through ±14.
BROAD_BOUNDS = [
    -100, -80, -60, -45, -35, -25, -15, -10, -5, -1, 0,
    1, 5, 10, 15, 25, 35, 45, 60, 80, 100,
]
BROAD_TICKS = [-100, -60, -35, -15, -1, 0, 1, 15, 35, 60, 100]
# Use one-inch bins through ±8, then two-inch bins at ±10, ±12, and ±14.
COMPACT_BOUNDS = [-14, -12, -10, -8, *range(-7, 9), 10, 12, 14]
COMPACT_TICKS = list(COMPACT_BOUNDS)

DISPLAY = {
    "quantity": "estimated snowfall depth departure", "units": "in",
    "snow_to_liquid_ratio": RATIO, "scale_inches": [-100, 100],
    "scale_profile": "broad_nonlinear",
    "scale_bounds_inches": list(BROAD_BOUNDS),
    "legend_ticks_inches": list(BROAD_TICKS),
    "white_band_inches": [-1, 1],
    "canonical_grid_quantity": "snowfall liquid-water-equivalent departure",
    "canonical_grid_units": "inches liquid-water equivalent",
    "numeric_grid_quantity": "estimated snowfall depth departure",
    "numeric_grid_units": "inches snow",
    "conversion_kind": "snowfall_lwe_to_snow_depth_10_to_1",
    "conversion": "estimated snow-depth departure (in) = snowfall LWE departure (in) × 10.0",
    "calendar_alignment_version": 2,
    "native_blend_version": 1,
}

COMPACT_DISPLAY_PROFILE = "c3s_readable"


def display_metadata_for_product(product=None):
    """Return metadata for the snowfall image profile selected by a product."""
    if product and product.get("snowfall_display_profile") == COMPACT_DISPLAY_PROFILE:
        metadata = deepcopy(DISPLAY)
        metadata.update(
            scale_inches=[COMPACT_BOUNDS[0], COMPACT_BOUNDS[-1]],
            scale_profile=COMPACT_DISPLAY_PROFILE,
            scale_bounds_inches=list(COMPACT_BOUNDS),
            legend_ticks_inches=list(COMPACT_TICKS),
        )
        return metadata
    return deepcopy(DISPLAY)


def convert_lwe_to_snow_depth(grid):
    """Return a signed snow-depth departure grid using the fixed 10:1 ratio."""
    return type(grid)(grid.lons[:], grid.lats[:],
                      [[value * RATIO for value in row] for row in grid.values])


def depth_departure(grid, product, palette):
    """Return an inches-of-snow grid and its discrete display specification.

    Most providers keep their comparison math in LWE and arrive here with an
    LWE grid.  Native adapters that already converted their published grid set
    ``snowfall_values_are_depth`` so this helper remains idempotent.
    """
    if product["name"] != "snowfall_anomaly":
        return grid, product
    values_are_depth = bool(product.get("snowfall_values_are_depth"))
    spec = deepcopy(product)
    for key in list(spec):
        if key.startswith(("monthly_anomaly_", "seasonal_anomaly_")):
            del spec[key]
    # CFSv2 and C3S use the compact C3S-style one-inch legend. Other
    # providers retain the broader nonlinear profile unless they opt in.
    compact = product.get("snowfall_display_profile") == COMPACT_DISPLAY_PROFILE
    bounds = list(COMPACT_BOUNDS if compact else BROAD_BOUNDS)
    ticks = list(COMPACT_TICKS if compact else BROAD_TICKS)
    scale_min, scale_max = bounds[0], bounds[-1]
    if len(palette) == len(bounds) - 1:
        display_palette = list(palette)
    else:
        # The broad profile uses the legacy 20-band subset of the shared palette.
        display_palette = [*palette[:9], "#ffffff", "#ffffff", *palette[13:]]
    if len(display_palette) != len(bounds) - 1:
        raise ValueError("snowfall palette length must match display intervals")
    spec.update(
        anomaly_min=scale_min, anomaly_max=scale_max, anomaly_ticks=ticks, anomaly_bounds=bounds,
        anomaly_palette=display_palette,
        anomaly_endpoint_labels={"minimum": f"≤−{abs(scale_min)}", "maximum": f"≥+{scale_max}"},
        anomaly_tick_decimals=0, native_snow_depth_display=True,
        snowfall_values_are_depth=True,
        snowfall_input_units="inches liquid-water equivalent",
        snowfall_output_units="inches snow",
        conversion_kind="snowfall_lwe_to_snow_depth_10_to_1",
        conversion="estimated snow-depth departure (in) = snowfall LWE departure (in) × 10.0",
        header_detail="{source_label}  •  Snowfall LWE departure × 10 = estimated snow depth (in)  •  fixed 10:1 ratio",
    )
    spec["header_detail"] = (
        "{source_label}  •  Snowfall LWE departure × 10 = estimated snow depth (in)"
        "  •  fixed 10:1 ratio"
    )
    title = (spec.get("title", "Snowfall Departure")
             .replace("Estimated Snowfall", "Snowfall")
             .replace("Derived Snowfall", "Snowfall")
             .replace(" (in LWE)", "")
             .replace(" (in snow)", "")
             .replace(" (in)", ""))
    spec["title"] = title + " (in)"
    return (grid if values_are_depth else convert_lwe_to_snow_depth(grid)), spec
