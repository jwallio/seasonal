"""Canonical snowfall display and LWE-to-depth conversion contracts.

Snowfall departures remain in liquid-water equivalent for provider comparison
math. Public numeric grids and maps convert the signed departure once to an
estimated snow-depth departure with the established fixed 10:1 ratio. Display
selection is based only on aggregation period, never provider identity.
"""

from copy import deepcopy


RATIO = 10.0
MONTHLY_WHITE_BAND = [-1.0, 1.0]
SEASONAL_WHITE_BAND = [-2.0, 2.0]

# Approved CFSv2 snowfall-departure presentation. Monthly maps retain one-inch
# bins through +/-8 inches and two-inch outer bins through +/-14. Three-month
# totals use two-inch bins through +/-20. The seasonal palette is the matching
# 20-band subset of the monthly palette, retaining the two white near-zero
# intervals.
MONTHLY_BOUNDS = [-14, -12, -10, -8, *range(-7, 9), 10, 12, 14]
MONTHLY_TICKS = list(MONTHLY_BOUNDS)
MONTHLY_PALETTE = [
    "#572308", "#6b2d0c", "#7b370d", "#8c4712", "#9d5517", "#ae691f",
    "#bd7d34", "#ca9156", "#d7a875", "#e3c99a", "#ffffff", "#ffffff",
    "#b9dce8", "#96c9d7", "#75b8cc", "#5ca5bd", "#4a93b2", "#3a80a5",
    "#2e6d93", "#245b83", "#1b496e", "#123856",
]
SEASONAL_BOUNDS = list(range(-20, 21, 2))
SEASONAL_TICKS = list(SEASONAL_BOUNDS)
SEASONAL_PALETTE = [*MONTHLY_PALETTE[:9], "#ffffff", "#ffffff", *MONTHLY_PALETTE[13:]]

# Backwards-compatible names used by older retained-data helpers. They now
# identify the one canonical monthly style rather than a provider opt-in.
COMPACT_BOUNDS = MONTHLY_BOUNDS
COMPACT_TICKS = MONTHLY_TICKS

# Native positive-only accumulation bounds are scientifically distinct from a
# departure. They retain the approved CFSv2 scales and a separate upper
# overflow state is supplied by the shared rendering contract.
ACCUMULATION_MONTHLY_BOUNDS = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 24,
    28, 32, 36, 42, 48, 60, 72, 84, 96, 120, 144, 180,
]
ACCUMULATION_SEASONAL_BOUNDS = [
    0, 1, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 42, 48, 54, 60, 66,
    72, 84, 96, 108, 120, 132, 144, 150, 156, 162, 168, 174, 180,
]
ACCUMULATION_PALETTE = [
    "#ffffff", "#e1f2ff", "#86c7fa", "#4ba4f3", "#287eee", "#0c5dc9",
    "#4b0392", "#5a048d", "#67038d", "#870389", "#c7037f", "#f4067c",
    "#f62f94", "#f962ac", "#f789c2", "#ed97ca", "#dca7d3", "#cdbbdc",
    "#bdcbe4", "#a6e5ed", "#a0f2f4", "#98f9f7", "#90f1ed", "#81d8d7",
    "#7cb9c9", "#89b1d3", "#9ea5db", "#ac9ae4", "#bc92ed", "#c48df1",
]

DISPLAY = {
    "quantity": "estimated snowfall depth departure",
    "units": "in",
    "snow_to_liquid_ratio": RATIO,
    "canonical_grid_quantity": "snowfall liquid-water-equivalent departure",
    "canonical_grid_units": "inches liquid-water equivalent",
    "numeric_grid_quantity": "estimated snowfall depth departure",
    "numeric_grid_units": "inches snow",
    "conversion_kind": "snowfall_lwe_to_snow_depth_10_to_1",
    "conversion": "estimated snow-depth departure (in) = snowfall LWE departure (in) x 10.0",
    "calendar_alignment_version": 2,
    "native_blend_version": 1,
}


def departure_style(*, seasonal: bool = False) -> dict:
    """Return the provider-independent display scale for one aggregation."""

    bounds = SEASONAL_BOUNDS if seasonal else MONTHLY_BOUNDS
    ticks = SEASONAL_TICKS if seasonal else MONTHLY_TICKS
    palette = SEASONAL_PALETTE if seasonal else MONTHLY_PALETTE
    return {
        "bounds": list(bounds),
        "ticks": list(ticks),
        "palette": list(palette),
        "minimum": float(bounds[0]),
        "maximum": float(bounds[-1]),
        "profile": "seasonal_snowfall_departure" if seasonal else "monthly_snowfall_departure",
    }


