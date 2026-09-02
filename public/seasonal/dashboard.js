const siteMatch = location.pathname.match(/^(.*\/seasonal)(?:\/|$)/);
const root = siteMatch ? siteMatch[1] : '';
const normalizeAssetPath = value => String(value || '').replace(/^\/+/, '').replace(/^public\//, '').replace(/^seasonal\//, '');
const assetPath = value => `${root}/${normalizeAssetPath(value)}`;
const CATALOG_URL = assetPath('seasonal/catalog.json');
const ANALOG_MANIFEST_URL = assetPath('seasonal/analog_z500_manifest.json');
const ANALOG_PRODUCTS_MANIFEST_URL = assetPath('seasonal/analog_products_manifest.json');
const ANALOG_PRODUCT_ORDER = ['psl_500mb_height_anomaly', 'psl_2m_temperature_anomaly', 'mrcc_snowfall_departure'];
function shareImagePath(value) {
  const relative = normalizeAssetPath(value);
  return assetPath(relative.startsWith('share/') ? relative : `share/${relative}`);
}
function thumbnailPath(value) {
  const relative = normalizeAssetPath(value);
  const webp = /\.[^/.]+$/.test(relative) ? relative.replace(/\.[^/.]+$/, '.webp') : `${relative}.webp`;
  const version = seasonalCatalog?.generated_utc || seasonalCatalog?.source_revision || '';
  return `${assetPath(`thumbnails/${webp}`)}${version ? `?v=${encodeURIComponent(version)}` : ''}`;
}
function setImageFallbacks(image, sources, onFailure) {
  const queue = [...new Set(sources.filter(Boolean))];
  const loadNext = () => {
    const next = queue.shift();
    if (next) image.src = next;
    else if (onFailure) onFailure();
  };
  image.onerror = loadNext;
  loadNext();
}
const el = id => document.getElementById(id);
const MODEL_CONFIG = {
  superensemble: { label: 'Super Ensemble', role: 'blend', kind: 'seasonal', manifest: assetPath('seasonal/superensemble_manifest.json'), direct: assetPath('seasonal/superensemble/'), source: 'Deduplicated seasonal forecast families, including target-aligned CMA CPSv3' },
  cfsv2: { label: 'CFSv2', role: 'family', kind: 'seasonal', manifest: assetPath('seasonal/cfsv2_manifest.json'), direct: assetPath('seasonal/cfsv2/'), source: 'NOAA CFSv2 NOMADS' },
  seas5: { label: 'ECMWF SEAS5', role: 'family', kind: 'seasonal', manifest: assetPath('seasonal/seas5_manifest.json'), direct: assetPath('seasonal/seas5/'), source: 'ECMWF SEAS5 / Copernicus CDS' },
  cansips: { label: 'CanSIPS v3', role: 'family', kind: 'seasonal', manifest: assetPath('seasonal/cansips_manifest.json'), direct: assetPath('seasonal/cansips/'), source: 'ECCC MSC Datamart' },
  cma_cpsv3: { label: 'CMA CPSv3', role: 'family', kind: 'seasonal', manifest: assetPath('seasonal/cma_cpsv3_manifest.json'), direct: assetPath('seasonal/cma_cpsv3/'), source: 'WMO LC-SPMME / GPC Beijing' },
  c3s: { label: 'C3S multi-system', role: 'blend', preferredComponent: 'multisystem', kind: 'seasonal', manifest: assetPath('seasonal/c3s_manifest.json'), direct: assetPath('seasonal/c3s/'), source: 'Copernicus C3S seasonal forecasts' },
  jma: { label: 'JMA', role: 'component', kind: 'seasonal', manifest: assetPath('seasonal/jma_manifest.json'), direct: assetPath('seasonal/jma/'), source: 'JMA/MRI-CPS4 via Copernicus C3S' },
  apcc: { label: 'APCC MME', role: 'blend', kind: 'seasonal', manifest: assetPath('seasonal/apcc_manifest.json'), direct: assetPath('seasonal/apcc/'), source: 'APCC multi-model ensemble via CLIK' },
  geos_s2s3: { label: 'NASA GEOS-S2S-3', role: 'family', kind: 'seasonal', manifest: assetPath('seasonal/geos_s2s3_manifest.json'), direct: assetPath('seasonal/geos_s2s3/'), source: 'NASA GEOS-S2S-3 NCCS numerical forecasts' },
  nmme: { label: 'NOAA NMME', role: 'blend', preferredComponent: 'ENSMEAN', kind: 'seasonal', manifest: assetPath('seasonal/nmme_manifest.json'), direct: assetPath('seasonal/nmme/'), source: 'NOAA CPC NMME' },
};
// Kept as a client fallback while an older published catalog is being
// replaced. The catalog builder is the source of truth for new releases.
const MODEL_SCHEDULE_FALLBACKS = {
  superensemble: { cadenceGroup: 'monthly', cadenceLabel: 'Monthly · derived', officialSchedule: 'Derived after the monthly component releases; wall.cloud targets the 22nd.', officialUrl: 'https://www.wmolc.org/seasonalDownload/direct', expectedCycle: { kind: 'monthly_day', runDay: 1, runTimeUtc: '00:00', publishDay: 22, publishTimeUtc: '20:30', publishLagMinutes: 45, lateAfterMinutes: 180 } },
  c3s: { cadenceGroup: 'monthly', cadenceLabel: 'Monthly · release window', officialSchedule: 'C3S seasonal data are released monthly; this multi-system suite uses the 10th-day window.', officialUrl: 'https://climate.copernicus.eu/seasonal-forecasts', expectedCycle: { kind: 'monthly_day', runDay: 1, runTimeUtc: '00:00', publishDay: 10, publishTimeUtc: '12:00', publishLagMinutes: 90, lateAfterMinutes: 360 } },
  apcc: { cadenceGroup: 'monthly', cadenceLabel: 'Monthly · mid-month', officialSchedule: 'APCC seasonal forecasts are issued around the 15th; wall.cloud targets the post-collection window on the 20th.', officialUrl: 'https://www.apcc21.org/prediction/global/outlook?lang=eng', expectedCycle: { kind: 'monthly_day', runDay: 15, runTimeUtc: '00:00', publishDay: 20, publishTimeUtc: '16:30', publishLagMinutes: 60, lateAfterMinutes: 360 } },
  nmme: { cadenceGroup: 'monthly', cadenceLabel: 'Monthly · CPC', officialSchedule: 'NMME inputs are delivered by 17:00 ET on the 8th; CPC publishes the graphics and data on the 9th.', officialUrl: 'https://www.cpc.ncep.noaa.gov/products/NMME/users_guide.html', expectedCycle: { kind: 'monthly_day', runDay: 8, runTimeUtc: '00:00', publishDay: 9, publishTimeUtc: '15:30', publishLagMinutes: 60, lateAfterMinutes: 360 } },
  cfsv2: { cadenceGroup: 'frequent', cadenceLabel: 'Twice daily', officialSchedule: 'CFSv2 runs four times daily at 00, 06, 12, and 18 UTC; wall.cloud harvests the 06Z and 18Z cycles.', officialUrl: 'https://www.ncei.noaa.gov/products/weather-climate-models/climate-forecast-system', expectedCycle: { kind: 'daily_times', runTimesUtc: ['06:00', '18:00'], publishTimesUtc: ['10:35', '22:35'], publishLagMinutes: 45, lateAfterMinutes: 90 } },
  seas5: { cadenceGroup: 'monthly', cadenceLabel: 'Monthly · release window', officialSchedule: 'SEAS5 is disseminated on the 5th at 12 UTC; the CDS-backed suite is checked from the 6th.', officialUrl: 'https://www.ecmwf.int/en/forecasts/datasets/set-v', expectedCycle: { kind: 'monthly_day', runDay: 1, runTimeUtc: '00:00', publishDay: 6, publishTimeUtc: '12:00', publishLagMinutes: 90, lateAfterMinutes: 360 } },
  cansips: { cadenceGroup: 'monthly', cadenceLabel: 'Monthly · ECCC', officialSchedule: 'ECCC global seasonal forecasts are produced on the first day at 00 UTC; wall.cloud publishes after the Datamart window.', officialUrl: 'https://weather.gc.ca/saisons/GPC_Montreal_e.html', expectedCycle: { kind: 'monthly_day', runDay: 1, runTimeUtc: '00:00', publishDay: 2, publishTimeUtc: '16:30', publishLagMinutes: 60, lateAfterMinutes: 360 } },
  cma_cpsv3: { cadenceGroup: 'monthly', cadenceLabel: 'Monthly · WMO window', officialSchedule: 'CMA CPSv3 is a monthly seasonal system; wall.cloud targets the 21st after the WMO GPC Beijing exchange window.', officialUrl: 'https://www.wmolc.org/contents2/index/Beijing', expectedCycle: { kind: 'monthly_day', runDay: 1, runTimeUtc: '00:00', publishDay: 21, publishTimeUtc: '18:30', publishLagMinutes: 60, lateAfterMinutes: 360 } },
  geos_s2s3: { cadenceGroup: 'monthly', cadenceLabel: 'Monthly · NASA', officialSchedule: 'NASA produces GEOS seasonal forecasts monthly; wall.cloud checks the public archive during the first week.', officialUrl: 'https://gmao.gsfc.nasa.gov/seasonal-decadal-analysis_prediction/', expectedCycle: { kind: 'monthly_day', runDay: 1, runTimeUtc: '00:00', publishDay: 6, publishTimeUtc: '16:30', publishLagMinutes: 60, lateAfterMinutes: 360 } },
  jma: { cadenceGroup: 'monthly', cadenceLabel: 'Monthly · JMA/C3S', officialSchedule: 'JMA seasonal guidance is monthly; the C3S component is checked in the 10th-day release window.', officialUrl: 'https://www.data.jma.go.jp/wmc/products/model/', expectedCycle: { kind: 'monthly_day', runDay: 1, runTimeUtc: '00:00', publishDay: 10, publishTimeUtc: '12:00', publishLagMinutes: 90, lateAfterMinutes: 360 } },
};
const MODEL_ROLE_LABELS = { blend: 'Blend', family: 'Forecast family', component: 'Component model' };
const MODEL_ROLE_GROUPS = [
  { role: 'blend', label: 'Multi-model blends' },
  { role: 'family', label: 'Forecast families' },
  { role: 'component', label: 'Component models' },
];
const COMPONENT_LABELS = {
  ENSMEAN: 'NMME Ensemble Mean', PROBABILITY: 'NMME Official Probability', CONSENSUS: 'NMME Multi-Model Consensus',
  CanESM5: 'ECCC CanESM5', CFSv2: 'NCEP CFSv2', 'GEM5.2_NEMO': 'ECCC GEM5.2-NEMO', NASA_GEOS5v2: 'NASA GEOS5v2',
  NCAR_CCSM4: 'NCAR CCSM4', NCAR_CESM1: 'NCAR CESM1', multisystem: 'C3S multi-system',
};
let seasonalCatalog = null;
let seasonalAnalogs = null;
let seasonalAnalogProducts = null;
let analogManifestError = '';
let analogProductsManifestError = '';
const modelStates = Object.fromEntries(Object.keys(MODEL_CONFIG).map(key => [key, { manifest: null, catalog: null, runs: [], error: null }]));
const selection = { view: 'overview', model: 'cfsv2', product: '', run: '', target: '', compareProduct: '500mb_height_anomaly', compareTarget: '', compareBaseline: '', compareRole: 'all', compareAvailableOnly: true, ratio: '10' };
const DEFAULT_PRODUCT_PRIORITY = [
  '500mb_height_anomaly',
  '2m_temperature_anomaly',
  'surface_temperature_anomaly',
  'temperature_anomaly',
];
const DEFAULT_PERIOD_PRIORITY = ['djf', 'december'];
function defaultTargetPeriod(target) {
  const targetMonth = String(target?.target_month || '');
  if (/^\d{6}-\d{6}$/.test(targetMonth) && targetMonth.slice(4, 6) === '12' && targetMonth.slice(-2) === '02') return 'djf';
  if (/^\d{6}$/.test(targetMonth) && targetMonth.slice(4, 6) === '12') return 'december';
  return '';
}
function defaultTargetKey(target, index) { return String(target?.id || index); }
function defaultTargetForPeriod(run, period) {
  return (Array.isArray(run?.targets) ? run.targets : []).find((target) => {
    const status = String(target?.status || '').toLowerCase();
    return !['failed', 'error'].includes(status) && Boolean(target?.image) && defaultTargetPeriod(target) === period;
  }) || null;
}
function orderedDefaultRuns(runs, modelKey, productKey) {
  const remaining = [...runs];
  const ordered = [];
  while (remaining.length) {
    const candidate = preferredRun(remaining, modelKey, productKey);
    if (!candidate) break;
    ordered.push(candidate);
    const index = remaining.indexOf(candidate);
    if (index < 0) break;
    remaining.splice(index, 1);
  }
  return ordered;
}
function defaultSelectionForModel(model, products) {
  if (model.kind !== 'seasonal') return null;
  const runs = modelStates[selection.model].runs || [];
  for (const product of DEFAULT_PRODUCT_PRIORITY) {
    if (!products.includes(product)) continue;
    const candidates = runs.filter(run => supportsProduct(model, run, product) && !isFailedRun(run));
    for (const period of DEFAULT_PERIOD_PRIORITY) {
      for (const run of orderedDefaultRuns(candidates, selection.model, product)) {
        const target = defaultTargetForPeriod(run, period);
        if (target) {
          const targetIndex = (Array.isArray(run.targets) ? run.targets : []).indexOf(target);
          return { product, run: String(run.id), target: defaultTargetKey(target, targetIndex), period };
        }
      }
    }
  }
  return null;
}
function genericSelectionForModel(model, products) {
  const product = DEFAULT_PRODUCT_PRIORITY.find(value => products.includes(value)) || products[0] || '';
  const runs = modelStates[selection.model].runs.filter(run => supportsProduct(model, run, product));
  const run = preferredRun(runs, selection.model, product);
  const targets = targetItems(model, run);
  return { product, run: String(run?.id || ''), target: String(targets[0]?.key || '') };
}
const DEFAULT_COMPARE_PRODUCT = '500mb_height_anomaly';
const COMPARE_MIN_VALID_MONTH = 202612;
const COMPARE_MODELS = ['superensemble', 'c3s', 'apcc', 'nmme', 'cfsv2', 'seas5', 'cansips', 'cma_cpsv3', 'geos_s2s3', 'jma'];
const COMPARE_PRODUCTS = [
  { value: '500mb_height_anomaly', label: '500-mb Height Anomaly', aliases: ['500mb_height_anomaly'] },
  { value: '850mb_temperature_anomaly', label: '850-mb Temperature Anomaly', aliases: ['850mb_temperature_anomaly'] },
  { value: '2m_temperature_anomaly', label: '2-m Temperature Anomaly', aliases: ['2m_temperature_anomaly', 'surface_temperature_anomaly', 'temperature_anomaly'] },
  { value: 'precipitation_anomaly', label: 'Precipitation Anomaly', aliases: ['precipitation_anomaly'] },
  { value: 'snowfall_anomaly', label: 'CONUS Snowfall Water-Equivalent Departure', aliases: ['snowfall_anomaly'] },
  { value: 'mslp_anomaly', label: 'MSLP Anomaly', aliases: ['mslp_anomaly'] },
];
const COMPARE_BASELINES = [
  { value: 'native', label: 'Native model reference' },
  { value: 'common_1991_2020', label: 'Common 1991–2020 (limited)' },
];
const PRODUCT_LABELS = {
  '500mb_height_anomaly': '500-mb Height Anomaly', '500mb_height_absolute': '500-mb Geopotential Height',
  '500mb_height_anomaly_nh': '500-mb Height Anomaly · Northern Hemisphere',
  '2m_temperature_anomaly': '2-m Temperature Anomaly', '850mb_temperature_anomaly': '850-mb Temperature Anomaly',
  'precipitation_anomaly': 'CONUS Precipitation Anomaly', 'snow_water_equivalent_anomaly': 'Snow-Water-Equivalent Anomaly',
  'snow_depth_anomaly': 'CONUS Snow-Depth Anomaly', 'snowfall_anomaly': 'CONUS Snowfall Water-Equivalent Departure',
  'mslp_anomaly': 'CONUS MSLP Anomaly', '200mb_height_anomaly': '200-mb Height Anomaly',
  'probability_above_normal': 'Above Normal Probability', 'probability_near_normal': 'Near Normal Probability', 'probability_below_normal': 'Below Normal Probability',
  'multi_model_consensus': 'Multi-Model Consensus',
};
function readUrlState() {
  const params = new URLSearchParams(location.search);
  const view = params.get('view');
  if (['overview', 'single', 'compare'].includes(view)) selection.view = view;
  const model = params.get('model');
  if (MODEL_CONFIG[model]) selection.model = model;
  if (params.has('product')) selection.product = params.get('product') || '';
  if (params.has('run')) selection.run = params.get('run') || '';
  if (params.has('target')) selection.target = params.get('target') || '';
  if (params.has('ratio')) selection.ratio = params.get('ratio') || '10';
  if (params.has('compare')) selection.compareProduct = params.get('compare') || DEFAULT_COMPARE_PRODUCT;
  if (params.has('period')) selection.compareTarget = params.get('period') || '';
  if (params.has('reference')) selection.compareBaseline = params.get('reference') || '';
  const role = params.get('role');
  if (['all', 'blend', 'family', 'component'].includes(role)) selection.compareRole = role;
  if (params.has('available')) selection.compareAvailableOnly = params.get('available') !== '0';
}
function syncUrlState() {
  const params = new URLSearchParams();
  params.set('view', selection.view);
  if (selection.view === 'single') {
    params.set('model', selection.model);
    if (selection.product) params.set('product', selection.product);
    if (selection.run) params.set('run', selection.run);
    if (selection.target) params.set('target', selection.target);
    if (selection.ratio !== '10') params.set('ratio', selection.ratio);
  } else if (selection.view === 'compare') {
    if (selection.compareProduct) params.set('compare', selection.compareProduct);
    if (selection.compareTarget) params.set('period', selection.compareTarget);
    if (selection.compareBaseline) params.set('reference', selection.compareBaseline);
    if (selection.compareRole !== 'all') params.set('role', selection.compareRole);
    params.set('available', selection.compareAvailableOnly ? '1' : '0');
  }
  const query = params.toString();
  history.replaceState(null, '', `${location.pathname}${query ? `?${query}` : ''}${location.hash}`);
}
readUrlState();
const numberList = (values, min = null, max = null) => [...new Set((Array.isArray(values) ? values : []).map(value => Number(value)).filter(value => Number.isFinite(value) && (min === null || value >= min) && (max === null || value <= max)))].sort((a,b) => a - b);
const pretty = value => String(value || '').replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase());
function componentLabel(run) {
  const component = String(run?.component || '');
  const explicit = String(run?.component_label || '').trim();
  if (explicit && !(component === 'multisystem' && explicit.toLowerCase() === 'multi-system')) return explicit;
  return COMPONENT_LABELS[component] || explicit || (component ? pretty(component) : '');
}
function runDisplayName(model, run) {
  if (!run) return model.label;
  if (run.component) return componentLabel(run) || String(run.model || model.label);
  return String(run.model || run.component_label || model.label);
}
function preferredComponent(modelKey, productKey) {
  if (modelKey === 'nmme') {
    if (String(productKey || '').startsWith('probability_')) return 'PROBABILITY';
    if (productKey === 'multi_model_consensus') return 'CONSENSUS';
    return 'ENSMEAN';
  }
  return MODEL_CONFIG[modelKey]?.preferredComponent || '';
}
function runCoverageCounts(run, target = null) {
  const targetValue = target?.value || target;
  const sources = [targetValue, ...(Array.isArray(run?.targets) ? run.targets : []), run].filter(Boolean);
  const availableKeys = ['ensemble_members', 'available_members', 'available_cycles', 'successful_exports'];
  const expectedKeys = ['ensemble_expected_members', 'expected_members', 'expected_cycles', 'expected_exports'];
  for (const source of sources) {
    const available = availableKeys.map(key => Number(source[key])).find(Number.isFinite);
    const expected = expectedKeys.map(key => Number(source[key])).find(value => Number.isFinite(value) && value > 0);
    if (Number.isFinite(available) && Number.isFinite(expected)) return { available, expected };
  }
  return null;
}
function runCoverageRatio(run) {
  const counts = runCoverageCounts(run);
  return counts ? counts.available / counts.expected : null;
}
function defaultEligibleRun(run) {
  if (isFailedRun(run)) return false;
  const coverage = runCoverageRatio(run);
  return String(run?.status || '').toLowerCase() !== 'partial' || coverage === null || coverage >= 0.8;
}
const initLabel = value => { if (!value) return '—'; const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat(undefined, {day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit', hour12:false, timeZone:'UTC'}).format(date).replace(',', '') + 'Z'; };
function scheduleFor(modelKey) {
  const fallback = MODEL_SCHEDULE_FALLBACKS[modelKey] || {};
  const published = modelStates[modelKey]?.catalog?.schedule;
  if (!published) return fallback;
  const cycle = published.expected_cycle || {};
  return {
    cadenceGroup: published.cadence_group || fallback.cadenceGroup,
    cadenceLabel: published.cadence_label || fallback.cadenceLabel,
    officialSchedule: published.official_schedule || fallback.officialSchedule,
    officialUrl: published.official_url || fallback.officialUrl,
    expectedCycle: {
      ...(fallback.expectedCycle || {}),
      kind: cycle.kind || fallback.expectedCycle?.kind,
      runDay: cycle.run_day ?? fallback.expectedCycle?.runDay,
      runTimeUtc: cycle.run_time_utc || fallback.expectedCycle?.runTimeUtc,
      runTimesUtc: cycle.run_times_utc || fallback.expectedCycle?.runTimesUtc,
      publishDay: cycle.publish_day ?? fallback.expectedCycle?.publishDay,
      publishTimeUtc: cycle.publish_time_utc || fallback.expectedCycle?.publishTimeUtc,
      publishTimesUtc: cycle.publish_times_utc || fallback.expectedCycle?.publishTimesUtc,
      publishLagMinutes: Number(cycle.publish_lag_minutes ?? fallback.expectedCycle?.publishLagMinutes ?? 0),
      lateAfterMinutes: Number(cycle.late_after_minutes ?? fallback.expectedCycle?.lateAfterMinutes ?? 0),
    },
  };
}
function utcClock(value) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(value || ''));
  return match ? [Number(match[1]), Number(match[2])] : [0, 0];
}
function utcAt(day, clock) {
  const [hour, minute] = utcClock(clock);
  return new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), hour, minute));
}
function addMinutes(date, minutes) { return new Date(date.valueOf() + Number(minutes || 0) * 60000); }
function scheduledCycles(modelKey, anchor = new Date()) {
  const cycle = scheduleFor(modelKey).expectedCycle || {};
  const candidates = [];
  if (cycle.kind === 'daily_times') {
    const publishTimes = cycle.publishTimesUtc || [];
    const runTimes = cycle.runTimesUtc || [];
    for (let offset = -2; offset <= 2; offset += 1) {
      const day = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth(), anchor.getUTCDate() + offset));
      publishTimes.forEach((clock, index) => candidates.push({
        publish: addMinutes(utcAt(day, clock), cycle.publishLagMinutes),
        run: utcAt(day, runTimes[index] || clock),
      }));
    }
  } else if (cycle.kind === 'monthly_day') {
    for (let offset = -2; offset <= 2; offset += 1) {
      const month = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() + offset, 1));
      const publishDay = Number(cycle.publishDay || 1);
      const runDay = Number(cycle.runDay || 1);
      const publishBase = new Date(Date.UTC(month.getUTCFullYear(), month.getUTCMonth(), publishDay, ...utcClock(cycle.publishTimeUtc)));
      const run = new Date(Date.UTC(month.getUTCFullYear(), month.getUTCMonth(), runDay, ...utcClock(cycle.runTimeUtc)));
      candidates.push({ publish: addMinutes(publishBase, cycle.publishLagMinutes), run });
    }
  }
  return candidates.sort((left, right) => left.publish - right.publish);
}
function scheduleCycle(modelKey, direction, now = new Date()) {
  const cycles = scheduledCycles(modelKey, now);
  const matches = direction === 'previous'
    ? cycles.filter(item => item.publish.valueOf() <= now.valueOf())
    : cycles.filter(item => item.publish.valueOf() > now.valueOf());
  return direction === 'previous' ? matches.at(-1) || null : matches[0] || null;
}
function formatInZone(value, timeZone) {
  const date = new Date(value || '');
  if (Number.isNaN(date.valueOf())) return '—';
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    hour12: false, timeZone, timeZoneName: 'short',
  }).formatToParts(date).map(part => [part.type, part.value]));
  return `${parts.month} ${parts.day}, ${parts.year} · ${parts.hour}:${parts.minute} ${parts.timeZoneName}`;
}
function formatEdt(value) { return formatInZone(value, 'America/New_York'); }
function compactUtc(value) { return formatInZone(value, 'UTC'); }
function compactRunUtc(value) {
  const date = new Date(value || '');
  if (Number.isNaN(date.valueOf())) return '—';
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC',
  }).formatToParts(date).map(part => [part.type, part.value]));
  return `${parts.month} ${parts.day} · ${parts.hour}:${parts.minute}Z`;
}
function compactEdt(value) {
  const date = new Date(value || '');
  if (Number.isNaN(date.valueOf())) return '—';
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
    timeZone: 'America/New_York', timeZoneName: 'short',
  }).formatToParts(date).map(part => [part.type, part.value]));
  return `${parts.month} ${parts.day} · ${parts.hour}:${parts.minute} ${parts.timeZoneName}`;
}
function ageLabel(value, now = new Date()) {
  const date = new Date(value || '');
  if (Number.isNaN(date.valueOf())) return 'Age unavailable';
  const minutes = Math.max(0, Math.floor((now.valueOf() - date.valueOf()) / 60000));
  if (minutes < 60) return `${minutes}m old`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h old`;
  return `${Math.floor(hours / 24)}d old`;
}
function durationLabel(milliseconds) {
  const minutes = Math.max(1, Math.floor(Math.abs(milliseconds) / 60000));
  if (minutes >= 1440) return `${Math.floor(minutes / 1440)}d`;
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h`;
  return `${minutes}m`;
}
function hasUsableTarget(run) {
  return (Array.isArray(run?.targets) ? run.targets : []).some(target =>
    !['failed', 'error'].includes(String(target?.status || '').toLowerCase()) && Boolean(target?.image));
}
function latestUsableModelRun(modelKey) {
  return [...(modelStates[modelKey]?.runs || [])]
    .filter(run => !isFailedRun(run) && hasUsableTarget(run))
    .sort((left, right) => String(right.init_utc || '').localeCompare(String(left.init_utc || '')) || String(right.id || '').localeCompare(String(left.id || '')))[0] || null;
}
function availabilityScheduleState(modelKey, lastRun, now = new Date()) {
  const schedule = scheduleFor(modelKey);
  const previous = scheduleCycle(modelKey, 'previous', now);
  const next = scheduleCycle(modelKey, 'next', now);
  if (!previous || !next) return { key: 'unknown', label: 'Timing unavailable', className: 'schedule-unknown', previous, next, title: 'No expected release rule is configured for this model.' };
  const lastInit = new Date(lastRun?.init_utc || '');
  const cycleMissing = Number.isNaN(lastInit.valueOf()) || lastInit.valueOf() < previous.run.valueOf();
  const lateAfter = Number(schedule.expectedCycle?.lateAfterMinutes || 0) * 60000;
  const overdue = cycleMissing && now.valueOf() >= previous.publish.valueOf() + lateAfter;
  const dueSoon = next.publish.valueOf() - now.valueOf() <= 36 * 3600000;
  if (overdue) return {
    key: 'overdue', label: 'Overdue', className: 'schedule-overdue', previous, next,
    title: `Expected availability was ${formatEdt(previous.publish)}; the latest usable initialization is ${lastRun ? compactUtc(lastRun.init_utc) : 'unavailable'}.`,
  };
  if (cycleMissing) return {
    key: 'processing', label: 'Due / processing', className: 'schedule-processing', previous, next,
    title: `The expected release window is open; the latest usable initialization is ${lastRun ? compactUtc(lastRun.init_utc) : 'unavailable'}.`,
  };
  if (dueSoon) return {
    key: 'due', label: 'Due soon', className: 'schedule-due', previous, next,
    title: `Next expected availability is ${formatEdt(next.publish)}.`,
  };
  return {
    key: 'on_time', label: 'On schedule', className: 'schedule-on-time', previous, next,
    title: `Next expected availability is ${formatEdt(next.publish)}.`,
  };
}
const monthLabel = value => { if (!value) return '—'; const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat(undefined, {month:'short', year:'numeric', timeZone:'UTC'}).format(date); };
const monthCodeLabel = code => /^\d{6}$/.test(String(code || '')) ? monthLabel(`${String(code).slice(0,4)}-${String(code).slice(4,6)}-01T00:00:00Z`) : String(code || '—');
function periodLabel(value) {
  const text = String(value || '');
  const match = /^(\d{4})(\d{2})-(\d{4})(\d{2})$/.exec(text);
  if (!match) return monthCodeLabel(text);
  const months = [Number(match[2]), Number(match[4])];
  const season = months[0] === 12 && months[1] === 2 ? `DJF ${match[1]}–${match[3].slice(2)}` : months[0] === 3 && months[1] === 5 ? `MAM ${match[3]}` : months[0] === 6 && months[1] === 8 ? `JJA ${match[3]}` : months[0] === 9 && months[1] === 11 ? `SON ${match[3]}` : `${monthCodeLabel(match[1] + match[2])}–${monthCodeLabel(match[3] + match[4])}`;
  return season;
}
function populate(select, values, chosen) {
  select.replaceChildren();
  values.forEach(item => { const option = document.createElement('option'); option.value = String(item.value); option.textContent = item.label; select.appendChild(option); });
  select.disabled = values.length === 0;
  if (values.some(item => String(item.value) === String(chosen))) select.value = String(chosen);
}
function catalogProductConfig(productKey) {
  const products = seasonalCatalog?.products || {};
  if (products[productKey]) return { value: productKey, ...products[productKey] };
  const match = Object.entries(products).find(([, product]) => (product.aliases || []).includes(productKey));
  return match ? { value: match[0], ...match[1] } : null;
}
function canonicalProductKey(productKey) {
  return catalogProductConfig(productKey)?.value || productKey;
}
function compareProductConfig(productKey) {
  const canonical = canonicalProductKey(productKey);
  const fallback = COMPARE_PRODUCTS.find(item => item.value === canonical) || COMPARE_PRODUCTS[0];
  const catalog = catalogProductConfig(canonical);
  if (!catalog) return fallback;
  return {
    ...fallback,
    ...catalog,
    value: canonical,
    aliases: [...new Set([canonical, ...(fallback?.aliases || []), ...(catalog.aliases || [])])],
  };
}
function compareProductAliases(productKey) {
  return new Set(compareProductConfig(productKey)?.aliases || [productKey]);
}
function compareProductLabel(productKey) {
  return compareProductConfig(productKey)?.label || PRODUCT_LABELS[productKey] || pretty(productKey);
}
function compareRuns(modelKey, productKey = selection.compareProduct || DEFAULT_COMPARE_PRODUCT) {
  const aliases = compareProductAliases(productKey);
  const preferred = preferredComponent(modelKey, productKey);
  return (modelStates[modelKey]?.runs || [])
    .filter(run => aliases.has(String(run.product || '')) && !isFailedRun(run) && run?._catalog?.comparable !== false)
    .sort((left, right) => {
      const leftPreferred = preferred && String(left.component || '') === preferred ? 1 : 0;
      const rightPreferred = preferred && String(right.component || '') === preferred ? 1 : 0;
      const leftEligible = defaultEligibleRun(left) ? 1 : 0;
      const rightEligible = defaultEligibleRun(right) ? 1 : 0;
      return rightEligible - leftEligible || rightPreferred - leftPreferred || String(right.init_utc || '').localeCompare(String(left.init_utc || ''));
    });
}
function compareTargetAsset(target, baseline) {
  if (!target || target.status === 'failed') return null;
  if (baseline === 'native') return target.image ? { image: target.image, baseline: target.baseline || null } : null;
  const comparison = target.comparison?.[baseline];
  return comparison?.image ? comparison : null;
}
function compareTargetMeetsValidCutoff(target) {
  const match = /^(\d{6})(?:-\d{6})?$/.exec(String(target?.target_month || ''));
  return Boolean(match) && Number(match[1]) >= COMPARE_MIN_VALID_MONTH;
}
function compareTarget(run, targetKey, baseline = 'native') {
  return (Array.isArray(run?.targets) ? run.targets : []).find(target => compareTargetMeetsValidCutoff(target) && String(target.target_month || '') === String(targetKey || '') && compareTargetAsset(target, baseline)) || null;
}
function compareTargetKeys(modelKey, productKey = selection.compareProduct || DEFAULT_COMPARE_PRODUCT) {
  const keys = new Set();
  compareRuns(modelKey, productKey).forEach(run => (Array.isArray(run.targets) ? run.targets : []).forEach(target => {
    if (compareTargetMeetsValidCutoff(target) && compareTargetAsset(target, 'native')) keys.add(String(target.target_month));
  }));
  return keys;
}
function comparePeriodSort(left, right) {
  const startMonth = value => Number(String(value || '').slice(0, 6)) || Number.MAX_SAFE_INTEGER;
  const startDifference = startMonth(left) - startMonth(right);
  if (startDifference) return startDifference;
  const leftIsRange = String(left).includes('-') ? 1 : 0;
  const rightIsRange = String(right).includes('-') ? 1 : 0;
  return rightIsRange - leftIsRange || String(left).localeCompare(String(right));
}
function compareProductOptions() {
  return COMPARE_PRODUCTS
    .filter(product => COMPARE_MODELS.some(modelKey => compareTargetKeys(modelKey, product.value).size))
    .map(product => ({ value: product.value, label: compareProductLabel(product.value) }));
}
function comparePeriodOptions(productKey = selection.compareProduct || DEFAULT_COMPARE_PRODUCT) {
  const keys = new Set();
  COMPARE_MODELS.forEach(modelKey => compareTargetKeys(modelKey, productKey).forEach(key => keys.add(key)));
  return [...keys].sort(comparePeriodSort).map(value => ({ value, label: periodLabel(value) }));
}
function compareBaselineOptions(productKey = selection.compareProduct || DEFAULT_COMPARE_PRODUCT) {
  return productKey === DEFAULT_COMPARE_PRODUCT ? COMPARE_BASELINES : COMPARE_BASELINES.filter(item => item.value === 'native');
}
function compareRunForTarget(modelKey, targetKey, baseline = 'native', productKey = selection.compareProduct || DEFAULT_COMPARE_PRODUCT) {
  return compareRuns(modelKey, productKey).find(run => compareTarget(run, targetKey, baseline)) || null;
}
function manifestProducts(model) {
  const keys = new Set();
  modelStates[selection.model].runs.forEach(run => {
    if (model.kind === 'weathernext') {
      (Array.isArray(run.products) ? run.products : Object.keys(run.product_hours || {})).forEach(key => keys.add(String(key)));
    } else if (run.product) keys.add(String(run.product));
  });
  const retired = new Set(['sea_surface_temperature_anomaly', 'sst_anomaly']);
  if (selection.model === 'nmme') retired.add('model_spread');
  return [...keys].filter(key => !retired.has(key));
}
function productLabel(model, key) {
  const configured = modelStates[selection.model].manifest?.product_labels?.[key];
  return configured || catalogProductConfig(key)?.label || PRODUCT_LABELS[key] || pretty(key) || 'Seasonal guidance';
}

