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
          and "uses: ./.github/actions/publish-pages" in cfsv2
          and "source-workflow: CFSv2 Rolling Seasonal Graphics" in cfsv2
          and "source-run-id: ${{ github.run_id }}" in cfsv2
          and "github.actor == 'github-actions[bot]'" in cfsv2,
          "token-launched CFSv2 renders must use the queue-safe Pages handoff")
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
    style_refresh = (WORKFLOWS / "height-style-refresh.yml").read_text(encoding="utf-8")
    for path in (
        "scripts/seasonal_rendering.py", "scripts/seasonal_products.py",
        "scripts/height_display.py", "scripts/temperature_display.py",
        "scripts/snowfall_display.py", "scripts/cfsv2_seasonal.py",
        "scripts/cfsv2_native_snow.py",
    ):
        check(path in style_refresh, f"canonical style refresh is missing trigger path {path}")
    for workflow_name in (
        "seas5.yml", "c3s.yml", "jma.yml", "cfsv2.yml",
        "apcc.yml", "cma-cpsv3.yml", "geos-s2s3.yml", "sfs.yml",
        "superensemble.yml", "nmme.yml",
    ):
        check(f"- {workflow_name}" in style_refresh, f"canonical style refresh is missing {workflow_name}")
    check("gh workflow run cansips.yml" in style_refresh
          and "after shared CDS workers" in style_refresh
          and "producer-refs/cansips/producer.tsv" in style_refresh,
          "CanSIPS must run after the shared CDS matrix and join the atomic payload")
    check("group: canonical-seasonal-style-" in style_refresh and "cancel-in-progress: true" in style_refresh,
          "canonical styling should supersede a stale fan-out")
    check(style_refresh.count("max-parallel: 4") == 1,
          "canonical styling should bound provider refresh concurrency")
    check("args+=(-f product=all)" in style_refresh and "-f products=2m_temperature_anomaly" in style_refresh,
          "canonical styling should request each provider's complete supported comparison suite")
    check("gh workflow run" in style_refresh
          and ".headSha ==" in style_refresh and "$GITHUB_SHA" in style_refresh
          and "same_sha_live" not in style_refresh,
          "canonical styling must dispatch a complete suite and resolve its exact-revision producer")
    check("${provider}_manifest.json" in style_refresh and "STYLE_INIT=" in style_refresh,
          "C3S/JMA style refresh should reuse an accessible published cycle")
    check("style_refresh=true" in style_refresh,
          "provider children should identify coordinated complete-suite refreshes")
    check("needs: refresh" in style_refresh
          and "always() && needs.refresh.result != 'cancelled'" in style_refresh
          and "canonical-seasonal-pages-${{ github.run_id }}" in style_refresh
          and "at least CFSv2 and C3S staged provider references" in style_refresh
          and "source-workflow: Canonical Seasonal Style Refresh" in style_refresh,
          "canonical styling must publish the successful provider subset as one aggregate payload")
    check("corrected-snowfall-ready" in style_refresh and "payloads/cfsv2-snow" in style_refresh,
          "the atomic payload must include both CFSv2 weather and corrected snowfall artifacts")

    staged_workers = (
        "cfsv2.yml", "cansips.yml", "c3s.yml", "seas5.yml", "jma.yml",
        "apcc.yml", "cma-cpsv3.yml", "geos-s2s3.yml", "sfs.yml",
        "superensemble.yml", "nmme.yml",
    )
    for name in staged_workers:
        workflow = (WORKFLOWS / name).read_text(encoding="utf-8")
        check("style_refresh:" in workflow and "inputs.style_refresh != true" in workflow,
              f"{name} must expose staged rendering and suppress its individual Pages publish")
    check("style_refresh: ${{ inputs.style_refresh }}" in cfsv2,
          "CFSv2 must pass coordinated staging through to corrected snowfall")
    check("style_refresh:" in snow and "inputs.style_refresh != true" in snow,
          "corrected snowfall must be stageable without an individual Pages publish")
    check("Canonical Seasonal Style Refresh" in publisher
          and "canonical-seasonal-pages-${{ env.SOURCE_RUN_ID }}" in publisher
          and "payloads/cfsv2-snow" in publisher
          and "Canonical payload must contain all 11 provider references" in publisher
          and "payloads/${slug}" in publisher
          and "migrate_legacy_height_artifacts.py" in publisher,
          "Pages must require every canonical provider payload and migrate legacy height paths")
    check("transient Pages tree containing a mixture" in publisher
          and "diff --name-only" in publisher
          and "needs.route_push.outputs.run_workflow == 'true'" in publisher,
          "direct push publication must be gated while a canonical style release is staging")

    temperature = (WORKFLOWS / "temperature-style-refresh.yml").read_text(encoding="utf-8")
    check("push:" not in temperature.split("permissions:", 1)[0],
          "legacy temperature maintenance must not duplicate canonical push fan-out")
    sfs = (WORKFLOWS / "sfs.yml").read_text(encoding="utf-8")
    sfs_trigger = sfs.split("workflow_dispatch:", 1)[0]
    check("scripts/seasonal_products.py" not in sfs_trigger and "scripts/height_display.py" not in sfs_trigger,
          "SFS push triggers must leave shared-style invalidation to the canonical coordinator")
    snow_trigger = snow.split("workflow_call:", 1)[0]
    check("scripts/seasonal_products.py" not in snow_trigger and "scripts/snowfall_display.py" not in snow_trigger,
          "CFS snowfall push triggers must leave shared-style invalidation to the canonical coordinator")
    for name in ("apcc.yml", "cansips.yml", "sfs.yml", "cfsv2-snow.yml"):
        workflow = (WORKFLOWS / name).read_text(encoding="utf-8")
        check("diff --name-only" in workflow
              and "github.event.before" in workflow
              and "needs: route_push" in workflow
              and "--filter=blob:none" in workflow
              and "[canonical-style-release]" in workflow
              and "seasonal_rendering" in workflow,
              f"{name} must route from Git history and suppress its push-triggered producer during an atomic shared-style release")
    check("scripts/seasonal_rendering.py" in publisher,
          "Pages sparse checkout must include the canonical style registry")
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
