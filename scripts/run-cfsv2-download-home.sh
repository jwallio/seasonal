#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv-cfsv2-download
.venv-cfsv2-download/bin/python -m pip install numpy requests eccodes
exec .venv-cfsv2-download/bin/python scripts/cfsv2_download_campaign.py --checkpoint-dir "$HOME/.local/share/seasonal-cfsv2-downloads" "$@"
