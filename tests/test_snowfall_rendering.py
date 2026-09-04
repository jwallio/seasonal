#!/usr/bin/env python3
"""Offline numerical and actual-render checks for estimated snowfall colors."""

from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import QuadMesh
from matplotlib.backends.backend_agg import FigureCanvasAgg
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cfsv2_seasonal as cfsv2


def main() -> int:
    monthly_cmap, monthly_norm, monthly = cfsv2.snowfall_accumulation_style()
    seasonal_cmap, seasonal_norm, seasonal = cfsv2.snowfall_accumulation_style(True)
    amounts = np.linspace(0, 100, 1001)
    np.testing.assert_array_equal(
        monthly_cmap(monthly_norm(amounts)), seasonal_cmap(seasonal_norm(amounts))
    )
    assert monthly["maximum"] == 100 and seasonal["maximum"] == 200
    for is_seasonal, clipped_fraction in [(False, 0.5), (True, 0.0)]:
        qc = cfsv2.grid_quality_control(
            cfsv2.PRODUCT_SNOWFALL_ACCUMULATION, [[0, 100, 101, 200]],
            units="in", field="snowfall_accumulation", seasonal=is_seasonal,
        )
        assert qc["display"]["clipped_fraction"] == clipped_fraction
    assert cfsv2.SEASONAL_PRODUCTS[cfsv2.PRODUCT_SNOWFALL_ACCUMULATION]["hard_range"] == {"minimum": 0.0, "maximum": 500.0}
    assert monthly_cmap(monthly_norm(np.nan))[3] == 0
    assert monthly_cmap(monthly_norm(0))[3] == 1
    # Former hard transitions should now be continuous, including purple/cyan.
    for amount, _ in cfsv2.SNOWFALL_ACCUMULATION_COLOR_ANCHORS[1:-1]:
        colors = monthly_cmap(monthly_norm([amount - 0.05, amount + 0.05]))
        assert np.max(np.abs(colors[1] - colors[0])) < 0.025
    high_colors = monthly_cmap(monthly_norm(np.linspace(20, 200, 1000)))[:, :3]
    assert np.all(np.min(high_colors, axis=1) < 0.75), "high totals must not fade to near-white"

    lons = np.linspace(-140, -50, 91)
    lats = np.linspace(10, 65, 56)
    values = np.broadcast_to(np.linspace(0, 300, len(lons)), (len(lats), len(lons))).copy()
    values[20:30, 35:40] = np.nan
    before = values.copy()
    grid = cfsv2.Grid(lons.tolist(), lats.tolist(), values.tolist())
    spec = dict(cfsv2.get_product_spec(cfsv2.PRODUCT_SNOWFALL_ACCUMULATION))
    # Keep this contract independent of downloaded geographic files.
    spec.pop("map_domain")
    spec["fit_frame_to_domain"] = False
    figures = []
    real_close = plt.close

    def capture_close(figure=None):
        if hasattr(figure, "axes"):
            figures.append(figure)
        real_close(figure)

    with tempfile.TemporaryDirectory(prefix="snowfall-render-") as directory:
        for is_seasonal, maximum in [(False, 100), (True, 200)]:
            output = Path(directory) / f"snow-{maximum}.png"
            with patch.object(plt, "close", side_effect=capture_close):
                cfsv2.render_map(
                    grid, "2026090406", "202701", 4, [0], output,
                    False, "", [], seasonal=is_seasonal, product_spec=spec,
                )
            assert output.is_file() and output.stat().st_size > 10000
            figure = figures[-1]
            mesh = next(item for item in figure.axes[0].collections if isinstance(item, QuadMesh))
            assert mesh.get_clim() == (0, 200), "monthly colorbar must not renormalize the map"
            rendered = mesh.get_array()
            assert rendered.max() == maximum, "overflow should use the displayed endpoint color"
            assert np.ma.getmaskarray(rendered).any(), "missing cells must remain masked"
            bar = mesh.colorbar
            assert bar.extend == "max" and not bar.drawedges
            np.testing.assert_allclose(bar.ax.get_xlim(), (0, maximum))
            labels = bar.ax.get_xticklabels()
            assert labels[-1].get_text() == f"{maximum}+"
            FigureCanvasAgg(figure)
            figure.canvas.draw()
            boxes = [label.get_window_extent(figure.canvas.get_renderer()) for label in labels]
            assert all(left.x1 + 2 < right.x0 for left, right in zip(boxes, boxes[1:])), "legend labels overlap"
            np.testing.assert_array_equal(mesh.cmap(mesh.norm(amounts)), monthly_cmap(monthly_norm(amounts)))
    np.testing.assert_array_equal(np.asarray(grid.values), before)
    np.testing.assert_array_equal(values, before)
    print("PASS: identical amount colors, smooth transitions, transparent missing data, overflow, actual legends, unchanged source arrays")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
