#!/usr/bin/env bash
# macOS / Linux equivalent of scripts/start.ps1: build the frontend, fetch the app's NASA data, start MOONMESH.
# Run from anywhere: bash scripts/start.sh   then open http://127.0.0.1:8765
set -euo pipefail
cd "$(dirname "$0")/.."
data_root="${MOONMESH_DATA_ROOT:-data}"
# Surface coverage and the ML comparison need the training venv (rasterio, numba, lightgbm); use it when present.
if [ -x "$data_root/venv/bin/python" ]; then
    py="$data_root/venv/bin/python"
else
    py=".moonmesh-data/venv/bin/python"
    [ -x "$py" ] || uv venv --python 3.13 .moonmesh-data/venv
fi
uv pip install --python "$py" -r requirements.txt
npm ci
npm run build
"$py" -m moonmesh.data
exec "$py" -m uvicorn moonmesh.server:app --host 127.0.0.1 --port 8765
