"""Shared 500-mb display styling for all seasonal provider adapters.

The map uses 10-metre labelled transitions so the near-zero signal remains
visible instead of being swallowed by a broad neutral swatch.  The fixed
range is deliberately symmetric and shared by monthly, seasonal, CONUS, and
northern-hemisphere views.  Fields interpolate smoothly between the shared
control colors at render time.
"""

HEIGHT_ANOMALY_TICKS = list(range(-120, 121, 10))
HEIGHT_ANOMALY_PALETTE = [
    "#0f2b60", "#173b7a", "#173b7a", "#205dad",
    "#2889c7", "#3e9acf", "#6fbce0", "#91cce8",
    "#aed9ed", "#c6e4f2", "#cce7f4", "#eef6f9",
    "#fffbe0", "#ffeebe", "#ffe29e", "#ffd284",
    "#ffbe69", "#ffa34e", "#f68443", "#e15b31",
    "#c63525", "#9e1e1f", "#b71f22", "#84181d",
]
HEIGHT_ANOMALY_STYLE = {
    "anomaly_min": -120.0,
    "anomaly_max": 120.0,
    "anomaly_ticks": HEIGHT_ANOMALY_TICKS,
    "anomaly_bounds": HEIGHT_ANOMALY_TICKS,
    "anomaly_palette": HEIGHT_ANOMALY_PALETTE,
    # Keep the approved palette as the control points, but interpolate
    # between them in the field so adjacent 10-m bands do not render as
    # hard-edged swatches.
    "anomaly_continuous": True,
    "anomaly_endpoint_labels": {"minimum": "≤−120", "maximum": "≥+120"},
}
# Same pole orientation as the approved SEAS5 image, with room for the Keys
# and southern Texas at the lower edge. Source coverage remains hemispheric.
HEIGHT_NH_FRAME = {
    "projection": "north_polar_stereographic",
    "projection_central_longitude": -100.0,
    "polar_frame_latitude": 24.0,
}