function productSupport(modelKey, productKey) {
  const canonical = canonicalProductKey(productKey);
  return modelStates[modelKey]?.catalog?.support?.[canonical] || null;
}
function productSurface(modelKey, productKey) {
  const canonical = canonicalProductKey(productKey);
  return modelStates[modelKey]?.catalog?.surfaces?.[canonical] || null;
}
function supportsProduct(model, run, product) {
  if (model.kind === 'weathernext') return (Array.isArray(run.products) && run.products.includes(product)) || Boolean(run.product_hours?.[product]);
  return String(run.product || '') === String(product);
}
function runLabel(model, run) {
  if (model.kind === 'weathernext') return `${run.label || run.run_date || run.id || 'Published run'} · ${run.status || 'available'}`;
  return `${runDisplayName(model, run)} · Init ${initLabel(run.init_utc)} · ${run.status || 'available'}`;
}
function isFailedRun(run) {
  return String(run?.status || '').toLowerCase() === 'failed';
}
function preferredRun(runs, modelKey = selection.model, productKey = selection.product) {
  const usable = runs.filter(run => !isFailedRun(run));
  if (!usable.length) return null;
  const preferred = preferredComponent(modelKey, productKey);
  const preferredRuns = preferred ? usable.filter(run => String(run.component || '') === preferred) : [];
  const primaryPool = preferredRuns.length ? preferredRuns : usable;
  let eligible = primaryPool.filter(defaultEligibleRun);
  if (!eligible.length && preferredRuns.length) eligible = usable.filter(defaultEligibleRun);
  const candidates = eligible.length ? eligible : primaryPool;
  return [...candidates].sort((left, right) => String(right.init_utc || '').localeCompare(String(left.init_utc || '')) || String(right.id || '').localeCompare(String(left.id || '')))[0] || null;
}
function freshnessState(modelKey, productKey) {
  const support = productSupport(modelKey, productKey);
  if (support && support.state !== 'supported') {
    const quarantined = support.state === 'quarantined';
    return {
      label: quarantined ? 'Blocked' : 'N/A',
      className: 'status-na',
      title: support.reason || (quarantined ? 'This field is blocked by quality control.' : 'This model does not publish this parameter.'),
      run: null,
      product: productKey,
      available: false,
      applicable: false,
    };
  }
  const aliases = compareProductAliases(productKey);
  const runs = (modelStates[modelKey]?.runs || []).filter(run => aliases.has(String(run.product || '')));
  const run = preferredRun(runs, modelKey, productKey);
  if (!run) {
    const failed = [...runs].filter(isFailedRun).sort((left, right) => String(right.init_utc || '').localeCompare(String(left.init_utc || '')))[0];
    if (failed) return { label: 'Failed', className: 'status-failed', title: `Latest published run failed · Init ${initLabel(failed.init_utc)}`, run: null, product: productKey, available: false, applicable: true };
    return { label: 'No map', className: 'status-unavailable', title: 'No published map for this model and parameter', run: null, product: productKey, available: false, applicable: true };
  }
  const target = (Array.isArray(run.targets) ? run.targets : []).find(item => !['failed', 'error'].includes(String(item?.status || '').toLowerCase()) && Boolean(item?.image));
  if (!target) return { label: 'No map', className: 'status-unavailable', title: `A run exists, but it has no usable rendered target · Init ${initLabel(run.init_utc)}`, run: null, product: String(run.product || productKey), available: false, applicable: true };
  const counts = runCoverageCounts(run, target);
  const partial = String(run.status || '').toLowerCase() === 'partial' || String(target.status || '').toLowerCase() === 'partial';
  const coverage = counts ? ` ${counts.available}/${counts.expected}` : '';
  const titlePrefix = `${runDisplayName(MODEL_CONFIG[modelKey], run)} · Init ${initLabel(run.init_utc)}`;
  if (partial) return { label: `Partial${coverage}`, className: 'status-partial', title: `${titlePrefix} · partial coverage${coverage}`, run, product: String(run.product || productKey), available: true, applicable: true };
  const initialized = new Date(run.init_utc || '');
  if (Number.isNaN(initialized.valueOf())) return { label: 'Available', className: 'status-fresh', title: titlePrefix, run, product: String(run.product || productKey), available: true, applicable: true };
  const ageDays = Math.max(0, (Date.now() - initialized.valueOf()) / 86400000);
  const freshDays = modelKey === 'cfsv2' ? 2 : 35;
  const agingDays = modelKey === 'cfsv2' ? 4 : 50;
  if (ageDays <= freshDays) return { label: 'Fresh', className: 'status-fresh', title: `${titlePrefix} · ${Math.floor(ageDays)} day(s) old`, run, product: String(run.product || productKey), available: true, applicable: true };
  if (ageDays <= agingDays) return { label: 'Aging', className: 'status-aging', title: `${titlePrefix} · ${Math.floor(ageDays)} day(s) old`, run, product: String(run.product || productKey), available: true, applicable: true };
  return { label: 'Stale', className: 'status-stale', title: `${titlePrefix} · ${Math.floor(ageDays)} day(s) old`, run, product: String(run.product || productKey), available: true, applicable: true };
}
function selectedRun(model) {
  const available = modelStates[selection.model].runs.filter(run => supportsProduct(model, run, selection.product));
  return available.find(run => String(run.id) === String(selection.run)) || preferredRun(available, selection.model, selection.product);
}
function targetItems(model, run) {
  if (!run) return [];
  if (model.kind === 'weathernext') {
    const hours = numberList(run.product_hours?.[selection.product] || run.hours, 0);
    return hours.map(hour => ({ key: `hour:${hour}`, value: hour, label: `Hour ${String(hour).padStart(3, '0')}` }));
  }
  return (Array.isArray(run.targets) ? run.targets : []).map((target, index) => ({ key: String(target.id || index), value: target, label: target.label || periodLabel(target.target_month || target.valid_start_utc || `Target ${index + 1}`) }));
}
function isSnowProduct(model, product) {
  return model.kind === 'weathernext' && ((modelStates[selection.model].manifest?.snow_products || []).includes(product) || product.includes('snow'));
}
function frameName(run, hour, ratio) {
  const product = selection.product;
  const hourText = String(hour).padStart(3, '0');
  return isSnowProduct(MODEL_CONFIG[selection.model], product) ? `${product}_r${String(ratio || 10).padStart(2, '0')}_${hourText}.jpg` : `${product}_${hourText}.jpg`;
}
function imagePath(model, run, target) {
  if (!run || !target) return '';
  if (model.kind === 'weathernext') return assetPath(`runs/${run.id}/${frameName(run, target.value, selection.ratio)}`);
  return target.value?.image ? assetPath(target.value.image) : '';
}
function targetText(model, target) {
  if (!target) return '—';
  if (model.kind === 'weathernext') return `Hour ${String(target.value).padStart(3, '0')}`;
  return target.label;
}
function ensembleText(model, run, target) {
  if (!run) return '—';
  if (model.kind === 'weathernext') return run.ensemble_mode === 'member' ? `Member ${run.ensemble_member || '—'}` : pretty(run.ensemble_mode || 'ensemble');
  const source = target?.value || {};
  const members = source.ensemble_members || run.ensemble_members;
  if (run.ensemble_scope === 'rolling_initial_conditions') return `${source.ensemble_members || members || 0}/${source.ensemble_expected_members || 40} cycles`;
  return members ? `${members} members` : pretty(source.ensemble_scope || run.ensemble_scope || 'ensemble');
}
function statusText(run, target) {
  const targetValue = target?.value || target;
  const runStatus = String(run?.status || '').toLowerCase();
  const status = runStatus === 'partial' ? 'partial' : (targetValue?.status || run?.status || 'available');
  const counts = runCoverageCounts(run, targetValue);
  if (!counts) return status;
  const unit = run?.ensemble_scope === 'rolling_initial_conditions' ? 'cycles' : 'members';
  return `${status} · ${counts.available}/${counts.expected} ${unit}`;
}
function runMethodText(model, run) {
  if (model === MODEL_CONFIG.nmme) {
    const component = String(run?.component || '');
    if (component === 'ENSMEAN') return 'Official NMME multi-model ensemble mean';
    if (component === 'PROBABILITY') return 'Official CPC NMME category probability';
    if (component === 'CONSENSUS') return 'Equal-weight NMME component-model consensus';
    if (component) return `Individual ${componentLabel(run)} ensemble mean`;
  }
  return run?.aggregation || run?.statistic || 'Seasonal ensemble mean';
}
function leadText(model, target) {
  if (!target) return '—';
  if (model.kind === 'weathernext') return `Hour ${String(target.value).padStart(3, '0')}`;
  const lead = target.value?.lead_month;
  return lead === undefined || lead === null ? '—' : `Month ${lead}`;
}
function fieldText(target) {
  const value = target?.value;
  if (!value) return '—';
  return value.units ? `${value.field || 'Field'} (${value.units})` : (value.field || '—');
}
function setMessage(message) { el('map-wrap').replaceChildren(Object.assign(document.createElement('div'), { className: 'empty', textContent: message })); }
function downloadFileName(src) {
  try { return decodeURIComponent(new URL(src, location.href).pathname.split('/').pop() || 'seasonal-map.png'); }
  catch (_) { return 'seasonal-map.png'; }
}
let dialogMap = { src: '', title: '' };
function openMapDialog(src, title) {
  const dialog = el('map-dialog');
  dialogMap = { src, title };
  el('map-dialog-title').textContent = title;
  setImageFallbacks(el('map-dialog-image'), [src]);
  el('map-dialog-image').alt = title;
  el('map-dialog-download').href = src;
  el('map-dialog-download').download = downloadFileName(src);
  el('map-dialog-status').textContent = '';
  if (typeof dialog.showModal === 'function') dialog.showModal();
}
async function shareCurrentMap() {
  const status = el('map-dialog-status');
  if (!dialogMap.src) return;
  status.textContent = '';
  try {
    const response = await fetch(dialogMap.src);
    if (!response.ok) throw new Error(`Image returned ${response.status}`);
    const blob = await response.blob();
    const file = new File([blob], downloadFileName(dialogMap.src), { type: blob.type || 'image/png' });
    if (navigator.canShare?.({ files: [file] })) {
      await navigator.share({ title: dialogMap.title, files: [file] });
      status.textContent = 'Shared';
    } else if (navigator.share) {
      await navigator.share({ title: dialogMap.title, url: dialogMap.src });
      status.textContent = 'Shared';
    } else {
      await navigator.clipboard.writeText(dialogMap.src);
      status.textContent = 'Image link copied';
    }
  } catch (error) {
    if (error?.name !== 'AbortError') status.textContent = 'Unable to share this image';
  }
}
function renderModelOptions() {
  const select = el('model-select');
  select.replaceChildren();
  MODEL_ROLE_GROUPS.forEach(groupConfig => {
    const entries = Object.entries(MODEL_CONFIG).filter(([, config]) => config.role === groupConfig.role);
    if (!entries.length) return;
    const group = document.createElement('optgroup'); group.label = groupConfig.label;
    entries.forEach(([key, config]) => {
      const state = modelStates[key];
      const suffix = state.error ? ' · unavailable' : state.manifest ? '' : ' · loading';
      const option = document.createElement('option'); option.value = key; option.textContent = config.label + suffix; group.appendChild(option);
    });
    select.appendChild(group);
  });
  select.disabled = false;
  if (MODEL_CONFIG[selection.model]) select.value = selection.model;
}
function renderUnavailable(model) {
  el('product-select').replaceChildren(); el('product-select').disabled = true;
  el('run-select').replaceChildren(); el('run-select').disabled = true;
  el('target-controls').replaceChildren();
  el('ratio-control')?.remove();
  setMessage(modelStates[selection.model].error || 'No published manifest is available for this model yet.');
  ['fact-model','fact-target','fact-lead','fact-ensemble','fact-field','fact-status'].forEach(id => el(id).textContent = id === 'fact-model' ? model.label : '—');
  el('scope').textContent = 'The model workflow has not published a readable manifest for this dashboard.';
  el('source-detail').textContent = `Source: ${model.source}`;
  el('source-link').href = model.direct;
  el('direct-link').href = model.direct;
  el('download-link').hidden = true;
  el('warning').style.display = 'none';
  syncUrlState();
}
function renderOverview() {
  const body = el('overview-matrix-body');
  const states = [];
  const scheduleRows = [];
  const rows = COMPARE_MODELS.map(modelKey => {
    const model = MODEL_CONFIG[modelKey];
    const lastRun = latestUsableModelRun(modelKey);
    const schedule = scheduleFor(modelKey);
    const scheduleState = availabilityScheduleState(modelKey, lastRun);
    const row = document.createElement('tr');
    row.className = `availability-row availability-${scheduleState.key}`;
    const heading = document.createElement('th'); heading.scope = 'row';
    heading.className = 'overview-model-cell';
    const name = document.createElement('span'); name.className = 'overview-model'; name.textContent = model.label; heading.appendChild(name);
    const role = document.createElement('span'); role.className = 'overview-role'; role.textContent = MODEL_ROLE_LABELS[model.role] || pretty(model.role); heading.appendChild(role);
    row.appendChild(heading);
    const cadenceCell = document.createElement('td'); cadenceCell.className = 'overview-cadence';
    const cadence = document.createElement('strong'); cadence.className = 'availability-cadence-label'; cadence.textContent = schedule.cadenceLabel || 'Schedule not set'; cadenceCell.appendChild(cadence);
    const scheduleBadge = document.createElement('span'); scheduleBadge.className = `availability-schedule ${scheduleState.className}`; scheduleBadge.textContent = scheduleState.label; scheduleBadge.title = scheduleState.title; cadenceCell.appendChild(scheduleBadge);
    if (schedule.officialUrl) {
      const source = document.createElement('a'); source.className = 'availability-source'; source.href = schedule.officialUrl; source.target = '_blank'; source.rel = 'noopener'; source.textContent = 'Official timing'; source.title = schedule.officialSchedule || 'Open the official timing reference'; cadenceCell.appendChild(source);
    }
    row.appendChild(cadenceCell);
    const lastCell = document.createElement('td'); lastCell.className = 'overview-last';
    if (lastRun) {
      const last = document.createElement('strong'); last.className = 'availability-last-time'; last.textContent = `Init ${compactRunUtc(lastRun.init_utc)}`; lastCell.appendChild(last);
      const age = document.createElement('span'); age.className = 'availability-detail'; age.textContent = ageLabel(lastRun.init_utc); lastCell.appendChild(age);
      lastCell.title = `Latest usable initialization: ${initLabel(lastRun.init_utc)}.`;
    } else {
      const missing = document.createElement('strong'); missing.className = 'availability-last-time'; missing.textContent = 'No usable run'; lastCell.appendChild(missing);
      lastCell.title = 'No usable rendered run is currently published.';
    }
    row.appendChild(lastCell);
    const nextCell = document.createElement('td'); nextCell.className = 'overview-next';
    const next = document.createElement('strong'); next.className = 'availability-next-time'; next.textContent = scheduleState.next ? compactEdt(scheduleState.next.publish) : '—'; nextCell.appendChild(next);
    const nextDetail = document.createElement('span'); nextDetail.className = 'availability-detail'; nextDetail.textContent = 'Expected wall.cloud availability'; nextCell.appendChild(nextDetail);
    nextCell.title = scheduleState.next ? `Expected wall.cloud availability: ${formatEdt(scheduleState.next.publish)}. ${schedule.officialSchedule || ''}` : scheduleState.title;
    row.appendChild(nextCell);
    COMPARE_PRODUCTS.forEach(productConfig => {
      const state = freshnessState(modelKey, productConfig.value); states.push({ ...state, modelKey, productKey: productConfig.value });
      const cell = document.createElement('td'); cell.className = 'availability-status-cell'; cell.dataset.parameter = productConfig.label;
      const button = document.createElement('button'); button.type = 'button'; button.className = `status-pill ${state.className}`; button.textContent = state.label; button.title = state.title;
      button.setAttribute('aria-label', `${model.label} ${productConfig.label}: ${state.label}; refresh ${scheduleState.label}`);
      if (!state.available) button.disabled = true;
      else button.addEventListener('click', () => {
        selection.model = modelKey; selection.product = state.product; selection.run = String(state.run.id); selection.target = '';
        setView('single');
      });
      cell.appendChild(button); row.appendChild(cell);
    });
    scheduleRows.push({ modelKey, row, schedule, scheduleState });
    return row;
  });
  const groupedRows = [];
  [
    { key: 'frequent', label: 'Frequent refresh', detail: 'High-frequency source cycles' },
    { key: 'monthly', label: 'Monthly and release-window models', detail: 'One forecast family per row' },
  ].forEach(group => {
    const members = scheduleRows.filter(item => (item.schedule.cadenceGroup || 'monthly') === group.key);
    if (!members.length) return;
    const divider = document.createElement('tr'); divider.className = 'availability-group-row';
    const cell = document.createElement('th'); cell.scope = 'rowgroup'; cell.colSpan = COMPARE_PRODUCTS.length + 4;
    const label = document.createElement('span'); label.textContent = group.label;
    const detail = document.createElement('small'); detail.textContent = group.detail;
    cell.append(label, detail); divider.appendChild(cell); groupedRows.push(divider, ...members.map(item => item.row));
  });
  body.replaceChildren(...groupedRows);
  const online = COMPARE_MODELS.filter(modelKey => Boolean(modelStates[modelKey].manifest)).length;
  const applicable = states.filter(state => state.applicable !== false);
  const available = applicable.filter(state => state.available).length;
  const overdueModels = scheduleRows.filter(item => item.scheduleState.key === 'overdue');
  const processingModels = scheduleRows.filter(item => item.scheduleState.key === 'processing');
  const fresh = applicable.filter(state => state.className === 'status-fresh' && !overdueModels.some(item => item.modelKey === state.modelKey)).length;
  const attention = applicable.filter(state => ['status-aging', 'status-stale', 'status-partial', 'status-failed'].includes(state.className)).length;
  const stats = [
    { label: 'Models online', value: `${online}/${COMPARE_MODELS.length}`, detail: 'published manifests loaded' },
    { label: 'Map coverage', value: `${available}/${applicable.length}`, detail: 'supported model-parameter surfaces' },
    { label: 'On schedule', value: `${COMPARE_MODELS.length - overdueModels.length}/${COMPARE_MODELS.length}`, detail: 'provider windows' },
    { label: 'Needs attention', value: String(attention + overdueModels.length), detail: 'aging, stale, partial, failed, or late' },
  ];
  el('overview-stats').replaceChildren(...stats.map(stat => {
    const card = document.createElement('article'); card.className = 'card overview-stat';
    const label = document.createElement('small'); label.textContent = stat.label;
    const value = document.createElement('strong'); value.textContent = stat.value;
    const detail = document.createElement('span'); detail.textContent = stat.detail;
    card.append(label, value, detail); return card;
  }));
  const unavailableModels = COMPARE_MODELS.filter(modelKey => modelStates[modelKey].error).map(modelKey => MODEL_CONFIG[modelKey].label);
  const partialCount = states.filter(state => state.className === 'status-partial').length;
  const staleCount = states.filter(state => state.className === 'status-stale').length;
  const notApplicableCount = states.filter(state => state.applicable === false).length;
  const notices = [];
  if (unavailableModels.length) notices.push(`Manifest unavailable: ${unavailableModels.join(' · ')}`);
  if (partialCount) notices.push(`${partialCount} surface${partialCount === 1 ? '' : 's'} have partial ensemble coverage`);
  if (staleCount) notices.push(`${staleCount} surface${staleCount === 1 ? '' : 's'} are beyond the expected refresh window`);
  if (overdueModels.length) notices.push(`Old run: ${overdueModels.map(item => MODEL_CONFIG[item.modelKey].label).join(' · ')} is past its expected publication window`);
  if (processingModels.length) notices.push(`${processingModels.map(item => MODEL_CONFIG[item.modelKey].label).join(' · ')} is inside its expected publication grace window`);
  if (notApplicableCount) notices.push(`${notApplicableCount} intentionally unsupported or quarantined surface${notApplicableCount === 1 ? ' is' : 's are'} excluded from coverage`);
  el('overview-notices').textContent = notices.length ? notices.join('. ') + '.' : `${fresh} map surfaces are fresh and all model rows are within their expected publication windows.`;
  el('footer-copy').textContent = seasonalCatalog?.generated_utc ? `Catalog updated ${initLabel(seasonalCatalog.generated_utc)}` : 'Seasonal catalog loaded';
  syncUrlState();
}
function compareEmpty(message) {
  const empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = message; return empty;
}
function compareBaselineLabel(value) {
  return COMPARE_BASELINES.find(item => item.value === value)?.label || 'Model reference';
}
function compareFilteredModels() {
  return selection.compareRole === 'all' ? COMPARE_MODELS : COMPARE_MODELS.filter(modelKey => MODEL_CONFIG[modelKey].role === selection.compareRole);
}
function compareModelListLabel(models = compareFilteredModels()) {
  return models.map(modelKey => MODEL_CONFIG[modelKey].label).join(' · ');
}
function renderCompareGrid(targetKey) {
  const grid = el('compare-grid');
  const models = compareFilteredModels();
  const available = targetKey ? models.filter(modelKey => Boolean(compareRunForTarget(modelKey, targetKey, selection.compareBaseline, selection.compareProduct))) : [];
  const availableOnly = selection.compareAvailableOnly && Boolean(targetKey);
  const visible = availableOnly ? available : models;
  if (visible.length) grid.replaceChildren(...visible.map(modelKey => renderCompareCard(modelKey, targetKey)));
  else {
    const empty = compareEmpty('No forecast surface is available for this parameter, period, and reference.');
    empty.classList.add('card'); grid.replaceChildren(empty);
  }
  const missing = targetKey ? models.filter(modelKey => !available.includes(modelKey)) : [];
  const intentional = missing.filter(modelKey => {
    const support = productSupport(modelKey, selection.compareProduct);
    return Boolean(support && support.state !== 'supported');
  });
  const incompatible = missing.filter(modelKey => {
    const surface = productSurface(modelKey, selection.compareProduct);
    return !intentional.includes(modelKey) && Boolean(surface?.available && surface.comparable === false);
  });
  const unpublished = missing.filter(modelKey => !intentional.includes(modelKey) && !incompatible.includes(modelKey));
  const missingMessages = [];
  if (unpublished.length) missingMessages.push(`Not published for this selection: ${unpublished.map(modelKey => MODEL_CONFIG[modelKey].label).join(' · ')}`);
  if (incompatible.length) missingMessages.push(`Excluded until regenerated with canonical units/metadata: ${incompatible.map(modelKey => MODEL_CONFIG[modelKey].label).join(' · ')}`);
  if (intentional.length) missingMessages.push(`Not supported or QC-blocked: ${intentional.map(modelKey => MODEL_CONFIG[modelKey].label).join(' · ')}`);
  el('compare-missing').textContent = missingMessages.join('. ');
  return available.length;
}
function renderCompareCard(modelKey, targetKey) {
  const model = MODEL_CONFIG[modelKey];
  const state = modelStates[modelKey];
  const productKey = selection.compareProduct || DEFAULT_COMPARE_PRODUCT;
  const product = compareProductLabel(productKey);
  const support = productSupport(modelKey, productKey);
  const surface = productSurface(modelKey, productKey);
  const card = document.createElement('article'); card.className = 'card compare-card';
  const header = document.createElement('div'); header.className = 'compare-card-head';
  const heading = document.createElement('div'); heading.className = 'compare-card-title';
  const title = document.createElement('h2'); title.textContent = model.label; heading.appendChild(title);
  const role = document.createElement('span'); role.className = 'model-role'; role.textContent = MODEL_ROLE_LABELS[model.role] || pretty(model.role); heading.appendChild(role);
  header.appendChild(heading);
  const direct = document.createElement('a'); direct.href = model.direct; direct.textContent = 'Model page'; header.appendChild(direct);
  card.appendChild(header);
  const imageWrap = document.createElement('div'); imageWrap.className = 'compare-image-wrap';
  const baseline = selection.compareBaseline || 'native';
  const run = compareRunForTarget(modelKey, targetKey, baseline, productKey);
  const target = run ? compareTarget(run, targetKey, baseline) : null;
  const asset = target ? compareTargetAsset(target, baseline) : null;
  if (!state.manifest) {
    imageWrap.appendChild(compareEmpty(state.error || 'Manifest unavailable.'));
  } else if (support && support.state !== 'supported') {
    imageWrap.appendChild(compareEmpty(support.reason || `${product} is not supported by this model adapter.`));
  } else if (surface?.available && surface.comparable === false) {
    imageWrap.appendChild(compareEmpty(surface.reason || `${product} is excluded until it is regenerated with canonical units and field metadata.`));
  } else if (!run || !target || !asset) {
    const reference = baseline === 'native' ? product : `${product} with a common 1991–2020 reference`;
    imageWrap.appendChild(compareEmpty(targetKey ? `No ${reference} published for ${periodLabel(targetKey)}.` : `No ${reference} is available.`));
  } else {
    const image = document.createElement('img');
    const originalImage = assetPath(asset.image);
    const fullImage = shareImagePath(asset.image);
    image.alt = `${runDisplayName(model, run)} ${product} ${periodLabel(target.target_month)} · ${compareBaselineLabel(baseline)}`;
    image.loading = 'lazy';
    image.decoding = 'async';
    setImageFallbacks(image, [thumbnailPath(asset.image), fullImage, originalImage], () => {
      imageWrap.replaceChildren(compareEmpty('The manifest target exists, but its image is not in the published Pages tree.'));
    });
    const imageButton = document.createElement('button'); imageButton.type = 'button'; imageButton.className = 'image-button'; imageButton.setAttribute('aria-label', `Open full-size ${image.alt}`);
    imageButton.addEventListener('click', () => openMapDialog(fullImage, image.alt));
    imageButton.appendChild(image); imageWrap.appendChild(imageButton);
  }
  card.appendChild(imageWrap);
  const metadata = document.createElement('p'); metadata.className = 'compare-meta';
  const runIdentity = run ? runDisplayName(model, run) : '';
  const identityPrefix = runIdentity && runIdentity !== model.label ? `${runIdentity} · ` : '';
  metadata.textContent = target && asset && run
    ? `${identityPrefix}Init ${initLabel(run.init_utc)} · ${asset.status || target.status || run.status || 'available'}`
    : (support && support.state !== 'supported' ? `${support.state === 'quarantined' ? 'QC blocked' : 'Not supported'} · ${support.reason || product}` : (surface?.available && surface.comparable === false ? `Excluded from comparison · ${surface.reason || product}` : (state.manifest ? `No matching ${product} target for this period.` : `Unavailable: ${state.error || 'manifest not published'}`)));
  card.appendChild(metadata);
  return card;
}
function analogEntry(modelKey, targetKey) {
  return (seasonalAnalogs?.entries || []).find(entry => String(entry.model || '') === modelKey && String(entry.target || '') === String(targetKey || '')) || null;
}
function analogProductEntry(modelKey, targetKey) {
  return (seasonalAnalogProducts?.entries || []).find(entry => String(entry.model || '') === modelKey && String(entry.target || '') === String(targetKey || '')) || null;
}
function renderAnalogProductGrid(section, products, entry, analogLabel) {
  const grid = document.createElement('div');
  grid.className = 'analog-product-grid';
  products.filter(Boolean).forEach(product => {
    const tile = document.createElement('article');
    tile.className = 'analog-product';
    const title = document.createElement('h5');
    title.textContent = product.label || product.product || 'Analog product';
    tile.appendChild(title);
    const originalImage = product.image && ['ready', 'stale'].includes(String(product.status || '').toLowerCase()) ? assetPath(product.image) : '';
    const image = originalImage ? shareImagePath(product.image) : '';
    if (image) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'image-button';
      button.setAttribute('aria-label', `Open full-size ${title.textContent}`);
      const img = document.createElement('img');
      img.alt = `${title.textContent} for ${analogLabel || entry.top_analog?.label || entry.period?.label || 'the selected period'}`;
      img.loading = 'lazy';
      setImageFallbacks(img, [image, originalImage], () => { tile.replaceChildren(title, Object.assign(document.createElement('p'), { className: 'analog-product-note', textContent: 'The generated image is not in the published tree.' })); });
      button.addEventListener('click', () => openMapDialog(image, img.alt));
      button.appendChild(img);
      tile.appendChild(button);
    } else {
      const message = document.createElement('p');
      message.className = 'analog-product-note';
      message.textContent = product.error || 'Waiting for the source map.';
      tile.appendChild(message);
    }
    const meta = document.createElement('p');
    meta.className = 'analog-product-meta';
    meta.textContent = `${product.provider || 'Source'} · ${product.status || 'unavailable'}${product.status === 'stale' ? ' · retained last good map' : ''}`;
    tile.appendChild(meta);
    if (product.source_url) {
      const source = document.createElement('a');
      source.href = product.source_url;
      source.target = '_blank';
      source.rel = 'noopener';
      source.textContent = 'Source';
      tile.appendChild(source);
    }
    grid.appendChild(tile);
  });
  if (!grid.children.length) {
    const message = document.createElement('p');
    message.className = 'analog-product-note';
    message.textContent = 'No generated maps are available for this target.';
    section.appendChild(message);
  } else {
    section.appendChild(grid);
  }
}
function renderAnalogProducts(card, modelKey, targetKey) {
  const entry = analogProductEntry(modelKey, targetKey);
  const section = document.createElement('div');
  section.className = 'analog-products';
  if (!seasonalAnalogProducts) {
    const heading = document.createElement('h4');
    heading.textContent = 'Analog maps';
    section.appendChild(heading);
    const message = document.createElement('p');
    message.className = 'analog-product-note';
    message.textContent = analogProductsManifestError ? 'Map products are unavailable for this release.' : 'Map products are loading…';
    section.appendChild(message);
    card.appendChild(section);
    return;
  }
  if (!entry) {
    const heading = document.createElement('h4');
    heading.textContent = 'Analog maps';
    section.appendChild(heading);
    const message = document.createElement('p');
    message.className = 'analog-product-note';
    message.textContent = 'No generated maps are available for this target.';
    section.appendChild(message);
    card.appendChild(section);
    return;
  }

  const compositeKeys = ['psl_500mb_height_anomaly', 'psl_2m_temperature_anomaly', 'mrcc_snowfall_departure_composite'];
  const compositeProducts = compositeKeys.map(key => entry.composites?.[key]).filter(Boolean);
  if (entry.composite?.count >= 2 || compositeProducts.length) {
    const compositeSection = document.createElement('div');
    compositeSection.className = 'analog-products analog-composite-products';
    const compositeHeading = document.createElement('h4');
    const compositeCount = entry.composite?.count || compositeProducts[0]?.composite_count || 5;
    compositeHeading.textContent = `Top-${compositeCount} weighted blend`;
    compositeSection.appendChild(compositeHeading);
    const compositeNote = document.createElement('p');
    compositeNote.className = 'analog-product-note';
    compositeNote.textContent = 'Weights use 80% pattern similarity and 20% amplitude similarity. Snowfall applies the same weights to MRCC/ACIS departures.';
    compositeSection.appendChild(compositeNote);
    renderAnalogProductGrid(compositeSection, compositeProducts, entry, `${compositeCount}-analog composite`);
    section.appendChild(compositeSection);
  }

  const topSection = document.createElement('div');
  topSection.className = 'analog-products';
  const heading = document.createElement('h4');
  heading.textContent = `Top analog: ${entry.top_analog?.label || entry.period?.label || 'not available'}`;
  topSection.appendChild(heading);
  renderAnalogProductGrid(topSection, ANALOG_PRODUCT_ORDER.map(key => entry.products?.[key]), entry, entry.top_analog?.label || entry.period?.label || targetKey);
  section.appendChild(topSection);
  card.appendChild(section);
}
function renderAnalogPanel(targetKey) {
  const panel = el('analog-panel');
  const grid = el('analog-grid');
  const summary = el('analog-summary');
  const isHeight = canonicalProductKey(selection.compareProduct || DEFAULT_COMPARE_PRODUCT) === DEFAULT_COMPARE_PRODUCT;
  if (!panel || !grid || !summary || !isHeight || !targetKey) {
    if (panel) panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const models = ['superensemble', 'cfsv2'];
  const entries = models.map(modelKey => analogEntry(modelKey, targetKey)).filter(Boolean);
  if (!seasonalAnalogs) {
    summary.textContent = 'Analog search is waiting for the first published CFSv2/Super Ensemble numeric grids.';
    grid.replaceChildren(compareEmpty(analogManifestError ? 'The analog manifest is not available for this release.' : 'Loading historical analogs…'));
    return;
  }
  const compositeCount = seasonalAnalogs.source?.composite?.count || 5;
  summary.textContent = `${periodLabel(targetKey)} · AnalogWX ERA5 · top-${compositeCount} weighted blends`;
  if (!entries.length) {
    grid.replaceChildren(compareEmpty(`No historical analog result is published for ${periodLabel(targetKey)}.`));
    return;
  }
  grid.replaceChildren(...models.map(modelKey => {
    const entry = analogEntry(modelKey, targetKey);
    const card = document.createElement('section');
    card.className = 'analog-card';
    const heading = document.createElement('h3');
    heading.textContent = MODEL_CONFIG[modelKey].label;
    card.appendChild(heading);
    const meta = document.createElement('p');
    meta.className = 'analog-meta';
    meta.textContent = entry ? `Init ${initLabel(entry.init_utc)} · ${entry.results?.length || 0} ranked analogs` : 'No numeric grid published for this target';
    card.appendChild(meta);
    if (!entry) return card;
    const table = document.createElement('table');
    table.className = 'analog-table';
    table.innerHTML = '<thead><tr><th scope="col">Rank</th><th scope="col" title="Historical analog month or season">Period</th><th scope="col" title="Centered spatial pattern correlation">Pattern</th><th scope="col" title="Area-weighted anomaly amplitude similarity">Amplitude</th><th scope="col" title="Inverse similarity-distance composite weight">Weight</th></tr></thead>';
    const body = document.createElement('tbody');
    (entry.results || []).forEach(result => {
      const row = document.createElement('tr');
      const rank = document.createElement('th'); rank.scope = 'row'; rank.textContent = String(result.rank ?? '—');
      const label = document.createElement('td'); label.textContent = result.label || '—';
      const score = document.createElement('td'); score.textContent = Number.isFinite(Number(result.pattern_correlation)) ? Number(result.pattern_correlation).toFixed(3) : '—';
      score.dataset.label = 'Pattern';
      score.title = 'Centered spatial correlation; higher values indicate a more similar pattern.';
      const amplitude = document.createElement('td');
      amplitude.textContent = Number.isFinite(Number(result.amplitude_similarity)) ? `${(Number(result.amplitude_similarity) * 100).toFixed(0)}%` : '—';
      amplitude.dataset.label = 'Amp';
      amplitude.title = 'Area-weighted RMS anomaly amplitude similarity; 100% means equal amplitude.';
      const weight = document.createElement('td');
      weight.textContent = Number.isFinite(Number(result.composite_weight)) && Number(result.composite_weight) > 0 ? `${(Number(result.composite_weight) * 100).toFixed(1)}%` : '—';
      weight.dataset.label = 'Weight';
      weight.title = 'Inverse similarity-distance weight in the displayed top-analog composite.';
      row.append(rank, label, score, amplitude, weight); body.appendChild(row);
    });
    table.appendChild(body); card.appendChild(table);
    renderAnalogProducts(card, modelKey, targetKey);
    return card;
  }));
}
function renderCompare() {
  const productSelect = el('compare-product-select');
  const select = el('compare-target-select');
  const baselineSelect = el('compare-baseline-select');
  const roleSelect = el('compare-role-select');
  roleSelect.value = selection.compareRole;
  el('compare-available-only').checked = selection.compareAvailableOnly;
  const productOptions = compareProductOptions();
  if (!productOptions.length) {
    productSelect.replaceChildren(Object.assign(document.createElement('option'), { textContent: 'No comparable parameters' }));
    productSelect.disabled = true;
    select.replaceChildren(Object.assign(document.createElement('option'), { textContent: 'No matching targets' }));
    select.disabled = true;
    baselineSelect.replaceChildren(Object.assign(document.createElement('option'), { textContent: 'No reference available' }));
    baselineSelect.disabled = true;
    selection.compareProduct = '';
    selection.compareTarget = '';
    selection.compareBaseline = '';
    el('compare-summary').textContent = 'No comparable anomaly products have been published across the model manifests';
    renderAnalogPanel('');
    renderCompareGrid('');
    el('footer-copy').textContent = seasonalCatalog?.generated_utc ? `Catalog updated ${initLabel(seasonalCatalog.generated_utc)}` : 'Seasonal comparison';
    syncUrlState();
    return;
  }
  if (!productOptions.some(item => item.value === selection.compareProduct)) {
    selection.compareProduct = productOptions.find(item => item.value === DEFAULT_COMPARE_PRODUCT)?.value || productOptions[0].value;
  }
  populate(productSelect, productOptions, selection.compareProduct);
  selection.compareProduct = productSelect.value || productOptions[0].value;
  const product = compareProductLabel(selection.compareProduct);
  const options = comparePeriodOptions(selection.compareProduct);
  const baselineOptions = compareBaselineOptions(selection.compareProduct);
  if (!baselineOptions.some(item => item.value === selection.compareBaseline)) selection.compareBaseline = 'native';
  populate(baselineSelect, baselineOptions, selection.compareBaseline);
  selection.compareBaseline = baselineSelect.value || 'native';
  baselineSelect.disabled = baselineOptions.length <= 1;
  if (!options.length) {
    select.replaceChildren(Object.assign(document.createElement('option'), { textContent: 'No matching targets' }));
    select.disabled = true;
    selection.compareTarget = '';
    el('compare-summary').textContent = `${product} · no matching target has been published across the model manifests`;
    renderAnalogPanel('');
    renderCompareGrid('');
    el('footer-copy').textContent = seasonalCatalog?.generated_utc ? `Catalog updated ${initLabel(seasonalCatalog.generated_utc)}` : 'Seasonal comparison';
    syncUrlState();
    return;
  }
  const preferred = options.find(item => /^\d{6}-\d{6}$/.test(String(item.value))) || options[options.length - 1];
  if (!options.some(item => String(item.value) === String(selection.compareTarget))) selection.compareTarget = preferred.value;
  populate(select, options, selection.compareTarget);
  selection.compareTarget = select.value || preferred.value;
  const availableCount = renderCompareGrid(selection.compareTarget);
  renderAnalogPanel(selection.compareTarget);
  el('compare-summary').textContent = `${periodLabel(selection.compareTarget)} · ${compareBaselineLabel(selection.compareBaseline)} · ${availableCount}/${compareFilteredModels().length} models available`;
  el('footer-copy').textContent = seasonalCatalog?.generated_utc ? `Catalog updated ${initLabel(seasonalCatalog.generated_utc)}` : 'Seasonal comparison';
  syncUrlState();
}
function renderControls(model, run, targets) {
  const controls = el('target-controls'); controls.replaceChildren();
  targets.forEach((target, index) => {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = target.label; button.dataset.targetIndex = String(index);
    const active = String(target.key) === String(selection.target); button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
    button.addEventListener('click', () => { selection.target = target.key; renderAll(); });
    button.addEventListener('keydown', event => {
      let next = null;
      if (event.key === 'ArrowLeft') next = (index - 1 + targets.length) % targets.length;
      if (event.key === 'ArrowRight') next = (index + 1) % targets.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = targets.length - 1;
      if (next === null) return;
      event.preventDefault(); selection.target = targets[next].key; renderAll();
      requestAnimationFrame(() => el('target-controls').querySelector(`[data-target-index="${next}"]`)?.focus());
    });
    controls.appendChild(button);
  });
  if (isSnowProduct(model, selection.product)) {
    const wrapper = document.createElement('label'); wrapper.className = 'ratio-control'; wrapper.id = 'ratio-control'; wrapper.innerHTML = '<span class="control-label">Snow ratio</span><select id="ratio-select"></select>';
    controls.appendChild(wrapper);
    const ratios = numberList(run?.product_snow_ratios?.[selection.product] || run?.snow_ratios, 10, 20); const ratioSelect = wrapper.querySelector('select');
    populate(ratioSelect, (ratios.length ? ratios : [10]).map(value => ({ value, label: `${value}:1` })), selection.ratio);
    selection.ratio = ratioSelect.value || '10'; ratioSelect.addEventListener('change', () => { selection.ratio = ratioSelect.value; renderAll(); });
  }
}
function renderAll() {
  renderModelOptions();
  el('download-link').hidden = true;
  const model = MODEL_CONFIG[selection.model]; const modelState = modelStates[selection.model];
  if (!modelState.manifest) { renderUnavailable(model); return; }
  const products = manifestProducts(model);
  const productWasSelected = products.includes(selection.product);
  const preferred = productWasSelected ? null : (defaultSelectionForModel(model, products) || genericSelectionForModel(model, products));
  selection.product = productWasSelected ? selection.product : (preferred?.product || products[0] || '');
  populate(el('product-select'), products.map(value => ({ value, label: productLabel(model, value) })), selection.product);
  const runs = modelState.runs.filter(run => supportsProduct(model, run, selection.product));
  const defaultRun = preferredRun(runs, selection.model, selection.product);
  const preferredRunId = preferred?.product === selection.product ? String(preferred.run || '') : '';
  if (!runs.some(run => String(run.id) === String(selection.run))) {
    selection.run = runs.some(run => String(run.id) === preferredRunId) ? preferredRunId : String(defaultRun?.id || '');
  }
  const runSelect = el('run-select');
  populate(runSelect, runs.map(run => ({ value: run.id, label: runLabel(model, run) })), selection.run);
  if (!selection.run && runs.length) {
    const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = 'No usable run selected'; placeholder.disabled = true; placeholder.selected = true;
    runSelect.insertBefore(placeholder, runSelect.firstChild); runSelect.value = '';
  }
  const run = selectedRun(model); const targets = targetItems(model, run);
  const preferredTarget = targets.find(target => model.kind === 'seasonal' && /^\d{6}-\d{6}$/.test(String(target.value?.target_month || ''))) || targets[0];
  const preferredTargetKey = preferred?.product === selection.product && String(preferred?.run || '') === String(selection.run) ? String(preferred.target || '') : '';
  if (!targets.some(target => String(target.key) === String(selection.target))) {
    selection.target = targets.some(target => String(target.key) === preferredTargetKey) ? preferredTargetKey : String(preferredTarget?.key || '');
  }
  renderControls(model, run, targets);
  const target = targets.find(item => String(item.key) === String(selection.target)) || targets[0];
  if (!run || !target) {
    setMessage(runs.length ? 'No usable rendered target is available by default. Choose a retained run to inspect its failure.' : 'No rendered target is available for the selected parameter.');
    ['fact-target','fact-lead','fact-ensemble','fact-field','fact-status'].forEach(id => el(id).textContent = '—');
    el('fact-model').textContent = model.label;
    el('scope').textContent = runs.length ? 'All published runs for this parameter are failed or lack a rendered target.' : 'No published run is available for this parameter.';
    const warning = el('warning'); warning.style.display = runs.length ? 'block' : 'none'; warning.textContent = runs.length ? 'No failed run was selected automatically; retained history remains available for diagnosis.' : '';
    syncUrlState();
    return;
  }
  const targetValue = target.value;
  const label = productLabel(model, selection.product);
  el('fact-model').textContent = runDisplayName(model, run); el('fact-target').textContent = model.kind === 'weathernext' ? targetText(model, target) : periodLabel(targetValue.target_month || targetValue.valid_start_utc);
  el('fact-lead').textContent = leadText(model, target); el('fact-ensemble').textContent = ensembleText(model, run, target); el('fact-field').textContent = fieldText(target); el('fact-status').textContent = statusText(run, target);
  if (model.kind === 'weathernext') {
    el('scope').textContent = `${run.source_label || run.source || model.source} · Updated ${initLabel(run.updated_utc)}. ${run.successful_exports || 0} successful exports.`;
  } else {
    const baseline = run.climatology?.source || targetValue?.baseline?.source || 'model calibration baseline';
    el('scope').textContent = `${runMethodText(model, run)} from ${initLabel(run.init_utc)}. Baseline: ${baseline}.`;
  }
  el('source-detail').textContent = `Source: ${run.source || run.source_label || model.source}`;
  el('source-link').href = run.source_url || model.direct; el('direct-link').href = model.direct;
  const originalImage = imagePath(model, run, target);
  const image = targetValue?.image ? shareImagePath(targetValue.image) : originalImage;
  setMessage('');
  el('map-wrap').replaceChildren();
  if (image) {
    const imageElement = document.createElement('img'); imageElement.alt = `${runDisplayName(model, run)} ${label} ${targetText(model, target)}`; imageElement.loading = 'eager';
    setImageFallbacks(imageElement, [image, originalImage], () => { el('download-link').hidden = true; setMessage('The manifest is available, but this image is not present in the published Pages tree.'); });
    const imageButton = document.createElement('button'); imageButton.type = 'button'; imageButton.className = 'image-button'; imageButton.setAttribute('aria-label', `Open full-size ${imageElement.alt}`); imageButton.addEventListener('click', () => openMapDialog(imageElement.src, imageElement.alt)); imageButton.appendChild(imageElement); el('map-wrap').appendChild(imageButton);
    el('download-link').href = image; el('download-link').download = downloadFileName(image); el('download-link').hidden = false;
  } else setMessage('No rendered image is available for this target.');
  const warning = el('warning');
  if (targetValue?.status === 'failed') { warning.style.display = 'block'; warning.textContent = targetValue.error || 'This target failed; retained history remains selectable.'; }
  else if (targetValue?.status === 'partial' || run.status === 'partial') { const counts = runCoverageCounts(run, targetValue); const coverage = counts ? ` (${counts.available}/${counts.expected} ${run.ensemble_scope === 'rolling_initial_conditions' ? 'cycles' : 'members'})` : ''; warning.style.display = 'block'; warning.textContent = `This run is partial${coverage}; retained history remains selectable.`; }
  else if (run.source_warning) { warning.style.display = 'block'; warning.textContent = run.source_warning; }
  else warning.style.display = 'none';
  el('footer-copy').textContent = modelState.manifest.generated_utc ? `Updated ${initLabel(modelState.manifest.generated_utc)} · ${model.source}` : model.source;
  syncUrlState();
}
function renderCurrentView() {
  if (selection.view === 'overview') renderOverview();
  else if (selection.view === 'compare') renderCompare();
  else renderAll();
}
function setView(view) {
  selection.view = ['overview', 'single', 'compare'].includes(view) ? view : 'overview';
  ['overview', 'single', 'compare'].forEach(item => {
    const active = selection.view === item; const tab = el(`${item}-tab`);
    tab.classList.toggle('active', active); tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
  });
  el('overview-view').hidden = selection.view !== 'overview';
  el('single-toolbar').hidden = selection.view !== 'single'; el('single-view').hidden = selection.view !== 'single';
  el('compare-view').hidden = selection.view !== 'compare';
  renderCurrentView();
}
async function copyCurrentLink() {
  syncUrlState(); const status = el('copy-status');
  try {
    await navigator.clipboard.writeText(location.href);
    status.textContent = 'Link copied';
  } catch (_) {
    const field = document.createElement('textarea'); field.value = location.href; field.setAttribute('readonly', ''); field.style.position = 'fixed'; field.style.opacity = '0'; document.body.appendChild(field); field.select();
    const copied = document.execCommand('copy'); field.remove(); status.textContent = copied ? 'Link copied' : 'Copy failed';
  }
  window.setTimeout(() => { el('page-menu').open = false; }, 700);
  window.setTimeout(() => { status.textContent = ''; }, 2200);
}
el('model-select').addEventListener('change', event => { selection.model = event.target.value; selection.product = ''; selection.run = ''; selection.target = ''; renderAll(); });
el('product-select').addEventListener('change', event => { selection.product = event.target.value; selection.run = ''; selection.target = ''; renderAll(); });
el('run-select').addEventListener('change', event => { selection.run = event.target.value; selection.target = ''; renderAll(); });
el('overview-tab').addEventListener('click', () => setView('overview'));
el('single-tab').addEventListener('click', () => setView('single'));
el('compare-tab').addEventListener('click', () => setView('compare'));
el('compare-controls-toggle').addEventListener('click', () => {
  const toolbar = document.querySelector('.compare-toolbar');
  setCompareControlsCollapsed(!toolbar.classList.contains('is-collapsed'));
});
el('compare-product-select').addEventListener('change', event => { selection.compareProduct = event.target.value; selection.compareTarget = ''; selection.compareBaseline = 'native'; renderCompare(); });
el('compare-target-select').addEventListener('change', event => { selection.compareTarget = event.target.value; renderCompare(); });
el('compare-baseline-select').addEventListener('change', event => { selection.compareBaseline = event.target.value; renderCompare(); });
el('compare-role-select').addEventListener('change', event => { selection.compareRole = event.target.value; renderCompare(); });
el('compare-available-only').addEventListener('change', event => { selection.compareAvailableOnly = event.target.checked; renderCompare(); });
el('copy-link').addEventListener('click', copyCurrentLink);
el('map-dialog-share').addEventListener('click', shareCurrentMap);
el('map-dialog-close').addEventListener('click', () => el('map-dialog').close());
el('map-dialog').addEventListener('click', event => { if (event.target === el('map-dialog')) el('map-dialog').close(); });
document.querySelector('.view-tabs').addEventListener('keydown', event => {
  const ids = ['overview-tab', 'single-tab', 'compare-tab']; const current = ids.indexOf(event.target.id);
  if (current < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? ids.length - 1 : event.key === 'ArrowLeft' ? (current - 1 + ids.length) % ids.length : (current + 1) % ids.length;
  const nextView = ids[next].replace('-tab', ''); setView(nextView); el(ids[next]).focus();
});
document.querySelectorAll('[data-overview-model]').forEach(button => button.addEventListener('click', () => { selection.model = button.dataset.overviewModel; selection.product = ''; selection.run = ''; selection.target = ''; setView('single'); }));
const provenanceMedia = window.matchMedia('(min-width: 901px)');
function syncProvenanceDisclosure(event = provenanceMedia) { el('provenance-details').open = event.matches; }
provenanceMedia.addEventListener('change', syncProvenanceDisclosure);
syncProvenanceDisclosure();
const compareControlsMedia = window.matchMedia('(max-width: 600px)');
function setCompareControlsCollapsed(collapsed) {
  const shouldCollapse = compareControlsMedia.matches && collapsed;
  document.querySelector('.compare-toolbar').classList.toggle('is-collapsed', shouldCollapse);
  el('compare-controls-toggle').setAttribute('aria-expanded', String(!shouldCollapse));
  el('compare-controls-toggle-state').textContent = shouldCollapse ? 'Show' : 'Hide';
}
function syncCompareControlsDisclosure(event = compareControlsMedia) { setCompareControlsCollapsed(event.matches); }
compareControlsMedia.addEventListener('change', syncCompareControlsDisclosure);
syncCompareControlsDisclosure();
renderModelOptions();
async function loadManifest(key, config) {
  try {
    const response = await fetch(config.manifest);
    if (!response.ok) throw new Error(`Manifest returned ${response.status}`);
    const manifest = await response.json();
    modelStates[key].manifest = manifest;
    modelStates[key].runs = Array.isArray(manifest.runs) ? manifest.runs.filter(run => run && run.id) : [];
  } catch (error) {
    modelStates[key].error = error.message;
  }
}
async function loadAnalogManifest() {
  try {
    const response = await fetch(ANALOG_MANIFEST_URL);
    if (!response.ok) throw new Error(`Analog manifest returned ${response.status}`);
    const manifest = await response.json();
    if (manifest?.schema_version !== 'seasonal_z500_analogs_v1' || manifest?.kind !== 'seasonal_z500_analog_manifest') throw new Error('Analog manifest schema is not recognized');
    seasonalAnalogs = manifest;
  } catch (error) {
    analogManifestError = error.message;
  }
}
async function loadAnalogProductsManifest() {
  try {
    const response = await fetch(ANALOG_PRODUCTS_MANIFEST_URL);
    if (!response.ok) throw new Error(`Analog product manifest returned ${response.status}`);
    const manifest = await response.json();
    if (manifest?.schema_version !== 'seasonal_analog_products_v1' || manifest?.kind !== 'seasonal_analog_products_manifest') throw new Error('Analog product manifest schema is not recognized');
    seasonalAnalogProducts = manifest;
  } catch (error) {
    analogProductsManifestError = error.message;
  }
}
async function loadDashboardData() {
  const catalogModels = new Set();
  try {
    const response = await fetch(CATALOG_URL);
    if (!response.ok) throw new Error(`Catalog returned ${response.status}`);
    const catalog = await response.json();
    if (catalog?.kind !== 'seasonal_dashboard_catalog' || !catalog.models) throw new Error('Catalog schema is not recognized');
    seasonalCatalog = catalog;
    Object.entries(catalog.models).forEach(([key, entry]) => {
      if (!MODEL_CONFIG[key] || !entry) return;
      catalogModels.add(key);
      const config = MODEL_CONFIG[key];
      config.label = entry.label || config.label;
      config.role = entry.role || config.role;
      config.source = entry.source || config.source;
      config.preferredComponent = entry.preferred_component || config.preferredComponent || '';
      config.direct = assetPath(entry.direct || `seasonal/${key}/`);
      config.manifest = assetPath(entry.manifest || `seasonal/${key}_manifest.json`);
      const state = modelStates[key];
      state.catalog = entry;
      state.manifest = entry;
      state.runs = Array.isArray(entry.runs) ? entry.runs.filter(run => run && run.id) : [];
      if (entry.status === 'invalid' || entry.status === 'unavailable') {
        state.error = entry.validation?.issues?.[0]?.message || `Catalog reports ${entry.status}`;
      }
    });
  } catch (_) {
    seasonalCatalog = null;
  }
  await Promise.all(Object.entries(MODEL_CONFIG)
    .filter(([key]) => !catalogModels.has(key))
    .map(([key, config]) => loadManifest(key, config)));
  await Promise.all([loadAnalogManifest(), loadAnalogProductsManifest()]);
}
loadDashboardData().then(() => {
  if (!modelStates[selection.model].manifest) selection.model = Object.keys(MODEL_CONFIG).find(key => modelStates[key].manifest) || selection.model;
  setView(selection.view);
});

