# Step 16: Put surface coverage into the app

This step may modify `moonmesh/` and `frontend/`. Read `moonmesh/server.py` and `frontend/` first, and follow their existing patterns. Use `reports/eval/final/decision.json`: the default predictor is the learned model only if it was chosen there, and the deterministic predictor is always available.

- Backend coverage route:
  - Inputs: site, tx position, h_tx, h_rx, radio profile, threshold, and predictor (deterministic or model).
  - Returns the LOS, margin, class and valid layers.
  - Cache key: terrain version, grid, tx coordinates, both heights, propagation model and profile hash. A threshold-only change reclassifies the cached margin without recomputing.
  - Fall back to deterministic when inputs are outside the evaluated ranges (sites, heights, range).
- Handle these states: loading, missing data, no valid pixels, no link, out of domain.
- Frontend "Surface coverage" mode:
  - layer toggles for elevation, slope, LOS, RF margin, covered/dead, and unknown (in its own color and hatch)
  - a legend, plus the active model, terrain source and resolution
  - click-to-inspect: coordinates, terrain source, both heights, an obstruction profile chart, margin and state
  - stale-response protection when the tx is moved quickly
- Export: GeoTIFF layers plus a JSON sidecar (sources, checksums, threshold, masks, units, nodata, model and code versions, assumptions).
- Tests for the route and the cache key.

## Gates

1. The app's unittest suite and the training pytest suite pass.
2. `npm.cmd run build` succeeds.
3. Smoke test: start the server, request the coverage route for the Haworth research point (h_tx 5, h_rx 2), check that the response has valid layers, then stop the server.

Status file: `reports/pipeline/step_16.json`.
