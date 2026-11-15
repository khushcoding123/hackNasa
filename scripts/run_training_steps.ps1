<#
Runs the remaining MOONMESH training steps (docs/steps/step_NN.md) with Codex, unattended.

For each step it:
  1. runs Codex non-interactively on the step file,
  2. reads the step's status file (pass / skipped / fail),
  3. runs the training and app test suites,
  4. commits the result,
and stops at the first problem so you can fix it and resume with -From.

Usage, from the repository root:
  powershell -ExecutionPolicy Bypass -File scripts\run_training_steps.ps1 -From 6 -To 11
#>
param(
    [int]$From = 6,
    [int]$To = 17
)

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
if (-not $env:MOONMESH_DATA_ROOT) { $env:MOONMESH_DATA_ROOT = "C:\moonmesh-data" }
$py = "C:\moonmesh-data\venv\Scripts\python.exe"
$statusDir = Join-Path $env:MOONMESH_DATA_ROOT "reports\pipeline"
New-Item -ItemType Directory -Force -Path $statusDir | Out-Null
$script:current = $From

function Stop-Run([string]$message) {
    Write-Host ""
    Write-Host "STOPPED: $message" -ForegroundColor Red
    Write-Host "Nothing from this step was committed. Fix the problem (or send this message to Claude)," -ForegroundColor Yellow
    Write-Host "then resume with: powershell -ExecutionPolicy Bypass -File scripts\run_training_steps.ps1 -From $script:current -To $To" -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path $py)) { Write-Host "Training venv not found at $py"; exit 1 }
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne "coverage-training") { Write-Host "Switch to the coverage-training branch first (current: $branch)."; exit 1 }
$dirty = git status --porcelain
if ($dirty) {
    Write-Host "You have uncommitted changes. Commit or discard them first:" -ForegroundColor Yellow
    $dirty
    exit 1
}

$steps = Get-ChildItem -Path "docs\steps" -Filter "step_*.md" | Sort-Object Name
foreach ($file in $steps) {
    $n = [int]($file.BaseName.Substring(5))
    if ($n -lt $From -or $n -gt $To) { continue }
    $script:current = $n
    $id = "{0:D2}" -f $n
    $statusFile = Join-Path $statusDir "step_$id.json"
    if (Test-Path $statusFile) { Remove-Item $statusFile }

    Write-Host ""
    Write-Host "=== Step $id ($($file.Name)) started $(Get-Date -Format 'HH:mm') ===" -ForegroundColor Cyan
    $prompt = "Read docs/steps/COMMON.md, then carry out docs/steps/$($file.Name) exactly, following AGENTS.md. This is an unattended run: never ask questions or wait for input; make the most reasonable documented choice and continue. Finish by writing the status file named at the end of the step file."
    codex exec --dangerously-bypass-approvals-and-sandbox $prompt
    if ($LASTEXITCODE -ne 0) { Stop-Run "Codex exited with code $LASTEXITCODE on step $id." }

    if (-not (Test-Path $statusFile)) { Stop-Run "Step $id did not write its status file ($statusFile)." }
    $status = Get-Content $statusFile -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-Host "Step $id status: $($status.status). $($status.summary)"
    foreach ($w in $status.warnings) { Write-Host "  warning: $w" -ForegroundColor Yellow }
    if ($status.status -ne "pass" -and $status.status -ne "skipped") { Stop-Run "Step $id reported status '$($status.status)'. See $statusFile." }

    & $py -m pytest training -q
    if ($LASTEXITCODE -ne 0) { Stop-Run "Training tests failed after step $id." }
    & $py -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) { Stop-Run "App tests failed after step $id." }

    git add -A
    git commit -q -m "step ${id}: $($status.title) [$($status.status)]"
    Write-Host "Step $id committed $(Get-Date -Format 'HH:mm')." -ForegroundColor Green
}

Write-Host ""
Write-Host "Finished steps $From to $To. Status files are in $statusDir." -ForegroundColor Green
