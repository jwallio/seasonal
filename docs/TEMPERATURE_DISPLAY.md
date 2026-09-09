# Seasonal temperature display

All supported 850-mb and 2-m temperature-anomaly maps use one shared −6°C to +6°C scale in 0.5°C bands. Every boundary, including ±0.5°C, is labeled on the colorbar and is paired with a visible gradient color; no half-degree band is rendered as pure white. Negative anomalies progress through blue and pale blue, near-zero anomalies transition through pale blue and cream, and positive anomalies progress through salmon, orange, and red.

The 24 colors align with 25 boundaries. Values outside the scale saturate at the endpoint colors; forecast values and downloaded numerical grids are unchanged. Monthly and seasonal products use the same scale, colorbar formatting, CONUS frame, and renderer. Existing images acquire the new style when rerendered. The temperature-style-refresh workflow refreshes supported products and publishes successful producer runs.
