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
    "#082552",
    "#103870",
    "#174986",
    "#1b5b9e",
    "#206db5",
    "#287fc7",
    "#3693d6",
    "#4ba6e2",
    "#66b8ea",
    "#c7e8fa",
    "#eaf4f8",
    "#dfeff3",
    "#fff4dc",
    "#f9d1c9",
    "#e7685f",
    "#dc4a44",
    "#cd3436",
    "#b92230",
    "#a5162a",
    "#901024",
    "#790c20",
    "#62091b",
    "#4c0616",
    "#3b0411",
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
