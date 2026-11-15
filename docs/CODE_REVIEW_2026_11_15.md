# Review of satellite, outage, and relay-evidence changes

Reviewed the working-tree additions across orbit/geometry, mission validation, path evaluation, shared-failure search, environment integration, API, scene/charts, exports, tests and claims. Existing user edits were retained. The outage/relay-evidence plan was still pending at the start of this pass and is now implemented; its contract is documented in OUTAGE_AND_RELAY_EVIDENCE_PLAN.md.

## Findings fixed

1. **The blackout list described the lander instead of the active network.** Added explicit direct-Earth, satellite and relay paths, plus state-specific network and individual-path outage intervals for every environmental case. Relay rescue no longer appears as a network outage.
2. **A single cause string hid simultaneous blockers and cause changes.** Link/node evidence retains all failed or unknown conditions; outage cause segments preserve changes within each interval. Numeric values, thresholds, endpoints, actual sampled horizon rays and approximate terrain locations are inspectable. Unknown RF remains a geometry-only upper bound, not evaluated RF coverage.
3. **Relay recommendations lacked comparison evidence.** The production exhaustive search records all affordable counts without changing its winner, objective or tie behavior. The UI/export now show baselines, alternatives, best-per-count results, ties, removals, replacements and conditional gains. Equal-score candidates are labeled as ties rather than assigned invented advantages.
4. **Scene state could survive a recalculation.** Mission entities and preview state now reset when applying a new scenario, preventing old lander/tower positions from remaining visible.
5. **Scene colors could credit satellite-assisted relay access after S1 loss.** Moon, terrain-map and local-site colors now use explicit complete-path availability and the selected environmental case, including combined disabled nodes. Satellite paths also require the lander operational state.
6. **Equivalent epoch strings could make a newly applied mission appear stale.** Dirty-state checks compare epoch instants. The browser accepted an epoch without explicit seconds and retained the applied navigation state. Blank elevation-mask overrides use the existing validated backend default rather than silently converting to zero.
7. **Environmental-case changes could mix active timelines, summary labels and exports.** Active/assisted/lander series, same-case before/after metrics, scene colors, inspectors, power charts and Excel now follow the selected case. Marginal summaries explicitly identify their cold/dust basis.
8. **Code provenance was not attached to mission snapshots, and generic provenance formatting assumed every object value was a URL.** Reports now include the existing code-revision/working-tree hash inventory. The UI renders structured provenance as values and only turns HTTP(S) strings into links.
9. **The terrain test command depended on a missing executable shim.** npm now invokes the installed esbuild module directly. Added an analogous frontend evidence test command; no packages were installed.

10. **Earth annotations could use the satellite-union or power-gated flag as RF evidence.** Map rays now use the actual direct-Earth path, and horizon notes compare the configured Earth RF threshold directly. A power outage is no longer described as insufficient RF, and satellite rescue cannot change the Earth geometry annotation. Added frontend regressions for both cases.

## Verification

- App pytest: **155 passed, 7 skipped, 51 subtests passed**. Existing Starlette/httpx deprecation warning remains.
- App unittest: **120 tests, OK, 2 skipped**.
- TypeScript production build, frontend evidence tests, orbital-preview tests and terrain-mesh tests pass. Node's existing module-type warning remains on the orbital-preview test.
- `git diff --check` passes.
- Saved before-change snapshots for Haworth, 2026-11-01 through 2026-11-04, with and without the satellite. Recommendations, network/worst/direct/solar metrics, physics samples, configuration, environmental output and all pre-existing failure-state fields match after implementation. Both runs evaluated 22 configurations for the new evidence report.
- Independent synthetic enumeration checks selection and feasibility with/without evidence collection across budgets, environmental cases, fallback and shared-satellite failures. Additional tests cover ties, overlapping coverage, removal/replacement gains, simultaneous blockers, unknown data, boundary intervals and sampled-ray locations.
- Cached-data integration reconstructs production timelines from explicit paths across every failure/environment case, checks outage totals, rejects non-finite JSON, and inspects the evidence export sheets.
- Browser: Haworth, three days, 30-minute step, simulated polar satellite. Recommendation R05; R03 and R02 have equal objective scores. Previewing R03 leaves R05 applied. Direct Earth has a 29-hour outage starting 2026-11-02 19:00 UTC; the selected network remains available through relay rescue. Clicking the interval synchronizes its sample and numerical inspector.
- Browser: loss of S1 alone retains the Earth-capable relay path. Loss of S1 plus R05 produces 59.7% active coverage, a 29-hour blackout and 37 cause segments. Environmental-case switching updates the inspector. The actual browser-downloaded workbook was inspected: Network Outages, Outage Causes, Link Evidence, Node Evidence, Relay Alternatives and Marginal Gains are present, with the chosen nominal case, disabled nodes and R03 preview reference.

## Files changed in this pass

- Backend: `moonmesh/diagnostics.py`, `science.py`, `environment.py`, `excel.py`.
- UI: `frontend/mission-evidence.ts`, `types.ts`, `app.ts`, `charts.ts`, `moon.ts`, `site3d.ts`.
- Tests/tooling: `tests/test_mission_evidence.py`, `tests/test_mission_evidence.mjs`, the expanded visibility stub in `tests/test_environment.py`, `package.json`.
- Documentation: this report, `docs/OUTAGE_AND_RELAY_EVIDENCE_PLAN.md`, `README.md`, `PRODUCT_FLOW.md`, `MOONMESH_DELIVERABLES.md`.

Earlier satellite additions in `lunar_core/orbit.py`, `geometry.py`, mission models/server, cost/evidence scope and satellite tests were also inspected and regression-tested.

## Commands run

Used `.venv/bin/python -m pytest tests -q`, focused pytest runs after edits, `.venv/bin/python -m unittest discover -s tests -v`, `npm run build`, `npm run test:evidence`, `npm run test:terrain`, `npm run test:orbital`, `git diff --check`, snapshot comparisons and read-only workbook XML inspection. Browser verification used a local uvicorn server and existing cached terrain/kernels. The optional training-backed surface-coverage feature is unavailable in this app-only venv; no training environment was changed.

## Open questions and evidence limits

No blocking code decision remains for this scope. Verified satellite radios, service cost, independent spacecraft ephemerides, calibrated satellite/hardware response and a specified Earth station remain external evidence gaps. Values were not invented. Orbital geometry and environment scenarios remain simulated, timing is sampled, and ORA results remain separate.

No commits, pushes, new terrain downloads, raw-data edits or model-weight operations were performed.
