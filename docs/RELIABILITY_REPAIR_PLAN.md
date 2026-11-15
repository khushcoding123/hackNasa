# MOONMESH reliability repair plan

Prepared 14 November 2026 from the repository audit. This is proposed work, not an implementation or a validation certificate.

## Target and completion levels

Build a reproducible planner that explains when a communication path fails, identifies the controlling link or node, and reports the evidence and uncertainty behind each result.

Keep three distinct outputs:

1. **Calculated windows:** terrain obstruction, Sun/Earth/satellite geometry, and other deterministic constraints.
2. **Scenario outcomes:** cold, dust, equipment loss, and compound events under explicit assumptions.
3. **Forecasts or probabilities:** only where historical observations and a validated equipment-response model support them, within a stated forecast horizon. A long mission simulation is not a long-range solar-storm forecast.

Milestone A is a reproducible direct-to-Earth planning demo. Milestone B adds the satellite architecture. Milestone C adds independently validated environmental risk analysis. No milestone means every possible failure is predictable or the hardware is flight qualified.

## 0. Establish the baseline and remove misleading status

**Problem:** reports describe trained models that are absent locally; documentation contains older results; the full test run was interrupted during an import and included skipped data-dependent tests.

Work:

- Preserve current uncommitted work and record the code revision plus a diff hash for every new benchmark. Do not overwrite historical reports.
- Add a preflight command that inventories terrain, kernels, Earth training inputs, lunar feature caches, model files, library versions, checksums, and missing dependencies.
- Expose the actual loaded model, version, fallback method, and load error through the API, UI, and exports. A historical model card must not establish that a model is active.
- Separate result status into requirements met, requirements failed, and insufficient evidence. Show the architecture, evaluated hazards, and assumed parameters beside the result.
- Reconcile README, ORA documentation, Earth-data plans, and deliverable checklists with the implementation. Mark thermal/twin figures predating the new solver as historical.
- Diagnose the slow import and finish the full test suite. Separate dependency startup, computation, and data-download time before deciding what needs optimization.

**Acceptance:** one status report accurately describes this checkout; missing required data cannot produce a validated status; the baseline test report lists every failure, skip, and its reason.

Primary areas: `ora/scenario.py`, `ora/api.py`, `moonmesh/server.py`, frontend status and exports, documentation.

## 1. Make data and model restoration reproducible

**Depends on:** phase 0.

Work:

- Create a versioned source manifest: URL/product ID, acquisition date, license, checksum, units, frame/datum, spatial/time coverage, and intended use.
- Recover the original training assets if available. Otherwise reacquire FCC/SRTM inputs and regenerate lunar windows, with changed source snapshots explicitly recorded.
- Restore missing ORA site crops, including Shackleton and Connecting Ridge, from the existing LOLA products.
- Train and package the siting prior, lunar surrogate, and marginal-gain model with their feature schema, seed, dependency lock, dataset/split hashes, and model hashes. Prefer a versioned artifact bundle over committing bulk data.
- Check artifact compatibility on load and invalidate cached model loading when artifacts change; require restart or explicit reload where necessary.
- Reproduce evaluation and stress reports from the packaged models. If old inputs cannot be recovered, publish a new baseline rather than promising identical old numbers.

**Acceptance:** a clean environment can prepare inputs, load the models, and generate traceable reports. An offline demo bundle works. Corrupt or incompatible artifacts produce a visible failure or labeled heuristic fallback.

Primary areas: `moonmesh/data.py`, `ora/data.py`, `ora/lunar_windows.py`, `ora/train.py`, `ora/scenario.py`, setup scripts.

## 2. Validate relay placement without overstating machine learning

**Depends on:** phase 1.

Work:

- Compare heuristic-only, exact greedy, lunar-surrogate-only, Earth-prior-only, the existing 80/20 blend, and the marginal-gain selector using identical candidate sets and budgets.
- Choose blend weights and other settings on training/validation data. Keep the Earth prior only if it adds measurable lunar placement value.
- Treat previously inspected test windows as a legacy benchmark. Reserve a new geographically buffered final holdout before further tuning; account for overlapping feature and label footprints.
- Freeze acceptance criteria before evaluating the new holdout. Record any later changes as a new evaluation version. Do not present coverage calibration as failure-probability calibration.
- Address independent DEM noise with smoothed/multiscale features; inspect peaks, low-variance windows, mast-height shifts, and out-of-domain terrain. Retain exact final geometry checks.
- Report coverage error, per-region ranking, selection efficiency versus exact greedy, runtime, uncertainty, and worst-case regressions. Preserve limitations when a simpler baseline wins.

