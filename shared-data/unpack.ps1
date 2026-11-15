# Rebuild moonmesh-shared-data-haworth.zip from its parts and unpack it into the MOONMESH data folder.
# Usage, from the hackNasa folder:
#   powershell -ExecutionPolicy Bypass -File shared-data\unpack.ps1
# Optional: -DataRoot D:\some\folder   (default: $env:MOONMESH_DATA_ROOT, otherwise C:\moonmesh-data)
param([string]$DataRoot = "")
$ErrorActionPreference = "Stop"
if (-not $DataRoot) {
    $DataRoot = if ($env:MOONMESH_DATA_ROOT) { $env:MOONMESH_DATA_ROOT } else { "C:\moonmesh-data" }
}
$expected = "a85982563c8ba0bd2624a3e2cb7c907f9affb388cc61eb2a5d439d3b25e1ebf2"
$parts = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter "moonmesh-shared-data-haworth.zip.*" | Sort-Object Name)
if ($parts.Count -ne 7) {
    throw "Expected 7 zip parts in $PSScriptRoot but found $($parts.Count). Run git pull again."
}
New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
$zip = Join-Path $DataRoot "moonmesh-shared-data-haworth.zip"
Write-Host "Joining $($parts.Count) parts into $zip ..."
$out = [System.IO.File]::Create($zip)
try {
    foreach ($part in $parts) {
        $in = [System.IO.File]::OpenRead($part.FullName)
        try { $in.CopyTo($out) } finally { $in.Dispose() }
    }
} finally {
    $out.Dispose()
}
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLower()
if ($hash -ne $expected) {
    throw "Checksum mismatch ($hash). The parts are incomplete or changed; run git pull again."
}
Write-Host "Checksum OK. Unpacking into $DataRoot (this can take a few minutes) ..."
Expand-Archive -LiteralPath $zip -DestinationPath $DataRoot -Force
Remove-Item -LiteralPath $zip
Write-Host "Done. Next: follow $DataRoot\README_SHARED_DATA.md for the one-time setup and how to start the app."
