"""Shared temperature-anomaly colors and labels for every seasonal provider.

The fixed scale is deliberately shared by 850-mb and 2-m temperature maps so
a color has the same meaning in every model comparison. Each half-degree
interval has a visible (non-white) transition color, including the ±0.5°C
intervals.
"""
TEMPERATURE_ANOMALY_MIN_C = -6.0
TEMPERATURE_ANOMALY_MAX_C = 6.0
TEMPERATURE_ANOMALY_TICKS = [value / 2.0 for value in range(-12, 13)]
TEMPERATURE_ANOMALY_PALETTE = [
    "#24527a",
    "#2e678c",
    "#387b9e",
    "#428aab",
    "#4b94b2",
    "#559db9",
    "#60a7bf",
    "#6db0c4",
    "#87bdce",
    "#a9ceda",
    "#ccdee4",
    "#e2e4e6",
    "#ede0e1",
    "#f1d3d2",
    "#edbab8",
    "#e79e9d",
    "#e08888",
    "#db787a",
    "#d46a6e",
    "#cd5d64",
    "#c4505b",
    "#b54252",
    "#9c3548",
    "#84283f",
]
TEMPERATURE_ANOMALY_STYLE = {
    "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
    "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
    "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS,
    "anomaly_bounds": TEMPERATURE_ANOMALY_TICKS,
    "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
    "anomaly_endpoint_labels": {"minimum": "≤−6", "maximum": "≥+6"},
}

# Provider adapters import this module so a half-degree color means the same
# thing in every seasonal model and in every publication path.
# Keep edits here as the trigger for the shared styling refresh.