def accumulation_style(*, seasonal: bool = False) -> dict:
    """Return the fixed positive-only native snowfall accumulation scale."""

    bounds = ACCUMULATION_SEASONAL_BOUNDS if seasonal else ACCUMULATION_MONTHLY_BOUNDS
    return {
        "bounds": list(bounds),
        "ticks": list(bounds),
        "palette": list(ACCUMULATION_PALETTE),
        "minimum": float(bounds[0]),
        "maximum": float(bounds[-1]),
        "profile": "seasonal_snowfall_accumulation" if seasonal else "monthly_snowfall_accumulation",
    }


def display_metadata_for_product(product=None, *, seasonal: bool = False) -> dict:
    """Return canonical snowfall metadata; ``product`` cannot select a style."""

    del product
    style = departure_style(seasonal=seasonal)
    metadata = deepcopy(DISPLAY)
    metadata.update(
        white_band_inches=list(SEASONAL_WHITE_BAND if seasonal else MONTHLY_WHITE_BAND),
        scale_inches=[style["minimum"], style["maximum"]],
        scale_profile=style["profile"],
        scale_bounds_inches=style["bounds"],
        legend_ticks_inches=style["ticks"],
    )
    return metadata


def display_metadata_for_aggregations(product=None) -> dict:
    """Return common snowfall metadata plus exact monthly/seasonal profiles."""

    metadata = deepcopy(DISPLAY)
    metadata["aggregation_styles"] = {
        "monthly": display_metadata_for_product(product, seasonal=False),
        "seasonal": display_metadata_for_product(product, seasonal=True),
    }
    return metadata


def convert_lwe_to_snow_depth(grid):
    """Return a signed snow-depth departure grid using the fixed 10:1 ratio."""

    return type(grid)(
        grid.lons[:],
        grid.lats[:],
        [[value * RATIO for value in row] for row in grid.values],
    )


def depth_departure(grid, product, palette=None, *, seasonal: bool = False):
    """Return an inches-of-snow grid and canonical discrete display metadata.

    ``palette`` is accepted only for compatibility with older adapter calls;
    provider input cannot change the public style. Native adapters that have
    already converted their published grid set ``snowfall_values_are_depth``
    so the conversion remains idempotent.
    """

    del palette
    if product["name"] != "snowfall_anomaly":
        return grid, product
    values_are_depth = bool(product.get("snowfall_values_are_depth"))
    spec = deepcopy(product)
    for key in list(spec):
        if key.startswith(("monthly_anomaly_", "seasonal_anomaly_")) or key == "snowfall_display_profile":
            del spec[key]
    style = departure_style(seasonal=seasonal)
    scale_min = style["minimum"]
    scale_max = style["maximum"]
    spec.update(
        anomaly_min=scale_min,
        anomaly_max=scale_max,
        anomaly_ticks=style["ticks"],
        anomaly_bounds=style["bounds"],
        anomaly_palette=style["palette"],
        anomaly_endpoint_labels={"minimum": f"≤−{abs(scale_min):g}", "maximum": f"{scale_max:g}+"},
        anomaly_tick_decimals=0,
        native_snow_depth_display=True,
        snowfall_values_are_depth=True,
        snowfall_input_units="inches liquid-water equivalent",
        snowfall_output_units="inches snow",
        conversion_kind="snowfall_lwe_to_snow_depth_10_to_1",
        conversion="estimated snow-depth departure (in) = snowfall LWE departure (in) x 10.0",
        header_detail=(
            "{source_label}  •  Snowfall LWE departure x 10 = estimated snow depth (in)"
            "  •  fixed 10:1 ratio"
        ),
    )
    title = (
        spec.get("title", "Snowfall Departure")
        .replace("Estimated Snowfall", "Snowfall")
        .replace("Derived Snowfall", "Snowfall")
        .replace(" (in LWE)", "")
        .replace(" (in snow)", "")
        .replace(" (in)", "")
    )
    spec["title"] = title + " (in)"
    return (grid if values_are_depth else convert_lwe_to_snow_depth(grid)), spec
