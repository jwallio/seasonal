import ee
import os
import json
import time
import glob
import hashlib
import math
import re
import shutil
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone

# --- 1. AUTHENTICATION ---
# We use the key you stored in GitHub Secrets
key = os.environ.get('EE_KEY')
ee_project = os.environ.get('EE_PROJECT')
print('Local run: set $env:EE_PROJECT="snowcast-1" then python main.py')
if not ee_project:
    raise ValueError('EE_PROJECT is required. PowerShell: $env:EE_PROJECT="snowcast-1"; python main.py')
if key:
    key_json = json.loads(key)
    client_email = key_json.get('client_email')
    print(f'EE_KEY client_email: {client_email}')
    creds = ee.ServiceAccountCredentials(client_email, key_data=key)
    ee.Initialize(creds, project=ee_project)
else:
    ee.Initialize(project=ee_project)  # Local fallback

# --- 2. SETUP ---
ASSET = 'projects/gcp-public-data-weathernext/assets/weathernext_2_0_0'
WN2_Z500_BAND = '500_geopotential'
WN2_500_U_BAND = '500_u_component_of_wind'
WN2_500_V_BAND = '500_v_component_of_wind'
WN2_MSLP_BAND = 'mean_sea_level_pressure'
WN2_PRECIP_6H_BAND = 'total_precipitation_6hr'
WN2_T2M_BAND = '2m_temperature'
WN2_T850_BAND = '850_temperature'
WN2_T700_BAND = '700_temperature'
WN2_T500_BAND = '500_temperature'
WN2_500_OMEGA_BAND = '500_vertical_velocity'
WN2_10M_U_BAND = '10m_u_component_of_wind'
WN2_10M_V_BAND = '10m_v_component_of_wind'
WN2_SST_BAND = 'sea_surface_temperature'
MERRA2_CLIMO_ASSET = 'NASA/GSFC/MERRA/slv/2'
MERRA2_CLIMO_H500_BAND = 'H500'
MERRA2_CLIMO_T2M_BAND = 'T2M'
ERA5_HOURLY_CLIMO_ASSET = 'ECMWF/ERA5/HOURLY'
ERA5_DAILY_CLIMO_ASSET = 'ECMWF/ERA5/DAILY'
ERA5_DAILY_CLIMO_T2M_BAND = 'mean_2m_air_temperature'
CLIMO_START_YEAR = 1991
CLIMO_END_YEAR = 2020
CLIMO_DOY_WINDOW_DAYS = 0
OUTPUT = 'public'  # The folder that becomes the website
os.makedirs(OUTPUT, exist_ok=True)
DEBUG_BANDS = os.environ.get('DEBUG_BANDS') == '1'
TARGET_CRS = 'EPSG:4326'
NH_SOURCE_REGION = [-179.5, 20.0, 179.5, 89.0]
NH_W_BOUNDS = [-179.5, 20.0, 0.0, 89.0]
NH_E_BOUNDS = [0.0, 20.0, 179.5, 89.0]
try:
    NH_LON0 = float(os.environ.get('NH_LON0', '80.0'))
except ValueError:
    NH_LON0 = 80.0

# Regions
NH_W = ee.Geometry.Rectangle([-179.5, 20.0, 0.0, 89.5], geodesic=False)
NH_E = ee.Geometry.Rectangle([0.0, 20.0, 179.5, 89.5], geodesic=False)
NH_REGION = NH_W.union(NH_E, maxError=1)
NA_REGION = ee.Geometry.Rectangle([-170.0, 10.0, -45.0, 80.0], geodesic=False)
CONUS_REGION = ee.Geometry.Rectangle([-127.0, 22.0, -65.0, 50.0], geodesic=False)
WORLD_REGION = ee.Geometry.Rectangle([-180.0, -89.9, 180.0, 89.9], geodesic=False)
NH_THUMB_REGION = [-180.0, 8.0, 20.0, 88.0]
NA_THUMB_REGION = [-170.0, 8.0, -40.0, 82.0]
NA_Z500A_REGION = [-170.0, 16.0, -34.0, 70.0]
CONUS_THUMB_REGION = [-127.0, 22.0, -65.0, 50.0]
# Northeast-only domain (excludes VA/NC by southern boundary; caps near tip of Maine)
NE_THUMB_REGION = [-82.5, 39.2, -66.0, 47.6]
# New England zoom snowfall domain: eastern NY through New England, tuned for a tighter regional view
NE_ZOOM_SNOW_THUMB_REGION = [-76.8, 39.7, -66.4, 45.2]
MI_WI_SNOW_THUMB_REGION = [-93.8, 41.2, -82.1, 49.3]
CAROLINAS_SNOW_THUMB_REGION = [-85.9, 31.8, -74.5, 37.8]

# Boundaries overlays
COUNTRIES = ee.FeatureCollection('USDOS/LSIB_SIMPLE/2017')
COUNTRIES_BORDERS = ee.FeatureCollection('USDOS/LSIB/2017')
US_STATES = ee.FeatureCollection('TIGER/2018/States')
GLOBAL_SURFACE_WATER = ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('max_extent').unmask(0).gt(0)
NE_STATE_NAMES = [
    'Connecticut', 'Delaware', 'Maine', 'Maryland', 'Massachusetts',
    'New Hampshire', 'New Jersey', 'New York', 'Pennsylvania',
    'Rhode Island', 'Vermont'
]
NE_STATES = US_STATES.filter(ee.Filter.inList('NAME', NE_STATE_NAMES))
NE_EXCLUDED_STATES = US_STATES.filter(ee.Filter.inList('NAME', ['Virginia', 'North Carolina']))

