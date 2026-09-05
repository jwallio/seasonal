"""Unpublished matched monthly-mean snowfall preview from frozen cached inputs.

This is a mean-input approximation, not a member-derived hindcast climatology
or an observationally calibrated forecast. Production calculations are untouched.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import cfsv2_seasonal as cf
from build_seasonal_share_images import save_branded_image


MONTHS = ("202701", "202702", "202703")
LABELS = {"202701": "January 2027", "202702": "February 2027", "202703": "March 2027", "JFM": "JFM 2027"}
SEASON_BOUNDS = [-60, -48, -36, -30, -24, -18, -12, -8, -4, -2, -1, 0, 1, 2, 4, 8, 12, 18, 24, 30, 36, 48, 60]
MONTH_BOUNDS = [-24, -20, -16, -12, -10, -8, -6, -4, -2, -1, -.5, 0, .5, 1, 2, 4, 6, 8, 10, 12, 16, 20, 24]


def strict_sum(grids):
    first = grids[0]
    for grid in grids[1:]:
        first.assert_compatible(grid, "preview seasonal sum")
    # An incomplete month is missing, not zero.
    return cf.Grid(first.lons[:], first.lats[:], np.sum([g.values for g in grids], axis=0).tolist())


def matched_month(forecast, reference, month):
    outputs = {}
    for label, inputs in (("total", forecast), ("reference", reference)):
        t2, t850, pr = (inputs[name] for name in ("t2", "t850", "pr"))
        t850 = cf.regrid_nearest(t850, t2.lons, t2.lats, "preview reference T850")
        lwe, _ = cf.derive_snowfall_lwe_grid({"mean": t2}, {"mean": t850}, {"mean": pr}, month)
        depth, _ = cf.derive_snowfall_accumulation_grid(lwe, month)
        outputs[label] = depth
        outputs[label + "_lwe"] = lwe
    outputs["departure"] = cf.subtract_grids(outputs["total"], outputs["reference"])
    outputs["departure_lwe"] = cf.subtract_grids(outputs["total_lwe"], outputs["reference_lwe"])
    return outputs


def render_spec(kind, seasonal):
    spec = dict(cf.get_product_spec(cf.PRODUCT_SNOWFALL_ACCUMULATION))
    title = {"total": "CFSv2 Estimated Snowfall Total (in)",
             "reference": "CFSv2 Approx. Snowfall Reference (in)",
             "departure": "CFSv2 Snowfall Depth Departure (in)",
             "previous": "CFSv2 Previous Snowfall Estimate (in)"}[kind]
    spec.update(title=title, absolute_title=title,
                header_detail="PREVIEW ONLY  •  Snow depth in inches  •  Monthly-mean estimate; not observed normals",
                header_summary="Init 04 Sep 2026 12Z  •  24/24 cycles  •  Reference: approximate model 1982–2010")
    if kind == "previous":
        spec.update(header_detail="PREVIEW RECONSTRUCTION  •  Snow depth in inches  •  Original cycle-level phase estimate",
                    header_summary="Init 04 Sep 2026 12Z  •  24/24 cycles  •  Same retained forecast inputs")
    if kind == "departure":
        bounds = SEASON_BOUNDS if seasonal else MONTH_BOUNDS
        prefix = "seasonal" if seasonal else "monthly"
        palette = cf.anomaly_style(cf.get_product_spec(cf.PRODUCT_SNOWFALL_ANOMALY), True)[3]
        spec.update(anomaly_bounds=bounds, anomaly_tick_format="signed_trimmed", anomaly_tick_decimals=1)
        spec.update({f"{prefix}_anomaly_min": bounds[0], f"{prefix}_anomaly_max": bounds[-1],
                     f"{prefix}_anomaly_ticks": bounds, f"{prefix}_anomaly_palette": palette,
                     f"{prefix}_anomaly_endpoint_labels": {"minimum": f"≤{bounds[0]}", "maximum": f"≥{bounds[-1]}"}})
    return spec


def render(grid, kind, period, output, borders):
    seasonal = period == "JFM"
    source = output / f"{period}-{kind}-source.png"
    cf.render_map(grid, "2026090412", "202701" if seasonal else period,
                  "4–6" if seasonal else {"202701": 4, "202702": 5, "202703": 6}[period],
                  [1], source, kind == "departure", "Approximate model reference", [borders],
                  period_label=LABELS[period], seasonal=seasonal, product_spec=render_spec(kind, seasonal),
                  footer_text=("Original cycle-level method; no reference subtracted. Colors saturate at 200 inches; data remain unchanged."
                               if kind == "previous" else
                               "Total = reference + departure. Same monthly snow-to-liquid ratios. Total/reference colors saturate at 200 inches."))
    save_branded_image(source, output / f"{period}-{kind}.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--borders", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to((Path(__file__).resolve().parents[1] / "public").resolve()):
        raise ValueError("Preview output must be outside published directories")
    if args.output.exists():
        raise ValueError("Use a new output directory; do not overwrite a reviewed preview")
    args.output.mkdir(parents=True)
    provenance = json.loads((args.inputs / "provenance.json").read_text())
    periods, previous = {}, {}
    for month in MONTHS:
        load = lambda group: {name: cf.read_grid_state(args.inputs / f"{month}-{group}-{name}.csv.gz") for name in ("t2", "t850", "pr")}
        periods[month] = matched_month(load("forecast"), load("reference"), month)
        cycle_lwe = cf.read_grid_state(args.inputs / f"{month}-cycle-lwe.csv.gz")
        previous[month], _ = cf.derive_snowfall_accumulation_grid(cycle_lwe, month)
    periods["JFM"] = {key: strict_sum([periods[m][key] for m in MONTHS]) for key in periods[MONTHS[0]]}
    previous["JFM"] = strict_sum([previous[m] for m in MONTHS])
    summary = {"method": "symmetric_monthly_mean_input_approximation", "published": False,
               "units": "inches of estimated accumulated snowfall depth", "source": provenance,
               "periods": {}, "locations": {}}
    locations = {"Pittsburgh": (-80.0, 40.44), "State College": (-77.86, 40.79), "New York City": (-74.0, 40.71),
                 "Syracuse": (-76.15, 43.05), "Chicago": (-87.63, 41.88), "Raleigh": (-78.64, 35.78)}
    for period, grids in periods.items():
        arrays = {key: np.asarray(grid.values) for key, grid in grids.items()}
        residual = np.nanmax(np.abs(arrays["total"] - arrays["reference"] - arrays["departure"]))
        if residual > 1e-10:
            raise AssertionError("Triplet arithmetic mismatch")
        summary["periods"][period] = {"max_identity_residual_inches": float(residual)}
        for kind in ("total", "reference", "departure"):
            cf.write_grid_state(grids[kind], args.output / f"{period}-{kind}.csv.gz")
            render(grids[kind], kind, period, args.output, args.borders)
        summary["locations"][period] = {}
        grid = grids["total"]
        for name, (lon, lat) in locations.items():
            x = int(np.argmin(np.abs(np.asarray(grid.lons) - lon)))
            y = int(np.argmin(np.abs(np.asarray(grid.lats) - lat)))
            summary["locations"][period][name] = {key: round(float(arrays[key][y, x]), 2) for key in ("total", "reference", "departure")}
            summary["locations"][period][name].update(previous_total=round(previous[period].values[y][x], 2),
                                                        longitude=grid.lons[x], latitude=grid.lats[y])
        print(f"Rendered {period}; total-reference-departure residual {residual:.2g} in", flush=True)
    render(previous["JFM"], "previous", "JFM", args.output, args.borders)
    (args.output / "preview.json").write_text(json.dumps(summary, indent=2))
    write_html(args.output, summary)


def write_html(output, summary):
    html = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>wall.cloud · Matched snowfall preview</title><style>
*{box-sizing:border-box}body{margin:0;background:#edf3f5;color:#172735;font:16px system-ui}main{max-width:1500px;margin:auto;padding:18px}h1{font-size:26px;margin:10px 0}p{max-width:1000px;line-height:1.5}.badge{font-size:12px;font-weight:700;letter-spacing:.08em;color:#96510c}.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:18px 0}button,select{font:inherit;padding:10px 14px;border:1px solid #718a98;border-radius:6px;background:white;color:#172735;min-height:44px}button[aria-pressed=true]{background:#173e55;color:white}button:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid #d77900;outline-offset:3px}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.cards.single{grid-template-columns:1fr;max-width:1080px}figure{margin:0;min-width:0}figcaption{font-weight:650;padding:8px 0}img{display:block;width:100%;height:auto;border:1px solid #ccd5db}a{color:#145578}.links{padding:8px 0;font-size:14px}.tablewrap{overflow:auto}table{border-collapse:collapse;font-size:14px}th,td{text-align:right;padding:10px;border-bottom:1px solid #bacad2}th:first-child,td:first-child{text-align:left}details{margin:18px 0}summary{cursor:pointer;font-weight:650}small{color:#536875}figure[hidden]{display:none}.prior{max-width:800px}@media(max-width:800px){main{padding:12px}.cards{grid-template-columns:1fr}h1{font-size:23px}table{table-layout:fixed;width:100%;font-size:12px}th,td{padding:8px 4px}th:first-child,td:first-child{width:27%}}
</style><main><div class="badge">UNPUBLISHED SCIENTIFIC PREVIEW · wall.cloud</div><h1>Snowfall total, reference & departure</h1>
<p><strong>All three maps show inches of estimated accumulated snowfall depth.</strong> Total = model reference + departure. The reference is an approximate calculation from model climate fields, not observed average snowfall.</p>
<div class="controls"><label for="period">Period </label><select id="period"><option value="JFM">January–March 2027</option><option value="202701">January 2027</option><option value="202702">February 2027</option><option value="202703">March 2027</option></select><button data-view="all" aria-pressed="true">All three</button><button data-view="total" aria-pressed="false">Total</button><button data-view="reference" aria-pressed="false">Reference</button><button data-view="departure" aria-pressed="false">Departure</button></div>
<div class="cards" id="cards">'''
    for key, label in (("total", "Estimated total"), ("reference", "Approximate model reference"), ("departure", "Departure from that reference")):
        html += f'<figure data-kind="{key}"><figcaption>{label} · inches snow</figcaption><a class="expand" href="JFM-{key}.png"><img src="JFM-{key}.png" alt="{label}, JFM 2027, inches of estimated snowfall"></a><div class="links"><a class="download" href="JFM-{key}.png" download>Download graphic</a> · <a class="grid" href="JFM-{key}.csv.gz" download>Numerical grid</a></div></figure>'
    html += '''</div><p><small>Total and reference use identical color scales within each period. Departure has a separate signed scale. Color limits affect display only; numerical grids retain the full values.</small></p>
<details open><summary>Compare the numbers</summary><p>Nearest model grid cells, not station forecasts. “Previous” is the original cycle-level snowfall total reconstructed from the same cached inputs. Differences here come from the averaging method, not new weather data.</p><div class="tablewrap"><table><thead><tr><th>Location</th><th>Previous total</th><th>Preview total</th><th>Reference</th><th>Departure</th></tr></thead><tbody id="numbers"></tbody></table></div></details>
<details><summary>What changed—and what remains approximate</summary><p>Both sides now derive snow fraction from monthly mean 2-m/850-hPa temperature and precipitation. Previously the forecast applied snow phase separately to each cycle before averaging, while the reference applied it after averaging. This preview makes that operation consistent by changing the forecast estimate; it does not reconstruct the unavailable historical cycle distribution.</p><p>January and February use winter snow-phase/ratio settings; March uses spring/late-winter settings. Monthly snowfall-depth fields are summed for JFM. The 1982–2010 model reference and the 1971–2000 ratio climatology remain distinct. Monthly mean conditions cannot resolve precipitation-event phase, and these totals remain uncalibrated estimates. No hindcast skill study or observed-snowfall adjustment was performed.</p><p>The sharp, discrete bands are preserved. Expanding a graphic opens the exact same selected image.</p></details>
<details><summary>Previous JFM accumulation for comparison</summary><p>Same forecast inputs; original cycle-level method.</p><a href="JFM-previous.png"><img class="prior" src="JFM-previous.png" alt="Previous cycle-level JFM estimated snowfall accumulation" loading="lazy"></a></details>
<p><a href="preview.json">Provenance and validation</a></p></main><script>const data=DATA;let view='all';const period=document.querySelector('#period');function update(){const p=period.value;document.querySelector('#cards').classList.toggle('single',view!=='all');document.querySelectorAll('figure[data-kind]').forEach(f=>{const k=f.dataset.kind;f.hidden=view!=='all'&&view!==k;const url=p+'-'+k+'.png';f.querySelector('img').src=url;f.querySelector('img').alt=k+', '+period.selectedOptions[0].text+', inches estimated snowfall';f.querySelector('.expand').href=url;f.querySelector('.download').href=url;f.querySelector('.grid').href=p+'-'+k+'.csv.gz'});document.querySelector('#numbers').replaceChildren(...Object.entries(data.locations[p]).map(([name,v])=>{const row=document.createElement('tr');[name,v.previous_total,v.total,v.reference,v.departure].forEach(value=>{const cell=document.createElement('td');cell.textContent=typeof value==='number'?value.toFixed(1):value;row.append(cell)});return row}));}period.addEventListener('change',update);document.querySelectorAll('button[data-view]').forEach(b=>b.addEventListener('click',()=>{view=b.dataset.view;document.querySelectorAll('button[data-view]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));update()}));update();</script></html>'''
    (output / "index.html").write_text(html.replace("DATA", json.dumps({"locations": summary["locations"]})), encoding="utf-8")


if __name__ == "__main__":
    main()

