#!/usr/bin/env python3
"""Canonical product and model contracts for the seasonal dashboard.

Provider adapters retain their source-specific retrieval metadata, but public
products need one stable interpretation for labels, units, pressure levels,
display scales, and comparison eligibility.  This module is deliberately
stdlib-only at import time so the Pages publisher can build the catalog
without installing the numerical rendering stack.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable


REGISTRY_VERSION = 1

SNOWFALL_DISPLAY_BREAKPOINTS = [
    -4.0, -3.5, -3.0, -2.5, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75,
    -0.5, 0.0, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5,
    4.0,
]


PRODUCTS: dict[str, dict[str, Any]] = {
    "500mb_height_anomaly": {
        "label": "500-mb Height Anomaly",
        "aliases": [],
        "units": "m",
        "compatible_units": [],
        "field_tokens": ("z500", "500mb", "500_mb", "500-hpa", "500 hpa"),
        "forbidden_field_tokens": ("z200", "200mb", "200_mb", "200-hpa", "200 hpa"),
        "level": {"type": "pressure", "value_hpa": 500},
        "aggregation": {"monthly": "mean", "seasonal": "mean"},
        "display": {
            "monthly": {"minimum": -100.0, "maximum": 100.0, "step": 10.0},
            "seasonal": {"minimum": -100.0, "maximum": 100.0, "step": 10.0},
        },
        "hard_range": {"minimum": -500.0, "maximum": 500.0},
        "minimum_finite_fraction": 0.2,
        "domain": "north_america",
        "comparison": True,
    },
    "850mb_temperature_anomaly": {
        "label": "850-mb Temperature Anomaly",
        "aliases": [],
        "units": "°C",
        "compatible_units": ("C", "degC"),
        "field_tokens": ("t850", "850mb", "850_mb", "850-hpa", "850 hpa"),
        "forbidden_field_tokens": ("t2m", "tmp2m", "z500", "z200"),
        "level": {"type": "pressure", "value_hpa": 850},
        "aggregation": {"monthly": "mean", "seasonal": "mean"},
        "display": {
            "monthly": {"minimum": -7.0, "maximum": 7.0, "step": 1.0},
            "seasonal": {"minimum": -7.0, "maximum": 7.0, "step": 1.0},
        },
        "hard_range": {"minimum": -50.0, "maximum": 50.0},
        "minimum_finite_fraction": 0.2,
        "domain": "north_america",
        "comparison": True,
    },
    "2m_temperature_anomaly": {
        "label": "2-m Temperature Anomaly",
        "aliases": ("surface_temperature_anomaly", "temperature_anomaly"),
        "units": "°C",
        "compatible_units": ("C", "degC"),
        "field_tokens": ("t2m", "tmp2m", "2m_temperature", "temperature_2m", "2-m temperature"),
        "forbidden_field_tokens": ("t850", "z500", "z200"),
        "level": {"type": "height", "value_m": 2},
        "aggregation": {"monthly": "mean", "seasonal": "mean"},
        "display": {
            "monthly": {"minimum": -7.0, "maximum": 7.0, "step": 1.0},
            "seasonal": {"minimum": -7.0, "maximum": 7.0, "step": 1.0},
        },
        "hard_range": {"minimum": -50.0, "maximum": 50.0},
        "minimum_finite_fraction": 0.2,
        "domain": "north_america",
        "comparison": True,
    },
    "precipitation_anomaly": {
        "label": "Precipitation Anomaly",
        "aliases": [],
        "units": "in",
        # Legacy APCC maps used millimetres.  They remain viewable but are not
        # comparison-eligible until regenerated in the canonical unit.
        "compatible_units": ("mm",),
        "field_tokens": ("precip", "prate", "prec"),
        "forbidden_field_tokens": (),
        "level": {"type": "surface"},
        "aggregation": {"monthly": "total", "seasonal": "total"},
        "display": {
            "monthly": {"minimum": -4.0, "maximum": 4.0, "step": 0.5},
            "seasonal": {"minimum": -8.0, "maximum": 8.0, "step": 1.0},
        },
        "hard_range": {"minimum": -100.0, "maximum": 100.0},
        "minimum_finite_fraction": 0.2,
        "domain": "conus",
        "comparison": True,
    },
    "snowfall_anomaly": {
        "label": "CONUS Snowfall Water-Equivalent Departure",
        "aliases": [],
        # C3S/SEAS5 expose snowfall as a rate of liquid-water equivalent.  The
        # public unit is therefore inches of water equivalent, not snow depth.
        "units": "in",
        "compatible_units": (),
        "field_tokens": ("snowfall", "sf"),
        "forbidden_field_tokens": ("snow_depth", "snow water equivalent", "swe"),
        "level": {"type": "surface"},
        "aggregation": {"monthly": "total", "seasonal": "total"},
        "display": {
            "monthly": {
                "minimum": -4.0,
                "maximum": 4.0,
                "breakpoints": SNOWFALL_DISPLAY_BREAKPOINTS,
            },
            "seasonal": {
                "minimum": -4.0,
                "maximum": 4.0,
                "breakpoints": SNOWFALL_DISPLAY_BREAKPOINTS,
            },
        },
        "hard_range": {"minimum": -100.0, "maximum": 100.0},
        "minimum_finite_fraction": 0.02,
        "domain": "conus",
        "comparison": True,
    },
    "mslp_anomaly": {
        "label": "MSLP Anomaly",
        "aliases": [],
        "units": "hPa",
        "compatible_units": ("mb",),
        "field_tokens": ("mslp", "slp", "mean_sea_level_pressure"),
        "forbidden_field_tokens": (),
        "level": {"type": "mean_sea_level"},
        "aggregation": {"monthly": "mean", "seasonal": "mean"},
        "display": {
            "monthly": {"minimum": -10.0, "maximum": 10.0, "step": 1.0},
            "seasonal": {"minimum": -10.0, "maximum": 10.0, "step": 1.0},
        },
        "hard_range": {"minimum": -100.0, "maximum": 100.0},
        "minimum_finite_fraction": 0.2,
        "domain": "north_america",
        "comparison": True,
    },
    "sea_surface_temperature_anomaly": {
        "label": "Sea-Surface Temperature Anomaly",
        "aliases": ("sst_anomaly",),
        "units": "°C",
        "compatible_units": ("C", "degC"),
        "field_tokens": ("sst", "sea_surface_temperature"),
        "forbidden_field_tokens": ("t2m", "t850"),
        "level": {"type": "sea_surface"},
        "aggregation": {"monthly": "mean", "seasonal": "mean"},
        "display": {
            "monthly": {"minimum": -3.0, "maximum": 3.0, "step": 1.0},
            "seasonal": {"minimum": -3.0, "maximum": 3.0, "step": 1.0},
        },
        "hard_range": {"minimum": -20.0, "maximum": 20.0},
        "minimum_finite_fraction": 0.02,
        "domain": "ocean",
        "comparison": True,
    },
    "500mb_height_absolute": {
        "label": "500-mb Geopotential Height",
        "aliases": [],
        "units": "m",
        "compatible_units": (),
        "field_tokens": ("z500", "500mb", "500_mb", "500-hpa", "500 hpa"),
        "forbidden_field_tokens": ("z200", "200mb", "200_mb"),
        "level": {"type": "pressure", "value_hpa": 500},
        "aggregation": {"monthly": "mean", "seasonal": "mean"},
        "hard_range": {"minimum": 3500.0, "maximum": 7000.0},
        "minimum_finite_fraction": 0.2,
        "domain": "north_america",
        "comparison": False,
    },
    "200mb_height_anomaly": {
        "label": "200-mb Height Anomaly",
        "aliases": [],
        "units": "m",
        "compatible_units": (),
        "field_tokens": ("z200", "200mb", "200_mb", "200-hpa", "200 hpa"),
        "forbidden_field_tokens": ("z500", "500mb", "500_mb"),
        "level": {"type": "pressure", "value_hpa": 200},
        "aggregation": {"monthly": "mean", "seasonal": "mean"},
        "display": {
            "monthly": {"minimum": -200.0, "maximum": 200.0, "step": 20.0},
            "seasonal": {"minimum": -200.0, "maximum": 200.0, "step": 20.0},
        },
        "hard_range": {"minimum": -1000.0, "maximum": 1000.0},
        "minimum_finite_fraction": 0.2,
        "domain": "north_america",
        "comparison": False,
    },
    "snow_water_equivalent_anomaly": {
        "label": "Snow-Water-Equivalent Anomaly",
        "aliases": [],
        "units": "in",
        "compatible_units": (),
        "field_tokens": ("snow_water_equivalent", "swe", "weasd"),
        "forbidden_field_tokens": (),
        "level": {"type": "surface"},
        "aggregation": {"monthly": "mean", "seasonal": "mean"},
        "hard_range": {"minimum": -100.0, "maximum": 100.0},
        "minimum_finite_fraction": 0.02,
        "domain": "land",
        "comparison": False,
    },
    "sea_surface_height_anomaly": {
        "label": "Sea-Surface Height Anomaly",
        "aliases": [],
        "units": "m",
        "compatible_units": (),
        "field_tokens": ("sea_surface_height", "ssh"),
        "forbidden_field_tokens": ("z500", "z200"),
        "level": {"type": "sea_surface"},
        "aggregation": {"monthly": "mean", "seasonal": "mean"},
        "display": {
            "monthly": {"minimum": -0.5, "maximum": 0.5, "step": 0.1},
            "seasonal": {"minimum": -0.5, "maximum": 0.5, "step": 0.1},
        },
        "hard_range": {"minimum": -5.0, "maximum": 5.0},
        "minimum_finite_fraction": 0.02,
        "domain": "ocean",
        "comparison": False,
    },
    "probability_above_normal": {
        "label": "Above-Normal Probability",
        "aliases": [],
        "units": "%",
        "compatible_units": (),
        "field_tokens": ("prob", "probability"),
        "forbidden_field_tokens": (),
        "level": {"type": "derived"},
        "aggregation": {"monthly": "category_probability", "seasonal": "category_probability"},
        "hard_range": {"minimum": 0.0, "maximum": 100.0},
        "minimum_finite_fraction": 0.02,
        "domain": "global",
        "comparison": False,
    },
    "probability_near_normal": {
        "label": "Near-Normal Probability",
        "aliases": [],
        "units": "%",
        "compatible_units": (),
        "field_tokens": ("prob", "probability"),
        "forbidden_field_tokens": (),
        "level": {"type": "derived"},
        "aggregation": {"monthly": "category_probability", "seasonal": "category_probability"},
        "hard_range": {"minimum": 0.0, "maximum": 100.0},
        "minimum_finite_fraction": 0.02,
        "domain": "global",
        "comparison": False,
    },
    "probability_below_normal": {
        "label": "Below-Normal Probability",
        "aliases": [],
        "units": "%",
        "compatible_units": (),
        "field_tokens": ("prob", "probability"),
        "forbidden_field_tokens": (),
        "level": {"type": "derived"},
        "aggregation": {"monthly": "category_probability", "seasonal": "category_probability"},
        "hard_range": {"minimum": 0.0, "maximum": 100.0},
        "minimum_finite_fraction": 0.02,
        "domain": "global",
        "comparison": False,
    },
    "multi_model_consensus": {
        "label": "Multi-Model Consensus",
        "aliases": [],
        "units": None,
        "compatible_units": ("°C", "C", "degC", "in", "mm", "m", "hPa"),
        "field_tokens": (),
        "forbidden_field_tokens": (),
        "level": {"type": "derived"},
        "aggregation": {"monthly": "component_consensus", "seasonal": "component_consensus"},
        "minimum_finite_fraction": 0.02,
        "domain": "global",
        "comparison": False,
    },
}


CORE_COMPARISON_PRODUCTS = (
    "500mb_height_anomaly",
    "850mb_temperature_anomaly",
    "2m_temperature_anomaly",
    "precipitation_anomaly",
    "mslp_anomaly",
    "sea_surface_temperature_anomaly",
)

# Products shown in the cross-model overview and Compare view.  Keep the
# original core tuple stable for provider contracts; snowfall is a supported
# comparison surface only for providers with a native or explicitly derived
# snowfall liquid-water-equivalent field.
COMPARISON_PRODUCTS = (
    *CORE_COMPARISON_PRODUCTS[:4],
    "snowfall_anomaly",
    *CORE_COMPARISON_PRODUCTS[4:],
)


def _supported() -> dict[str, str]:
    return {"state": "supported", "reason": "Provider adapter publishes this comparison product."}


def _unsupported(reason: str) -> dict[str, str]:
    return {"state": "unsupported", "reason": reason}


MODELS: dict[str, dict[str, Any]] = {
    "superensemble": {
        "label": "Super Ensemble", "role": "blend", "manifest": "seasonal/superensemble_manifest.json",
        "source": "Deduplicated seasonal forecast families, including target-aligned CMA CPSv3",
        "preferred_component": "", "support": {key: _supported() for key in CORE_COMPARISON_PRODUCTS},
    },
    "c3s": {
        "label": "C3S multi-system", "role": "blend", "manifest": "seasonal/c3s_manifest.json",
        "source": "Copernicus C3S seasonal forecasts", "preferred_component": "multisystem",
        "support": {key: _supported() for key in CORE_COMPARISON_PRODUCTS},
    },
    "apcc": {
        "label": "APCC MME", "role": "blend", "manifest": "seasonal/apcc_manifest.json",
        "source": "APCC multi-model ensemble via CLIK", "preferred_component": "",
        "support": {key: _supported() for key in CORE_COMPARISON_PRODUCTS},
    },
    "nmme": {
        "label": "NOAA NMME", "role": "blend", "manifest": "seasonal/nmme_manifest.json",
        "source": "NOAA CPC NMME", "preferred_component": "ENSMEAN",
        "support": {
            key: (_supported() if key == "2m_temperature_anomaly" else {
                "state": "unsupported",
                "reason": "The current NMME adapter does not yet publish this field as a core comparison product.",
            })
            for key in CORE_COMPARISON_PRODUCTS
        },
    },
    "cfsv2": {
        "label": "CFSv2", "role": "family", "manifest": "seasonal/cfsv2_manifest.json",
        "source": "NOAA CFSv2 NOMADS", "preferred_component": "",
        "support": {
            key: (_supported() if key in {
                "500mb_height_anomaly", "2m_temperature_anomaly", "precipitation_anomaly", "mslp_anomaly",
            } else {
                "state": "unsupported",
                "reason": "The standalone rolling CFSv2 adapter does not expose this field.",
            })
            for key in CORE_COMPARISON_PRODUCTS
        },
    },
    "seas5": {
        "label": "ECMWF SEAS5", "role": "family", "manifest": "seasonal/seas5_manifest.json",
        "source": "ECMWF SEAS5 / Copernicus CDS", "preferred_component": "",
        "support": {key: _supported() for key in CORE_COMPARISON_PRODUCTS},
    },
    "cansips": {
        "label": "CanSIPS v3", "role": "family", "manifest": "seasonal/cansips_manifest.json",
        "source": "ECCC MSC Datamart", "preferred_component": "",
        "support": {key: _supported() for key in CORE_COMPARISON_PRODUCTS},
    },
    "cma_cpsv3": {
        "label": "CMA CPSv3", "role": "family", "manifest": "seasonal/cma_cpsv3_manifest.json",
        "source": "WMO LC-SPMME / GPC Beijing", "preferred_component": "",
        "support": {key: _supported() for key in CORE_COMPARISON_PRODUCTS},
    },
    "geos_s2s3": {
        "label": "NASA GEOS-S2S-3", "role": "family", "manifest": "seasonal/geos_s2s3_manifest.json",
        "source": "NASA GEOS-S2S-3 NCCS numerical forecasts", "preferred_component": "",
        "support": {
            key: ({
                "state": "quarantined",
                "reason": "NASA's current long-range archive labelled z500 declares 200 hPa and is blocked by pressure-level QC.",
            } if key == "500mb_height_anomaly" else _supported())
            for key in CORE_COMPARISON_PRODUCTS
        },
    },
    "jma": {
        "label": "JMA", "role": "component", "manifest": "seasonal/jma_manifest.json",
        "source": "JMA/MRI-CPS4 via Copernicus C3S", "preferred_component": "jma",
        "support": {key: _supported() for key in CORE_COMPARISON_PRODUCTS},
    },
}


SNOWFALL_SUPPORTED_MODELS = frozenset({"c3s", "seas5", "cansips", "superensemble"})
SNOWFALL_SUPPORT_REASONS = {
    "c3s": "C3S publishes native snowfall liquid-water-equivalent accumulation.",
    "seas5": "SEAS5 publishes native snowfall liquid-water-equivalent accumulation.",
    "cansips": "CanSIPS derives member-level snowfall liquid-water equivalent from 2-m/850-hPa temperature and total precipitation using the season-appropriate Dai (2008) land phase curve (DJF for winter).",
    "superensemble": "The super ensemble blends eligible native and CanSIPS-derived snowfall fields in common LWE units.",
}
SNOWFALL_UNSUPPORTED_REASON = (
    "The current seasonal adapter does not publish a native or explicitly derived "
    "snowfall accumulation field; snow-water equivalent or precipitation is not "
    "interchangeable with snowfall."
)
for _model_key, _model_definition in MODELS.items():
    _model_definition["support"]["snowfall_anomaly"] = (
        {
            "state": "supported",
            "reason": SNOWFALL_SUPPORT_REASONS[_model_key],
        }
        if _model_key in SNOWFALL_SUPPORTED_MODELS
        else _unsupported(SNOWFALL_UNSUPPORTED_REASON)
    )


_ALIASES = {
    alias: canonical
    for canonical, definition in PRODUCTS.items()
    for alias in (canonical, *definition.get("aliases", ()))
}


def canonical_product(product: str | None) -> str:
    """Return the canonical public key while preserving unknown products."""

    value = str(product or "").strip()
    return _ALIASES.get(value, value)


def product_definition(product: str | None) -> dict[str, Any] | None:
    """Return a defensive copy of a canonical product definition."""

    definition = PRODUCTS.get(canonical_product(product))
    return deepcopy(definition) if definition else None


def public_product_registry() -> dict[str, dict[str, Any]]:
    """Return JSON-safe product definitions for the generated catalog."""

    result: dict[str, dict[str, Any]] = {}
    for key, definition in PRODUCTS.items():
        public = deepcopy(definition)
        public["aliases"] = list(public.get("aliases", ()))
        public["compatible_units"] = list(public.get("compatible_units", ()))
        public["field_tokens"] = list(public.get("field_tokens", ()))
        public["forbidden_field_tokens"] = list(public.get("forbidden_field_tokens", ()))
        result[key] = public
    return result


def public_model_registry() -> dict[str, dict[str, Any]]:
    return deepcopy(MODELS)


def _issue(code: str, severity: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def metadata_issues(product: str, *, units: str | None, field: str | None) -> list[dict[str, str]]:
    """Validate a product's public unit and level-bearing field name."""

    canonical = canonical_product(product)
    definition = PRODUCTS.get(canonical)
    if not definition:
        return [_issue("unknown_product", "warning", f"No canonical contract exists for {product!r}.")]
    issues: list[dict[str, str]] = []
    expected_units = definition.get("units")
    actual_units = str(units or "").strip()
    compatible_units = {str(value) for value in definition.get("compatible_units", ())}
    if expected_units and not actual_units:
        issues.append(_issue("units_missing", "error", f"{canonical} must declare units {expected_units}."))
    elif expected_units and actual_units != expected_units:
        if actual_units in compatible_units:
            issues.append(_issue(
                "noncanonical_units", "warning",
                f"{canonical} uses compatible legacy units {actual_units}; canonical comparison units are {expected_units}.",
            ))
        else:
            issues.append(_issue(
                "units_mismatch", "error",
                f"{canonical} declares {actual_units or 'no units'}; expected {expected_units}.",
            ))
    field_text = str(field or "").strip().lower().replace("−", "-")
    required_tokens = tuple(str(value).lower() for value in definition.get("field_tokens", ()))
    forbidden_tokens = tuple(str(value).lower() for value in definition.get("forbidden_field_tokens", ()))
    if required_tokens and not field_text:
        issues.append(_issue("field_missing", "error", f"{canonical} must declare its decoded field."))
    elif required_tokens and not any(token in field_text for token in required_tokens):
        issues.append(_issue(
            "field_identity_mismatch", "error",
            f"Field {field!r} does not identify the expected {canonical} level or variable.",
        ))
    forbidden = next((token for token in forbidden_tokens if token in field_text), None)
    if forbidden:
        issues.append(_issue(
            "forbidden_field_identity", "error",
            f"Field {field!r} contains {forbidden!r}, which conflicts with {canonical}.",
        ))
    return issues


