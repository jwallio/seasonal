"""Build the compact Compare-page analog manifest for CFSv2 and Super Ensemble."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any, Callable, Iterable

import seasonal_analogs as analogs


MODEL_SPECS = {
    "cfsv2": {
        "label": "CFSv2",
        "manifest": "public/seasonal/cfsv2_manifest.json",
    },
    "superensemble": {
        "label": "Super Ensemble",
        "manifest": "public/seasonal/superensemble_manifest.json",
    },
}
USABLE_STATUSES = frozenset({"rendered", "partial", "decoded"})


class AnalogBuildError(RuntimeError):
    """A published forecast or archive cannot be used for analog matching."""


def _now_iso() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _resolve_asset(root: Path, value: str) -> Path:
    candidate = Path(str(value))
    if candidate.is_absolute():
        return candidate
    options = [root / candidate]
    text = candidate.as_posix()
    if text.startswith("public/"):
        options.append(root / text.removeprefix("public/"))
    elif text.startswith("seasonal/"):
        options.append(root / "public" / text)
    for option in options:
        if option.exists():
            return option
    return options[0]


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnalogBuildError(f"could not read seasonal manifest {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("runs"), list):
        raise AnalogBuildError(f"seasonal manifest has no run list: {path}")
    return payload


def _target_entries(
    root: Path,
    model_key: str,
    manifest_path: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Select newest usable target entries without ever selecting failed runs."""

    payload = _read_manifest(manifest_path)
    runs = sorted(
        (run for run in payload["runs"] if isinstance(run, dict)),
        key=lambda run: (
            str(run.get("init_utc", "")),
            str(run.get("generated_utc", "")),
            str(run.get("id", "")),
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    selected_targets: set[str] = set()
    skipped: list[str] = []
    for run in runs:
        if str(run.get("status", "")) not in USABLE_STATUSES:
            skipped.append(f"{model_key}:{run.get('id', 'unknown')}:{run.get('status', 'unknown')}")
            continue
        for target_entry in run.get("targets", []):
            if not isinstance(target_entry, dict):
                continue
            if str(target_entry.get("status", "")) not in USABLE_STATUSES:
                continue
            target = str(target_entry.get("target_month") or target_entry.get("target") or "")
            if not target or target in selected_targets:
                continue
            try:
                metadata = analogs.parse_target(target)
            except analogs.SeasonalAnalogError:
                continue
            numeric_grid = target_entry.get("numeric_grid")
            if not numeric_grid:
                skipped.append(f"{model_key}:{run.get('id', 'unknown')}:{target}:numeric_grid_missing")
                continue
            grid_path = _resolve_asset(root, str(numeric_grid))
            if not grid_path.exists():
                skipped.append(f"{model_key}:{run.get('id', 'unknown')}:{target}:numeric_grid_unavailable")
                continue
            selected_targets.add(target)
            selected.append(
                {
                    "model": model_key,
                    "model_label": MODEL_SPECS[model_key]["label"],
                    "run_id": str(run.get("id", target_entry.get("id", "unknown"))),
                    "init_utc": str(run.get("init_utc", "")),
                    "target": target,
                    "target_label": metadata["label"],
                    "image": target_entry.get("image"),
                    "grid_path": grid_path,
                }
            )
    return selected, skipped


def build_manifest(
    *,
    root: Path,
    archive_path: Path,
    output_path: Path,
    models: Iterable[str] = ("cfsv2", "superensemble"),
    top_n: int = analogs.DEFAULT_TOP_N,
    variable: str = "z500_anom",
    archive_loader: Callable[..., analogs.HistoricalSeasonalFields] = analogs.load_zarr_historical_fields,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Build one manifest from the current usable forecast artifacts."""

    selected_models = list(models)
    unknown = [model for model in selected_models if model not in MODEL_SPECS]
    if unknown:
        raise AnalogBuildError(f"unsupported analog model(s): {', '.join(unknown)}")
    if not archive_path.exists():
        raise AnalogBuildError(f"AnalogWX archive does not exist: {archive_path}")

    entries: list[dict[str, Any]] = []
    skipped: list[str] = []
    historical_cache: dict[str, analogs.HistoricalSeasonalFields] = {}
    for model_key in selected_models:
        manifest_path = _resolve_asset(root, MODEL_SPECS[model_key]["manifest"])
        if not manifest_path.exists():
            skipped.append(f"{model_key}:manifest_missing")
            continue
        candidates, candidate_skips = _target_entries(root, model_key, manifest_path)
        skipped.extend(candidate_skips)
        for candidate in candidates:
            target = candidate["target"]
            if target not in historical_cache:
                historical_cache[target] = archive_loader(
                    archive_path,
                    target,
                    variable=variable,
                )
            lons, lats, values = analogs.read_grid_state(candidate["grid_path"])
            match = analogs.build_artifact(
                model_key=model_key,
                run_id=candidate["run_id"],
                init_utc=candidate["init_utc"],
                target=target,
                forecast_values=values,
                forecast_lats=lats,
                forecast_lons=lons,
                historical=historical_cache[target],
                top_n=top_n,
            )
            entries.append(
                {
                    "model": model_key,
                    "model_label": candidate["model_label"],
                    "run_id": candidate["run_id"],
                    "init_utc": candidate["init_utc"],
                    "target": target,
                    "target_label": candidate["target_label"],
                    "image": candidate["image"],
                    "results": match["results"],
                }
            )

    if not entries and not allow_empty:
        detail = "; ".join(skipped[:6]) or "no usable forecast targets found"
        raise AnalogBuildError(f"analog manifest would be empty: {detail}")
    entries.sort(key=lambda entry: (entry["model"], entry["target"], entry["init_utc"]), reverse=True)
    return {
        "schema_version": analogs.SCHEMA_VERSION,
        "kind": "seasonal_z500_analog_manifest",
        "generated_utc": _now_iso(),
        "models": [
            {"key": model, "label": MODEL_SPECS[model]["label"]}
            for model in selected_models
        ],
        "source": {
            "label": analogs.ARCHIVE_LABEL,
            "archive_variable": variable,
            "climatology_years": "1981-2010",
            "method": "cosine-latitude-weighted centered pattern correlation",
            "regional_weights": {"nh": analogs.NH_WEIGHT, "conus": analogs.CONUS_WEIGHT},
            "failed_runs_excluded": True,
        },
        "status": "ready" if entries and not skipped else ("partial" if entries else "empty"),
        "entries": entries,
        "skipped": skipped,
    }


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="WN2 repository or Pages tree root")
    parser.add_argument("--archive", required=True, help="AnalogWX z500_anom.zarr path")
    parser.add_argument("--models", default="cfsv2,superensemble", help="comma-separated model keys")
    parser.add_argument("--top-n", type=int, default=analogs.DEFAULT_TOP_N)
    parser.add_argument("--variable", default="z500_anom")
    parser.add_argument("--output", default="public/seasonal/analog_z500_manifest.json")
    parser.add_argument("--allow-empty", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    models = [value.strip() for value in args.models.split(",") if value.strip()]
    archive_path = Path(args.archive)
    if not archive_path.is_absolute():
        archive_path = root / archive_path
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = root / output_path
    try:
        payload = build_manifest(
            root=root,
            archive_path=archive_path,
            output_path=output_path,
            models=models,
            top_n=args.top_n,
            variable=args.variable,
            allow_empty=args.allow_empty,
        )
        write_manifest(output_path, payload)
    except (AnalogBuildError, analogs.SeasonalAnalogError) as exc:
        print(f"SEASONAL ANALOG ERROR: {exc}")
        return 2
    print(f"wrote seasonal analog manifest: {output_path} ({len(payload['entries'])} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
