# Shared data: Haworth terrain + trained coverage model

The seven `moonmesh-shared-data-haworth.zip.00N` files are one 332 MB zip, split because GitHub
rejects files over 100 MB. It holds everything needed to run Surface coverage, the experimental
ML comparison, `training/scripts/try_model.py` and the Haworth demo without re-running the training:

- NASA LOLA PGDA 90 Haworth DEM (20 m) and the terrain features the model uses
- the trained LightGBM models (LOS, margin, clearance) and their metadata
- the final evaluation decision and metrics, acceptance checklist, model and dataset reports
- the offline Haworth demo bundle

All radio results are simulated. The LightGBM model is experimental and was **not** approved by the
final evaluation, which ships the deterministic physics map. Not included: the 11 GB training dataset
and the raw NASA/LunarRF downloads, which are only needed to retrain from scratch
(see `docs/CODEX_PROMPTS.md`).

## Set up (Windows PowerShell, from the hackNasa folder)

1. Rebuild and unpack the zip into `C:\moonmesh-data` (it checks the SHA-256 first):

   ```powershell
   powershell -ExecutionPolicy Bypass -File shared-data\unpack.ps1
   ```

   To use another folder, add `-DataRoot D:\your\folder` and use that folder below.

2. Once, create the training environment:

   ```powershell
   uv venv --python 3.13 C:\moonmesh-data\venv
   uv pip install --python C:\moonmesh-data\venv\Scripts\python.exe -r requirements.txt -r requirements-training.txt -e .
   ```

3. Every time, start the app:

   ```powershell
   $env:MOONMESH_DATA_ROOT = "C:\moonmesh-data"
   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
   ```

4. Open http://127.0.0.1:8765, open the menu, choose **Surface coverage**, click **Calculate**, then
   scroll to **Compare with ML model** (about 5 minutes at 8 km; set Range to 3000 for a quick test).

SHA-256 of the joined zip: `a85982563c8ba0bd2624a3e2cb7c907f9affb388cc61eb2a5d439d3b25e1ebf2`.
