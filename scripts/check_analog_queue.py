#!/usr/bin/env python3
"""Read-only queue watchdog; never dispatches, cancels, or publishes a run."""

import argparse
from datetime import datetime, timezone
import json
import os
import subprocess
import sys


def stalled_jobs(jobs, now, threshold_minutes=15):
    """Only admitted, unassigned Seasonal jobs count as runner queue stalls."""
    stalled = []
    for job in jobs:
        if (job.get("status") != "queued" or job.get("runner_id")
                or "wn2-analogwx" not in job.get("labels", [])):
            continue
        # Workflow creation includes concurrency wait; use job admission instead.
        stamp = job.get("created_at") or job.get("started_at")
        if not stamp:
            raise ValueError("Queued analog job has no admission timestamp")
        admitted = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        minutes = (now - admitted).total_seconds() / 60
        if minutes >= threshold_minutes:
            stalled.append((job, minutes))
    return stalled


def api(path):
    result = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, timeout=45,
    )
    if result.returncode:
        # Do not echo authentication diagnostics or environment values.
        raise RuntimeError(f"GitHub API request failed (exit {result.returncode})")
    return json.loads(result.stdout)


def inspect_queue(repo, fetch=api, now=None, threshold_minutes=15):
    now = now or datetime.now(timezone.utc)
    runs = {}
    for status in ("queued", "in_progress", "waiting", "pending"):
        data = fetch(f"repos/{repo}/actions/workflows/seasonal-analogs.yml/runs?status={status}&per_page=20")
        if data.get("total_count", 0) > 20:
            raise RuntimeError("Analog queue exceeds bounded inspection limit; inspect Actions")
        runs.update((run["id"], run) for run in data["workflow_runs"])
    jobs = []
    for run_id in runs:
        data = fetch(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
        if data.get("total_count", 0) > 100:
            raise RuntimeError("Analog job list exceeds bounded inspection limit")
        jobs.extend(data["jobs"])
    return stalled_jobs(jobs, now, threshold_minutes), len(runs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "jwallio/seasonal"))
    parser.add_argument("--threshold-minutes", type=int, default=15)
    args = parser.parse_args()
    if args.threshold_minutes < 1:
        parser.error("threshold must be positive")
    try:
        stalled, active_count = inspect_queue(args.repo, threshold_minutes=args.threshold_minutes)
    except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"::error::Could not verify analog queue health: {exc}")
        return 1
    lines = [f"Inspected {active_count} active analog run(s)."]
    for job, minutes in stalled:
        lines.append(f"Unassigned analog job {job['id']} has queued for {minutes:.0f} minutes: {job.get('html_url', '')}")
    if stalled:
        lines.append("Check GitHubActionsRunner-seasonal on the archive host and runner label wn2-analogwx. No builds were canceled or restarted.")
        print("::error::Seasonal analog job is waiting for a runner beyond the queue threshold.")
    else:
        lines.append("No stalled admitted jobs. This checks demand waiting for a runner; it is not an idle-runner heartbeat.")
    summary = "\n\n".join(lines) + "\n"
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(summary)
    return int(bool(stalled))


if __name__ == "__main__":
    sys.exit(main())
