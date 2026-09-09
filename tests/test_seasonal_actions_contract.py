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
    check("paths:" in runner and "main.py" in runner and "public/**" not in runner, "WeatherNext push runs should be path-scoped")
    check("group: weathernext-" in runner, "WeatherNext wrapper should define push concurrency")

    publisher = (WORKFLOWS / "publish-pages.yml").read_text(encoding="utf-8")
    check("cancel-in-progress: false" in publisher, "Pages publishing must remain serialized")
    check("cache-dependency-path: dashboard-source/scripts/temperature_display.py" in publisher,
          "Pages pip cache must reference a file included in the sparse checkout")
    product_scoped = {
        "apcc.yml": "inputs.product",
        "cansips.yml": "inputs.product",
        "c3s.yml": "inputs.product",
        "cma-cpsv3.yml": "inputs.product",
        "geos-s2s3.yml": "inputs.product",
        "jma.yml": "inputs.product",
        "seas5.yml": "inputs.product",
        "superensemble.yml": "inputs.product",
        "nmme.yml": "inputs.products",
    }
    for name, token in product_scoped.items():
        workflow = (WORKFLOWS / name).read_text(encoding="utf-8")
        check(token in workflow.split("concurrency:", 1)[1].split("jobs:", 1)[0],
              f"{name} concurrency must include the requested product scope")
    snow = (WORKFLOWS / "cfsv2-snow.yml").read_text(encoding="utf-8")
    check("cancel-in-progress: true" in snow, "snowfall acquisition must release stale locks")
    check("max-parallel: 4" in snow and "--workers 4" in snow, "snowfall acquisition should overlap bounded workers")
    check("run.get('conclusion') != 'success'" in snow and "No successful CFSv2 snowfall input artifact" in snow,
          "snowfall reuse must reject partial or failed source runs")
    height = (WORKFLOWS / "height-style-refresh.yml").read_text(encoding="utf-8")
    check("scripts/height_display.py" in height and "workflow_dispatch:" in height,
          "500-mb styling should refresh when the shared height contract changes")
    check("group: height-maintenance-" in height and "cancel-in-progress: true" in height,
          "500-mb styling should supersede stale maintenance fan-out")
    check("max-parallel: 4" in height and "500mb_height_anomaly_nh" in height,
          "500-mb styling should refresh both views with bounded workers")
    check("publish_wait >= 1800" in height and "Pages publisher queue did not drain" in height,
          "500-mb handoff should wait for a bounded serialized Pages queue")
    print("SEASONAL ACTIONS CONTRACT OK: planned matrices, shared tools, product-scoped workers, and bounded publishers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
