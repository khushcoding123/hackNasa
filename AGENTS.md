# Standing rules

- MOONMESH_TERRAIN_COVERAGE_TRAINING.md (repo root) is the spec. Follow it. docs/CODEX_PROMPTS.md lists the tasks.
- There is no trained model in this repo yet. Everything is built from scratch in these prompts: the label generator (Prompts 5-6), the dataset (Prompt 9), the first model (LightGBM, Prompt 10) and an optional neural network (U-Net, Prompt 13). Never look for, download or assume pretrained weights. The existing moonmesh/ and lunar_core/ code is deterministic physics, not ML: use it as the reference our new code is checked against.
- Python for training work is the uv venv at C:\moonmesh-data\venv (Python 3.13). Run Python and pytest with C:\moonmesh-data\venv\Scripts\python.exe. Install packages with `uv pip install --python C:\moonmesh-data\venv\Scripts\python.exe ...`. Never install training packages into the app's .moonmesh-data\venv.
- The app's existing tests must keep passing: `python -m unittest discover -s tests -v`. New training tests use pytest under training/tests/.
- .moonmesh-data/ is the app's own cache (LOLA PDS terrain, SPICE kernels, its venv). Treat it as read-only. Reuse its files instead of downloading them again.
- Never invent metadata (units, nodata, sentinels, thresholds, radio settings, file names). Inspect the files. If a value isn't found, write null with a TODO and report it.
- Never modify anything in data/raw/. data/ is gitignored. Never commit rasters, archives or model weights.
- All data lives under a data root read from the MOONMESH_DATA_ROOT environment variable (default: <repo>/data). Paths written as data/... in docs/CODEX_PROMPTS.md mean paths under that root.
- This repo runs on Windows, inside OneDrive. Use pathlib for every path. Don't rely on Unix-only tools (sha256sum, tar, curl, sed); use Python's hashlib, tarfile and requests/urllib instead.
- New code goes in training/. Import from moonmesh/ and lunar_core/, but don't modify them unless the task says so.
- Every script has an argparse CLI with --help, reads configs from training/configs/, writes reports to data/reports/, uses fixed seeds and logs the git commit.
- Simulated labels (ours and LunarRF's) are always marked "simulated" in outputs and reports.
- Model inputs must never include LunarRF output layers (best_prx, best_id, best_los, any counts), IDs, or full-trace LOS outputs (min_clearance, obstruction_*).
- Never tune anything on the test split.
- Never run git commit, git push, git reset or git checkout. The user handles git.
- Run pytest after every change. End every task with: files changed, commands run, test results, open questions.
