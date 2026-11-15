<#
MOONMESH partner setup for Windows: brings a machine to the same working state as the main development machine.

Every step CHECKS first and only downloads, installs or builds what is missing, so it is safe to run again at any time.
See docs/PARTNER_SETUP.md for what each step does, the sources and sizes.

Usage (Windows PowerShell, from anywhere):
  powershell -ExecutionPolicy Bypass -File scripts\partner_setup.ps1                 # check + fix everything, then run the tests
  powershell -ExecutionPolicy Bypass -File scripts\partner_setup.ps1 -CheckOnly      # report only; change nothing
  powershell -ExecutionPolicy Bypass -File scripts\partner_setup.ps1 -Start          # also start the app at http://127.0.0.1:8765
  powershell -ExecutionPolicy Bypass -File scripts\partner_setup.ps1 -ModelsFrom D:\usb\models   # copy the six ORA models instead of training
  powershell -ExecutionPolicy Bypass -File scripts\partner_setup.ps1 -Train          # rebuild missing ORA + hazard models (slow, several GB)

If you do not have the repository yet, download only this file and run it with -RepoDir "$HOME\Documents\hackNasa": it clones the repo there.
#>
param(
    [string]$RepoDir = "",
    [string]$DataRoot = "C:\moonmesh-data",
    [string]$RepoUrl = "https://github.com/khushcoding123/hackNasa.git",
    [string]$ModelsFrom = "",
    [switch]$CheckOnly,
    [switch]$Train,
    [switch]$SkipTests,
    [switch]$PersistEnv,
    [switch]$Start,
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$script:report = New-Object System.Collections.Generic.List[object]

function Say([string]$text, [string]$color = "Gray") { Write-Host $text -ForegroundColor $color }
function Section([string]$title) { Write-Host ""; Write-Host "== $title ==" -ForegroundColor Cyan }
function Record([string]$name, [string]$state, [string]$detail) {
    $script:report.Add([pscustomobject]@{Step = $name; State = $state; Detail = $detail})
    $color = @{ok = "Green"; fixed = "Green"; missing = "Yellow"; skipped = "DarkGray"; failed = "Red"}[$state]
    Say ("  [{0,-7}] {1}: {2}" -f $state, $name, $detail) $color
}
function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine"); $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user;$HOME\.local\bin;$env:APPDATA\Python\Python313\Scripts"
}
function Has([string]$command) { return [bool](Get-Command $command -ErrorAction SilentlyContinue) }
function Run([string]$exe, [string[]]$arguments) {
    Say ("    > {0} {1}" -f $exe, ($arguments -join " ")) "DarkGray"
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$exe exited with code $LASTEXITCODE" }
}
function Winget-Install([string]$id, [string]$what) {
    if (-not (Has "winget")) { throw "winget is not available. Install $what by hand (see docs/PARTNER_SETUP.md), then run this script again." }
    Run "winget" @("install", "--id", $id, "-e", "--accept-source-agreements", "--accept-package-agreements")
    Refresh-Path
}

Refresh-Path

# ---------------------------------------------------------------- 1. tools
Section "1. Tools (Git, uv, Python 3.13, Node.js 22+)"

if (Has "git") { Record "Git" "ok" (git --version) }
elseif ($CheckOnly) { Record "Git" "missing" "winget install --id Git.Git -e" }
else { Winget-Install "Git.Git" "Git (https://git-scm.com/download/win)"; Record "Git" "fixed" (git --version) }

if (Has "uv") { Record "uv" "ok" (uv --version) }
elseif ($CheckOnly) { Record "uv" "missing" "winget install --id astral-sh.uv -e   (or https://docs.astral.sh/uv/)" }
else {
    try { Winget-Install "astral-sh.uv" "uv" } catch {
        Say "    winget could not install uv; using the official installer from astral.sh" "Yellow"
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
        Refresh-Path
    }
    if (-not (Has "uv")) { throw "uv is still not on PATH. Open a new PowerShell window and run this script again." }
    Record "uv" "fixed" (uv --version)
}