CONUS_SNOW_LABEL_AIRPORTS = [
    ('SEA', -122.3088, 47.4502),
    ('PDX', -122.5951, 45.5898),
    ('BOI', -116.2228, 43.5644),
    ('SFO', -122.3790, 37.6213),
    ('SMF', -121.5908, 38.6951),
    ('RNO', -119.7681, 39.4986),
    ('LAX', -118.4085, 33.9416),
    ('SAN', -117.1897, 32.7338),
    ('LAS', -115.1512, 36.0840),
    ('PHX', -112.0116, 33.4343),
    ('TUS', -110.9410, 32.1161),
    ('ABQ', -106.6090, 35.0402),
    ('SLC', -111.9778, 40.7884),
    ('DEN', -104.6737, 39.8561),
    ('ELP', -106.3778, 31.8073),
    ('DFW', -97.0403, 32.8998),
    ('AUS', -97.6699, 30.1975),
    ('SAT', -98.4698, 29.5337),
    ('IAH', -95.3368, 29.9902),
    ('HOU', -95.2789, 29.6454),
    ('OKC', -97.6007, 35.3931),
    ('ICT', -97.4331, 37.6499),
    ('OMA', -95.8941, 41.3032),
    ('MCI', -94.7139, 39.2976),
    ('STL', -90.3700, 38.7487),
    ('ORD', -87.9073, 41.9742),
    ('MKE', -87.8966, 42.9472),
    ('MSP', -93.2218, 44.8848),
    ('DTW', -83.3534, 42.2162),
    ('CLE', -81.8498, 41.4117),
    ('CMH', -82.8919, 39.9980),
    ('IND', -86.2944, 39.7173),
    ('PIT', -80.2329, 40.4915),
    ('CVG', -84.6678, 39.0488),
    ('ATL', -84.4277, 33.6407),
    ('BNA', -86.6689, 36.1263),
    ('MSY', -90.2580, 29.9934),
    ('RDU', -78.7875, 35.8776),
    ('IAD', -77.4565, 38.9531),
    ('BWI', -76.6684, 39.1754),
    ('PHL', -75.2424, 39.8729),
    ('BUF', -78.7322, 42.9405),
    ('CLT', -80.9431, 35.2140),
    ('JFK', -73.7781, 40.6413),
    ('BOS', -71.0052, 42.3656),
    ('PWM', -70.3093, 43.6462),
    ('TPA', -82.5332, 27.9755),
    ('MCO', -81.3089, 28.4312),
    ('JAX', -81.6879, 30.4941),
    ('MIA', -80.2906, 25.7959),
]
NE_SNOW_LABEL_AIRPORTS = [
    ('BUF', -78.7322, 42.9405),
    ('ROC', -77.6724, 43.1189),
    ('SYR', -76.1063, 43.1112),
    ('ABE', -75.4408, 40.6521),
    ('AVP', -75.7234, 41.3385),
    ('MDT', -76.7634, 40.1935),
    ('ALB', -73.8017, 42.7483),
    ('BTV', -73.1533, 44.4719),
    ('BDL', -72.6832, 41.9389),
    ('ORH', -71.8757, 42.2673),
    ('BOS', -71.0052, 42.3656),
    ('PVD', -71.4332, 41.7240),
    ('PWM', -70.3093, 43.6462),
    ('BGR', -68.8281, 44.8074),
    ('MHT', -71.4357, 42.9326),
    ('PSM', -70.8233, 43.0779),
    ('JFK', -73.7781, 40.6413),
    ('LGA', -73.8740, 40.7769),
    ('EWR', -74.1745, 40.6895),
    ('HPN', -73.7076, 41.0670),
    ('ISP', -73.1002, 40.7952),
    ('SWF', -74.1048, 41.5041),
    ('PHL', -75.2424, 39.8729),
    ('ACY', -74.5772, 39.4576),
]
NE_ZOOM_SNOW_LABEL_AIRPORTS = [
    ('EWR', -74.1745, 40.6895),
    ('ALB', -73.8027, 42.7483),
    ('BTV', -73.1533, 44.4719),
    ('BDL', -72.6832, 41.9389),
    ('ORH', -71.8757, 42.2673),
    ('PVD', -71.4332, 41.7240),
    ('BOS', -71.0052, 42.3656),
    ('MHT', -71.4357, 42.9326),
    ('PSM', -70.8233, 43.0779),
    ('PWM', -70.3093, 43.6462),
    ('AUG', -69.7973, 44.3206),
    ('BGR', -68.8281, 44.8074),
    ('HYA', -70.2804, 41.6693),
]
MI_WI_SNOW_LABEL_AIRPORTS = [
    ('MSN', -89.3375, 43.1399),
    ('MKE', -87.8966, 42.9472),
    ('GRB', -88.1308, 44.4851),
    ('ATW', -88.5191, 44.2581),
    ('RHI', -89.4675, 45.6312),
    ('CMX', -88.4891, 47.1684),
    ('ESC', -87.0937, 45.7227),
    ('MQT', -87.3954, 46.3536),
    ('TVC', -85.5822, 44.7414),
    ('PLN', -84.7967, 45.5709),
    ('APN', -83.5603, 45.0781),
    ('GRR', -85.5228, 42.8808),
    ('LAN', -84.5874, 42.7787),
    ('DTW', -83.3534, 42.2162),
]
CAROLINAS_SNOW_LABEL_AIRPORTS = [
    ('CLT', -80.9431, 35.2140),
    ('AVL', -82.5418, 35.4362),
    ('HKY', -81.3896, 35.7411),
    ('GSO', -79.9373, 36.0978),
    ('RDU', -78.7875, 35.8776),
    ('FAY', -78.8803, 34.9912),
    ('ILM', -77.9026, 34.2706),
    ('MYR', -78.9283, 33.6797),
    ('CRE', -78.7239, 33.8117),
    ('CAE', -81.1195, 33.9388),
    ('GSP', -82.2189, 34.8956),
    ('CHS', -80.0405, 32.8986),
]


def _airport_features(airports):
    features = [ee.Feature(ee.Geometry.Point([lon, lat]), {'code': code}) for code, lon, lat in airports]
    lookup = {code: (lon, lat) for code, lon, lat in airports}
    return ee.FeatureCollection(features), lookup


CONUS_SNOW_AIRPORT_FC, CONUS_SNOW_AIRPORT_LOOKUP = _airport_features(CONUS_SNOW_LABEL_AIRPORTS)
NE_SNOW_AIRPORT_FC, NE_SNOW_AIRPORT_LOOKUP = _airport_features(NE_SNOW_LABEL_AIRPORTS)
NE_ZOOM_SNOW_AIRPORT_FC, NE_ZOOM_SNOW_AIRPORT_LOOKUP = _airport_features(NE_ZOOM_SNOW_LABEL_AIRPORTS)
MI_WI_SNOW_AIRPORT_FC, MI_WI_SNOW_AIRPORT_LOOKUP = _airport_features(MI_WI_SNOW_LABEL_AIRPORTS)
CAROLINAS_SNOW_AIRPORT_FC, CAROLINAS_SNOW_AIRPORT_LOOKUP = _airport_features(CAROLINAS_SNOW_LABEL_AIRPORTS)


def ts():
    return time.strftime('%Y-%m-%d %H:%M:%S')

