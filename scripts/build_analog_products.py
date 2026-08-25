"""Generate source-backed maps for the current top seasonal analogs.

The analog matcher is intentionally independent from these external map
services.  This builder consumes its published manifest, requests the maps
only for a new top historical period, and retains the previous image when a
provider is unavailable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from pathlib import Path
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

import seasonal_analogs as analogs


SCHEMA_VERSION = "seasonal_analog_products_v1"
PSL_MAP_URL = "https://psl.noaa.gov/cgi-bin/data/atmoswrit/map.proc.pl"
PSL_MAP_PAGE = "https://psl.noaa.gov/data/atmoswrit/map/"
MRCC_MAP_URL = "https://gridded.geddes.rcac.purdue.edu/generate-map"
MRCC_MAP_PAGE = "https://mrcc.purdue.edu/CLIMATE/maps/interpolated"
NWS_EASTERN_REGION = "ER"
MRCC_REQUEST_ATTEMPTS = 2
MRCC_RETRY_DELAY_SECONDS = 2.0

PRODUCT_SPECS: dict[str, dict[str, str]] = {
    "psl_500mb_height_anomaly": {
        "label": "500-mb Geopotential Height Anomaly",
        "provider": "NOAA PSL WRIT",
        "source": PSL_MAP_PAGE,
        "variable": "Geopotential Height",
        "level": "500mb",
        "contourtype": "Shaded w/overlying contours",
    },
    "psl_2m_temperature_anomaly": {
        "label": "2-m Temperature Anomaly",
        "provider": "NOAA PSL WRIT",
        "source": PSL_MAP_PAGE,
        "variable": "2m Air Temperature",
        "level": "1000mb",
        "contourtype": "Shaded",
    },
    "mrcc_snowfall_departure": {
        "label": "Snowfall Departure · NWS Eastern Region",
        "provider": "MRCC / ACIS",
        "source": MRCC_MAP_PAGE,
    },
}
MODEL_LABELS = {"cfsv2": "CFSv2", "superensemble": "Super Ensemble"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MONTH_LABEL = re.compile(r"^(?:December|January|February)\s+(?P<year>\d{4})$")
_DJF_LABEL = re.compile(r"^DJF\s+(?P<start>\d{4})-(?P<end>\d{2})$")


class AnalogProductError(RuntimeError):
    """The analog product manifest or a source response is unusable."""


def _now_iso() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _resolve_rooted(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _relative_asset(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _read_json(path: Path, *, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise AnalogProductError(f"manifest does not exist: {path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnalogProductError(f"could not read manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnalogProductError(f"manifest is not an object: {path}")
    return payload


def _default_fetch(url: str, timeout: int) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "text/html,image/png,application/octet-stream",
            "User-Agent": "wall.cloud seasonal analog products/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed HTTPS sources
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise AnalogProductError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except URLError as exc:
        raise AnalogProductError(f"source request failed for {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise AnalogProductError(f"source request timed out for {url}") from exc


def _period_for_result(target: str, result: dict[str, Any]) -> dict[str, Any]:
    """Translate an analog result into the source service's date controls."""

    metadata = analogs.parse_target(target)
    try:
        winter_year = int(result["winter_year"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalogProductError(f"analog result has no valid winter_year: {result}") from exc

    if metadata["period_type"] == "month":
        month = int(metadata["month"])
        source_year = winter_year - 1 if month == 12 else winter_year
        start = dt.date(source_year, month, 1)
        end = dt.date(source_year, month, _days_in_month(source_year, month))
        label = str(result.get("label") or f"{start.strftime('%B')} {source_year}")
        return {
            "period_type": "month",
            "label": label,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "psl_year": source_year,
            "psl_start_month": month,
            "psl_end_month": month,
            "winter_year": winter_year,
        }

    start = dt.date(winter_year - 1, 12, 1)
    end = dt.date(winter_year, 2, _days_in_month(winter_year, 2))
    label = str(result.get("label") or f"DJF {winter_year - 1}-{str(winter_year)[-2:]}")
    return {
        "period_type": "djf",
        "label": label,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        # PSL asks for the year of the last month when a season crosses years.
        "psl_year": winter_year,
        "psl_start_month": 12,
        "psl_end_month": 2,
        "winter_year": winter_year,
    }


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = dt.date(year + 1, 1, 1)
    else:
        next_month = dt.date(year, month + 1, 1)
    return (next_month - dt.timedelta(days=1)).day


def _psl_url(period: dict[str, Any], spec: dict[str, str]) -> str:
    query = {
        "dataset1": "ERA5",
        "var": spec["variable"],
        "level": spec["level"],
        "iy": str(period["psl_year"]),
        "fmonth": str(int(period["psl_start_month"]) - 1),
        "fmonth2": str(int(period["psl_end_month"]) - 1),
        "type": "1",
        "map": "0",
        "mapt": "0",
        "proj": "North America",
        "colortable": "MPL_BrBG",
        "labelc": "0",
        "contourtype": spec["contourtype"],
        "scale": "100",
        "labelcon": "1",
        "switch": "0",
        "gridfill": "0",
        "google": "0",
        "Submit": "Create Plot",
    }
    return f"{PSL_MAP_URL}?{urlencode(query)}"


def _mrcc_url(period: dict[str, Any]) -> str:
    query = {
        "s": "station",
        "a": "region",
        "loc": NWS_EASTERN_REGION,
        "var": "snow",
        "ds": str(period["start_date"]).replace("-", ""),
        "de": str(period["end_date"]).replace("-", ""),
        "stat": "total",
        "calc": "departure",
        "con": "5",
        "lta": "F",
        "cwa": "F",
        "cities": "T",
        "counties": "T",
        "state": "F",
        "mask": "F",
        "lakes": "T",
        "oceans": "T",
        "roads": "F",
        "output": "map_btd.png",
    }
    return f"{MRCC_MAP_URL}?{urlencode(query)}"


def _mrcc_retryable(error: AnalogProductError) -> bool:
    message = str(error)
    return bool(
        re.search(r"\bHTTP (?:429|500|502|503|504)\b", message)
        or "timed out" in message.lower()
        or "source request failed" in message.lower()
    )


def _fetch_mrcc_image(
    fetcher: Callable[[str, int], bytes],
    url: str,
    timeout: int,
) -> bytes:
    """Retry transient MRCC generator failures before retaining a prior map."""

    errors: list[str] = []
    for attempt in range(1, MRCC_REQUEST_ATTEMPTS + 1):
        try:
            return fetcher(url, timeout)
        except AnalogProductError as exc:
            errors.append(str(exc))
            if attempt >= MRCC_REQUEST_ATTEMPTS or not _mrcc_retryable(exc):
                raise
            time.sleep(MRCC_RETRY_DELAY_SECONDS)
    raise AnalogProductError(
        f"MRCC request failed after {MRCC_REQUEST_ATTEMPTS} attempts: {' | '.join(errors)}"
    )


def _extract_psl_image_url(page: bytes) -> str:
    text = page.decode("latin-1", errors="replace")
    sources = re.findall(r"<img[^>]+src=[\"']([^\"']+\.png)[\"']", text, re.IGNORECASE)
    generated = next(
        (
            source
            for source in sources
            if "/tmp/" in source.lower() or "plot" in source.lower() or "map" in source.lower()
        ),
        None,
    )
    if generated is None:
        generated = next(
            (source for source in sources if "icon" not in source.lower()),
            None,
        )
    if generated is None:
        raise AnalogProductError("PSL response did not contain a generated PNG")
    return urljoin(PSL_MAP_URL, html.unescape(generated))


def _write_png(path: Path, data: bytes) -> None:
    if not data.startswith(PNG_SIGNATURE):
        raise AnalogProductError(f"source response is not a PNG: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def _top_key(model: str, target: str, result: dict[str, Any]) -> str:
    return f"{model}:{target}:{int(result['winter_year'])}"


def _existing_entries(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    payload = _read_json(path, required=False)
    return {
        (str(entry.get("model", "")), str(entry.get("target", ""))): entry
        for entry in payload.get("entries", [])
        if isinstance(entry, dict) and entry.get("model") and entry.get("target")
    }


def _retained_or_unavailable(
    *,
    root: Path,
    old: dict[str, Any] | None,
    product_key: str,
    top_key: str,
    source_url: str,
    error: str,
) -> dict[str, Any]:
    old_image = str((old or {}).get("image", ""))
    old_path = _resolve_rooted(root, old_image) if old_image else None
    if old_path and old_path.exists():
        return {
            **old,
            "status": "stale",
            "requested_top_analog_key": top_key,
            "retained_top_analog_key": old.get("top_analog_key"),
            "source_url": source_url,
            "checked_utc": _now_iso(),
            "error": error,
        }
    return {
        "product": product_key,
        "label": PRODUCT_SPECS[product_key]["label"],
        "provider": PRODUCT_SPECS[product_key]["provider"],
        "status": "unavailable",
        "top_analog_key": top_key,
        "source_url": source_url,
        "checked_utc": _now_iso(),
        "error": error,
    }


def _build_product(
    *,
    root: Path,
    output_dir: Path,
    model: str,
    target: str,
    top: dict[str, Any],
    period: dict[str, Any],
    product_key: str,
    old: dict[str, Any] | None,
    fetcher: Callable[[str, int], bytes],
    timeout: int,
    climatology_years: str,
) -> dict[str, Any]:
    spec = PRODUCT_SPECS[product_key]
    top_key = _top_key(model, target, top)
    if old and old.get("top_analog_key") == top_key:
        old_path = _resolve_rooted(root, str(old.get("image", ""))) if old.get("image") else None
        if old_path and old_path.exists() and old.get("status") == "ready":
            return old

    image_path = output_dir / model / target / str(period["winter_year"]) / f"{product_key}.png"
    source_url = _psl_url(period, spec) if product_key.startswith("psl_") else _mrcc_url(period)
    try:
        if product_key.startswith("psl_"):
            page = fetcher(source_url, timeout)
            image_url = _extract_psl_image_url(page)
            image = fetcher(image_url, timeout)
            provider_asset_url = image_url
        else:
            image = _fetch_mrcc_image(fetcher, source_url, timeout)
            provider_asset_url = source_url
        _write_png(image_path, image)
        return {
            "product": product_key,
            "label": spec["label"],
            "provider": spec["provider"],
            "status": "ready",
            "image": _relative_asset(root, image_path),
            "top_analog_key": top_key,
            "period": period,
            "source_url": source_url,
            "provider_asset_url": provider_asset_url,
            "dataset": "ERA5" if product_key.startswith("psl_") else "MRCC station-interpolated snowfall",
            "climatology_years": climatology_years,
            "generated_utc": _now_iso(),
        }
    except (AnalogProductError, OSError, ValueError) as exc:
        return _retained_or_unavailable(
            root=root,
            old=old,
            product_key=product_key,
            top_key=top_key,
            source_url=source_url,
            error=str(exc),
        )


def build_manifest(
    *,
    root: Path,
    analog_manifest_path: Path,
    output_manifest_path: Path,
    output_dir: Path,
    timeout: int = 180,
    fetcher: Callable[[str, int], bytes] = _default_fetch,
) -> dict[str, Any]:
    """Build products for every published CFSv2/Super Ensemble top analog."""

    analog_manifest = _read_json(analog_manifest_path)
    old_entries = _existing_entries(output_manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    source = analog_manifest.get("source") if isinstance(analog_manifest.get("source"), dict) else {}
    climatology_years = str(source.get("climatology_years") or "unspecified")
    entries: list[dict[str, Any]] = []
    for raw in analog_manifest.get("entries", []):
        if not isinstance(raw, dict) or str(raw.get("model")) not in MODEL_LABELS:
            continue
        results = raw.get("results")
        if not isinstance(results, list) or not results:
            continue
        top = next((item for item in results if isinstance(item, dict) and int(item.get("rank", 0)) == 1), None)
        if not top:
            top = next((item for item in results if isinstance(item, dict)), None)
        if not top:
            continue
        model = str(raw["model"])
        target = str(raw["target"])
        period = _period_for_result(target, top)
        old_entry = old_entries.get((model, target))
        old_products = old_entry.get("products", {}) if isinstance(old_entry, dict) else {}
        products = {
            key: _build_product(
                root=root,
                output_dir=output_dir,
                model=model,
                target=target,
                top=top,
                period=period,
                product_key=key,
                old=old_products.get(key) if isinstance(old_products, dict) else None,
                fetcher=fetcher,
                timeout=timeout,
                climatology_years=climatology_years,
            )
            for key in PRODUCT_SPECS
        }
        statuses = {str(product.get("status")) for product in products.values()}
        entry_status = "ready" if statuses == {"ready"} else "stale" if "stale" in statuses else "partial" if "ready" in statuses else "unavailable"
        entries.append(
            {
                "model": model,
                "model_label": str(raw.get("model_label") or MODEL_LABELS[model]),
                "target": target,
                "target_label": str(raw.get("target_label") or target),
                "init_utc": raw.get("init_utc"),
                "top_analog_key": _top_key(model, target, top),
                "top_analog": top,
                "period": period,
                "status": entry_status,
                "products": products,
            }
        )

    statuses = {str(entry.get("status")) for entry in entries}
    overall = "ready" if entries and statuses == {"ready"} else "partial" if entries and ("ready" in statuses or "stale" in statuses or "partial" in statuses) else "unavailable"
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "seasonal_analog_products_manifest",
        "generated_utc": _now_iso(),
        "source": {
            "analog_manifest": "seasonal/analog_z500_manifest.json",
            "climatology_years": climatology_years,
            "psl": PSL_MAP_PAGE,
            "mrcc": MRCC_MAP_PAGE,
            "nws_region": "NWS Eastern Region (ER)",
            "period_rule": "monthly analogs use that calendar month; DJF analogs use December through February",
            "retained_on_source_failure": True,
        },
        "status": overall,
        "entries": entries,
    }


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Pages tree root")
    parser.add_argument("--analog-manifest", default="seasonal/analog_z500_manifest.json")
    parser.add_argument("--output", default="seasonal/analog_products_manifest.json")
    parser.add_argument("--output-dir", default="seasonal/analog_products")
    parser.add_argument("--timeout", type=int, default=300)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    analog_manifest_path = _resolve_rooted(root, args.analog_manifest)
    output_manifest_path = _resolve_rooted(root, args.output)
    output_dir = _resolve_rooted(root, args.output_dir)
    try:
        payload = build_manifest(
            root=root,
            analog_manifest_path=analog_manifest_path,
            output_manifest_path=output_manifest_path,
            output_dir=output_dir,
            timeout=args.timeout,
        )
        write_manifest(output_manifest_path, payload)
    except AnalogProductError as exc:
        print(f"SEASONAL ANALOG PRODUCTS ERROR: {exc}")
        return 2
    print(f"wrote seasonal analog products manifest: {output_manifest_path} ({len(payload['entries'])} entries; {payload['status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
