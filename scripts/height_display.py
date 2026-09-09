"""Shared 500-mb display styling; values and regional NA framing are unchanged."""

HEIGHT_ANOMALY_TICKS = [-200, -150, -100, -75, -50, -30, -20, -10, 0,
                        10, 20, 30, 50, 75, 100, 150, 200]
HEIGHT_ANOMALY_PALETTE = [
    "#173b7a", "#205daa", "#2889c7", "#65b6de",
    "#a6d9ec", "#d4eaf2", "#eef3f5", "#ffffff",
    "#ffffff", "#f2f2ed", "#ffffc2", "#ffe39a",
    "#ffbd72", "#f5804b", "#d94128", "#9e1e20",
]
HEIGHT_ANOMALY_STYLE = {
    "anomaly_min": -200.0,
    "anomaly_max": 200.0,
    "anomaly_ticks": HEIGHT_ANOMALY_TICKS,
    "anomaly_bounds": HEIGHT_ANOMALY_TICKS,
    "anomaly_palette": HEIGHT_ANOMALY_PALETTE,
    "anomaly_endpoint_labels": {"minimum": "≤−200", "maximum": "≥+200"},
}
# Same pole orientation as the approved SEAS5 image, with room for the Keys
# and southern Texas at the lower edge. Source coverage remains hemispheric.
HEIGHT_NH_FRAME = {
    "projection": "north_polar_stereographic",
    "projection_central_longitude": -100.0,
    "polar_frame_latitude": 24.0,
}