**Acceptance:** the selected approach improves the declared placement objective against the simple baseline on new held-out data, or the simpler approach ships. Every recommended set passes exact geometry checks. Results outside evaluated terrain/mast ranges are labeled unsupported.

Primary areas: `ora/features.py`, `ora/train.py`, `ora/evaluate.py`, `ora/stress.py`, `ora/selector.py`, `ora/benchmark.py`.

## 3. Calibrate cold, power, and dust behavior

**Depends on:** phase 0; may progress alongside phases 1–2.

Work:

- Select Diviner products appropriate to each site and season, documenting coverage, resolution, interpolation, uncertainty, and gaps. Retain explicit assumed envelopes where observations do not support a site/time estimate.
- Derive or bound effective equipment thermal sinks from sky/terrain view, sunlight, ground contact, and hardware design. Do not substitute measured surface temperature directly for an electronics sink.
- Replace placeholder module values with sourced hardware specifications or measurements. Model lander and relay designs separately where their properties differ.
- Calibrate energy balance, insulation, heater control, battery usable capacity versus temperature, charging, and operational limits using bench data or a documented reference hardware model. Include hot-side limits and heating/recovery behavior where supported.
- Model dust exposure from declared sources, affected area, accumulation, cleaning, and solar/optical degradation. Calibrate supported effects; keep unsupported mechanisms and coefficients explicitly unknown.
- Rerun nominal, cold, dust, and combined scenarios on real mission terrain with the current solver. Refresh thermal/twin/battery benchmarks.

**Acceptance:** parameter provenance is inspectable; thermal residual and energy-balance tolerances are agreed before calibration testing; required nodes receive no service credit outside power or operating limits. Synthetic tests and independent physical/reference comparisons both pass. Uncalibrated dust cases remain scenario results.

Primary areas: `ora/thermal.py`, `ora/dust.py`, `ora/modules.yaml`, `ora/twin.py`, `moonmesh/environment.py`, `moonmesh/models.py`, `tests/test_environment.py`.

## 4. Add space weather and Earth receiver hazards

**Depends on:** phase 0 for contracts; calibrated equipment response from phase 3 for quantitative failure claims.

Work:

- Define a hazard-to-component map: particle events, charging, radiation effects, receiver-side propagation, station availability, and recovery. Distinguish solar wind measurements, energetic particles, and RF interference instead of assigning one generic storm penalty.
- Identify authoritative archived and live products with explicit timestamps, units, quality flags, latency, licenses, and usable forecast horizons. Verify relevance and transport/geometry assumptions before mapping an Earth-near observation to lunar exposure.
- Build historical event replay first, then optional live ingestion. Add stale, missing, and conflicting data behavior.
- Obtain equipment susceptibility and recovery evidence. Until then, expose bounded upset/shutdown/degradation scenarios rather than invented outage probabilities.
- For chosen Earth stations and frequencies, add supported atmospheric attenuation, antenna elevation/pointing, station scheduling, and receiver failures. Model terrestrial wind only where structural/pointing evidence supports a mechanism.
- If sufficient labeled outage and exposure data exists, train a separate hazard-response model with chronological/event-separated splits and quiet-period controls. For forecasts, use only inputs available at forecast issuance time.
- Evaluate event detection, false alarms, missed events, timing error, probability calibration, lead time, and skill against simple baselines. Freeze acceptance limits before testing.

**Acceptance:** each modeled hazard has an exposure source and a justified component response. Without outage ground truth, the product says scenario analysis and makes no predictive-skill claim. Forecast skill is established only on unseen events and at the stated lead time.

Proposed areas: new hazard ingestion/response modules; integration into `moonmesh/environment.py`, `ora/twin.py`, API schemas, exports, and event-replay fixtures.

## 5. Implement the complete satellite communication path

**Depends on:** phase 0; initial orbit/geometry work can proceed alongside calibration. Integrate validated environmental behavior as it becomes available.

Work:

- Select an explicit hypothetical or sourced orbit scenario, with trajectory, epoch, coordinate frame, time coverage, and provenance. Do not imply an operational satellite service exists.
- Implement relay-to-satellite clearance and RF margin, satellite-to-Earth occultation and RF margin, and satellite power/state.
- Model antenna pointing/tracking, handover interruptions, and the capacity assumptions required by the communication goal.
- Credit service only when lander → relay → satellite → Earth is simultaneously usable. Compare with direct and two-hop surface baselines over the same dates and samples.
- Optimize relay choices using complete paths; a relay may qualify without directly seeing Earth.
- Test individual relay, satellite, link, and ground-station losses separately. Include shared dependencies: two surface relays using one satellite do not provide satellite redundancy.
- Add active-path visualization, failed-hop explanations, orbital inputs, and architecture-specific exports.

