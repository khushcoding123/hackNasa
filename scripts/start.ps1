$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$moonmeshPython = ".moonmesh-data/venv/Scripts/python.exe"
# Surface coverage needs the training venv (rasterio, numba) and its prepared terrain; use it when this machine has one.
$trainingPython = if ($env:MOONMESH_DATA_ROOT) { Join-Path $env:MOONMESH_DATA_ROOT "venv\Scripts\python.exe" } else { "" }
if ($trainingPython -and (Test-Path -LiteralPath $trainingPython)) {
    $moonmeshPython = $trainingPython
} elseif (!(Test-Path -LiteralPath $moonmeshPython)) {
    uv venv --python 3.13 .moonmesh-data/venv
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
}
uv pip install --python $moonmeshPython -r requirements.txt
if ($LASTEXITCODE) { exit $LASTEXITCODE }
npm.cmd ci
if ($LASTEXITCODE) { exit $LASTEXITCODE }
npm.cmd run build
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $moonmeshPython -m moonmesh.data
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $moonmeshPython -m uvicorn moonmesh.server:app --host 127.0.0.1 --port 8765
exit $LASTEXITCODE
