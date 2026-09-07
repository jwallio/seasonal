"""Snowfall image units; numerical anomaly inputs remain inches water equivalent."""
from copy import deepcopy

RATIO = 10.0
DISPLAY = {
    "quantity": "estimated snowfall depth departure", "units": "in",
    "snow_to_liquid_ratio": RATIO, "scale_inches": [-10, 10],
    "white_band_inches": [-1, 1],
    "canonical_grid_quantity": "snowfall liquid-water-equivalent departure",
    "numeric_grid_quantity": "estimated snowfall depth departure",
    "calendar_alignment_version": 2,
    "native_blend_version": 1,
}


def depth_departure(grid, product, palette):
    """Convert once at the rendering boundary, including common-reference maps."""
    if product["name"] != "snowfall_anomaly" or product.get("native_snow_depth_display"):
        return grid, product
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
        header_detail="{source_label}  •  Snowfall departure (in)  •  10:1 snow-depth estimate",
    )
    source = str(spec.get("source_label", "NOAA CFSv2 / NOMADS"))
    kind = ("Native/derived blend" if "super ensemble" in source.lower() else
            "Derived snowfall" if "CFSv2" in source else "Native model snowfall")
    kind = spec.get("snowfall_input_kind", kind)
    spec["header_detail"] = "{source_label}  •  " + kind + "  •  10:1 snow-depth estimate"
    title = spec.get("title", "Snowfall Departure").replace("Estimated Snowfall", "Snowfall").replace("Derived Snowfall", "Snowfall")
    if product.get("estimated_snow_depth"):
        title = product.get("title", "Estimated Snowfall Departure")
    spec["title"] = title
    if "(in)" not in title:
        spec["title"] = title + " (in)"
    return type(grid)(grid.lons[:], grid.lats[:],
                      [[v * RATIO for v in row] for row in grid.values]), spec