**Acceptance:** independently checked reference trajectories and synthetic occultation cases agree within predefined tolerances. Losing any required hop removes the path. Shared-satellite failure tests pass. API, UI, and exports show identical end-to-end results.

Primary contract: `docs/COMMUNICATION_ARCHITECTURE.md`; integration areas: geometry, `moonmesh/science.py`, `moonmesh/network.py`, ORA twin, mission schemas, frontend.

## 6. Validate outage timing, compound failures, and uncertainty

**Depends on:** baseline phases 1–3; repeat for satellite and space-weather integrations.

Work:

- Establish reference scenarios across supported sites, seasons, shadow conditions, mast heights, and mission lengths, including feasible and infeasible missions.
- Refine time steps around contact and power transitions. Demonstrate convergence at 60, 30, 15, and 5 minutes, with finer boundary checks where needed. Report unresolved timing uncertainty.
- Inject cold plus dust, lost critical relay, lost satellite, regional damage, common receiver failure, missing terrain, stale hazard feeds, and delayed recovery. Include failures that eliminate every path.
- Sweep uncertain hardware and environmental inputs with reproducible seeds. Distinguish parameter ranges, simulation sampling uncertainty, and calibrated real-world event probabilities.
- Report availability, longest/total blackout, onset and recovery, reserve, temperatures, cause attribution, and worst single failure. Preserve multiple simultaneous causes.
- Compare against independent observations where available and independent analytical/reference implementations otherwise. Geometry agreement alone must not validate thermal or weather behavior.

**Acceptance:** predeclared timing/metric tolerances pass; uncertainty changes with input quality; no outage disappears through a missing-data default; each unavailable interval has traceable causes or an explicit unknown state.

## 7. Close the underground and external benchmark gaps

**Dependency:** separate track; not a blocker for a surface-only release when scope is clear.

- Compile and flash the ESP32 logger, collect distance/bend/RSSI and packet-delivery measurements, and record radio configuration and uncertainty.
- Fit the tunnel model on one measurement subset and test on independent runs/layouts. Keep the unsuitable UWB RSSI fit rejected; ray tracing is not measurement ground truth.
- Acquire a licensed real mine/cave survey, verify its coordinate scale and graph connectivity, and replay placement, repeater loss, and collapse scenarios.
- Resolve the Tall Lunar Towers comparison using matching location, radius, terrain, tower height, and receiver height. Publish remaining disagreement.
- Either implement the separate coverage-training specification with a demonstrated purpose or explicitly defer it; do not train a duplicate model merely to complete a checklist.
- Complete LunaNet terminology review, dataset attribution, and project licensing before a public release that relies on them.

**Acceptance:** measured tunnel validation is clearly separate from synthetic tests and lunar extrapolation. Benchmarks are reproducible, with unresolved mismatches visible.

## 8. Release gates and evidence package

- Keep fast unit CI, then add an explicitly invoked data-backed integration/release job with versioned artifacts. Missing mandatory fixtures or models must fail that job rather than skip.
- Run the complete Python suite, TypeScript checks, frontend build, terrain tests, API/UI/export consistency checks, and clean-install/offline-demo checks.
- Bind every report to code, data, model, configuration, and split hashes. Archive current screenshots, a backup demo, and source/assumption lists.
- For Milestone A require phases 0–3 and 6 for the surface baseline, with remaining unsupported hazards visible. For Milestone B additionally require phase 5 and its phase-6 rerun. For Milestone C require the applicable phase-4 independent validation. Phase 7 is required only for underground capability claims.
- Publish a hazard coverage table stating implemented, assumed, independently validated, or unsupported for every claimed risk. Release labels must match that table.

## Execution order and external dependencies

Start with **0 → 1 → 2**, while progressing **3** and the data/response design of **4**. Build **5** against the explicit architecture contract, then run **6** on each integrated milestone and finish **8**. Keep **7** as a distinct workstream.

The first implementation slice is preflight + honest model status + restored model/data artifacts + a complete baseline test report. It establishes whether subsequent improvements are actually active and reproducible.

External inputs that can limit scope: original training snapshots, representative hardware specifications and bench access, usable thermal/dust calibration data, equipment susceptibility/outage records, chosen satellite trajectories, chosen Earth receiver assumptions, and licensed mine surveys. Missing inputs trigger explicit scenario-only or unsupported status, not guessed validation. Delivery estimates should follow the preflight and input inventory.
