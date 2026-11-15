# Offline Haworth demonstration

All coverage and profiles are **simulated**, not measured lunar RF performance.
The released predictor is the deterministic map selected by
`<data-root>/reports/eval/final/decision.json`. No weights are required.

Build from the repository in PowerShell using the existing training environment:

```powershell
$env:MOONMESH_DATA_ROOT = 'C:\moonmesh-data'
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/17_demo.py
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/17_demo.py --verify-only
Start-Process (Join-Path $env:MOONMESH_DATA_ROOT 'demo/haworth/index.html')
```

The build reads `training/configs/demo.yaml` and the integrated coverage config.
It reuses prepared local terrain and computes two cached scenarios; it never
downloads. Socket connections and DNS are forbidden during build and verification.
The HTML embeds terrain, slope, LOS, continuous margin, classes, masks and profiles.
Open it directly with `file://`; no server, map service, CDN or internet connection
is needed. Its content security policy forbids connections. Copy the entire
`<data-root>/demo/haworth/` directory to another computer to present it: `index.html`,
`manifest.json`, `haworth_tx5m.zip`, and `haworth_tx50m.zip`. Building requires the
training dependencies; viewing only requires a modern browser. The verifier checks
all bundle SHA256 hashes and the inspected path's recovery. A matching completed
bundle is reused; interrupted builds can be rerun safely.

1. Open the page: it starts at Haworth, latitude −86.8°, east longitude 337.2°,
   with a 5 m mast placed at the research point. Press **Place 5 m mast** to reset.
   RX is 2 m, radio is `moonmesh_2p4_ref` at 2.4 GHz, range 8 km, margin threshold
   3 dB. The white cross marks the mast.
2. Use **RF covered / dead**: green is covered, red is below margin, gray hatch
   is unknown. Switch to **Geometric LOS**, **Elevation**, **Slope**, or **RF margin**
   to distinguish terrain obstruction from usable diffracted power. Unknown cells
   belong to neither class. Native terrain is 20 m; the page states display spacing.
3. Press **Inspect ridge path**, or click the yellow receiver marker. The receiver
   was selected deterministically from display cells that fail RF margin at 5 m
   despite adequate free-space margin, have negative terrain clearance, and recover
   at 50 m. The terrain rises through the antenna chord: the yellow line identifies
   the controlling obstruction distance. Read the receiver coordinates, LOS, margin
   and signed minimum clearance alongside the profile.
4. Press **Raise mast to 50 m**. Inspect the same receiver and compare the profile,
   RF boundary, covered count and margin. The page reports the count of changed
   native cells. Switch between the two heights to see the boundary change.
5. Download either local ZIP link for native GeoTIFF layers and `scenario.json`
   with source checksums, complete radio settings, masks, units and provenance.
   The bundle manifest records exact requests, code/config/terrain identity and
   file hashes. `<data-root>/reports/demo/offline_verification.json` records the
   measured receiver and before/after results.

This is a fixed, cached walkthrough of two scenarios. Arbitrary transmitter
placement and heights are available in the integrated app:

```powershell
& C:\moonmesh-data\venv\Scripts\python.exe -m uvicorn moonmesh.server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, choose **More → Surface coverage**, select Haworth,
set the transmitter coordinates/heights and calculate. This app flow uses prepared
terrain, but the offline guarantee in this step applies to the standalone bundle,
which avoids external basemaps entirely. The existing mission timeline is not part
of this demonstration; surface RF coverage is separate from DTE, power, scheduled
availability and relay timeline calculations.

Limitations: display sampling may hide narrow boundaries; use native exports for
full detail. The model uses a spherical chord and single-edge diffraction, without
measured validation. PGDA vertical reference and license wording remain unresolved
in supplied metadata. See `training/MODEL_CARD.md` and the data-root acceptance
checklist for evidence and outstanding acceptance items.
