"""Find newly complete CFSv2 cycles for the availability dispatcher.

The NOAA NOMADS directory is the source of truth.  A cycle directory can be
listed before all of its monthly or six-hourly files are ready, so this probe
checks the exact files needed by each producer before asking GitHub Actions to
render anything.  It deliberately treats a transient NOAA failure as
"not ready" so the next poll can retry without starting a partial run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Iterable

import requests

import cfsv2_seasonal as cf
import cfsv2_surface_schedule as snow


STANDARD_PRODUCTS = (
    cf.PRODUCT_HEIGHT_ANOMALY,
    cf.PRODUCT_HEIGHT_ANOMALY_NH,
    cf.PRODUCT_850_TEMPERATURE_ANOMALY,
    cf.PRODUCT_2M_TEMPERATURE_ANOMALY,
    cf.PRODUCT_MSLP_ANOMALY,
    cf.PRODUCT_PRECIPITATION_ANOMALY,
)
STANDARD_LEADS = (4, 5, 6)


def _cycle_from_iso(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(dt.timezone.utc).strftime("%Y%m%d%H")


def published_cycle(
    manifest_url: str,
    products: Iterable[str],
    *,
    get=requests.get,
) -> str | None:
    """Return the newest published cycle for a product subset."""

    try:
        response = get(manifest_url, timeout=(10, 20), headers={"Cache-Control": "no-cache"})
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError, AttributeError):
        return None
    allowed = set(products)
    by_cycle: dict[str, set[str]] = {}
    for run in payload.get("runs", []):
        if not isinstance(run, dict) or (allowed and run.get("product") not in allowed):
            continue
        cycle = _cycle_from_iso(run.get("init_utc"))
        if cycle:
            by_cycle.setdefault(cycle, set()).add(str(run.get("product")))
    complete = [cycle for cycle, seen in by_cycle.items() if not allowed or allowed.issubset(seen)]
    return max(complete) if complete else None


def _probe_monthly_cycle(candidates: list[str]) -> str | None:
    try:
        return cf.discover_latest_ready_init(
            STANDARD_PRODUCTS,
            STANDARD_LEADS,
            candidate_inits=candidates,
            wait_for_latest_minutes=0,
            retry_seconds=15,
        )
    except cf.CFSv2Error as exc:
        print(f"CFSv2 monthly source is not ready: {exc}")
        return None


def _probe_snow_cycle(candidates: list[str]) -> str | None:
    for init in candidates:
        try:
            leads, _windows = cf.default_winter_snowfall_windows(init)
            targets = [cf.target_month(init, lead) for lead in leads]
            if snow.listed_complete(init, targets):
                return init
        except (cf.CFSv2Error, requests.RequestException) as exc:
            print(f"CFSv2 snowfall source probe failed for {init}: {exc}")
    return None


def inspect(*, manifest_url: str) -> dict[str, object]:
    """Return source and publication state for both CFSv2 producers."""

    result: dict[str, object] = {
        "standard_ready_init": None,
        "snow_ready_init": None,
        "standard_published_init": published_cycle(manifest_url, STANDARD_PRODUCTS),
        "snow_published_init": published_cycle(
            manifest_url,
            (cf.PRODUCT_SNOWFALL_ANOMALY, cf.PRODUCT_SNOWFALL_ACCUMULATION),
        ),
    }
    try:
        candidates = cf.listed_cycle_inits()
    except cf.CFSv2Error as exc:
        print(f"CFSv2 source listing is unavailable: {exc}")
        return result
    result["standard_ready_init"] = _probe_monthly_cycle(candidates)
    result["snow_ready_init"] = _probe_snow_cycle(candidates)
    return result


def _is_newer(ready: object, published: object) -> bool:
    return isinstance(ready, str) and bool(ready) and (not isinstance(published, str) or ready > published)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-url", required=True)
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args()
    state = inspect(manifest_url=args.manifest_url)
    state["standard_new_cycle"] = _is_newer(state["standard_ready_init"], state["standard_published_init"])
    state["snow_new_cycle"] = _is_newer(state["snow_ready_init"], state["snow_published_init"])
    print(json.dumps(state, sort_keys=True))
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as handle:
            for key, value in state.items():
                handle.write(f"{key}={json.dumps(value) if isinstance(value, bool) else value or ''}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
