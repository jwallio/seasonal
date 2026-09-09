"""Checks the shared half-degree temperature display contract."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from temperature_display import (
    TEMPERATURE_ANOMALY_MAX_C,
    TEMPERATURE_ANOMALY_MIN_C,
    TEMPERATURE_ANOMALY_PALETTE,
    TEMPERATURE_ANOMALY_STYLE,
    TEMPERATURE_ANOMALY_TICKS,
)

def main():
    assert (TEMPERATURE_ANOMALY_MIN_C, TEMPERATURE_ANOMALY_MAX_C) == (-6.0, 6.0)
    assert TEMPERATURE_ANOMALY_TICKS == [value / 2.0 for value in range(-12, 13)]
    assert len(TEMPERATURE_ANOMALY_PALETTE) == len(TEMPERATURE_ANOMALY_TICKS) - 1
    for boundary in (-0.5, 0.5):
        index = TEMPERATURE_ANOMALY_TICKS.index(boundary)
        assert TEMPERATURE_ANOMALY_PALETTE[index - 1].lower() != "#ffffff"
        assert TEMPERATURE_ANOMALY_PALETTE[index].lower() != "#ffffff"
    assert TEMPERATURE_ANOMALY_STYLE["anomaly_bounds"] is TEMPERATURE_ANOMALY_TICKS
    print("TEMPERATURE DISPLAY CONTRACT OK: shared range, half-degree labels, and visible ±0.5°C transitions")

if __name__ == "__main__":
    main()