$python313 = ""
if (Has "uv") { $python313 = (uv python find 3.13 2>$null) }
if ($python313) { Record "Python 3.13" "ok" $python313 }
elseif ($CheckOnly) { Record "Python 3.13" "missing" "uv python install 3.13" }
else { Run "uv" @("python", "install", "3.13"); Record "Python 3.13" "fixed" (uv python find 3.13) }

$nodeMajor = 0
if (Has "node") { $nodeMajor = [int]((node --version).TrimStart("v").Split(".")[0]) }
if ($nodeMajor -ge 22) { Record "Node.js" "ok" (node --version) }
elseif ($CheckOnly) { Record "Node.js" "missing" "need 22 or newer: winget install --id OpenJS.NodeJS.LTS -e" }
else { Winget-Install "OpenJS.NodeJS.LTS" "Node.js 22+ (https://nodejs.org)"; Record "Node.js" "fixed" (node --version) }

# ---------------------------------------------------------------- 2. repository
Section "2. Repository"
if (-not $RepoDir) {
    $here = Split-Path -Parent $PSScriptRoot
    $RepoDir = if (Test-Path (Join-Path $here "moonmesh\server.py")) { $here } else { Join-Path $HOME "Documents\hackNasa" }
}
if (-not (Test-Path (Join-Path $RepoDir ".git"))) {
    if ($CheckOnly) { Record "Clone" "missing" "git clone $RepoUrl `"$RepoDir`"" }
    else { Run "git" @("clone", $RepoUrl, $RepoDir); Record "Clone" "fixed" $RepoDir }
} else {
    Push-Location $RepoDir
    $dirty = git status --porcelain --untracked-files=no
    if ($CheckOnly) { git fetch --quiet origin; $behind = git rev-list --count "HEAD..@{u}" 2>$null; Record "Up to date" ($(if ($behind -and [int]$behind -gt 0) {"missing"} else {"ok"})) "$behind commit(s) behind $(git rev-parse --abbrev-ref '@{u}' 2>$null)" }
    elseif ($dirty) { Record "Pull" "skipped" "you have uncommitted changes; commit or stash them, then git pull" }
    else { Run "git" @("pull", "--ff-only"); Record "Pull" "ok" (git log -1 --format="%h %s") }
    Pop-Location
}
if (-not (Test-Path $RepoDir)) { Say "Repository is not present; stopping here." "Red"; exit 1 }
Set-Location $RepoDir

# ---------------------------------------------------------------- 3. Python environment (one venv for the app, tests and training)
Section "3. Python environment ($DataRoot\venv)"
$py = Join-Path $DataRoot "venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    if ($CheckOnly) { Record "Training venv" "missing" "uv venv --python 3.13 $DataRoot\venv" }
    else { New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null; Run "uv" @("venv", "--python", "3.13", (Join-Path $DataRoot "venv")); Record "Training venv" "fixed" $py }
} else { Record "Training venv" "ok" $py }

$imports = "import fastapi, uvicorn, numpy, scipy, spiceypy, rasterio, numba, lightgbm, sklearn, xlsxwriter, httpx, pytest, moonmesh_train"
$importsOk = $false
if (Test-Path $py) { & $py -c $imports 2>$null; $importsOk = ($LASTEXITCODE -eq 0) }
if ($importsOk) { Record "Python packages" "ok" "app, test and training packages import" }
elseif ($CheckOnly) { Record "Python packages" "missing" "uv pip install --python $py -r requirements-dev.txt -r requirements-training.txt -e ." }
elseif (Test-Path $py) {
    Run "uv" @("pip", "install", "--python", $py, "-r", "requirements-dev.txt", "-r", "requirements-training.txt", "-e", ".")
    & $py -c $imports; if ($LASTEXITCODE -ne 0) { Record "Python packages" "failed" "a package still does not import; see the error above" } else { Record "Python packages" "fixed" "installed" }
}

$env:MOONMESH_DATA_ROOT = $DataRoot
$persisted = [Environment]::GetEnvironmentVariable("MOONMESH_DATA_ROOT", "User")
if ($persisted -eq $DataRoot) { Record "MOONMESH_DATA_ROOT" "ok" "set for your user" }
elseif ($PersistEnv -and -not $CheckOnly) { [Environment]::SetEnvironmentVariable("MOONMESH_DATA_ROOT", $DataRoot, "User"); Record "MOONMESH_DATA_ROOT" "fixed" "saved for your user (new windows see it)" }
else { Record "MOONMESH_DATA_ROOT" "skipped" "set for this run only; add -PersistEnv to keep it, or run `$env:MOONMESH_DATA_ROOT = `"$DataRoot`" in each window" }

# ---------------------------------------------------------------- 4. shared data (Haworth terrain + Khush's trained coverage model), from the repo
Section "4. Shared data from the repo (332 MB zip in 7 parts)"
$sharedMarker = Join-Path $DataRoot "models"
$sharedOk = (Test-Path (Join-Path $DataRoot "README_SHARED_DATA.md")) -and (Test-Path $sharedMarker)
if ($sharedOk) { Record "Shared data" "ok" "unpacked in $DataRoot" }
elseif ($CheckOnly) { Record "Shared data" "missing" "python shared-data\unpack.py --data-root $DataRoot" }
elseif (Test-Path $py) { Run $py @("shared-data\unpack.py", "--data-root", $DataRoot); Record "Shared data" "fixed" "unpacked in $DataRoot (SHA-256 checked)" }

# ---------------------------------------------------------------- 5. web UI
Section "5. Web UI (npm packages and build)"
if (Test-Path "node_modules\cesium\package.json") { Record "npm packages" "ok" "node_modules present" }
elseif ($CheckOnly) { Record "npm packages" "missing" "npm ci" }
else { Run "npm.cmd" @("ci"); Record "npm packages" "fixed" "npm ci" }

$built = Get-Item "web\app.js" -ErrorAction SilentlyContinue
$newest = Get-ChildItem "frontend\*.ts" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$stale = (-not $built) -or (-not (Test-Path "web\cesium")) -or ($newest -and $newest.LastWriteTime -gt $built.LastWriteTime)
if (-not $stale) { Record "UI build" "ok" "web\app.js is newer than the sources" }
elseif ($CheckOnly) { Record "UI build" "missing" "npm run build" }
else { Run "npm.cmd" @("run", "build"); Record "UI build" "fixed" "npm run build" }

# ---------------------------------------------------------------- 6. NASA data for the app (.moonmesh-data)
Section "6. NASA data for the app (.moonmesh-data, about 1 GB)"
if (Test-Path $py) {
    # moonmesh.setup checks SPICE kernels, LOLA 20 m / 240 m polar DEMs and the site windows, and downloads only what is missing.
    $setupArgs = @("-m", "moonmesh.setup", "--no-smoke"); if (-not $CheckOnly) { $setupArgs += "--apply" }
    & $py @setupArgs
    Record "Kernels + LOLA polar terrain" ($(if ($LASTEXITCODE -eq 0) {"ok"} else {"failed"})) "python -m moonmesh.setup$(if (-not $CheckOnly) {' --apply'})"

    $globalDem = ".moonmesh-data\lola64\ldem_64.img"
    if ((Test-Path $globalDem) -and (Get-Item $globalDem).Length -eq 530841600) { Record "Global LOLA DEM" "ok" "ldem_64.img (506 MB)" }
    elseif ($CheckOnly) { Record "Global LOLA DEM" "missing" "python -m moonmesh.globaldem   (506 MB; needed for areas drawn outside the three 20 m regions)" }
    else { Run $py @("-m", "moonmesh.globaldem"); Record "Global LOLA DEM" "fixed" "downloaded" }
}

# ---------------------------------------------------------------- 7. trained ORA models (.moonmesh-data\models)
Section "7. Trained ORA research models (.moonmesh-data\models)"
$models = "ora_coverage_surrogate", "ora_gain_model", "ora_gain_selector", "ora_siting_prior", "ora_view_estimator", "ora_hazard_thermal"
$modelDir = ".moonmesh-data\models"
$missingModels = @($models | Where-Object { -not (Test-Path (Join-Path $modelDir "$_.joblib")) })
if ($missingModels.Count -eq 0) { Record "ORA models" "ok" "all six present" }
elseif ($ModelsFrom -and -not $CheckOnly) {
    New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
    foreach ($m in $missingModels) { $src = Join-Path $ModelsFrom "$m.joblib"; if (Test-Path $src) { Copy-Item $src $modelDir } }
    $still = @($models | Where-Object { -not (Test-Path (Join-Path $modelDir "$_.joblib")) })
    Record "ORA models" ($(if ($still.Count) {"missing"} else {"fixed"})) ($(if ($still.Count) {"still missing: $($still -join ', ')"} else {"copied from $ModelsFrom"}))
} elseif ($Train -and -not $CheckOnly) {
    $core = @($missingModels | Where-Object { $_ -ne "ora_hazard_thermal" })
    if ($core.Count) { Run $py @("-m", "moonmesh.setup", "--apply", "--train", "--no-smoke") }   # ora.data --earth, ora.lunar_windows, ora.train, ora accuracy
    if ($missingModels -contains "ora_hazard_thermal") {
        Run $py @("training\scripts\18_build_hazard_dataset.py", "--download")   # Diviner PRP files, about 5 min
        Run $py @("training\scripts\19_train_hazard.py")                       # about 15 min
    }
    $still = @($models | Where-Object { -not (Test-Path (Join-Path $modelDir "$_.joblib")) })
    Record "ORA models" ($(if ($still.Count) {"failed"} else {"fixed"})) ($(if ($still.Count) {"still missing: $($still -join ', ')"} else {"trained"}))
} else {
    Record "ORA models" "missing" "missing: $($missingModels -join ', '). The app still runs. Fastest: -ModelsFrom <folder with the .joblib files>; or -Train (slow, several GB)"
}

# ---------------------------------------------------------------- 8. tests
Section "8. Tests"
if ($SkipTests -or $CheckOnly) { Record "Tests" "skipped" "run without -CheckOnly / -SkipTests to test" }
elseif (Test-Path $py) {
    & $py -m unittest discover -s tests
    Record "App tests (unittest)" ($(if ($LASTEXITCODE -eq 0) {"ok"} else {"failed"})) "python -m unittest discover -s tests"
    & $py -m pytest training -q -rs -p no:cacheprovider
    Record "Training + model tests (pytest)" ($(if ($LASTEXITCODE -eq 0) {"ok"} else {"failed"})) "pytest training (includes test_real_model.py)"
    & npm.cmd run test:terrain
    Record "Terrain mesh test (node)" ($(if ($LASTEXITCODE -eq 0) {"ok"} else {"failed"})) "npm run test:terrain"
}

# ---------------------------------------------------------------- summary
Section "Summary"
$script:report | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
$bad = @($script:report | Where-Object { $_.State -in @("missing", "failed") })
if ($bad.Count) { Say "Not everything is ready. Rows marked missing/failed say what to run. Re-run this script after fixing them." "Yellow" }
else { Say "Ready. Start the app with: powershell -ExecutionPolicy Bypass -File scripts\partner_setup.ps1 -Start -SkipTests" "Green" }

if ($Start -and -not $CheckOnly) {
    $busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($busy) { Say "Port $Port is already in use (is MOONMESH already running?). Use -Port 8766." "Yellow"; exit 1 }
    Say "Starting MOONMESH at http://127.0.0.1:$Port  (Ctrl+C to stop)" "Green"
    & $py -m uvicorn moonmesh.server:app --host 127.0.0.1 --port $Port
}
