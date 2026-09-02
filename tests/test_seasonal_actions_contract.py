#!/usr/bin/env python3
"""Contracts for the seasonal GitHub Actions orchestration layer."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
WGRIB2_ACTION = ROOT / ".github" / "actions" / "setup-wgrib2" / "action.yml"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    check(WGRIB2_ACTION.exists(), "shared wgrib2 composite action is missing")
    action = WGRIB2_ACTION.read_text(encoding="utf-8")
    for term in (
        "using: composite",
        "actions/cache/restore@v4",
        "actions/cache/save@v4",
        "wgrib2-${{ runner.os }}-v1",
        "make -C \"$makefile_dir\" -j\"$(nproc)\"",
        "CANSIPS_WGRIB2=",
        "CFSV2_WGRIB2=",
    ):
        check(term in action, f"shared wgrib2 action is missing: {term}")

    for name in ("c3s.yml", "superensemble.yml"):
        workflow = (WORKFLOWS / name).read_text(encoding="utf-8")
        check("  plan:" in workflow, f"{name} should resolve its matrix in a plan job")
        check("fromJSON(needs.plan.outputs.products)" in workflow, f"{name} should use the planned product matrix")
        check("matrix.product" not in workflow.split("jobs:", 1)[1].split("  plan:", 1)[0], f"{name} must not evaluate matrix in a job-level condition")

    for name in ("cansips.yml", "cfsv2.yml", "superensemble.yml"):
        workflow = (WORKFLOWS / name).read_text(encoding="utf-8")
        check("uses: ./.github/actions/setup-wgrib2" in workflow, f"{name} should use the shared wgrib2 action")
        check("sudo apt-get update" not in workflow, f"{name} should not duplicate the wgrib2 build")

    for name in ("apcc.yml", "cansips.yml", "cma-cpsv3.yml", "cfsv2.yml", "geos-s2s3.yml", "jma.yml", "nmme.yml", "seas5.yml"):
        workflow = (WORKFLOWS / name).read_text(encoding="utf-8")
        check("concurrency:" in workflow, f"{name} should define a worker concurrency group")
        check("cancel-in-progress: true" in workflow, f"{name} should cancel superseded retries")

    runner = (WORKFLOWS / "runner.yml").read_text(encoding="utf-8")
    check("paths:" in runner and "main.py" in runner and "public/**" in runner, "WeatherNext push runs should be path-scoped")
    check("group: weathernext-" in runner, "WeatherNext wrapper should define push concurrency")

    publisher = (WORKFLOWS / "publish-pages.yml").read_text(encoding="utf-8")
    check("cancel-in-progress: false" in publisher, "Pages publishing must remain serialized")
    print("SEASONAL ACTIONS CONTRACT OK: planned matrices, shared tools, worker concurrency, and scoped WeatherNext triggers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
