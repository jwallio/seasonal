#!/usr/bin/env python3
"""Write metadata identifying the current product fragment in a manifest."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re


def normalize_init(value: str) -> str:
    value = value.strip()
    if value == "latest":
        now = dt.datetime.now(dt.timezone.utc)
        parsed = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif re.fullmatch(r"\d{6}", value):
        parsed = dt.datetime.strptime(value, "%Y%m").replace(tzinfo=dt.timezone.utc)
    elif re.fullmatch(r"\d{8}", value):
        parsed = dt.datetime.strptime(value, "%Y%m%d").replace(tzinfo=dt.timezone.utc)
    elif re.fullmatch(r"\d{10}", value):
        parsed = dt.datetime.strptime(value, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    else:
        raise ValueError("initialization must be latest, YYYYMM, YYYYMMDD, or YYYYMMDDHH")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def write_fragment(manifest_path: Path, product: str, requested_init: str, output: Path) -> dict:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if requested_init.strip() == "latest":
        product_inits = sorted(
            {
                str(run.get("init_utc"))
                for run in payload.get("runs", [])
                if isinstance(run, dict)
                and run.get("product") == product
                and run.get("init_utc")
            }
        )
        init_utc = product_inits[-1] if product_inits else normalize_init(requested_init)
    else:
        init_utc = normalize_init(requested_init)
    runs = [
        run
        for run in payload.get("runs", [])
        if isinstance(run, dict)
        and run.get("product") == product
        and run.get("init_utc") == init_utc
        and run.get("id")
    ]
    if not runs:
        raise ValueError(f"manifest has no {product} run for {init_utc}")
    fragment = {
        "schema_version": 1,
        "product": product,
        "init_utc": init_utc,
        "run_ids": [str(run["id"]) for run in runs],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(fragment, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return fragment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--init", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        fragment = write_fragment(args.manifest, args.product, args.init, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"SEASONAL FRAGMENT ERROR: {exc}")
        return 2
    print(f"wrote seasonal fragment: {args.output} ({len(fragment['run_ids'])} run entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
