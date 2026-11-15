# Partner setup: get MOONMESH to the same working state

This brings a fresh Windows machine to the same level as the main development machine: the same tools, Python
environment, NASA data, trained models, built web UI and passing tests.

## The fast way: one script

From the `hackNasa` folder, in Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\partner_setup.ps1
```

Every step **checks first** and only downloads, installs or builds what is missing, so you can run it again at any
time, for example after a `git pull`. At the end it prints a table with `ok`, `fixed`, `missing` or `failed` for each step.

| Option | What it does |
|---|---|
| `-CheckOnly` | Report what is present and what is missing. Changes nothing. |
| `-Start` | After setup, start the app at http://127.0.0.1:8765 (`-Port 8766` if 8765 is busy). |
| `-ModelsFrom <folder>` | Copy the six trained ORA model files (`*.joblib`, about 20 MB) from a folder you were given, instead of training them. |
| `-Train` | Rebuild any missing ORA and hazard models from the NASA and FCC sources. Slow: several GB and about an hour. |
| `-PersistEnv` | Save `MOONMESH_DATA_ROOT=C:\moonmesh-data` for your Windows user, so new windows have it. |
| `-SkipTests` | Skip the test suites at the end. |
| `-DataRoot <folder>` | Use another data folder instead of `C:\moonmesh-data`. |
| `-RepoDir <folder>` | Where the repository is, or where to clone it if it is missing. |

No repository yet? Download only `scripts/partner_setup.ps1` from GitHub and run:

```powershell
powershell -ExecutionPolicy Bypass -File partner_setup.ps1 -RepoDir "$HOME\Documents\hackNasa"
```

## What it sets up, step by step

If you prefer to do it by hand, these are the same steps in the same order. Run them from the `hackNasa` folder.

### 1. Tools

| Tool | Check | Install if missing | Link |
|---|---|---|---|
| Git | `git --version` | `winget install --id Git.Git -e` | https://git-scm.com/download/win |
| uv (Python package manager) | `uv --version` | `winget install --id astral-sh.uv -e` | https://docs.astral.sh/uv/ |
| Python 3.13 (managed by uv) | `uv python find 3.13` | `uv python install 3.13` | https://www.python.org/downloads/ |
| Node.js 22 or newer | `node --version` | `winget install --id OpenJS.NodeJS.LTS -e` | https://nodejs.org |

After a `winget` install, open a new PowerShell window so the tool is on `PATH`.

### 2. Repository

```powershell
git clone https://github.com/khushcoding123/hackNasa.git      # first time only
git pull --ff-only                                            # later updates
```

`git pull` also brings the seven `shared-data\moonmesh-shared-data-haworth.zip.00N` parts (332 MB in total; no Git LFS needed).

### 3. One Python environment for the app, the tests and training

```powershell
uv venv --python 3.13 C:\moonmesh-data\venv
uv pip install --python C:\moonmesh-data\venv\Scripts\python.exe -r requirements-dev.txt -r requirements-training.txt -e .
$env:MOONMESH_DATA_ROOT = "C:\moonmesh-data"                  # every new window, or use -PersistEnv once
```

`requirements-dev.txt` includes the app's `requirements.txt` plus test tools. `requirements-training.txt` adds
rasterio, numba and LightGBM. `-e .` installs the training code (`moonmesh_train`) in editable mode.
Below, `py` means `C:\moonmesh-data\venv\Scripts\python.exe`.

### 4. Shared data: Haworth terrain and Khush's trained coverage model (from the repo)

```powershell
py shared-data\unpack.py --data-root C:\moonmesh-data
```

This joins the seven parts, checks the SHA-256 (`a85982563c8ba0bd2624a3e2cb7c907f9affb388cc61eb2a5d439d3b25e1ebf2`)
and unpacks the files: the LOLA PGDA 90 Haworth DEM (20 m), the terrain features, the trained LightGBM models
(LOS, margin, clearance), their reports and the offline Haworth demo. The script skips this step when
`C:\moonmesh-data\README_SHARED_DATA.md` and `C:\moonmesh-data\models` already exist. More detail: `shared-data/README.md`.

### 5. Web UI

```powershell
npm ci             # skipped when node_modules\cesium exists
npm run build      # skipped when web\app.js is newer than every frontend\*.ts
```

### 6. NASA data for the app (`.moonmesh-data`, about 1 GB, downloaded once)

```powershell
py -m moonmesh.setup             # check only
py -m moonmesh.setup --apply     # download whatever is missing
py -m moonmesh.globaldem         # global LOLA DEM, 506 MB
```

| Data | Size | Source |
|---|---|---|
| SPICE kernels (`de440s.bsp`, `naif0012.tls`, Moon frame and orientation kernels) | 44 MB | https://naif.jpl.nasa.gov/pub/naif/generic_kernels/ |
| LOLA south polar DEMs `LDEM_85S_20M` (20 m) and `LDEM_75S_240M` (240 m) | 490 MB | https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/ |
| Site terrain windows for Haworth, Nobile Rim 1 and Malapert | small | cut from the DEMs above |
| Global LOLA DEM `ldem_64.img`, used for areas drawn outside the three 20 m regions | 506 MB | https://pds-geosciences.wustl.edu/lro/lro-l-lola-3-rdr-v1/lrolol_1xxx/data/lola_gdr/cylindrical/img/ldem_64.img |

Map imagery (LROC WAC and NAC) streams live from https://quickmap.lroc.im-ldi.com/ while you use the app; nothing to download.

### 7. Trained ORA research models (`.moonmesh-data\models`)

Six files: `ora_coverage_surrogate`, `ora_gain_model`, `ora_gain_selector`, `ora_siting_prior`, `ora_view_estimator`
and `ora_hazard_thermal` (`.joblib`, about 20 MB in total). They are gitignored. The app runs without them, but the
research and benchmark views report them as unavailable.

- **Fastest:** ask for a copy of the `.moonmesh-data\models` folder and run the script with `-ModelsFrom <that folder>`.
- **From scratch:** `-Train`, or by hand:

  ```powershell
  py -m moonmesh.setup --apply --train                       # ora.data --earth, ora.lunar_windows, ora.train, ora accuracy
  py training\scripts\18_build_hazard_dataset.py --download  # Diviner PRP polar temperatures, about 5 min
  py training\scripts\19_train_hazard.py                     # about 15 min
  ```

  `ora.data --earth` downloads the FCC tower registry (https://data.fcc.gov/download/pub/uls/complete/r_tower.zip) and
  SRTM elevation tiles (https://s3.amazonaws.com/elevation-tiles-prod/skadi/), about 370 MB. The hazard dataset comes from the
  Diviner PRP bundle on the PDS Geosciences Node (doi:10.17189/wj0s-w188). See `docs/HAZARD_RESPONSE.md`.

### 8. Tests

```powershell
py -m unittest discover -s tests                 # app tests
py -m pytest training -q -rs                     # training tests, including Khush's real-model tests
npm run test:terrain                             # terrain mesh builder
```

The real-model tests (`training/tests/test_real_model.py`) skip, with a reason, when step 4 has not been done or
`MOONMESH_DATA_ROOT` points elsewhere. On a complete setup nothing should skip for missing data.

### 9. Start the app

```powershell
$env:MOONMESH_DATA_ROOT = "C:\moonmesh-data"
py -m uvicorn moonmesh.server:app --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765. Or: `powershell -ExecutionPolicy Bypass -File scripts\partner_setup.ps1 -Start -SkipTests`.

## Check you match

`py -m moonmesh.setup` should end with `READY` and `Trained research models: 6/6 loaded`, and
`py -m ora preflight` lists the data, models and library versions so the two machines can be compared line by line.

## macOS or Linux

The same steps apply with these differences (see `shared-data/README.md`): run `python3 shared-data/unpack.py`
(it defaults to `hackNasa/data`), create the venv at `data/venv` and use `data/venv/bin/python`, start with
`bash scripts/start.sh`, and on a Mac install OpenMP for LightGBM with `brew install libomp`.
