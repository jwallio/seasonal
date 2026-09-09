"""Acquire and cache surface-phase snowfall; build matched reference bundles.

Use --historical-year to shard the one-time backfill. This command never
publishes maps. --reference requires every 2011-2025 month/cycle bundle.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import hashlib
import os
import json
from pathlib import Path
import threading
import time
from email.utils import parsedate_to_datetime

import eccodes as ec
import requests

import cfsv2_surface_phase as phase
from cfsv2_native_reference import historical_cycle

class SourceResponseError(ValueError):
    """An incomplete source response that may recover on retry."""


HTTP = threading.local()


def session():
    if not hasattr(HTTP, "value"):
        HTTP.value = requests.Session()
    return HTTP.value


def get(url, start=None, end=None):
    headers = {} if start is None else {"Range": f"bytes={start}-{end}"}
    for attempt in range(8):
        try:
            r = session().get(url, headers=headers, timeout=(10, 40))
            r.raise_for_status()
            if start is not None and (r.status_code != 206 or len(r.content) > end-start+1):
                raise SourceResponseError("Source ignored bounded byte range")
            if url.endswith(".idx"):
                lines = r.content.decode("ascii", errors="replace").splitlines()
                if any(sum(f":{name.upper()}:surface:" in line for line in lines) != 1
                       for name in phase.PARAMETERS):
                    raise SourceResponseError("Incomplete/duplicate pressure-file index")
            return r.content
        except (requests.RequestException, SourceResponseError) as exc:
            response = getattr(exc, "response", None)
            status = response.status_code if response is not None else None
            if status is not None and status not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt == 7:
                raise
            delay = min(60 * 2**attempt, 900)
            if response is not None:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    try:
                        delay = max(delay, parsedate_to_datetime(retry_after).timestamp()-time.time())
                    except (ValueError, TypeError, OverflowError):
                        pass
            print(f"Transient download failure ({status or type(exc).__name__}); retry {attempt+1}/7 in {delay:.0f}s", flush=True)
            while delay > 0:
                chunk = min(delay, 60)
                time.sleep(chunk)
                delay -= chunk


def extract(data, init, valid):
    messages, pos = {}, 0
    by_number = {v: k for k, v in phase.PARAMETERS.items()}
    while (start := data.find(b"GRIB", pos)) >= 0:
        pos = start + 4
        if start+16 > len(data) or data[start+7] != 2:
            continue
        length = int.from_bytes(data[start+8:start+16], "big")
        raw = data[start:start+length]
        if len(raw) != length or raw[-4:] != b"7777":
            continue
        h = ec.codes_new_from_message(raw)
        try:
            if ec.codes_get(h, "discipline") != 0 or ec.codes_get(h, "parameterCategory") != 1:
                continue
            field = by_number.get(ec.codes_get(h, "parameterNumber"))
            if field is None or ec.codes_get(h, "typeOfLevel") != "surface":
                continue
            if field in messages:
                raise ValueError("Duplicate source parameter")
            for key, value in dict(dataDate=int(init[:8]), dataTime=int(init[8:])*100,
                                   validityDate=int(valid[:8]), validityTime=int(valid[8:])*100).items():
                if ec.codes_get(h, key) != value:
                    raise ValueError("Source initialization/valid-time mismatch")
            messages[field] = raw
        finally:
            ec.codes_release(h)
    return messages


class Source:
    def __init__(self, directory, init, offline=False):
        self.directory, self.init, self.offline = Path(directory), init, offline

    def __call__(self, valid, field):
        stem = self.directory / self.init / valid
        path = stem.with_suffix(f".{field}.grb2")
        provenance = stem.with_suffix(".sources.json")
        if path.exists() and provenance.exists():
            meta = json.loads(provenance.read_text())
            if phase.digest(path) != meta["sha256"][field]:
                raise ValueError("Raw cache checksum mismatch")
            return path
        if self.offline:
            raise FileNotFoundError(path)
        init = self.init
        aws = ("https://noaa-cfs-pds.s3.amazonaws.com/"
               f"cfs.{init[:8]}/{init[8:]}/6hrly_grib_01/pgbf{valid}.01.{init}.grb2")
        providers = [aws] if int(init[:4]) >= 2019 else []
        live_cycles = set(os.environ.get("CFSV2_LIVE_CYCLES", "").split(","))
        if init in live_cycles or init == os.environ.get("CFSV2_LIVE_INIT"):
            providers.insert(0, "https://nomads.ncep.noaa.gov/pub/data/nccf/com/cfs/prod/"
                             f"cfs.{init[:8]}/{init[8:]}/6hrly_grib_01/pgbf{valid}.01.{init}.grb2")
        messages, url = {}, aws
        for url in providers:
            try:
                lines = get(url+".idx").decode("ascii").splitlines()
                selected = [i for i, line in enumerate(lines) if any(
                    f":{name.upper()}:surface:" in line for name in phase.PARAMETERS)]
                if len(selected) != 5:
                    raise ValueError("Incomplete/duplicate pressure-file index")
                lo, hi = min(selected), max(selected)
                if hi+1 >= len(lines):
                    raise ValueError("Unbounded pressure index")
                start, end = int(lines[lo].split(":")[1]), int(lines[hi+1].split(":")[1])-1
                if end-start > 2_000_000:
                    raise ValueError("Unexpected pressure-field range")
                messages = extract(get(url, start, end), init, valid)
                if set(messages) != set(phase.PARAMETERS):
                    raise ValueError("Incomplete source fields")
                break
            except (requests.RequestException, SourceResponseError) as exc:
                response = getattr(exc, "response", None)
                status = response.status_code if response is not None else None
                if status is not None and status not in (404, 408, 429, 500, 502, 503, 504):
                    raise
                print(f"Indexed provider unavailable ({status or type(exc).__name__}); trying next exact-record source", flush=True)
        if not messages:
            url = ("https://www.ncei.noaa.gov/oa/prod-cfs-operational-forecast/"
                   f"6-hourly-by-pressure/{init[:4]}/{init[:6]}/{init[:8]}/{init}/"
                   f"pgbf{valid}.01.{init}.grb2")
            # NCEI exposes no .idx for these older files. The adjacent surface
            # records are near byte 16M; expand only if validated extraction fails.
            for start, end in [(15_500_000, 17_499_999), (14_000_000, 17_999_999),
                               (0, 29_999_999)]:
                try:
                    data = get(url, start, end)
                except requests.HTTPError as exc:
                    # A compact GRIB can end before a heuristic range begins.
                    # Try the next bounded window, including the byte-zero fallback.
                    if exc.response is None or exc.response.status_code != 416:
                        raise
                    print(f"NCEI range {start}-{end} unavailable; trying next bounded window", flush=True)
                    continue
                messages = extract(data, init, valid)
                if set(messages) == set(phase.PARAMETERS):
                    break
        if set(messages) != set(phase.PARAMETERS):
            raise ValueError(f"Missing required surface fields: {url}")
        stem.parent.mkdir(parents=True, exist_ok=True)
        for name, raw in messages.items():
            destination = stem.with_suffix(f".{name}.grb2")
            temporary = destination.with_suffix(destination.suffix+".tmp")
            temporary.write_bytes(raw)
            temporary.replace(destination)
        meta = dict(url=url, initialization=init, valid=valid,
                    sha256={k: hashlib.sha256(v).hexdigest() for k, v in messages.items()})
        provenance.write_text(json.dumps(meta, indent=2)+"\n")
        return path


def build_month(cache, output, init, target, offline=False):
    stem = phase.month_stem(output, init, target)
    if stem.with_suffix(".npz").exists() and stem.with_suffix(".json").exists():
        meta = json.loads(stem.with_suffix(".json").read_text())
        if meta.get("method") == phase.METHOD:
            phase.load_month(output, init, target)
            return
        print(f"Rebuilding obsolete method for {init}/{target}", flush=True)
    arrays, meta = phase.reconstruct(init, target, Source(cache, init, offline))
    phase.save_bundle(stem, arrays, meta)
    phase.load_month(output, init, target)
    print(f"Built surface-phase snowfall {init}/{target}", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--init", required=True)
    p.add_argument("--exclude-cycles", default="", help="comma-separated forecast initialization timestamps to omit from BOTH forecast and historical acquisition/reference")
    p.add_argument("--targets", required=True, help="comma-separated YYYYMM months")
    p.add_argument("--rolling-days", type=int, choices=range(1, 7), default=6)
    p.add_argument("--historical-year", type=int, choices=phase.YEARS)
    p.add_argument("--raw-cache", type=Path, default=Path(".cache/cfsv2-surface-phase/raw"))
    p.add_argument("--bundles", type=Path, default=Path(".cache/cfsv2-surface-phase/months"))
    p.add_argument("--reference-dir", type=Path, default=Path(".cache/cfsv2-surface-phase/reference"))
    p.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    p.add_argument("--month-pause-seconds", type=int, default=0,
                   help="optional pause between month bundles; requires one worker (default 0)")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--reference", action="store_true", help="assemble all years; no acquisition")
    p.add_argument("--plan", action="store_true", help="print required month/cycle tasks")
    args = p.parse_args()
    if args.month_pause_seconds < 0:
        p.error("month pause must be nonnegative")
    if args.month_pause_seconds and args.workers != 1:
        p.error("use --workers 1 for month pacing, or --month-pause-seconds 0 for parallel acquisition")
    anchor = datetime.strptime(args.init, "%Y%m%d%H")
    if anchor.hour not in (0, 6, 12, 18):
        p.error("initialization must be a six-hour cycle")
    cycles = [(anchor-timedelta(hours=6*i)).strftime("%Y%m%d%H")
              for i in reversed(range(args.rolling_days*4))]
    cycles = phase.selected_cycles(cycles, args.exclude_cycles)
    targets = args.targets.split(",")
    for target in targets:
        phase.endpoints(target)
    if args.reference:
        if args.historical_year:
            p.error("reference assembly always includes all 15 years")
        for target in targets:
            phase.build_reference(args.bundles, args.init, target, cycles, args.reference_dir)
        return
    tasks = set()
    for target in targets:
        if args.historical_year:
            ht = f"{args.historical_year+int(target[:4])-anchor.year}{target[4:]}"
            for cycle in cycles:
                tasks.update((hc, ht) for hc, weight in historical_cycle(cycle, args.historical_year, args.init))
        else:
            tasks.update((cycle, target) for cycle in cycles)
    if args.plan:
        print(json.dumps(sorted(tasks), indent=2))
        return
    ordered = sorted(tasks)
    failures, completed = [], []
    status_path = args.bundles / "acquisition-status.json"
    args.bundles.mkdir(parents=True, exist_ok=True)
    # Adjacent months share endpoint records. Serialize within an init to avoid
    # simultaneous writes to the same raw checkpoint; different cycles overlap.
    locks = {init: threading.Lock() for init, target in ordered}
    def acquire(task):
        with locks[task[0]]:
            stem = phase.month_stem(args.bundles, *task)
            cached = stem.with_suffix(".npz").exists() and stem.with_suffix(".json").exists()
            build_month(args.raw_cache, args.bundles, *task, args.offline)
            if not cached and args.month_pause_seconds:
                remaining = args.month_pause_seconds
                while remaining:
                    duration = min(60, remaining)
                    time.sleep(duration)
                    remaining -= duration
    def report(final=False):
        temporary = status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"complete": final and not failures,
            "completed": sorted(completed), "failures": failures, "expected": ordered}, indent=2))
        temporary.replace(status_path)
    report()
    # Cycle-round-robin submission avoids filling every worker with one init.
    ordered = sorted(ordered, key=lambda task: (task[1], task[0]))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(acquire, task): task for task in ordered}
        for future in as_completed(futures):
            task = futures[future]
            try:
                future.result()
                completed.append(task)
            except Exception as exc:
                failures.append({"init": task[0], "target": task[1], "error": str(exc)})
                print(f"Incomplete cycle {task}: {exc}; continuing remaining cycles", flush=True)
            report()
    report(final=True)
    if failures:
        raise SystemExit(f"Acquisition incomplete: {len(failures)}/{len(ordered)} cycle-months failed; see {status_path}")


if __name__ == "__main__":
    main()
