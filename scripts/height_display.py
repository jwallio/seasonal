"""Shared 500-mb display styling for all seasonal provider adapters.

The map uses 10-metre labelled transitions so the near-zero signal remains
visible instead of being swallowed by a broad neutral swatch.  The fixed
range is deliberately symmetric and shared by monthly, seasonal, CONUS, and
northern-hemisphere views. Each 10-metre interval remains a discrete band;
colors darken monotonically away from zero on each side.
"""

HEIGHT_ANOMALY_TICKS = list(range(-120, 121, 10))
HEIGHT_ANOMALY_PALETTE = [
    "#0f2b60", "#173b7a", "#1b4c93", "#205dad",
    "#2889c7", "#3e9acf", "#6fbce0", "#91cce8",
    "#aed9ed", "#c6e4f2", "#cce7f4", "#eef6f9",
    "#fffbe0", "#ffeebe", "#ffe29e", "#ffd284",
    "#ffbe69", "#ffa34e", "#f68443", "#e15b31",
    "#c63525", "#b71f22", "#9e1e1f", "#84181d",
]
HEIGHT_ANOMALY_STYLE = {
    "anomaly_min": -120.0,
    "anomaly_max": 120.0,
    "anomaly_ticks": HEIGHT_ANOMALY_TICKS,
    "anomaly_bounds": HEIGHT_ANOMALY_TICKS,
    "anomaly_palette": HEIGHT_ANOMALY_PALETTE,
    # Preserve visible contour bands; continuity refers to color ordering.
    "anomaly_continuous": False,
    "anomaly_endpoint_labels": {"minimum": "≤−120", "maximum": "≥+120"},
}
# Same pole orientation as the approved SEAS5 image, with room for the Keys
# and southern Texas at the lower edge. Source coverage remains hemispheric.
HEIGHT_NH_FRAME = {
    "projection": "north_polar_stereographic",
    "projection_central_longitude": -100.0,
    "polar_frame_latitude": 24.0,
}
