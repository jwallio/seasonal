"""Convert snowfall liquid-water-equivalent departures to snow-depth inches."""
from copy import deepcopy

RATIO = 10.0
DISPLAY = {
    "quantity": "estimated snowfall depth departure", "units": "in",
    "snow_to_liquid_ratio": RATIO, "scale_inches": [-10, 10],
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
    ticks = list(range(-10, 11))
    spec.update(
        anomaly_min=-10, anomaly_max=10, anomaly_ticks=ticks, anomaly_bounds=ticks,
        anomaly_palette=[*palette[:9], "#ffffff", "#ffffff", *palette[13:]],
        anomaly_endpoint_labels={"minimum": "≤−10", "maximum": "≥+10"},
        anomaly_tick_decimals=0, native_snow_depth_display=True,
        snowfall_values_are_depth=True,
        snowfall_input_units="inches liquid-water equivalent",
        snowfall_output_units="inches snow",
        conversion_kind="snowfall_lwe_to_snow_depth_10_to_1",
        conversion="estimated snow-depth departure (in) = snowfall LWE departure (in) × 10.0",
        header_detail="{source_label}  •  Snowfall depth departure (in)  •  LWE × 10 at a fixed 10:1 ratio",
    )
    source = str(spec.get("source_label", "NOAA CFSv2 / NOMADS"))
    kind = ("Native/derived blend" if "super ensemble" in source.lower() else
            "Derived snowfall" if "CFSv2" in source else "Native model snowfall")
    kind = spec.get("snowfall_input_kind", kind)
    spec["header_detail"] = "{source_label}  •  " + kind + "  •  LWE × 10 = estimated snow depth (in)  •  fixed 10:1 ratio"
    title = (spec.get("title", "Snowfall Departure")
             .replace("Estimated Snowfall", "Snowfall")
             .replace("Derived Snowfall", "Snowfall")
             .replace(" (in LWE)", "")
             .replace(" (in snow)", "")
             .replace(" (in)", ""))
    spec["title"] = title + " (in snow)"
    return (grid if values_are_depth else convert_lwe_to_snow_depth(grid)), spec
