#!/usr/bin/env python3
"""Deterministic contracts for the CDS seasonal release checker."""

import datetime as dt
import importlib.util
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_seasonal_releases.py"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_checker():
    spec = importlib.util.spec_from_file_location("seasonal_release_check_contract", CHECKER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load seasonal release checker")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def synthetic_inventory(module, workers, target: str):
    inventories = {name: [] for name in module.COLLECTIONS}
    seen = set()
    for worker in workers:
        for requirement in module.source_requirements(worker):
            selection = requirement.selection(target)
            key = (requirement.collection, tuple(sorted(selection.items())))
            if key in seen:
                continue
            seen.add(key)
            inventories[requirement.collection].append(
                {dimension: [selected] for dimension, selected in selection.items()}
            )
    return inventories


def complete_manifest(module, worker: str, target: str, products=None):
    products = products or module.EXPECTED_PRODUCTS[worker]
    component = {"seas5": "", "c3s": "multisystem", "jma": "jma"}[worker]
    runs = []
    for product in products:
        run = {
            "product": product,
            "init_utc": f"{target[:4]}-{target[4:]}-01T00:00:00Z",
            "status": "rendered",
            "targets": [{"status": "rendered"} for _ in range(4)],
        }
        if component:
            run["component"] = component
        runs.append(run)
    return {"runs": runs}


def main() -> int:
    check(CHECKER.exists(), "seasonal release checker script is missing")
    module = load_checker()
    target = "202609"
    workers = ("seas5", "c3s", "jma")
    inventories = synthetic_inventory(module, workers, target)

    for worker in workers:
        missing = module.missing_source_requirements(worker, target, inventories)
        check(not missing, f"synthetic {worker} inventory should satisfy every source requirement")

    seas5_inventory = synthetic_inventory(module, ("seas5",), target)
    removed = seas5_inventory["seasonal-postprocessed-pressure-levels"].pop()
    missing = module.missing_source_requirements("seas5", target, seas5_inventory)
    check(len(missing) == 1, "one absent catalogue selection should produce one readiness gap")
    check(
        module.record_supports(removed, {key: values[0] for key, values in removed.items()}),
        "catalogue records should represent the Cartesian product of their listed constraints",
    )

    for worker in workers:
        present = module.published_products(complete_manifest(module, worker, target), worker, target)
        check(present == set(module.EXPECTED_PRODUCTS[worker]), f"complete {worker} suite was not recognized")

    c3s_individual = complete_manifest(module, "c3s", target)
    for run in c3s_individual["runs"]:
        run["component"] = "ecmwf"
    check(
        not module.published_products(c3s_individual, "c3s", target),
        "individual C3S components must not masquerade as the multi-system live suite",
    )

    partial = complete_manifest(module, "jma", target)
    partial["runs"][0]["targets"][0]["status"] = "failed"
    found = module.published_products(partial, "jma", target)
    check(
        partial["runs"][0]["product"] not in found,
        "a product with a failed target must remain incomplete",
    )

    before_release = dt.datetime(2026, 9, 6, 11, 59, tzinfo=dt.timezone.utc)
    after_release = dt.datetime(2026, 9, 6, 12, 0, tzinfo=dt.timezone.utc)
    missing_product_manifest = complete_manifest(
        module,
        "seas5",
        target,
        products=module.EXPECTED_PRODUCTS["seas5"][:-1],
    )
    before = module.evaluate_worker(
        "seas5",
        target,
        inventories,
        missing_product_manifest,
        now=before_release,
    )
    after = module.evaluate_worker(
        "seas5",
        target,
        inventories,
        missing_product_manifest,
        now=after_release,
    )
    check(before["source_ready"] and not before["needs_dispatch"], "SEAS5 must not dispatch before the CDS release window")
    check(after["needs_dispatch"], "SEAS5 should dispatch once inventory is ready and a live product is missing")

    complete = module.evaluate_worker(
        "seas5",
        target,
        inventories,
        complete_manifest(module, "seas5", target),
        now=after_release,
    )
    check(complete["published"] and not complete["needs_dispatch"], "a complete live suite must suppress duplicate dispatch")

    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "github-output.txt"
        module.write_github_outputs(output, target, {"seas5": complete})
        output_text = output.read_text(encoding="utf-8")
        check(f"target={target}" in output_text, "checker must expose the exact target to Actions")
        check("seas5_source_ready=true" in output_text, "checker must expose source readiness to Actions")
        check("seas5_needs_dispatch=false" in output_text, "checker must suppress complete-suite dispatch in Actions")

    check(module.parse_target("202602") == "202602", "valid target parsing changed")
    for invalid in ("2026-02", "202613", "latest"):
        try:
            module.parse_target(invalid)
        except module.ReleaseCheckError:
            pass
        else:
            raise AssertionError(f"invalid target {invalid!r} should fail closed")

    print("SEASONAL RELEASE CHECK CONTRACT OK: inventory, publication, timing, and idempotence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
