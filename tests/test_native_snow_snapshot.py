"""Validate the actual released arrays/assets, not just renderer label presence."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from validate_native_snow_snapshot import validate

if __name__ == '__main__':
    validate(Path(__file__).resolve().parents[1] / 'public/seasonal')
