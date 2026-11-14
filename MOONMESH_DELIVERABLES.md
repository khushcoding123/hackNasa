# MOONMESH Deliverables and Ownership

## Final product objective

MOONMESH will compare lunar south-pole landing sites, identify terrain-induced direct-to-Earth communication blackouts, and find the smallest screened surface-relay configuration that satisfies mission availability and failure-resilience requirements under clearly stated assumptions.

The hackathon deliverable is a reliable planning prototype, not flight software. Every result must distinguish flat-horizon visibility, terrain-aware visibility, and estimated RF link feasibility.

## Ownership summary

| Owner | Primary area | Final outcome |
| --- | --- | --- |
| **Khush** | Lunar science, terrain, ephemerides, and data validation | Trustworthy Sun/Earth geometry, terrain horizons, site data, and scientific provenance |
| **Jeswanth** | Frontend, visual explanation, reports, and presentation | A clear end-to-end interface and a polished judge-facing demonstration |
| **Ayaan** | Relay network, optimization, resilience, API integration, and release | Physically constrained relay recommendations, failure analysis, integrated builds, and final submission |

Each person owns implementation, tests, documentation, and verification for their section. Cross-owner changes should be coordinated before editing another owner's primary files.

## Khush — Science, terrain, and validation

Primary files: `lunar_core/`, `moonmesh/data.py`, `moonmesh/science.py`, and science-related tests in `tests/test_moonmesh.py`.

### P0: Required for the final demo

- Verify the official landing-site catalog and ensure every displayed coordinate is described accurately as an official point, approximate research point, or custom point.
- Verify SPICE Earth and Sun azimuth, elevation, and range calculations against JPL Horizons for representative timestamps.
- Verify that the SPICE lunar frame, LOLA coordinate system, longitude convention, and polar-stereographic projection are consistent.
- Finalize multiresolution terrain horizons using 20 m near-field and 240 m far-field LOLA data, lunar-curvature correction, documented ray spacing, and a documented search radius.
- Confirm that missing terrain remains `uncertain` and is never counted as successful communication coverage.
- Verify blackout attribution: terrain direction, obstruction elevation, clearance, and obstruction distance must agree with the underlying horizon sample.
- Verify solar visibility and darkness-window calculations used by the relay battery model.
- Add or finish tests for projection round trips, curvature, missing terrain, ridge obstruction, time-sampling behavior, and real-scenario output.
- Finalize the data provenance and scientific limitations shown in the application and exports.

### P1: Credibility improvements

- Add a convergence check comparing coarse and fine time sampling around at least one blackout transition.
- Add terrain-clearance sensitivity using the configured DEM elevation bias and antenna height.
- Produce one validated reference scenario that the team can explain during judging, including the controlling terrain obstruction.
- Record known differences between geometric DE440 calculations and apparent JPL Horizons values.

### Khush definition of done

- The default mission runs from prepared NASA data without geometry or projection errors.
- Science tests pass and the reference scenario has defensible expected values.
- Every science output includes units, source information, sampling limits, and uncertainty language.
- Khush can answer: “Where did this number come from, and how was it validated?”

## Jeswanth — Frontend, explanation, and judge experience

Primary files: `frontend/`, `web/`, user-facing sections of `README.md`, screenshots, and presentation/demo materials.

### P0: Required for the final demo

- Finish the landing-site comparison view for sunlight, direct communication, longest blackout, longest darkness, and relay-assisted availability.
- Make the communication timeline clearly distinguish flat-horizon visibility, terrain-aware visibility, direct RF feasibility, relay assistance, and uncertain samples.
- Finish the terrain-horizon visualization showing Earth and Sun positions, terrain elevation, clearance, and the controlling obstruction.
- Finish the relay-planner map showing the landing site, all screened candidates, selected relays, distances, slopes, and link state.
- Finish the before/after comparison for direct communication versus the recommended network.
- Finish the resilience simulator with relay-disable controls, worst-case availability, and longest-blackout changes.
- Make assumptions and limitations visible without overwhelming the main workflow.
- Ensure the interface always says “minimum among screened candidates” or “best evaluated configuration,” never “global optimum.”
- Verify responsive layout, keyboard access, readable contrast, loading states, error states, and empty or infeasible scenarios.
- Finish JSON, CSV, and printable-report presentation so exported results include scenario configuration and provenance.

### P1: Presentation and storytelling

- Prepare a concise demo path: compare sites, reveal a blackout, explain the terrain cause, optimize relays, disable one relay, and show assumptions.
- Capture final screenshots and prepare a backup recorded walkthrough in case the live demo fails.
- Draft the three-minute judge presentation and keep the core explanation understandable to a non-specialist.
- Prepare concise answers for differentiation from LunarRF, physical realism of surface relays, and the planning-level scope.

