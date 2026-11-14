$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$moonmeshPython = ".moonmesh-data/venv/Scripts/python.exe"
if (!(Test-Path -LiteralPath $moonmeshPython)) {
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
