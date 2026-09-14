"""Provider-independent visual contracts for public seasonal graphics.

``seasonal_products`` owns scientific identity, units, aggregation, and the
canonical domain name.  The existing display modules own product palettes and
breakpoints.  This module combines those contracts with one map/canvas layout
and exposes deterministic, provider-free style fingerprints.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from copy import deepcopy
import hashlib
import json
from typing import Any

from height_display import HEIGHT_ANOMALY_PALETTE, HEIGHT_ANOMALY_TICKS
from seasonal_products import (
    COMPARISON_PRODUCTS,
    MSLP_DISPLAY_BREAKPOINTS,
    canonical_product,
    product_definition,
)
from snowfall_display import accumulation_style, departure_style
from temperature_display import TEMPERATURE_ANOMALY_PALETTE, TEMPERATURE_ANOMALY_TICKS


RENDER_STYLE_VERSION = 1
RENDER_DPI = 120
RENDER_FIGURE_PIXELS = (1080, 1080)

DEFAULT_REGION = (-160.0, -10.0, 22.0, 85.0)
CONUS_REGION = (-126.0, -66.0, 24.0, 50.0)
CONUS_PRECIP_REGION = CONUS_REGION
NORTHERN_HEMISPHERE_REGION = (-180.0, 180.0, 0.0, 90.0)

PROJECTED_X_SHIFT_FRACTION = 0.035
CONUS_PROJECTED_X_SHIFT_FRACTION = 0.010
SEASONAL_LCC_PROJECTION_NAME = "Lambert Conformal Conic"
SEASONAL_LCC_STANDARD_PARALLEL_1 = 30.0
SEASONAL_LCC_STANDARD_PARALLEL_2 = 60.0
SEASONAL_LCC_LATITUDE_ORIGIN = 45.0
SEASONAL_LCC_CENTRAL_LONGITUDE = -100.0
SEASONAL_NORTH_POLAR_STEREOGRAPHIC_PROJECTION_NAME = "North Polar Stereographic"

CONUS_STATE_NAMES = (
    "Alabama", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "Florida", "Georgia", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts",
    "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska",
    "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah",
    "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming",
)

# These normalized axes reproduce the approved CFSv2 geographic panels.  They
# are fixed allocations, not tight-layout results; a provider subtitle or
# footer therefore cannot move a map or its legend.
DOMAIN_STYLES: dict[str, dict[str, Any]] = {
    "north_america": {
        "projection": "lambert_conformal_conic",
        "extent": list(DEFAULT_REGION),
        "projection_central_longitude": SEASONAL_LCC_CENTRAL_LONGITUDE,
        "projection_latitude_origin": SEASONAL_LCC_LATITUDE_ORIGIN,
        "projection_standard_parallel_1": SEASONAL_LCC_STANDARD_PARALLEL_1,
        "projection_standard_parallel_2": SEASONAL_LCC_STANDARD_PARALLEL_2,
        "projected_x_shift_fraction": PROJECTED_X_SHIFT_FRACTION,
        "map_axes_bounds": [0.044478, 0.112000, 0.911044, 0.768000],
        "colorbar_bounds": [0.043352, 0.055000, 0.913296, 0.032000],
    },
    "northern_hemisphere": {
        "projection": "north_polar_stereographic",
        "extent": list(NORTHERN_HEMISPHERE_REGION),
        "projection_central_longitude": -100.0,
        "polar_frame_latitude": 24.0,
        "map_axes_bounds": [0.116000, 0.112000, 0.768000, 0.768000],
        "colorbar_bounds": [0.116000, 0.055000, 0.768000, 0.032000],
    },
    "conus": {
        "projection": "lambert_conformal_conic",
        "extent": list(CONUS_REGION),
        "projection_central_longitude": SEASONAL_LCC_CENTRAL_LONGITUDE,
        "projection_latitude_origin": SEASONAL_LCC_LATITUDE_ORIGIN,
        "projection_standard_parallel_1": SEASONAL_LCC_STANDARD_PARALLEL_1,
        "projection_standard_parallel_2": SEASONAL_LCC_STANDARD_PARALLEL_2,
        "projected_x_shift_fraction": CONUS_PROJECTED_X_SHIFT_FRACTION,
        "map_axes_bounds": [0.039260, 0.181676, 0.921480, 0.698324],
        "colorbar_bounds": [0.035000, 0.124676, 0.930000, 0.032000],
    },
    "conus_land": {
        "projection": "lambert_conformal_conic",
        "extent": list(CONUS_REGION),
        "projection_central_longitude": SEASONAL_LCC_CENTRAL_LONGITUDE,
        "projection_latitude_origin": SEASONAL_LCC_LATITUDE_ORIGIN,
        "projection_standard_parallel_1": SEASONAL_LCC_STANDARD_PARALLEL_1,
        "projection_standard_parallel_2": SEASONAL_LCC_STANDARD_PARALLEL_2,
        "projected_x_shift_fraction": CONUS_PROJECTED_X_SHIFT_FRACTION,
        "map_domain": "land",
        "fit_frame_to_domain": True,
        "domain_frame_padding_fraction": 0.0,
        "mask_states": list(CONUS_STATE_NAMES),
        "border_files": ["us-states.geojson"],
        "map_axes_bounds": [0.042538, 0.293633, 0.914925, 0.586367],
        "colorbar_bounds": [0.035000, 0.249633, 0.930000, 0.032000],
    },
}

PRECIPITATION_ANOMALY_PALETTE = [
    "#7f3b08", "#914b0d", "#a6611a", "#bd7a2d", "#d0a052", "#dfbd7d",
    "#ead8b3", "#f5ead8", "#edf7e9", "#d9efd2", "#bfe4b6", "#9bd694",
    "#74c476", "#41ab5d", "#238b45", "#006d2c",
]
MSLP_ANOMALY_BOUNDS = list(MSLP_DISPLAY_BREAKPOINTS)
MSLP_ANOMALY_TICKS = [value / 2.0 for value in range(-10, 11)]
MSLP_ANOMALY_PALETTE = [
    "#24527a", "#306b90", "#3d83a6", "#4891b0", "#539cb8", "#61a7bf",
    "#70b2c6", "#95c4d3", "#c4dce3", "#f4f3ef", "#f2cecd", "#eaaaa8",
    "#e28c8b", "#db797b", "#d3686c", "#ca5861", "#bf4856", "#a1384a",
    "#84283f",
]


def darken_hex(color: str, factor: float = 0.68) -> str:
    """Return a deterministic darker endpoint hue for an overflow cell."""

    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected a six-digit hex color, got {color!r}")
    channels = [max(0, min(255, round(int(value[index:index + 2], 16) * factor))) for index in (0, 2, 4)]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def _period_name(seasonal: bool) -> str:
    return "seasonal" if seasonal else "monthly"


def _domain_variant(product: str) -> str:
    if product in {"snowfall_anomaly", "snowfall_accumulation"}:
        return "conus_land"
    definition = product_definition(product) or {}
    return str(definition.get("domain") or "conus")


def _anomaly_scale(product: str, seasonal: bool) -> dict[str, Any] | None:
    if product in {"500mb_height_anomaly", "500mb_height_anomaly_nh"}:
        bounds = list(HEIGHT_ANOMALY_TICKS)
        return {"bounds": bounds, "ticks": bounds, "palette": list(HEIGHT_ANOMALY_PALETTE)}
    if product in {"850mb_temperature_anomaly", "2m_temperature_anomaly"}:
        bounds = list(TEMPERATURE_ANOMALY_TICKS)
        return {"bounds": bounds, "ticks": bounds, "palette": list(TEMPERATURE_ANOMALY_PALETTE)}
    if product == "precipitation_anomaly":
        bounds = list(range(-8, 9)) if seasonal else [value / 2.0 for value in range(-8, 9)]
        return {"bounds": bounds, "ticks": bounds, "palette": list(PRECIPITATION_ANOMALY_PALETTE)}
    if product == "snowfall_anomaly":
        return departure_style(seasonal=seasonal)
    if product == "mslp_anomaly":
        return {
            "bounds": list(MSLP_ANOMALY_BOUNDS),
            "ticks": list(MSLP_ANOMALY_TICKS),
            "palette": list(MSLP_ANOMALY_PALETTE),
            "inclusive_upper_boundaries": [0.5],
        }
    return None


def canonical_render_style(product: str, *, seasonal: bool = False) -> dict[str, Any] | None:
    """Resolve one canonical style using only product, aggregation, and domain."""

    canonical = canonical_product(product)
    definition = product_definition(canonical)
    if definition is None:
        return None
    domain_variant = _domain_variant(canonical)
    domain = deepcopy(DOMAIN_STYLES.get(domain_variant))
    if domain is None:
        return None
    period = _period_name(seasonal)
    style: dict[str, Any] = {
        "style_version": RENDER_STYLE_VERSION,
        "style_key": f"{canonical}|{period}|{domain_variant}",
        "product": canonical,
        "aggregation": period,
        "domain_variant": domain_variant,
        "units": definition.get("units"),
        "canvas_dimensions": [1080, 845] if canonical == "snowfall_anomaly" else [1080, 882] if canonical == "snowfall_accumulation" else list(RENDER_FIGURE_PIXELS),
        "render_figure_dimensions": list(RENDER_FIGURE_PIXELS),
        "dpi": RENDER_DPI,
        "orientation": "horizontal",
        "spacing": "uniform",
        "extendrect": True,
        "extendfrac": "auto",
        "tick_length": 5.0,
        "tick_width": 0.85,
        **domain,
    }
    if canonical == "snowfall_accumulation":
        scale = accumulation_style(seasonal=seasonal)
        style.update(
            canvas_dimensions=[1080, 882],
            render_figure_dimensions=[1080, 882],
            map_axes_bounds=[0.038, 0.150, 0.924, 0.700],
            colorbar_bounds=[0.038, 0.100, 0.924, 0.034],
            bounds=scale["bounds"],
            ticks=scale["ticks"],
            palette=scale["palette"],
            under_color=None,
            over_color=darken_hex(scale["palette"][-1]),
            extend="max",
            endpoint_labels={"maximum": f"{scale['maximum']:g}+"},
            inclusive_upper_boundaries=[scale["maximum"]],
        )
    else:
        scale = _anomaly_scale(canonical, seasonal)
        if scale is None:
            return None
        bounds = list(scale["bounds"])
        palette = list(scale["palette"])
        style.update(
            bounds=bounds,
            ticks=list(scale["ticks"]),
            palette=palette,
            under_color=darken_hex(palette[0]),
            over_color=darken_hex(palette[-1]),
            extend="both",
            endpoint_labels={"minimum": f"≤−{abs(float(bounds[0])):g}", "maximum": f"{float(bounds[-1]):g}+"},
            inclusive_upper_boundaries=[*scale.get("inclusive_upper_boundaries", []), float(bounds[-1])],
        )
    style["fingerprint"] = render_style_fingerprint(style)
    return style


def render_style_fingerprint(style: dict[str, Any]) -> str:
    """Return the SHA-256 identity of a canonical JSON style specification."""

    payload = {key: value for key, value in style.items() if key != "fingerprint"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_VISUAL_FIELDS = {
    "region", "projection", "projection_central_longitude", "projection_latitude_origin",
    "projection_standard_parallel_1", "projection_standard_parallel_2", "polar_frame_latitude",
    "projected_x_shift_fraction", "map_domain", "fit_frame_to_domain",
    "domain_frame_padding_fraction", "mask_states", "border_files", "anomaly_min",
    "anomaly_max", "anomaly_ticks", "anomaly_bounds", "anomaly_palette",
    "anomaly_endpoint_labels", "anomaly_under_color", "anomaly_over_color",
    "anomaly_tick_decimals", "anomaly_tick_format", "anomaly_continuous",
    "monthly_anomaly_min", "monthly_anomaly_max", "monthly_anomaly_ticks",
    "monthly_anomaly_palette", "monthly_anomaly_endpoint_labels", "seasonal_anomaly_min",
    "seasonal_anomaly_max", "seasonal_anomaly_ticks", "seasonal_anomaly_palette",
    "seasonal_anomaly_endpoint_labels", "snowfall_display_profile", "colorbar_gap",
    "colorbar_extend", "colorbar_extendrect", "colorbar_extendfrac", "map_axes_bounds",
    "colorbar_axes_bounds", "crop_bottom_to_legend", "crop_bottom_px",
    "monthly_absolute_bounds", "monthly_absolute_ticks", "monthly_absolute_palette",
    "seasonal_absolute_bounds", "seasonal_absolute_ticks", "seasonal_absolute_palette",
    "absolute_under_color", "absolute_over_color",
}


def canonical_artifact_token(product_spec: dict[str, Any]) -> str:
    """Return a provider-compatible token that cannot alias a domain variant.

    Source/cache variable names may legitimately be shared by the North
    America and Northern Hemisphere 500-mb products. Public images may not:
    they have different canonical domains and must remain separately
    addressable. Preserve each adapter's established token while adding the
    scientific domain suffix when it is absent.
    """

    style_product = str(product_spec.get("canonical_style_product") or product_spec.get("name") or "")
    token = str(
        product_spec.get("artifact_token")
        or product_spec.get("file_token")
        or product_spec.get("id_token")
        or product_spec.get("variable")
        or style_product
    ).strip()
    if not token:
        raise ValueError("canonical public artifact token is empty")
    normalized = token.lower().replace("_", "-")
    if style_product == "500mb_height_anomaly_nh" and not normalized.endswith("-nh"):
        token = f"{token}-nh"
    return token


def canonicalize_product_spec(product_spec: dict[str, Any], *, seasonal: bool = False) -> dict[str, Any]:
    """Replace provider visual fields with the resolved canonical contract."""

    spec = deepcopy(product_spec)
    style_product = str(spec.get("canonical_style_product") or spec.get("name") or "")
    height_contour_capability = spec.get("height_contours")
    style = canonical_render_style(style_product, seasonal=seasonal)
    if style is None:
        return spec
    for key in _VISUAL_FIELDS:
        spec.pop(key, None)
    spec.update(
        region=tuple(style["extent"]),
        projection=style["projection"],
        projection_central_longitude=style["projection_central_longitude"],
        projected_x_shift_fraction=style.get("projected_x_shift_fraction", 0.0),
        map_axes_bounds=list(style["map_axes_bounds"]),
        colorbar_axes_bounds=list(style["colorbar_bounds"]),
        colorbar_extend=style["extend"],
        colorbar_extendrect=style["extendrect"],
        colorbar_extendfrac=style["extendfrac"],
        output_pixel_dimensions=list(style["canvas_dimensions"]),
        render_figure_pixel_dimensions=list(style["render_figure_dimensions"]),
        crop_bottom_to_legend=style["canvas_dimensions"][1] < style["render_figure_dimensions"][1],
        crop_bottom_px=style["canvas_dimensions"][1],
        artifact_token=canonical_artifact_token(spec),
        canonical_style_key=style["style_key"],
        render_style_fingerprint=style["fingerprint"],
        inclusive_upper_boundaries=list(style["inclusive_upper_boundaries"]),
        suppress_footer=style["canvas_dimensions"][1] < style["render_figure_dimensions"][1],
        height_contours=(
            canonical_product(style_product) in {"500mb_height_anomaly", "500mb_height_anomaly_nh"}
            and height_contour_capability is not False
        ),
    )
    for key in (
        "projection_latitude_origin", "projection_standard_parallel_1",
        "projection_standard_parallel_2", "polar_frame_latitude", "map_domain",
        "fit_frame_to_domain", "domain_frame_padding_fraction", "mask_states", "border_files",
    ):
        if key in style:
            spec[key] = deepcopy(style[key])
    if canonical_product(style_product) == "snowfall_accumulation":
        prefix = "seasonal" if seasonal else "monthly"
        spec[f"{prefix}_absolute_bounds"] = list(style["bounds"])
        spec[f"{prefix}_absolute_ticks"] = list(style["ticks"])
        spec[f"{prefix}_absolute_palette"] = list(style["palette"])
        spec["absolute_over_color"] = style["over_color"]
    else:
        spec.update(
            anomaly_min=float(style["bounds"][0]),
            anomaly_max=float(style["bounds"][-1]),
            anomaly_ticks=list(style["ticks"]),
            anomaly_bounds=list(style["bounds"]),
            anomaly_palette=list(style["palette"]),
            anomaly_endpoint_labels=deepcopy(style["endpoint_labels"]),
            anomaly_under_color=style["under_color"],
            anomaly_over_color=style["over_color"],
            anomaly_continuous=False,
            anomaly_tick_format="signed_trimmed",
            anomaly_tick_decimals=(1 if any(not float(value).is_integer() for value in style["ticks"]) else 0),
        )
    return spec


def canonical_bin(style: dict[str, Any], value: float) -> dict[str, Any]:
    """Classify a scalar exactly as the renderer, including overflow states."""

    numeric = float(value)
    bounds = [float(item) for item in style["bounds"]]
    if numeric < bounds[0]:
        return {"kind": "under", "index": -1, "color": style.get("under_color")}
    if numeric > bounds[-1]:
        return {"kind": "over", "index": len(style["palette"]), "color": style.get("over_color")}
    inclusive = {float(item) for item in style.get("inclusive_upper_boundaries", [])}
    if numeric in inclusive:
        index = bisect_left(bounds, numeric) - 1
    else:
        index = bisect_right(bounds, numeric) - 1
    index = max(0, min(len(style["palette"]) - 1, index))
    return {"kind": "normal", "index": index, "color": style["palette"][index]}


def prepare_capped_values(values: Any, style: dict[str, Any]):
    """Keep inclusive upper boundaries in-range without clamping overflow."""

    import numpy as np

    prepared = np.ma.array(values, copy=True)
    for boundary in style.get("inclusive_upper_boundaries", []):
        numeric = float(boundary)
        prepared = np.ma.where(prepared == numeric, np.nextafter(numeric, -np.inf), prepared)
    return prepared


def public_render_style_registry() -> dict[str, dict[str, Any]]:
    """Return JSON-safe canonical style variants for the published catalog."""

    products = [*COMPARISON_PRODUCTS, "500mb_height_anomaly_nh", "snowfall_accumulation"]
    result: dict[str, dict[str, Any]] = {}
    for product in dict.fromkeys(products):
        for seasonal in (False, True):
            style = canonical_render_style(product, seasonal=seasonal)
            if style is not None:
                result[style["style_key"]] = style
    return result