### Jeswanth definition of done

- A new user can complete the core workflow without verbal instructions.
- All charts, maps, metrics, and exports agree with the API response.
- The application remains understandable in both feasible and infeasible scenarios.
- Jeswanth can deliver the complete demo within three minutes without skipping the limitations.

## Ayaan — Relay network, optimization, integration, and release

Primary files: `moonmesh/network.py`, `moonmesh/models.py`, `moonmesh/server.py`, relay-related portions of `moonmesh/science.py`, integration tests, repository management, and submission packaging.

### P0: Required for the final demo

- Finalize the reference relay model: mast height, deployment radius, slope limit, local and DTE radio profiles, required margins, data rate, battery endurance, and charging assumptions.
- Verify the separate lander-to-relay and relay-to-Earth link calculations and ensure a relay is available only when both links and power are available.
- Verify candidate screening for distance, local maxima, slope, separation, surface line of sight, DTE feasibility, and usable temporal coverage.
- Finalize the restricted candidate-set optimizer so it searches by increasing relay count and reports whether mission requirements are feasible.
- Verify tie-breaking using availability, blackout duration, deployment distance, terrain risk, and link margin.
- Finalize N-1 resilient optimization so requirements are evaluated after every individual selected-relay failure.
- Verify battery charge/discharge behavior, initial state of charge, darkness endurance, and power-related failure states.
- Verify network metrics: availability, longest blackout, total blackout, interval count, redundant-path time, and worst single failure.
- Finalize API validation, clear error responses, scenario comparison, sensitivity endpoints, and export-ready response schemas.
- Add or finish tests for RF margins, outage metrics, battery behavior, minimum configurations, infeasible constraints, and resilient selection.

### P1: Integration and submission

- Run full Python and TypeScript verification after science and frontend changes are merged.
- Check that README setup instructions work from a clean environment and that data-download failures are understandable.
- Freeze the final default scenario and expert-setting defaults used in the demonstration.
- Prepare a local-data and static-export fallback for the live demo.
- Coordinate final branch integration, resolve conflicts, tag the demo-ready commit, and verify the pushed repository.
- Complete the project submission text, source citations, limitations, related-work acknowledgement, and final links.

### Ayaan definition of done

- The optimizer never labels an approximate or restricted result as globally optimal.
- Relay recommendations satisfy the configured two-link, power, availability, blackout, and resilience rules.
- Python tests and the TypeScript build pass on the final integrated commit.
- Ayaan can answer: “Why was this relay selected, what assumptions make it work, and what happens if it fails?”

## Shared integration checkpoints

| Checkpoint | Owners | Acceptance condition |
| --- | --- | --- |
| Science/API contract | Khush + Ayaan | Units, status labels, uncertainty states, and response fields are agreed before frontend freeze |
| API/frontend contract | Ayaan + Jeswanth | Every displayed metric maps to a documented API field and survives missing or infeasible data |
| Reference scenario review | All | The team manually checks one site, blackout, relay recommendation, and failure case end to end |
| Claims review | All | No claim of flight readiness, guaranteed communication, global optimality, or unverified novelty remains |
| Demo rehearsal | All | Three-minute presentation and backup demo both complete successfully |
| Release gate | All | Python tests pass, TypeScript builds, exports work, sources are cited, and the final repository is pushed |

## Recommended merge order

1. **Khush:** finalize science outputs, reference scenario, and provenance.
2. **Ayaan:** finalize relay logic, API contract, resilience, and integration tests against those outputs.
3. **Jeswanth:** freeze the UI against the final API contract and complete the presentation assets.
4. **All:** run the end-to-end review, claims audit, and demo rehearsal.
5. **Ayaan:** create and push the final verified release commit.

## Final acceptance checklist

- [ ] Two or three landing sites can be compared over the same mission period.
- [ ] Earth and Sun geometry are terrain-aware and validated.
- [ ] Flat-horizon, terrain-visible, RF-feasible, and uncertain states are distinct.
- [ ] Blackout durations and causes are visible and exportable.
- [ ] Relay candidates have documented screening reasons.
- [ ] Both communication hops and relay power are required.
- [ ] The optimizer finds the minimum relay count only within the screened catalog.
- [ ] N-1 mode evaluates every individual selected-relay failure.
- [ ] Before/after and failure results use consistent metrics.
- [ ] Assumptions, data sources, uncertainty, and limitations are visible.
- [ ] Python tests and the TypeScript build pass.
- [ ] The live demo and backup presentation are ready.