# Forecast hour controls
hours_csv = os.environ.get('HOURS_CSV')
hours_step_env = os.environ.get('HOURS_STEP')
hours_max_env = os.environ.get('HOURS_MAX')
hours_limit_env = os.environ.get('HOURS_LIMIT')
run_init_utc_env = os.environ.get('RUN_INIT_UTC')
snow_ratio_csv_env = os.environ.get('SNOW_RATIO_CSV')
run_history_hours_env = os.environ.get('RUN_HISTORY_HOURS')
event_name = (os.environ.get('GITHUB_EVENT_NAME') or '').lower()
fast_render_env = os.environ.get('FAST_RENDER')
max_dimension_env = os.environ.get('WN2_MAX_DIMENSION')
geography_detail_env = os.environ.get('WN2_GEOGRAPHY_DETAIL')
product_mode_env = os.environ.get('WN2_PRODUCT_MODE')
hour_shard_index_env = os.environ.get('WN2_HOUR_SHARD_INDEX')
hour_shard_total_env = os.environ.get('WN2_HOUR_SHARD_TOTAL')
resume_existing_env = os.environ.get('WN2_RESUME_EXISTING')
skip_cleanup_run_dir_env = os.environ.get('WN2_SKIP_CLEANUP_RUN_DIR')
allow_no_products_env = os.environ.get('WN2_ALLOW_NO_PRODUCTS')
adaptive_long_range_env = os.environ.get('WN2_ADAPTIVE_LONG_RANGE')
long_range_threshold_env = os.environ.get('WN2_LONG_RANGE_THRESHOLD')
min_valid_frame_bytes_env = os.environ.get('WN2_MIN_VALID_FRAME_BYTES')
climo_window_days_env = os.environ.get('WN2_CLIMO_DOY_WINDOW_DAYS')
climo_start_year_env = os.environ.get('WN2_CLIMO_START_YEAR')
climo_end_year_env = os.environ.get('WN2_CLIMO_END_YEAR')
climo_source_env = os.environ.get('WN2_CLIMO_SOURCE')
z500_climo_baseline_env = os.environ.get('WN2_Z500_CLIMO_BASELINE')
short_range_accuracy_hours_env = os.environ.get('WN2_SHORT_RANGE_ACCURACY_HOURS')
ensemble_mode_env = os.environ.get('WN2_ENSEMBLE_MODE')
ensemble_member_env = os.environ.get('WN2_ENSEMBLE_MEMBER')
z500_style_env = os.environ.get('WN2_Z500_STYLE')
run_nh_z500a_env = os.environ.get('WN2_RUN_NH_Z500A')
run_na_z500a_env = os.environ.get('WN2_RUN_NA_Z500A')
run_conus_mslp_ptype_env = os.environ.get('WN2_RUN_CONUS_MSLP_PTYPE')
run_ne_mslp_ptype_env = os.environ.get('WN2_RUN_NE_MSLP_PTYPE')
run_conus_vort500_env = os.environ.get('WN2_RUN_CONUS_VORT500')
run_conus_snow_accum_env = os.environ.get('WN2_RUN_CONUS_SNOW_ACCUM')
run_ne_snow_accum_env = os.environ.get('WN2_RUN_NE_SNOW_ACCUM')
run_ne_zoom_snow_accum_env = os.environ.get('WN2_RUN_NE_ZOOM_SNOW_ACCUM')
run_mi_wi_snow_accum_env = os.environ.get('WN2_RUN_MI_WI_SNOW_ACCUM')
run_carolinas_snow_accum_env = os.environ.get('WN2_RUN_CAROLINAS_SNOW_ACCUM')
run_conus_t2m_env = os.environ.get('WN2_RUN_CONUS_T2M')
run_conus_t2m_anom_env = os.environ.get('WN2_RUN_CONUS_T2M_ANOM')
run_conus_wind10_env = os.environ.get('WN2_RUN_CONUS_WIND10')
run_conus_t850_env = os.environ.get('WN2_RUN_CONUS_T850')
run_conus_t500_env = os.environ.get('WN2_RUN_CONUS_T500')
run_conus_omega500_env = os.environ.get('WN2_RUN_CONUS_OMEGA500')
run_conus_sst_env = os.environ.get('WN2_RUN_CONUS_SST')
selected_products_csv_env = os.environ.get('WN2_SELECTED_PRODUCTS')
nh_render_mode_env = os.environ.get('WN2_NH_RENDER_MODE')
local_true_anom_render_env = os.environ.get('WN2_LOCAL_TRUE_ANOM_RENDER')
reconcile_only_env = os.environ.get('WN2_RECONCILE_ONLY')


def _env_flag(raw, default=False):
    if raw is None:
        return default
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def _select_product_flag(raw, default=True):
    if raw is None:
        return default
    return _env_flag(raw, default=default)


def _parse_product_keys_csv(raw):
    text = str(raw or '').strip().lower()
    if not text:
        return []
    parts = re.split(r'[\s,]+', text)
    return [p for p in parts if p]


def _normalize_baseline_key(raw):
    text = str(raw or '').strip().lower()
    return re.sub(r'[^0-9a-z]+', '', text)


def _resolve_z500_climo_years(raw, default_start_year, default_end_year):
    key = _normalize_baseline_key(raw)
    if key in ('', 'current', 'default', '19912020', '9120'):
        return 'current', int(default_start_year), int(default_end_year), False
    if key in ('19812010', '8110', 'classic', 'legacy'):
        return '1981-2010', 1981, 2010, True
    print(
        f'[{ts()}] Invalid WN2_Z500_CLIMO_BASELINE="{raw}", '
        f'defaulting to current H500 baseline {default_start_year}-{default_end_year}.'
    )
    return 'current', int(default_start_year), int(default_end_year), False


LOCAL_TRUE_ANOMALY_RENDER = _env_flag(local_true_anom_render_env, default=True)
RECONCILE_ONLY = _env_flag(reconcile_only_env, default=False)
RESUME_EXISTING = _env_flag(resume_existing_env, default=True)
SKIP_CLEANUP_RUN_DIR = _env_flag(skip_cleanup_run_dir_env, default=RESUME_EXISTING)
ALLOW_NO_PRODUCTS = _env_flag(allow_no_products_env, default=False)
ADAPTIVE_LONG_RANGE = _env_flag(adaptive_long_range_env, default=True)
NH_RENDER_MODE = str(nh_render_mode_env or 'legacy').strip().lower()
CLIMO_SOURCE = str(climo_source_env or 'era5').strip().lower()
ENSEMBLE_MODE = str(ensemble_mode_env or 'first').strip().lower()
ENSEMBLE_MEMBER = str(ensemble_member_env or '').strip()
Z500_STYLE = str(z500_style_env or 'default').strip().lower()
if NH_RENDER_MODE not in ('legacy', 'polar'):
    print(f'[{ts()}] Invalid WN2_NH_RENDER_MODE="{NH_RENDER_MODE}", defaulting to legacy.')
    NH_RENDER_MODE = 'legacy'
if CLIMO_SOURCE not in ('era5', 'merra2'):
    print(f'[{ts()}] Invalid WN2_CLIMO_SOURCE="{CLIMO_SOURCE}", defaulting to era5.')
    CLIMO_SOURCE = 'era5'
if ENSEMBLE_MODE not in ('first', 'mean', 'median', 'member'):
    print(f'[{ts()}] Invalid WN2_ENSEMBLE_MODE="{ENSEMBLE_MODE}", defaulting to first.')
    ENSEMBLE_MODE = 'first'
