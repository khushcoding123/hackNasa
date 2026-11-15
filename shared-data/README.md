# Shared data: Haworth terrain + trained coverage model

The seven `moonmesh-shared-data-haworth.zip.00N` files are one 332 MB zip, split because GitHub
rejects files over 100 MB. They come with a normal `git clone` or `git pull` (no Git LFS needed). The zip
holds everything needed to run Surface coverage, the experimental ML comparison,
`training/scripts/try_model.py` and the Haworth demo without re-running the training:

- NASA LOLA PGDA 90 Haworth DEM (20 m) and the terrain features the model uses
- the trained LightGBM models (LOS, margin, clearance) and their metadata
- the final evaluation decision and metrics, acceptance checklist, model and dataset reports
- the offline Haworth demo bundle

All radio results are simulated. The LightGBM model is experimental and was **not** approved by the
final evaluation, which ships the deterministic physics map. Not included: the 11 GB training dataset
and the raw NASA/LunarRF downloads, which are only needed to retrain from scratch
(see `docs/CODEX_PROMPTS.md`).

You need Python 3.13, [uv](https://docs.astral.sh/uv/) and Node.js 22. The first start also downloads the
app's own NASA data (`python -m moonmesh.data`), so it needs internet.

## Windows (PowerShell, from the hackNasa folder)

1. Rebuild and unpack the zip into `C:\moonmesh-data` (it checks the SHA-256 first):

   ```powershell
   powershell -ExecutionPolicy Bypass -File shared-data\unpack.ps1
   ```

   To use another folder, add `-DataRoot D:\your\folder` and use that folder below.

2. Once, create the training environment (`requirements-dev.txt` includes `requirements.txt` plus the test tools):

   ```powershell
   uv venv --python 3.13 C:\moonmesh-data\venv
   uv pip install --python C:\moonmesh-data\venv\Scripts\python.exe -r requirements-dev.txt -r requirements-training.txt -e .
   ```

3. Every time, start the app:

   ```powershell
   $env:MOONMESH_DATA_ROOT = "C:\moonmesh-data"
   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
   ```

## macOS / Linux (Terminal, from the hackNasa folder)

1. Rebuild and unpack the zip into `hackNasa/data`, where the app looks by default (it checks the SHA-256 first):

   ```bash
   python3 shared-data/unpack.py
   ```

   This also works on Windows. To use another folder, add `--data-root /your/folder` and
   `export MOONMESH_DATA_ROOT=/your/folder` before the steps below.

2. Once, create the training environment (`requirements-dev.txt` includes `requirements.txt` plus the test tools):

   ```bash
   uv venv --python 3.13 data/venv
   uv pip install --python data/venv/bin/python -r requirements-dev.txt -r requirements-training.txt -e .
   ```

   On a Mac, LightGBM also needs OpenMP: `brew install libomp`.

3. Every time, start the app:

   ```bash
   bash scripts/start.sh
   ```

## Use the model in the app

Open http://127.0.0.1:8765, open the menu, choose **Surface coverage** and click **Calculate surface coverage**.
Then scroll to **Experimental: compare with your ML model** and click **Compare with ML model**. At the full
8 km range it takes about 30 seconds to 2 minutes; the first run after starting the app is the slowest. Keep the range at 8 km for scores comparable with the evaluation: short
ranges contain little dead area, so a few pixels swing the percentages (at 3 km around the research point the
dead area is about 0.2 km²).

The model runs anywhere its training and test towers could have been placed: Haworth, the MOONMESH 2.4 GHz
profile, TX 5–50 m, RX 2 m, range up to 8 km, and a transmitter whose whole 8 km range is on the prepared terrain.
Elsewhere the app explains why it refuses.

## Check that it works

```bash
data/venv/bin/python -m pytest training/tests/test_real_model.py -v
# Windows: $env:MOONMESH_DATA_ROOT = "C:\moonmesh-data"; C:\moonmesh-data\venv\Scripts\python.exe -m pytest training\tests\test_real_model.py -v
```

These tests load the real model files and run them through the app's routes. One of them reproduces the published
result for a held-out test scenario. They skip, with instructions, if the data is not unpacked or
`MOONMESH_DATA_ROOT` points elsewhere.

SHA-256 of the joined zip: `a85982563c8ba0bd2624a3e2cb7c907f9affb388cc61eb2a5d439d3b25e1ebf2`.
