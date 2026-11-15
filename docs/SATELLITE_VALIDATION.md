# Simulated satellite validation

The mission planner supports an optional **simulated** single-satellite backhaul: lander → satellite → Earth and lander → surface relay → satellite → Earth, alongside the existing Earth paths. Configure a 500 km polar, south elliptical or custom two-body orbit in advanced mission settings. Elements are Moon-centred J2000; epoch defaults to mission start UTC. SPICE transforms the trajectory into MOON_ME at mission samples. Surface access uses terrain horizons, elevation mask and optional RF; the Earth hop uses lunar occultation and optional RF. Surface power and thermal states gate complete paths.

Satellite radio profiles are null until supplied. Such results are geometry-only upper bounds with blocking evidence gaps, not verified RF coverage. Satellite power and pointing are assumed available; capacity, scheduling, handover, light-time, station scheduling and satellite hardware survival are not modeled. Satellite service cost is null with a TODO and excluded from the surface budget. The illustrative south elliptical preset is not a validated frozen orbit, Pathfinder orbit, ELFO or NRHO.

Resilient optimization includes loss of the one shared satellite across all environmental cases. Resilience controls include S1 and combinations with surface-relay losses. Multiple paths behind S1 are not credited as satellite-independent redundancy. The mission scene, sample slider, per-hop diagnostics, API and Excel sheet use the computed track. ORA and the area-layout satellite preview remain separate, surface-only/illustrative tools; their metrics do not validate satellite service.

Validation covers Kepler residuals, period, energy and angular momentum, synthetic overhead/antipodal and occultation geometry, SPICE frame radius preservation, scenario/API/export contracts, backward compatibility and shared failures. There is no independent spacecraft state-vector or SPK cross-check; this remains an explicit validation gap. No operational constellation or flight performance is claimed.

Reproduce with `python -m pytest tests -q`, `python -m unittest discover -s tests -v`, `npm run build`, and `npm run test:orbital`. Use the app environment; no training packages or weights are needed. Existing cached terrain and kernels are reused read-only.


## Verification results

- `python -m pytest tests -q` via `.venv/bin/python`: 146 passed, 7 skipped, 51 subtests passed. Existing Starlette/httpx deprecation warning.
- `python -m unittest discover -s tests -v` via `.venv/bin/python`: 111 tests, 2 skipped, OK.
- `npm run build`: TypeScript and production bundles pass.
- `npm run test:orbital`: overhead, occultation, range, polar and longitude seam checks pass.
- `npm run test:terrain`: npm could not locate the esbuild executable in this environment. The equivalent build using the installed esbuild module followed by `node tests/test_terrain_mesh.mjs tmp/terrain-mesh-test.mjs` passes.
- `git diff --check`: clean.
- Default `python` has no pytest; the existing `.venv/bin/python` supplies the app dependencies. No dependencies were installed and no training environment was changed.

## Browser flow

Used the local FastAPI app on port 8766 and existing Haworth terrain/kernels, through the research-point mission option (no new terrain crops). Configured 2026-11-01 through 2026-11-04 UTC, 30-minute sampling, polar_500km. The UI displayed 144 dated samples, a 158.3-minute simulated orbit, 16.0% lander satellite-path access, per-hop obstruction states and null RF margins. Full selected network availability was 100%; actual direct-to-Earth availability was 59.7%. These are simulation outputs, not flight-validated service claims.

The mission slider changed the dated readout and satellite location. The framing button showed the computed satellite. S1 loss left the selected Earth-capable relay network at 100%; combined loss of S1 and R05 restored 59.7% availability with a 29-hour blackout. Excel download completed in the browser. API/export integration tests inspect the simulated sheet and per-hop fields.

## Files changed

- Orbit and geometry: `lunar_core/orbit.py`, `lunar_core/geometry.py`.
- Mission, optimization, evidence and export: `moonmesh/models.py`, `science.py`, `network.py`, `environment.py`, `server.py`, `excel.py`, `evidence.py`; existing satellite exclusion clarified in `costs.py`.
- User interface: `frontend/types.ts`, `app.ts`, `moon.ts`.
- Tests: `tests/test_orbit.py`, `tests/test_satellite_scenario.py`.
- Documentation: `README.md`, `PITCH.md`, `PRODUCT_FLOW.md`, `MOONMESH_DELIVERABLES.md`, `docs/COMMUNICATION_ARCHITECTURE.md`, this report.

## Open questions

- Supply verified surface-to-satellite and satellite-to-Earth radio profiles before claiming RF service.
- Supply a satellite service cost; it remains null with a TODO and excluded from the surface budget.
- Supply an independently sourced spacecraft ephemeris/SPK if mission-specific orbit validation is required. Only Earth/Sun Horizons fixtures are available under tests/data.
- Satellite power, tracking, capacity and hardware survival remain assumptions or unmodeled; NRHO/SPK and multi-satellite support are optional future work.

No commits or pushes were made. Existing user edits were retained. No pretrained models, raster downloads or model weights were used.