if ENSEMBLE_MODE == 'member' and not ENSEMBLE_MEMBER:
    print(f'[{ts()}] WN2_ENSEMBLE_MODE=member requires WN2_ENSEMBLE_MEMBER; defaulting to first.')
    ENSEMBLE_MODE = 'first'
if Z500_STYLE not in ('default', 'classic'):
    print(f'[{ts()}] Invalid WN2_Z500_STYLE="{Z500_STYLE}", defaulting to default.')
    Z500_STYLE = 'default'
USE_NH_TRUE_POLAR_RENDER = (NH_RENDER_MODE == 'polar')

FAST_RENDER = _env_flag(fast_render_env, default=(event_name == 'schedule'))


def _cap_dims_to_max(dimensions, max_px, min_px=360):
    if max_px is None:
        return dimensions
    try:
        max_px = int(max_px)
    except (TypeError, ValueError):
óNøêÚ$z{-®éÜj×W7E÷–ÆöBÒ°Ğ¢vvVæW&FVE÷WF2s¢—6õ÷WF2†æ÷u÷WF2’ÀĞ¢v†—7F÷'•ö†÷W'2s¢%Tåô„•5Dõ%•ô„õU%2ÀĞ¢vFVfVÇE÷'Våö–Bs¢&VfW'&VEöFVfVÇE÷'Våö–BÀĞ¢w6æ÷u÷&öGV7G2s¢6÷'FVB…4äõuõ$ôET5Eô´U•2’ÀĞ¢w&öGV7EöÆ&VÇ2s¢¶¶W“¢Æ&VÂf÷"¶W’ÂÆ&VÂÂòÂò–â$ôET5EôõD”ôå7ÒÀĞ¢w'Vç2s¢Öæ–fW7E÷'Vç2ÀĞ§ĞĞ§v—F‚Öæ–fW7E÷F‚æ÷Vâ‚wrrÂVæ6öF–æsÒwWFbÓ‚r’2c Ğ¢§6öâæGV×†Öæ–fW7E÷–ÆöBÂbÂ–æFVçCÓ"Ğ Ğ¦Öæ–fW7Eö§6öâÒ§6öâæGV×2†Öæ–fW7E÷–ÆöBÂ6W&F÷'3Ò‚rÂrÂs¢r’Ğ¦‡FÖÅ÷FV×ÆFRÒ"" Ğ£ÂDô5E•R‡FÖÃàĞ£Æ‡FÖÃàĞ£Æ†VCàĞ¢ÇF—FÆSåvVF†W$æW‡C"f–WvW#Â÷F—FÆSàĞ¢ÆÖWFæÖSÒ'f–Ww÷'B"6öçFVçCÒ'v–GFƒÖFWf–6R×v–GF‚Â–æ—F–Â×66ÆSÓ#àĞ¢Ç7G–ÆSàĞ¢§&ö÷B°Ğ¢ÒÖ6öçG&öÇ2Öƒ¢ƒ‡ƒ°Ğ¢ĞĞ¢&öG’°Ğ¢&6¶w&÷VæC¢3ccc°Ğ¢6öÆ÷#¢6VfVfVc°Ğ¢föçBÖfÖ–Ç“§7—7FVÒ×V’Â6ç2×6W&–c°Ğ¢FW‡BÖÆ–vã¦6VçFW#°Ğ¢Ö&v–ã£°Ğ¢FF–ærÖ&÷GFöÓ£°Ğ¢ĞĞ¢çw&°Ğ¢Ö‚×v–GFƒ£#Cƒ°Ğ¢Ö&v–ã£WFó°Ğ¢FF–æs£G‚‚6Æ2‡f"‚ÒÖ6öçG&öÇ2Ö‚’²G‚“°Ğ¢ĞĞ¢æÖ×w&°Ğ¢&6¶w&÷VæC¢3°Ğ¢&÷&FW#£‚6öÆ–B3FcFcFc°Ğ¢†V–v‡C£cfƒ°Ğ¢Ö‚Ö†V–v‡C¦6Æ2ƒf‚Òf"‚ÒÖ6öçG&öÇ2Ö‚’Ò3‚“°Ğ¢Ö–âÖ†V–v‡C£##ƒ°Ğ¢F—7Æ“¦fÆWƒ°Ğ¢Æ–vâÖ—FV×3¦6VçFW#°Ğ¢§W7F–g’Ö6öçFVçC¦6VçFW#°Ğ¢÷fW&fÆ÷s¦†–FFVã°Ğ¢ĞĞ¢–Ör°Ğ¢v–GFƒ¦WFó°Ğ¢†V–v‡C¦WFó°Ğ¢Ö‚×v–GFƒ£S°Ğ¢Ö‚Ö†V–v‡C£S°Ğ¢ö&¦V7BÖf—C¦6öçF–ã°Ğ¢F—7Æ“¦&Æö6³°Ğ¢&6¶w&÷VæC¢3°Ğ¢ĞĞ¢7F—FÆR°Ğ¢Ö&v–ã£g‚ƒ°Ğ¢föçB×6—¦S£#Gƒ°Ğ¢föçB×vV–v‡C£s°Ğ¢ÆWGFW"×76–æs£ãVÓ°Ğ¢ĞĞ¢'WGFöâ°Ğ¢FF–æs£‚gƒ°Ğ¢föçB×6—¦S£gƒ°Ğ¢7W'6÷#§ö–çFW#°Ğ¢&÷&FW#£‚6öÆ–B3ccc°Ğ¢&6¶w&÷VæC¢3&C&C&C°Ğ¢6öÆ÷#¢6ccc°Ğ¢&÷&FW"×&F—W3£‡ƒ°Ğ¢ĞĞ¢6VÆV7B°Ğ¢FF–æs£‡‚ƒ°Ğ¢föçB×6—¦S£Wƒ°Ğ¢&÷&FW"×&F—W3£‡ƒ°Ğ¢&÷&FW#£‚6öÆ–B3ccc°Ğ¢&6¶w&÷VæC¢3&&&°Ğ¢6öÆ÷#¢6ccc°Ğ¢Ö–â×v–GFƒ£#ƒ°Ğ¢ĞĞ¢6Æ&VÂ°Ğ¢F—7Æ“¦–æÆ–æRÖ&Æö6³°Ğ¢Ö–â×v–GFƒ£#ƒ°Ğ¢föçB×vV–v‡C£s°Ğ¢ÆWGFW"×76–æs£ã6VÓ°Ğ¢ĞĞ¢æ&÷GFöÒÖ6öçG&öÇ2°Ğ¢÷6—F–öã¦f—†VC°Ğ¢ÆVgC£°Ğ¢&–v‡C£°Ğ¢&÷GFöÓ£°Ğ¢&6¶w&÷VæC¢3CCC°Ğ¢&÷&FW"×F÷£‚6öÆ–B3636363°Ğ¢FF–æs£‚‚6Æ2ƒ'‚²Vçb‡6fRÖ&VÖ–ç6WBÖ&÷GFöÒ’“°Ğ¢&÷‚×6†F÷s£Óg‚g‚&v&ƒÂÂÂã3R“°Ğ¢ĞĞ¢ç&÷r°Ğ¢Ö‚×v–GFƒ£#Cƒ°Ğ¢Ö&v–ã£WFó°Ğ¢F—7Æ“¦fÆWƒ°Ğ¢Æ–vâÖ—FV×3¦6VçFW#°Ğ¢§W7F–g’Ö6öçFVçC¦6VçFW#°Ğ¢fÆW‚×w&§w&°Ğ¢v£ƒ°Ğ¢ĞĞ¢6†÷W%6Æ–FW"°Ğ¢v–GFƒ¦Ö–âƒ“c‚Â“Ggr“°Ğ¢†V–v‡C£3Gƒ°Ğ¢F÷V6‚Ö7F–öã§â×ƒ°Ğ¢ĞĞ¢ÖVF–†Ö‚×v–GFƒ¢sc‚’°Ğ¢7F—FÆR²föçB×6—¦S£#ƒ²Ö&v–ã£‡‚ƒ²ĞĞ¢'WGFöâ²föçB×6—¦S£Gƒ²FF–æs£‡‚'ƒ²ĞĞ¢6VÆV7B²föçB×6—¦S£Gƒ²Ö–â×v–GFƒ£“gƒ²ĞĞ¢6Æ&VÂ²Ö–â×v–GFƒ£ƒgƒ²ĞĞ¢æ&÷GFöÒÖ6öçG&öÇ2²FF–æs£‚‡‚6Æ2ƒG‚²Vçb‡6fRÖ&VÖ–ç6WBÖ&÷GFöÒ’“²ĞĞ¢ç&÷r²v£‡ƒ²ĞĞ¢6†÷W%6Æ–FW"²v–GFƒ¦Ö–âƒ“c‚Â“ggr“²ĞĞ¢æÖ×w&°Ğ¢†V–v‡C£S'fƒ°Ğ¢Ö–âÖ†V–v‡C£cƒ°Ğ¢Ö‚Ö†V–v‡C¦6Æ2ƒf‚Òf"‚ÒÖ6öçG&öÇ2Ö‚’Ò‡‚“°Ğ¢ĞĞ¢ĞĞ¢Â÷7G–ÆSàĞ£Âö†VCàĞ£Æ&öG“àĞ¢ÆF—b6Æ73Ò'w&#àĞ¢Æƒ"–CÒ'F—FÆR#åvVF†W$æW‡C"f–WvW#Âöƒ#àĞ¢ÆF—b6Æ73Ò&Ö×w&#àĞ¢Æ–Ör–CÒ&Ö"7&3Ò""ÇCÒ%tã"Ö#àĞ¢ÂöF—càĞ¢ÂöF—càĞ Ğ¢ÆF—b6Æ73Ò&&÷GFöÒÖ6öçG&öÇ2#àĞ¢ÆF—b6Æ73Ò'&÷r#àĞ¢ÆÆ&VÂf÷#Ò''Vâ#å'Vã£ÂöÆ&VÃàĞ¢Ç6VÆV7B–CÒ''Vâ#ãÂ÷6VÆV7CàĞ¢ÆÆ&VÂf÷#Ò'&öGV7B#äÖ£ÂöÆ&VÃàĞ¢Ç6VÆV7B–CÒ'&öGV7B#ãÂ÷6VÆV7CàĞ¢ÆÆ&VÂf÷#Ò'6æ÷u&F–ò#å6æ÷r&F–ó£ÂöÆ&VÃàĞ¢Ç6VÆV7B–CÒ'6æ÷u&F–ò#ãÂ÷6VÆV7CàĞ¢ÂöF—càĞ¢ÆF—b6Æ73Ò'&÷r"7G–ÆSÒ&Ö&v–â×F÷£‡ƒ²#àĞ¢Æ'WGFöâ–CÒ'&Wd'Fâ#å&WcÂö'WGFöãàĞ¢Ç7â–CÒ&Æ&VÂ#ä†÷W"ÒÒÓÂ÷7ãàĞ¢Æ'WGFöâ–CÒ&æW‡D'Fâ#äæW‡CÂö'WGFöãàĞ¢ÂöF—càĞ¢ÆF—b6Æ73Ò'&÷r"7G–ÆSÒ&Ö&v–â×F÷£gƒ²#àĞ¢Æ–çWBG—SÒ'&ævR"–CÒ&†÷W%6Æ–FW""Ö–ãÒ#"ÖƒÒ#"7FWÒ#"fÇVSÒ##àĞ¢ÂöF—càĞ¢ÂöF—càĞ Ğ¢Ç67&—CàĞ¢6öç7BÖæ–fW7BÒõôÔä”dU5Eô¥4ôåõó°Ğ¢6öç7B'Vç2Ò'&’æ—4'&’†Öæ–fW7Bç'Vç2’òÖæ–fW7Bç'Vç2¢µÓ°Ğ¢6öç7B6æ÷u&öGV7G2ÒæWr6WB„'&’æ—4'&’†Öæ–fW7Bç6æ÷u÷&öGV7G2’òÖæ–fW7Bç6æ÷u÷&öGV7G2¢µÒ“°Ğ¢6öç7B&öGV7DÆ&VÇ2Ò†Öæ–fW7Bç&öGV7EöÆ&VÇ2bbG—VöbÖæ–fW7Bç&öGV7EöÆ&VÇ2ÓÓÒvö&¦V7Br’òÖæ–fW7Bç&öGV7EöÆ&VÇ2¢·Ó°Ğ¢ÆWB7F—fT†÷W'2ÒµÓ°Ğ¢ÆWB–G‚Ò°Ğ Ğ¢6öç7BÖVÂÒFö7VÖVçBævWDVÆVÖVçD'”–B‚vÖr“°Ğ¢6öç7BF—FÆTVÂÒFö7VÖVçBævWDVÆVÖVçD'”–B‚wF—FÆRr“°Ğ¢6öç7BÖw&VÂÒFö7VÖVçBçVW'•6VÆV7F÷"‚ræÖ×w&r“°Ğ¢6öç7B6öçG&öÇ4VÂÒFö7VÖVçBçVW'•6VÆV7F÷"‚ræ&÷GFöÒÖ6öçG&öÇ2r“°Ğ¢6öç7B&ö÷DVÂÒFö7VÖVçBæFö7VÖVçDVÆVÖVçC°Ğ¢6öç7BÆ&VÄVÂÒFö7VÖVçBævWDVÆVÖVçD'”–B‚vÆ&VÂr“°Ğ¢6öç7B'VäVÂÒFö7VÖVçBævWDVÆVÖVçD'”–B‚w'Vâr“°Ğ¢6öç7B&öGV7DVÂÒFö7VÖVçBævWDVÆVÖVçD'”–B‚w&öGV7Br“°Ğ¢6öç7B&F–ôVÂÒFö7VÖVçBævWDVÆVÖVçD'”–B‚w6æ÷u&F–òr“°Ğ¢6öç7B6Æ–FW$VÂÒFö7VÖVçBævWDVÆVÖVçD'”–B‚v†÷W%6Æ–FW"r“°Ğ¢6öç7B&Wd'FâÒFö7VÖVçBævWDVÆVÖVçD'”–B‚w&Wd'Fâr“°Ğ¢6öç7BæW‡D'FâÒFö7VÖVçBævWDVÆVÖVçD'”–B‚væW‡D'Fâr“°Ğ Ğ¢gVæ7F–öâæ÷&ÖÆ—¦T–çDÆ—7B‡fÇVW2ÂÖ–åfÇVRÂÖ…fÇVR’°Ğ¢–b‚'&’æ—4'&’‡fÇVW2’’&WGW&âµÓ°Ğ¢6öç7B÷WBÒµÓ°Ğ¢f÷"†6öç7B&röbfÇVW2’°Ğ¢6öç7BâÒçVÖ&W"‡&r“°Ğ¢–b‚çVÖ&W"æ—4–çFVvW"†â’’6öçF–çVS°Ğ¢–b†Ö–åfÇVRÓÒçVÆÂbbâÂÖ–åfÇVR’6öçF–çVS°Ğ¢–b†Ö…fÇVRÓÒçVÆÂbbââÖ…fÇVR’6öçF–çVS°Ğ¢÷WBçW6‚†â“°Ğ¢ĞĞ¢&WGW&â'&’æg&öÒ†æWr6WB†÷WB’’ç6÷'B‚†Â"’ÓâÒ"“°Ğ¢ĞĞ Ğ¢gVæ7F–öâvWD7W'&VçE'Vâ‚’°Ğ¢6öç7B6VÆV7FVBÒ'VäVÂçfÇVS°Ğ¢f÷"†6öç7B'Vâöb'Vç2’°Ğ¢–b…7G&–ær‡'Vâæ–B’ÓÓÒ6VÆV7FVB’&WGW&â'Vã°Ğ¢ĞĞ¢&WGW&â'Vç2æÆVæwF‚ò'Vç5³Ò¢çVÆÃ°Ğ¢ĞĞ Ğ¢gVæ7F–öâ6WE6VÆV7D÷F–öç2‡6VÆV7DVÂÂfÇVW2ÂÆ&VÄfâ’°Ğ¢6öç7B&WbÒ6VÆV7DVÂçfÇVS°Ğ¢6VÆV7DVÂæ–ææW$…DÔÂÒrs°Ğ¢f÷"†6öç7BfÇVRöbfÇVW2’°Ğ¢6öç7B÷F–öâÒFö7VÖVçBæ7&VFTVÆVÖVçB‚v÷F–öâr“°Ğ¢÷F–öâçfÇVRÒ7G&–ær‡fÇVR“°Ğ¢÷F–öâçFW‡D6öçFVçBÒÆ&VÄfâ‡fÇVR“°Ğ¢6VÆV7DVÂæVæD6†–ÆB†÷F–öâ“°Ğ¢ĞĞ¢f÷"†6öç7BfÇVRöbfÇVW2’°Ğ¢–b…7G&–ær‡fÇVR’ÓÓÒ&Wb’°Ğ¢6VÆV7DVÂçfÇVRÒ&Wc°Ğ¢&WGW&ã°Ğ¢ĞĞ¢ĞĞ¢–b‡fÇVW2æÆVæwF‚’°Ğ¢6VÆV7DVÂçfÇVRÒ7G&–ær‡fÇVW5³Ò“°Ğ¢ĞĞ¢ĞĞ Ğ¢gVæ7F–öâ'V–ÆDg&ÖTæÖR‡&öGV7BÂ†÷W"Â&F–ò’°Ğ¢6öç7B†÷W%7G"Ò7G&–ær††÷W"’çE7F'Bƒ2Âsr“°Ğ¢–b‡6æ÷u&öGV7G2æ†2‡&öGV7B’’°Ğ¢6öç7B&F–õ7G"Ò7G&–ær‡&F–ò’çE7F'Bƒ"Âsr“°Ğ¢&WGW&â&öGV7B²u÷"r²&F–õ7G"²uòr²†÷W%7G"²ræ§rs°Ğ¢ĞĞ¢&WGW&â&öGV7B²uòr²†÷W%7G"²ræ§rs°Ğ¢ĞĞ Ğ¢gVæ7F–öâvWE&öGV7D†÷W'2‡'VâÂ&öGV7B’°Ğ¢–b‚'VâÇÂ&öGV7B’&WGW&âµÓ°Ğ¢6öç7B66÷VBÒ‡'Vâç&öGV7Eö†÷W'2bbG—Vöb'Vâç&öGV7Eö†÷W'2ÓÓÒvö&¦V7BrĞ¢ò'Vâç&öGV7Eö†÷W'5·&öGV7EĞĞ¢¢çVÆÃ°Ğ¢6öç7B66÷VD†÷W'2Òæ÷&ÖÆ—¦T–çDÆ—7B‡66÷VBÂÂçVÆÂ“°Ğ¢–b‡66÷VD†÷W'2æÆVæwF‚’&WGW&â66÷VD†÷W'3°Ğ¢&WGW&âæ÷&ÖÆ—¦T–çDÆ—7B‡'Vâæ†÷W'2ÂÂçVÆÂ“°Ğ¢ĞĞ Ğ¢gVæ7F–öâvWE&öGV7E&F–÷2‡'VâÂ&öGV7B’°Ğ¢–b‚'VâÇÂ&öGV7B’&WGW&âµÓ°Ğ¢6öç7B66÷VBÒ‡'Vâç&öGV7E÷6æ÷u÷&F–÷2bbG—Vöb'Vâç&öGV7E÷6æ÷u÷&F–÷2ÓÓÒvö&¦V7BrĞ¢ò'Vâç&öGV7E÷6æ÷u÷&F–÷5·&öGV7EĞĞ¢¢çVÆÃ°Ğ¢6öç7B66÷VE&F–÷2Òæ÷&ÖÆ—¦T–çDÆ—7B‡66÷VBÂÂ#“°Ğ¢–b‡66÷VE&F–÷2æÆVæwF‚’&WGW&â66÷VE&F–÷3°Ğ¢&WGW&âæ÷&ÖÆ—¦T–çDÆ—7B‡'Vâç6æ÷u÷&F–÷2ÂÂ#“°Ğ¢ĞĞ Ğ¢gVæ7F–öâ7–æ5&öGV7E66÷VD6öçG&öÇ2‚’°Ğ¢6öç7B'VâÒvWD7W'&VçE'Vâ‚“°Ğ¢–b‚'Vâ’°Ğ¢7F—fT†÷W'2ÒµÓ°Ğ¢6Æ–FW$VÂæÖ‚Òss°Ğ¢6Æ–FW$VÂçfÇVRÒss°Ğ¢–G‚Ò°Ğ¢&WGW&ã°Ğ¢ĞĞ¢6öç7B&öGV7BÒ&öGV7DVÂçfÇVS°Ğ¢6öç7B—56æ÷rÒ6æ÷u&öGV7G2æ†2‡&öGV7B“°Ğ¢6öç7B&F–÷2Ò—56æ÷ròvWE&öGV7E&F–÷2‡'VâÂ&öGV7B’¢µÓ°Ğ¢6WE6VÆV7D÷F–öç2‡&F–ôVÂÂ&F–÷2æÆVæwF‚ò&F–÷2¢³ÒÂ‡&F–ò’Óâ&F–ò²s£r“°Ğ¢6öç7B&Wf–÷W4†÷W"Ò7F—fT†÷W'5¶–G…Ó°Ğ¢7F—fT†÷W'2ÒvWE&öGV7D†÷W'2‡'VâÂ&öGV7B“°Ğ¢–b†7F—fT†÷W'2æÆVæwF‚ÓÓÒ’°Ğ¢–G‚Ò°Ğ¢ÒVÇ6R°Ğ¢6öç7Bf÷VæBÒ7F—fT†÷W'2æ–æFW„öb‡&Wf–÷W4†÷W"“°Ğ¢–G‚Òf÷VæBãÒòf÷VæB¢°Ğ¢ĞĞ¢6Æ–FW$VÂæÖ‚Ò7G&–ær„ÖF‚æÖ‚ƒÂ7F—fT†÷W'2æÆVæwF‚Ò’“°Ğ¢6Æ–FW$VÂçfÇVRÒ7G&–ær†–G‚“°Ğ¢ĞĞ Ğ¢gVæ7F–öâ7–æ5'Vå66÷VD6öçG&öÇ2‚’°Ğ¢6öç7B'VâÒvWD7W'&VçE'Vâ‚“°Ğ¢–b‚'Vâ’°Ğ¢7F—fT†÷W'2ÒµÓ°Ğ¢&öGV7DVÂæ–ææW$…DÔÂÒrs°Ğ¢&F–ôVÂæ–ææW$…DÔÂÒrs°Ğ¢6Æ–FW$VÂæÖ‚Òss°Ğ¢6Æ–FW$VÂçfÇVRÒss°Ğ¢–G‚Ò°Ğ¢&WGW&ã°Ğ¢ĞĞ¢6öç7B&öGV7G2Ò'&’æ—4'&’‡'Vâç&öGV7G2’ò'Vâç&öGV7G2¢µÓ°Ğ¢6WE6VÆV7D÷F–öç2‡&öGV7DVÂÂ&öGV7G2Â†¶W’’Óâ&öGV7DÆ&VÇ5¶¶W•ÒÇÂ¶W’“°Ğ¢7–æ5&öGV7E66÷VD6öçG&öÇ2‚“°Ğ¢ĞĞ Ğ¢gVæ7F–öâf–Ww÷'D†V–v‡B‚’°Ğ¢–b‡v–æF÷rçf—7VÅf–Ww÷'BbbçVÖ&W"æ—4f–æ—FR‡v–æF÷rçf—7VÅf–Ww÷'Bæ†V–v‡B’’°Ğ¢&WGW&âÖF‚æfÆö÷"‡v–æF÷rçf—7VÅf–Ww÷'Bæ†V–v‡B“°Ğ¢ĞĞ¢&WGW&âÖF‚æfÆö÷"‡v–æF÷ræ–ææW$†V–v‡BÇÂFö7VÖVçBæFö7VÖVçDVÆVÖVçBæ6Æ–VçD†V–v‡BÇÂƒ“°Ğ¢ĞĞ Ğ¢gVæ7F–öâ7–æ4&÷GFöÔ–ç6WB‚’°Ğ¢6öç7B‚Ò6öçG&öÇ4VÂòÖF‚æ6V–Â†6öçG&öÇ4VÂævWD&÷VæF–æt6Æ–VçE&V7B‚’æ†V–v‡B’¢°Ğ¢&ö÷DVÂç7G–ÆRç6WE&÷W'G’‚rÒÖ6öçG&öÇ2Ö‚rÂ7G&–ær„ÖF‚æÖ‚ƒÂ‚’’²w‚r“°Ğ¢Fö7VÖVçBæ&öG’ç7G–ÆRçFF–æt&÷GFöÒÒ7G&–ær†‚²B’²w‚s°Ğ¢–b†Öw&VÂ’°Ğ¢6öç7B—4Öö&–ÆRÒv–æF÷ræÖF6„ÖVF–‚r†Ö‚×v–GFƒ¢sc‚’r’æÖF6†W3°Ğ¢6öç7BÖF÷ÒÖF‚æÖ‚ƒÂÖF‚æ6V–Â†Öw&VÂævWD&÷VæF–æt6Æ–VçE&V7B‚’çF÷’“°Ğ¢6öç7Bf‚ÒÖF‚æÖ‚ƒ3#Âf–Ww÷'D†V–v‡B‚’“°Ğ¢6öç7BVFvTvÒ—4Öö&–ÆRòB¢#°Ğ¢6öç7BÖ–äÖ†V–v‡BÒ—4Öö&–ÆRòc¢##°Ğ¢6öç7BF&vWD†V–v‡BÒÖF‚æÖ‚†Ö–äÖ†V–v‡BÂf‚Ò‚ÒÖF÷ÒVFvTv“°Ğ¢Öw&VÂç7G–ÆRæ†V–v‡BÒ7G&–ær‡F&vWD†V–v‡B’²w‚s°Ğ¢ĞĞ¢ĞĞ Ğ¢gVæ7F–öâ&VæFW"‚’°Ğ¢6öç7B'VâÒvWD7W'&VçE'Vâ‚“°Ğ¢–b‚'VâÇÂ7F—fT†÷W'2æÆVæwF‚ÇÂ&öGV7DVÂçfÇVR’°Ğ¢ÖVÂç7&2Òrs°Ğ¢Æ&VÄVÂæ–ææW%FW‡BÒtæò†÷W'2s°Ğ¢F—FÆTVÂæ–ææW%FW‡BÒuvVF†W$æW‡C"f–WvW"s°Ğ¢7–æ4&÷GFöÔ–ç6WB‚“°Ğ¢&WGW&ã°Ğ¢ĞĞ Ğ¢6öç7B&öGV7BÒ&öGV7DVÂçfÇVS°Ğ¢6öç7B—56æ÷rÒ6æ÷u&öGV7G2æ†2‡&öGV7B“°Ğ¢&F–ôVÂæF—6&ÆVBÒ—56æ÷s°Ğ¢–b‚—56æ÷r’°Ğ¢&F–ôVÂçF—FÆRÒu6æ÷r&F–òÆ–W2Fò6æ÷vfÆÂ67V×VÆF–öâÖ2öæÇ’âs°Ğ¢ÒVÇ6R°Ğ¢&F–ôVÂçF—FÆRÒrs°Ğ¢6öç7B&F–ô÷F–öç2ÒvWE&öGV7E&F–÷2‡'VâÂ&öGV7B“°Ğ¢–b‡&F–ô÷F–öç2æÆVæwF‚bb&F–ô÷F–öç2æ–æ6ÇVFW2„çVÖ&W"‡&F–ôVÂçfÇVR’’’°Ğ¢&F–ôVÂçfÇVRÒ7G&–ær‡&F–ô÷F–öç5³Ò“°Ğ¢ĞĞ¢ĞĞ Ğ¢6öç7B†÷W"Ò7F—fT†÷W'5¶–G…Ó°Ğ¢6öç7B&F–òÒçVÖ&W"‡&F–ôVÂçfÇVRÇÂ“°Ğ¢6öç7Bg&ÖTæÖRÒ'V–ÆDg&ÖTæÖR‡&öGV7BÂ†÷W"Â&F–ò“°Ğ¢6öç7B'Vä–BÒ7G&–ær‡'Vâæ–B“°Ğ¢6öç7B†÷W%7G"Ò7G&–ær††÷W"’çE7F'Bƒ2Âsr“°Ğ Ğ¢ÖVÂç7&2Òw'Vç2òr²'Vä–B²ròr²g&ÖTæÖS°Ğ¢ÖVÂæÇBÒ'Vä–B²rr²&öGV7B²r†÷W"r²†÷W%7G#°Ğ¢Æ&VÄVÂæ–ææW%FW‡BÒt†÷W"r²†÷W%7G#°Ğ¢F—FÆTVÂæ–ææW%FW‡BÒuvVF†W$æW‡C"f–WvW"s°Ğ¢7–æ4&÷GFöÔ–ç6WB‚“°Ğ¢ĞĞ Ğ¢gVæ7F–öâ6†ævR†F—"’°Ğ¢–b‚7F—fT†÷W'2æÆVæwF‚’&WGW&ã°Ğ¢–G‚Ò†–G‚²F—"²7F—fT†÷W'2æÆVæwF‚’R7F—fT†÷W'2æÆVæwFƒ°Ğ¢6Æ–FW$VÂçfÇVRÒ7G&–ær†–G‚“°Ğ¢&VæFW"‚“°Ğ¢ĞĞ Ğ¢'VäVÂæFDWfVçDÆ—7FVæW"‚v6†ævRrÂ‚’Óâ°Ğ¢7–æ5'Vå66÷VD6öçG&öÇ2‚“°Ğ¢&VæFW"‚“°Ğ¢Ò“°Ğ¢&öGV7DVÂæFDWfVçDÆ—7FVæW"‚v6†ævRrÂ‚’Óâ°Ğ¢7–æ5&öGV7E66÷VD6öçG&öÇ2‚“°Ğ¢&VæFW"‚“°Ğ¢Ò“°Ğ¢&F–ôVÂæFDWfVçDÆ—7FVæW"‚v6†ævRrÂ‚’Óâ°Ğ¢7–æ5&öGV7E66÷VD6öçG&öÇ2‚“°Ğ¢&VæFW"‚“°Ğ¢Ò“°Ğ¢6Æ–FW$VÂæFDWfVçDÆ—7FVæW"‚v–çWBrÂ‚’Óâ°Ğ¢–G‚ÒçVÖ&W"‡6Æ–FW$VÂçfÇVR“°Ğ¢&VæFW"‚“°Ğ¢Ò“°Ğ¢&Wd'FâæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚’Óâ6†ævR‚Ó’“°Ğ¢æW‡D'FâæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚’Óâ6†ævRƒ’“°Ğ¢ÖVÂæFDWfVçDÆ—7FVæW"‚vÆöBrÂ7–æ4&÷GFöÔ–ç6WB“°Ğ Ğ¢6WE6VÆV7D÷F–öç2‡'VäVÂÂ'Vç2æÖ‚‡'Vâ’Óâ'Vâæ–B’Â†–B’Óâ°Ğ¢6öç7B'VâÒ'Vç2æf–æB‚†—FVÒ’Óâ7G&–ær†—FVÒæ–B’ÓÓÒ7G&–ær†–B’“°Ğ¢&WGW&â'Vâò‡'VâæÆ&VÂÇÂ7G&–ær†–B’’¢7G&–ær†–B“°Ğ¢Ò“°Ğ¢–b‡'Vç2æÆVæwF‚’°Ğ¢6öç7BFVfVÇD–BÒ'Vç2ç6öÖR‚‡'Vâ’Óâ7G&–ær‡'Vâæ–B’ÓÓÒ7G&–ær†Öæ–fW7BæFVfVÇE÷'Våö–B’Ğ¢ò7G&–ær†Öæ–fW7BæFVfVÇE÷'Våö–BĞ¢¢7G&–ær‡'Vç5³Òæ–B“°Ğ¢'VäVÂçfÇVRÒFVfVÇD–C°Ğ¢ĞĞ¢v–æF÷ræFDWfVçDÆ—7FVæW"‚w&W6—¦RrÂ7–æ4&÷GFöÔ–ç6WB“°Ğ¢v–æF÷ræFDWfVçDÆ—7FVæW"‚v÷&–VçFF–öæ6†ævRrÂ7–æ4&÷GFöÔ–ç6WB“°Ğ¢–b‡v–æF÷rçf—7VÅf–Ww÷'B’°Ğ¢v–æF÷rçf—7VÅf–Ww÷'BæFDWfVçDÆ—7FVæW"‚w&W6—¦RrÂ7–æ4&÷GFöÔ–ç6WB“°Ğ¢ĞĞ¢7–æ5'Vå66÷VD6öçG&öÇ2‚“°Ğ¢7–æ4&÷GFöÔ–ç6WB‚“°Ğ¢&VæFW"‚“°Ğ¢Â÷67&—CàĞ£Âö&öG“àĞ£Âö‡FÖÃàĞ¢"" Ğ¦‡FÖÂÒ‡FÖÅ÷FV×ÆFRç&WÆ6R‚uõôÔä”dU5Eô¥4ôåõòrÂÖæ–fW7Eö§6öâĞ Ğ§v—F‚÷Vâ†bw´õUEUGÒö–æFW‚æ‡FÖÂrÂwrrÂVæ6öF–æsÒwWFbÓ‚r’2c Ğ¢bçw&—FR†‡FÖÂĞ Ğ 