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

    for name in ("apcc.yml", "cansips.yml", "cma-cpsv3.yml", "geos-s2s3.yml", "jma.yml", "nmme.yml", "seas5.yml"):
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
    check("CFSv2 Snowfall Graphics" in publisher and "name: corrected-snowfall-ready" in publisher
          and "path: incoming/cfsv2" in publisher,
          "shared Pages publisher must accept the corrected snowfall artifact")
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
    cfsv2 = (WORKFLOWS / "cfsv2.yml").read_text(encoding="utf-8")
    check("cancel-in-progress: false" in cfsv2, "CFSv2 weather rendering must not be cancelled by the next availability poll")
    check(cfsv2.count("- name: Render rolling CFSv2 products") == 1,
          "CFSv2 workflow must not contain an incomplete duplicate render step")
    check("actions: write" in cfsv2, "CFSv2 wrapper must allow the snowfall child to dispatch Pages")
    check("Dispatch Pages publisher for token-launched render" in cfsv2
          and "gh workflow run publish-pages.yml" in cfsv2
          and "source_run_id=" in cfsv2
          and "github.actor == 'github-actions[bot]'" in cfsv2,
          "token-launched CFSv2 renders must hand off their artifact to the Pages publisher")
    snow = (WORKFLOWS / "cfsv2-snow.yml").read_text(encoding="utf-8")
    check("cancel-in-progress: false" in snow, "snowfall acquisition must not be cancelled by the next availability poll")
    check("max-parallel: 4" in snow and "--workers 4" in snow, "snowfall acquisition should overlap bounded workers")
    check("run.get('conclusion') != 'success'" in snow and "No successful CFSv2 snowfall input artifact" in snow,
          "snowfall reuse must reject partial or failed source runs")
    handoff = (ROOT / ".github" / "actions" / "publish-pages" / "action.yml").read_text(encoding="utf-8")
    check("./.github/actions/publish-pages" in snow
          and "source-workflow: CFSv2 Snowfall Graphics" in snow
          and "source-run-id: ${{ github.run_id }}" in snow
          and "endswith" in handoff
          and "conclusion" in handoff,
          "snowfall publication must use the retrying, queue-safe Pages handoff")
    height = (WORKFLOWS / "height-style-refresh.yml").read_text(encoding="utf-8")
    check("scripts/height_display.py" in height and "workflow_dispatch:" in height,
          "500-mb styling should refresh when the shared height contract changes")
    check("group: height-maintenance-" in height and "cancel-in-progress: true" in height,
          "500-mb styling should supersede stale maintenance fan-out")
    check(height.count("max-parallel: 4") == 1 and "500mb_height_anomaly_nh" in height,
          "500-mb styling should bound provider refreshes without serializing the whole fan-out")
    check("deferring this style refresh" in height,
          "500-mb styling must not cancel an active scheduled or release run")
    check("the provider will publish its Pages artifact on completion" in height
          and height.count("if (( deferred ));") == 1,
          "500-mb maintenance must not wait on child runs or use an out-of-scope deferred variable")
    check("publish_wait >= 1800" in height and "Pages publisher queue did not drain" in height,
          "500-mb handoff should wait for a bounded serialized Pages queue")
    check("${provider}_manifest.json" in height and "init_arg=$(PRODUCT=" in height
          and "No prior accessible" in height,
          "C3S/JMA style refresh should fall back or skip when no accessible cycle exists")
    for name, prefix in (("temperature-style-refresh.yml", "temperature-publish-refs-"),
                         ("height-style-refresh.yml", "height-publish-refs-")):
        maintenance = (WORKFLOWS / name).read_text(encoding="utf-8")
        check("run: : > publish-refs.txt" not in maintenance,
              f"{name} must use valid block-scalar YAML for its handoff file")
        check("style_refresh" in maintenance,
              f"{name} should suppress duplicate self-publishers during maintenance")
        check("Upload serialized Pages handoff" in maintenance
              and "actions/upload-artifact@v4" in maintenance
              and f"name: {prefix}" in maintenance,
              f"{name} should collect self-publishing producer refs")
        check("Download serialized Pages handoffs" in maintenance
              and "actions/download-artifact@v4" in maintenance
              and "Publish self-dispatching provider payloads" in maintenance,
              f"{name} should publish self-publishing refs in one batch")
        check("displayTitle" in maintenance and "endswith" in maintenance and "Pages publisher queue did not drain" in maintenance,
              f"{name} should resolve and retry the serialized Pages handoff")
    temperature = (WORKFLOWS / "temperature-style-refresh.yml").read_text(encoding="utf-8")
    check(".github/workflows/temperature-style-refresh.yml" in temperature,
          "temperature maintenance changes must trigger a replacement run")
    check("isinstance(value, bool)" in temperature and 'payload["style_refresh"] = True' in temperature,
          "temperature maintenance must stringify boolean workflow inputs before gh dispatch")
    check("the provider will publish its Pages artifact on completion" in temperature
          and temperature.count("if (( deferred ));") == 1,
          "temperature maintenance must not wait on child runs or use an out-of-scope deferred variable")
    check("deferring this style refresh" in temperature,
          "temperature styling must not cancel an active scheduled or release run")
    analogs = (WORKFLOWS / "seasonal-analogs.yml").read_text(encoding="utf-8")
    check("id: source_payload" in analogs and "actions/runs/{1}/artifacts" in analogs
          and "steps.source_payload.outputs.available == 'true'" in analogs
          and "if: ${{ env.SOURCE_WORKFLOW != '' }}" in analogs
          and "if: ${{ env.SOURCE_WORKFLOW != '' && steps.source_payload.outputs.available == 'true' }}" in analogs,
          "analog source-triggered builds must verify artifacts and tolerate source runs without Pages payloads")
    token_publishers = {
        "cansips.yml": "CanSIPS v3 Seasonal Graphics",
        "apcc.yml": "APCC MME Seasonal Graphics",
        "cma-cpsv3.yml": "CMA CPSv3 Seasonal Graphics",
        "geos-s2s3.yml": "NASA GEOS-S2S-3 Seasonal Graphics",
        "nmme.yml": "NOAA NMME Seasonal Graphics",
        "superensemble.yml": "Deduplicated Seasonal Super Ensemble",
    }
    for name, source_name in token_publishers.items():
        workflow = (WORKFLOWS / name).read_text(encoding="utf-8")
        check("actions: write" in workflow, f"{name} must allow token-launched Pages publication")
        check("Dispatch Pages publisher for token-launched render" in workflow,
              f"{name} must directly publish token-launched artifacts")
        check("uses: ./.github/actions/publish-pages" in workflow,
              f"{name} must use the shared Pages handoff")
        check(f"source-workflow: {source_name}" in workflow,
              f"{name} must identify its Pages source")
        check("github.actor == 'github-actions[bot]'" in workflow,
              f"{name} must guard direct publication to token-launched runs")
    print("SEASONAL ACTIONS CONTRACT OK: planned matrices, shared tools, product-scoped workers, and bounded publishers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