def grid_quality_control(
    product: str,
    values: Any,
    *,
    units: str | None,
    field: str | None,
    seasonal: bool = False,
) -> dict[str, Any]:
    """Return serializable numerical QC for a decoded forecast grid.

    The hard physical envelopes are intentionally broad.  Display clipping is
    reported separately: a small amount is a warning, while a map with more
    than half of its finite cells outside the fixed scale is rejected.
    """

    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - numerical workflows install numpy
        raise RuntimeError("seasonal grid QC requires numpy") from exc

    canonical = canonical_product(product)
    definition = PRODUCTS.get(canonical)
    issues = metadata_issues(canonical, units=units, field=field)
    array = np.asarray(values, dtype=float)
    finite_mask = np.isfinite(array)
    finite = array[finite_mask]
    total_points = int(array.size)
    finite_points = int(finite.size)
    finite_fraction = float(finite_points / total_points) if total_points else 0.0
    result: dict[str, Any] = {
        "registry_version": REGISTRY_VERSION,
        "product": canonical,
        "units": units,
        "field": field,
        "period": "seasonal" if seasonal else "monthly",
        "total_points": total_points,
        "finite_points": finite_points,
        "finite_fraction": round(finite_fraction, 6),
    }
    if finite_points == 0:
        issues.append(_issue("empty_grid", "error", "Decoded grid contains no finite values."))
    else:
        minimum = float(np.min(finite))
        maximum = float(np.max(finite))
        p01 = float(np.percentile(finite, 1))
        p99 = float(np.percentile(finite, 99))
        result.update({
            "minimum": round(minimum, 6),
            "maximum": round(maximum, 6),
            "p01": round(p01, 6),
            "p99": round(p99, 6),
        })
        if definition:
            minimum_fraction = float(definition.get("minimum_finite_fraction", 0.02))
            if finite_fraction < minimum_fraction:
                issues.append(_issue(
                    "insufficient_finite_coverage", "error",
                    f"Finite coverage {finite_fraction:.3f} is below the {minimum_fraction:.3f} contract.",
                ))
            hard_range = definition.get("hard_range") or {}
            hard_minimum = hard_range.get("minimum")
            hard_maximum = hard_range.get("maximum")
            if hard_minimum is not None and minimum < float(hard_minimum):
                issues.append(_issue(
                    "below_physical_envelope", "error",
                    f"Grid minimum {minimum:g} is below the hard {hard_minimum:g} envelope.",
                ))
            if hard_maximum is not None and maximum > float(hard_maximum):
                issues.append(_issue(
                    "above_physical_envelope", "error",
                    f"Grid maximum {maximum:g} exceeds the hard {hard_maximum:g} envelope.",
                ))
            display = (definition.get("display") or {}).get("seasonal" if seasonal else "monthly")
            if display:
                display_minimum = float(display["minimum"])
                display_maximum = float(display["maximum"])
                below_fraction = float(np.count_nonzero(finite < display_minimum) / finite_points)
                above_fraction = float(np.count_nonzero(finite > display_maximum) / finite_points)
                clipped_fraction = below_fraction + above_fraction
                result["display"] = {
                    **deepcopy(display),
                    "below_fraction": round(below_fraction, 6),
                    "above_fraction": round(above_fraction, 6),
                    "clipped_fraction": round(clipped_fraction, 6),
                }
                if clipped_fraction > 0.5:
                    issues.append(_issue(
                        "display_scale_rejected", "error",
                        f"{clipped_fraction:.1%} of finite cells would be clipped by the fixed display scale.",
                    ))
                elif clipped_fraction > 0.02:
                    issues.append(_issue(
                        "display_scale_clipping", "warning",
                        f"{clipped_fraction:.1%} of finite cells exceed the fixed display scale.",
                    ))
    result["issues"] = issues
    result["status"] = (
        "failed" if any(issue["severity"] == "error" for issue in issues)
        else "warning" if issues
        else "passed"
    )
    return result


def require_quality_control(qc: dict[str, Any], error_type: type[Exception] = ValueError) -> None:
    """Raise a provider-specific error when numerical QC has failed."""

    if qc.get("status") != "failed":
        return
    messages = [str(issue.get("message")) for issue in qc.get("issues", ()) if issue.get("severity") == "error"]
    raise error_type("seasonal field QC failed: " + "; ".join(messages or ["unknown validation error"]))


def issue_codes(issues: Iterable[dict[str, Any]]) -> list[str]:
    return sorted({str(issue.get("code")) for issue in issues if issue.get("code")})
