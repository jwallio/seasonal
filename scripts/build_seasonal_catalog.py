#!/usr/bin/env python3
"""Validate seasonal manifests and build the dashboard's compact catalog.

The provider manifests remain the detailed provenance records.  This builder
creates a normalized, bounded index containing only fields used by the public
dashboard, plus explicit support and validation states.  A strict build exits
non-zero before Pages publication when a rendered asset is missing or a
product's units/field identity violate the canonical contract.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Iterable

from seasonal_products import (
    CORE_COMPARISON_PRODUCTS,
    MODELS,
    PRODUCTS,
    REGISTRY_VERSION,
    canonical_product,
    issue_codes,
    metadata_issues,
    public_model_registry,
    public_product_registry,
)


CATALOG_SCHEMA_VERSION = 1
UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
TARGET_PATTERN = re.compile(r"^\d{6}(?:-\d{6})?$")
USABLE_STATUSES = {"available", "decoded", "partial", "rendered"}
FAILED_STATUSES = {"error", "failed"}

RUN_FIELDS = (
    "id", "model", "component", "component_label", "model_role", "source", "source_url", "source_urls",
    "product", "base_product", "init_utc", "status", "statistic", "aggregation", "ensemble_scope",
    "ensemble_members", "field", "units", "raw_field", "raw_units", "climatology", "baseline",
    "source_warning", "conversion",
)
TARGET_FIELDS = (
    "id", "target_month", "label", "period_label", "valid_start_utc", "valid_end_utc", "lead_month",
    "monthly_leads", "field", "units", "status", "error", "image", "comparison", "baseline",
    "ensemble_members", "ensemble_expected_members", "ensemble_complete", "ensemble_scope", "member_count",
    "expected_member_count", "included_members", "missing_members", "statistic", "aggregation",
    "probability_integrity", "quality_control",
)


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class IssueCollector:
    """Collect validation issues once per code/message instead of per target."""

    def __init__(self) -> None:
        self._issues: list[dict[str, str]] = []
        self._seen: set[tuple[str, str, str]] = set()

    def add(self, code: str, severity: str, message: str, path: str = "") -> None:
        key = (code, severity, message)
        if key in self._seen:
            return
        self._seen.add(key)
        issue = {"code": code, "severity": severity, "message": message}
        if path:
            issue["path"] = path
        self._issues.append(issue)

    def extend(self, issues: Iterable[dict[str, Any]], path: str = "") -> None:
        for issue in issues:
            self.add(
                str(issue.get("code") or "validation_issue"),
                str(issue.get("severity") or "warning"),
                str(issue.get("message") or "Unspecified validation issue."),
                path,
            )

    @property
    def issues(self) -> list[dict[str, str]]:
        return list(self._issues)

    @property
    def errors(self) -> list[dict[str, str]]:
        return [issue for issue in self._issues if issue["severity"] == "error"]

    @property
    def warnings(self) -> list[dict[str, str]]:
        return [issue for issue in self._issues if issue["severity"] == "warning"]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _valid_utc(value: Any) -> bool:
    text = str(value or "")
    if not UTC_PATTERN.fullmatch(text):
        return False
    try:
        dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _normalized_asset_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/").lstrip("/")
    if text.startswith("public/"):
        text = text[len("public/"):]
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe or empty public asset path: {value!r}")
    if not text.startswith("seasonal/"):
        raise ValueError(f"seasonal asset must remain under seasonal/: {value!r}")
    return path.as_posix()


def _validate_asset(
    value: Any,
    *,
    site_root: Path,
    check_assets: bool,
    collector: IssueCollector,
    path: str,
) -> str | None:
    try:
        normalized = _normalized_asset_path(value)
    except ValueError as exc:
        collector.add("unsafe_asset_path", "error", str(exc), path)
        return None
    if Path(normalized).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        collector.add("unsupported_asset_type", "error", f"Unsupported map asset type: {normalized}", path)
    if check_assets:
        resolved = (site_root / Path(*PurePosixPath(normalized).parts)).resolve()
        try:
            resolved.relative_to(site_root.resolve())
        except ValueError:
            collector.add("asset_outside_site", "error", f"Asset escapes the Pages tree: {normalized}", path)
        else:
            if not resolved.is_file() or resolved.stat().st_size <= 0:
                collector.add("asset_missing", "error", f"Rendered map asset is missing: {normalized}", path)
    return normalized


def _copy_fields(value: dict[str, Any], names: Iterable[str]) -> dict[str, Any]:
    return {name: value[name] for name in names if name in value}


def _validate_probability_integrity(value: Any, collector: IssueCollector, path: str) -> None:
    if not isinstance(value, dict):
        return
    error = value.get("maximum_sum_error_percent")
    try:
        maximum_error = float(error)
    except (TypeError, ValueError):
        collector.add("probability_integrity_invalid", "error", "Probability integrity metadata is not numeric.", path)
        return
    if maximum_error > 0.05:
        collector.add(
            "probability_sum_mismatch", "error",
            f"Probability categories differ from 100% by as much as {maximum_error:g} percentage points.",
            path,
        )


def _target_catalog_state(
    product: str,
    target: dict[str, Any],
    *,
    site_root: Path,
    check_assets: bool,
    collector: IssueCollector,
    path: str,
) -> tuple[dict[str, Any], bool]:
    normalized = _copy_fields(target, TARGET_FIELDS)
    status = str(target.get("status") or "").lower()
    target_month = str(target.get("target_month") or "")
    if not TARGET_PATTERN.fullmatch(target_month):
        collector.add("target_month_invalid", "error", f"Invalid target_month {target_month!r}.", path)
    for field_name in ("valid_start_utc", "valid_end_utc"):
        if not _valid_utc(target.get(field_name)):
            collector.add("target_time_invalid", "error", f"Invalid {field_name}: {target.get(field_name)!r}.", path)
    if _valid_utc(target.get("valid_start_utc")) and _valid_utc(target.get("valid_end_utc")):
        start = dt.datetime.fromisoformat(str(target["valid_start_utc"]).replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(str(target["valid_end_utc"]).replace("Z", "+00:00"))
        if end <= start:
            collector.add("target_window_invalid", "error", "Target end must be later than its start.", path)

    metadata = metadata_issues(
        product,
        units=target.get("units"),
        field=target.get("field"),
    )
    collector.extend(metadata, path)
    comparable = not any(issue["severity"] == "error" or issue["code"] == "noncanonical_units" for issue in metadata)

    image = target.get("image")
    if image:
        normalized_image = _validate_asset(
            image, site_root=site_root, check_assets=check_assets, collector=collector, path=f"{path}.image",
        )
        if normalized_image:
            normalized["image"] = normalized_image
    elif status in {"partial", "rendered"}:
        collector.add("rendered_image_missing", "error", "Rendered target does not declare an image.", path)

    comparison = target.get("comparison")
    if isinstance(comparison, dict):
        normalized_comparison: dict[str, Any] = {}
        for reference, payload in comparison.items():
            if not isinstance(payload, dict):
                collector.add("comparison_invalid", "error", f"Comparison {reference!r} is not an object.", path)
                continue
            copied = dict(payload)
            if payload.get("image"):
                normalized_image = _validate_asset(
                    payload["image"], site_root=site_root, check_assets=check_assets, collector=collector,
                    path=f"{path}.comparison.{reference}.image",
                )
                if normalized_image:
                    copied["image"] = normalized_image
            elif str(payload.get("status") or "").lower() == "rendered":
                collector.add(
                    "comparison_image_missing", "error",
                    f"Rendered comparison {reference!r} does not declare an image.", path,
                )
            normalized_comparison[str(reference)] = copied
        normalized["comparison"] = normalized_comparison

    quality = target.get("quality_control")
    qc_status = "legacy"
    if isinstance(quality, dict):
        qc_status = str(quality.get("status") or "unknown")
        if qc_status == "failed":
            collector.add("field_qc_failed", "error", "Provider field quality control failed.", path)
        elif int(quality.get("registry_version") or 0) < REGISTRY_VERSION:
            collector.add("field_qc_outdated", "warning", "Provider field QC uses an older registry version.", path)
    elif canonical_product(product) in PRODUCTS and status in USABLE_STATUSES:
        collector.add(
            f"legacy_qc_missing:{canonical_product(product)}", "warning",
            f"Retained {canonical_product(product)} targets predate numerical grid QC; regenerate this product to certify it.",
            path,
        )
    _validate_probability_integrity(target.get("probability_integrity"), collector, path)
    normalized["_catalog"] = {
        "canonical_product": canonical_product(product),
        "comparable": comparable,
        "quality_control": qc_status,
    }
    return normalized, comparable


def validate_manifest(
    model_key: str,
    manifest: dict[str, Any],
    *,
    site_root: Path,
    check_assets: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    collector = IssueCollector()
    if not isinstance(manifest.get("runs"), list):
        collector.add("runs_missing", "error", "Manifest must contain a runs array.", "runs")
        runs: list[Any] = []
    else:
        runs = manifest["runs"]
    if not _valid_utc(manifest.get("generated_utc")):
        collector.add(
            "manifest_time_invalid", "error",
            f"Manifest generated_utc is invalid: {manifest.get('generated_utc')!r}.", "generated_utc",
        )
    if not manifest.get("kind"):
        collector.add("manifest_kind_missing", "error", "Manifest kind is required.", "kind")

    normalized_runs: list[dict[str, Any]] = []
    run_ids: set[str] = set()
    for index, raw_run in enumerate(runs):
        run_path = f"runs[{index}]"
        if not isinstance(raw_run, dict):
            collector.add("run_invalid", "error", "Run entry is not an object.", run_path)
            continue
        run_id = str(raw_run.get("id") or "")
        if not run_id:
            collector.add("run_id_missing", "error", "Run id is required.", run_path)
        elif run_id in run_ids:
            collector.add("run_id_duplicate", "error", f"Duplicate run id {run_id!r}.", run_path)
        run_ids.add(run_id)
        if not _valid_utc(raw_run.get("init_utc")):
            collector.add("run_time_invalid", "error", f"Invalid run init_utc {raw_run.get('init_utc')!r}.", run_path)
        product = str(raw_run.get("product") or "")
        if not product:
            collector.add("run_product_missing", "error", "Run product is required.", run_path)
        canonical = canonical_product(product)
        if canonical not in PRODUCTS:
            collector.add("unknown_product", "warning", f"No canonical contract exists for {product!r}.", run_path)
        if not isinstance(raw_run.get("targets"), list):
            collector.add("targets_missing", "error", "Run must contain a targets array.", run_path)
            targets: list[Any] = []
        else:
            targets = raw_run["targets"]

        normalized_run = _copy_fields(raw_run, RUN_FIELDS)
        normalized_run["product"] = product
        normalized_targets: list[dict[str, Any]] = []
        target_ids: set[str] = set()
        comparable_targets: list[bool] = []
        for target_index, raw_target in enumerate(targets):
            target_path = f"{run_path}.targets[{target_index}]"
            if not isinstance(raw_target, dict):
                collector.add("target_invalid", "error", "Target entry is not an object.", target_path)
                continue
            target_id = str(raw_target.get("id") or "")
            if not target_id:
                collector.add("target_id_missing", "error", "Target id is required.", target_path)
            elif target_id in target_ids:
                collector.add("target_id_duplicate", "error", f"Duplicate target id {target_id!r}.", target_path)
            target_ids.add(target_id)
            normalized_target, comparable = _target_catalog_state(
                product,
                raw_target,
                site_root=site_root,
                check_assets=check_assets,
                collector=collector,
                path=target_path,
            )
            normalized_targets.append(normalized_target)
            comparable_targets.append(comparable)
        normalized_run["targets"] = normalized_targets
        normalized_run["_catalog"] = {
            "canonical_product": canonical,
            "comparable": bool(comparable_targets) and all(comparable_targets),
        }
        normalized_runs.append(normalized_run)

    validation = {
        "status": "failed" if collector.errors else "warning" if collector.warnings else "passed",
        "error_count": len(collector.errors),
        "warning_count": len(collector.warnings),
        "issue_codes": issue_codes(collector.issues),
        "issues": collector.issues,
    }
    return normalized_runs, validation


def _preferred_runs(model_key: str, runs: list[dict[str, Any]], product: str) -> list[dict[str, Any]]:
    matches = [run for run in runs if canonical_product(run.get("product")) == product]
    preferred = str(MODELS[model_key].get("preferred_component") or "")
    preferred_matches = [run for run in matches if str(run.get("component") or "") == preferred]
    return preferred_matches or matches


def _usable_targets(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        target for target in run.get("targets", [])
        if str(target.get("status") or "").lower() not in FAILED_STATUSES and bool(target.get("image"))
    ]


def _surface_state(model_key: str, runs: list[dict[str, Any]], product: str) -> dict[str, Any]:
    support = dict(MODELS[model_key]["support"][product])
    if support["state"] != "supported":
        return {**support, "available": False, "comparable": False}
    candidates = _preferred_runs(model_key, runs, product)
    candidates.sort(key=lambda run: (str(run.get("init_utc") or ""), str(run.get("id") or "")), reverse=True)
    usable = next((run for run in candidates if _usable_targets(run)), None)
    if not usable:
        failed = next((run for run in candidates if str(run.get("status") or "").lower() in FAILED_STATUSES), None)
        return {
            "state": "failed" if failed else "missing",
            "reason": "Latest supported run failed." if failed else "No rendered target is currently published.",
            "available": False,
            "comparable": False,
            "latest_init_utc": (failed or {}).get("init_utc"),
        }
    targets = _usable_targets(usable)
    partial = str(usable.get("status") or "").lower() == "partial" or any(
        str(target.get("status") or "").lower() == "partial" for target in targets
    )
    comparable = bool(usable.get("_catalog", {}).get("comparable", True))
    return {
        "state": "partial" if partial else "available",
        "reason": (
            "Rendered target uses noncanonical units or field metadata and is excluded from comparison."
            if not comparable
            else "Rendered with partial ensemble coverage."
            if partial
            else "Rendered target available."
        ),
        "available": True,
        "comparable": comparable,
        "latest_init_utc": usable.get("init_utc"),
        "run_id": usable.get("id"),
        "target_count": len(targets),
    }


def build_catalog(
    site_root: Path,
    *,
    model_keys: Iterable[str] | None = None,
    check_assets: bool = True,
    generated_utc: str | None = None,
    source_revision: str = "",
) -> dict[str, Any]:
    site_root = site_root.resolve()
    keys = list(model_keys or MODELS)
    unknown = [key for key in keys if key not in MODELS]
    if unknown:
        raise ValueError(f"unknown seasonal model key(s): {', '.join(unknown)}")

    catalog_models: dict[str, Any] = {}
    global_issues = IssueCollector()
    supported_surfaces = 0
    available_surfaces = 0
    partial_surfaces = 0
    intentional_surfaces = 0
    models_online = 0

    for model_key in keys:
        definition = public_model_registry()[model_key]
        manifest_relative = str(definition["manifest"])
        manifest_path = site_root / Path(*PurePosixPath(manifest_relative).parts)
        model_entry: dict[str, Any] = {
            "key": model_key,
            "label": definition["label"],
            "role": definition["role"],
            "source": definition["source"],
            "preferred_component": definition.get("preferred_component") or "",
            "manifest": manifest_relative,
            "direct": f"seasonal/{model_key}/",
            "support": definition["support"],
            "runs": [],
        }
        if not manifest_path.is_file():
            validation = {
                "status": "failed", "error_count": 1, "warning_count": 0,
                "issue_codes": ["manifest_missing"],
                "issues": [{
                    "code": "manifest_missing", "severity": "error",
                    "message": f"Missing model manifest: {manifest_relative}", "path": manifest_relative,
                }],
            }
            model_entry.update({"status": "unavailable", "validation": validation, "surfaces": {}})
            global_issues.add("manifest_missing", "error", f"{model_key}: missing {manifest_relative}")
            catalog_models[model_key] = model_entry
            continue
        try:
            manifest = _read_json(manifest_path)
        except ValueError as exc:
            validation = {
                "status": "failed", "error_count": 1, "warning_count": 0,
                "issue_codes": ["manifest_invalid"],
                "issues": [{"code": "manifest_invalid", "severity": "error", "message": str(exc)}],
            }
            model_entry.update({"status": "invalid", "validation": validation, "surfaces": {}})
            global_issues.add("manifest_invalid", "error", f"{model_key}: {exc}")
            catalog_models[model_key] = model_entry
            continue

        runs, validation = validate_manifest(
            model_key, manifest, site_root=site_root, check_assets=check_assets,
        )
        surfaces = {product: _surface_state(model_key, runs, product) for product in CORE_COMPARISON_PRODUCTS}
        for surface in surfaces.values():
            if surface["state"] in {"unsupported", "quarantined"}:
                intentional_surfaces += 1
                continue
            supported_surfaces += 1
            if surface.get("available"):
                available_surfaces += 1
            if surface["state"] == "partial":
                partial_surfaces += 1
        if validation["status"] == "failed":
            status = "invalid"
        elif validation["status"] == "warning" or any(surface["state"] in {"failed", "missing", "partial"} for surface in surfaces.values()):
            status = "degraded"
            models_online += 1
        else:
            status = "online"
            models_online += 1
        model_entry.update({
            "status": status,
            "schema_version": manifest.get("schema_version"),
            "manifest_kind": manifest.get("kind"),
            "generated_utc": manifest.get("generated_utc"),
            "source_url": manifest.get("source_url"),
            "product_labels": manifest.get("product_labels") if isinstance(manifest.get("product_labels"), dict) else {},
            "validation": validation,
            "surfaces": surfaces,
            "runs": runs,
        })
        for issue in validation["issues"]:
            if issue["severity"] == "error":
                global_issues.add(issue["code"], "error", f"{model_key}: {issue['message']}")
        catalog_models[model_key] = model_entry

    generated = generated_utc or iso_utc(dt.datetime.now(dt.timezone.utc))
    if not _valid_utc(generated):
        raise ValueError(f"invalid catalog generated_utc: {generated!r}")
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "registry_version": REGISTRY_VERSION,
        "kind": "seasonal_dashboard_catalog",
        "generated_utc": generated,
        "source_revision": source_revision,
        "model_order": keys,
        "products": public_product_registry(),
        "models": catalog_models,
        "summary": {
            "models_expected": len(keys),
            "models_online": models_online,
            "supported_surfaces": supported_surfaces,
            "available_surfaces": available_surfaces,
            "partial_surfaces": partial_surfaces,
            "intentional_unavailable_surfaces": intentional_surfaces,
        },
        "validation": {
            "status": "failed" if global_issues.errors else "passed",
            "error_count": len(global_issues.errors),
            "issue_codes": issue_codes(global_issues.issues),
            "issues": global_issues.issues,
        },
    }


def write_catalog(path: Path, catalog: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-root", type=Path, required=True, help="Merged Pages tree containing seasonal manifests/assets")
    parser.add_argument("--output", type=Path, help="Catalog path; defaults to SITE_ROOT/seasonal/catalog.json")
    parser.add_argument("--models", default=",".join(MODELS), help="Comma-separated model registry keys")
    parser.add_argument("--skip-asset-check", action="store_true", help="Validate metadata without checking image files")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when any catalog validation error is present")
    parser.add_argument("--generated-utc", default="", help="Deterministic UTC timestamp for tests")
    parser.add_argument("--source-revision", default=os.environ.get("GITHUB_SHA", ""), help="Source commit recorded in catalog")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    model_keys = [value.strip() for value in args.models.split(",") if value.strip()]
    catalog = build_catalog(
        args.site_root,
        model_keys=model_keys,
        check_assets=not args.skip_asset_check,
        generated_utc=args.generated_utc or None,
        source_revision=args.source_revision,
    )
    output = args.output or args.site_root / "seasonal" / "catalog.json"
    write_catalog(output, catalog)
    summary = catalog["summary"]
    print(
        "seasonal catalog: "
        f"{summary['models_online']}/{summary['models_expected']} models online; "
        f"{summary['available_surfaces']}/{summary['supported_surfaces']} supported surfaces available; "
        f"{summary['intentional_unavailable_surfaces']} intentional N/A; "
        f"{catalog['validation']['error_count']} validation error(s)"
    )
    if args.strict and catalog["validation"]["status"] == "failed":
        for issue in catalog["validation"]["issues"]:
            print(f"ERROR [{issue['code']}] {issue['message']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
