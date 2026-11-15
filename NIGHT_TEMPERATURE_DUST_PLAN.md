# Night temperature and lunar dust reliability plan

## Implementation status

The mission planner now evaluates nominal, cold, dust and combined cold/dust cases before choosing relays. `moonmesh/environment.py` shares the energy-conserving thermal solver with ORA, models local deposition and cleaning, and reduces solar output from accumulated dust. Both lander and relay power/temperature gate communication. The optimizer requires every case to meet communication and optional N−1 constraints. The combined case drives the displayed timeline; the original simplified result remains in the JSON baseline for comparison.

Expert mission inputs expose sink temperatures, heater and electronics power, hardware heat-loss parameters, lander endurance, and dust event timing/radius/severity/cleaning. UI, JSON, CSV and print exports show case outcomes and assumptions; JSON and CSV include node histories. ORA exposes thermal sinks and seeded dust hazards and now couples its dust load to solar output. ORA placement continues to report explicitly scoped static graph feasibility; its twin is the operational check.

Scientific calibration remains open: no Diviner product has been ingested and there are no hardware bench measurements. Effective thermal sinks, normalized deposition, and hardware defaults are labeled assumptions. Dust-induced thermal coating changes, quantitative mechanism damage, and stochastic event probabilities require evidence before adding coefficients. The current dust event is an explicit circular exposure scenario centered on the lander, rather than a plume trajectory simulation. The lander uses the same illustrative thermal design as the relays, with a separate battery and initial charge. The Earth receiver is externally powered. ORA mining equipment still assumes independent power.

## Original audit (before implementation)

MOONMESH does **not** yet include nighttime temperature or dust in its mission-level `feasible` result. In `moonmesh/science.py`, relay availability is Earth RF visibility × a fixed-load sunlight/battery estimate × surface line of sight; `moonmesh/network.py` then optimizes those paths. The lander's direct link is evaluated without a lander power or thermal state. `darkness_meets` only compares the longest dark interval with a configured limit.

ORA separately simulates battery charge, heater use, node temperature, freezing, and connectivity (`ora/thermal.py`, `ora/twin.py`). Its default dark thermal environment is a fixed **100 K**, and its 354-hour forced night is a stress case, not a site/date temperature forecast. ORA also has optional dust accumulation and random node failures; an independently entered, constant `dust_db` can disable optical trunks. Dust defaults to zero in the API and is absent from the main MOONMESH feasibility calculation. ORA's placement result is based on graph connectivity before those thermal and dust simulations.

Therefore, current “requirements met” means the stated geometry, RF, simplified power, and outage constraints pass. It does not certify that the hardware will remain operational through the night or dust exposure.

## Environmental model

The Moon has essentially no atmosphere and no wind driven dust storms. Model **dust exposure events and surface fouling** instead: lander ascent/descent plumes, rover traffic, excavation or mining, impacts, and a bounded electrostatic transport case. Keep occurrence and severity as explicit scenarios; do not invent a weather forecast or claim a measured storm probability.

Use LRO Diviner seasonal polar temperature products for site-specific surface-temperature bounds where coverage supports them. Preserve product ID, map resolution, season/local time, spatial interpolation, and missing-data status. A Diviner surface temperature is **not automatically the electronics radiative sink temperature**: derive or bound the effective sink using the hardware's view of sky and terrain, ground contact, solar input, and self-heating. Until that model is validated, run cold/nominal/warm thermal envelopes and label them as assumptions. Polar shadow duration must come from the existing time-dependent Sun/terrain geometry, not from a universal 354-hour night.

## Implementation sequence

1. **Define a shared per-node state.** Add a versioned environmental input and output contract for each mission sample: illumination, surface-temperature source/bounds, effective thermal sink, battery energy, internal temperature, heater draw, operating mode, dust load, solar/optical fouling, and failure reason. Validate units and time alignment. Make missing thermal data `unknown`, not a passing sample.
2. **Connect temperature to power and service.** Reuse and calibrate `ora/thermal.py` with candidate-specific site conditions and hardware parameters. Simulate heater load, charging, hibernation, recovery, and survival for each relay and for the lander/ground receiver if their power is part of the claimed direct service. A path is available only when all required endpoints are alive, powered, in an operating mode that can carry traffic, and have a feasible link. Keep the forced-night run as a separate stress test.
3. **Add dust exposure profiles.** Represent each event by time, source, affected area, deposition or optical-occlusion severity, duration, and mitigation/cleaning. Let accumulated dust reduce solar output and alter thermal surface properties where supported by a calibrated model; let local dust impair optical terminals and mechanisms. Preserve RF link behavior unless a specific mechanism and evidence support an RF penalty. Use `ora/dust.py` for parameterized failure-risk sweeps, with fixed random seeds and uncertainty ranges, rather than treating one stochastic run as a guarantee.
4. **Recompute the recommendation.** Feed the resulting time-varying node and link availability into `moonmesh/network.py` and ORA placement/resilience checks. Apply the existing availability, longest-blackout, and N−1 requirements to each selected environmental case. Report nominal, cold-night, dust-event, and combined cold-night-plus-dust results. A recommendation should be marked environmentally feasible only if it meets the declared cases; show which constraint fails otherwise. Keep the original geometry/RF result separately for comparison.
5. **Expose assumptions and evidence.** Add mission inputs for hardware thermal/power design and named dust scenarios. Show minimum node temperature, heater energy, minimum battery reserve, dust load/fouling, lost service intervals, and the source or assumed bounds for every environmental input in the UI, API, and exports. Change “requirements met” wording to identify which requirements were checked. Avoid an overall green status when thermal or dust inputs are missing or unvalidated.

## Verification and acceptance

- Unit tests cover temperature-dependent heater demand, battery depletion and recovery, freeze/restart behavior, dust fouling/cleaning, and time alignment. Test a powered relay that still fails thermally and a sunlit relay whose dust-covered panel cannot sustain its load.
- Integration tests show that a cold-night or dust case can change relay choice, mission availability, longest blackout, and N−1 status. Include the combined case and deterministic replay of stochastic dust seeds.
- Compare thermal predictions with Diviner-derived site bounds and, before any hardware-survival claim, bench measurements or a documented hardware thermal model. Record uncertainty and avoid claiming flight qualification.
- The acceptance condition is that every displayed pass/fail result identifies the evaluated environmental cases, data coverage, and assumptions, and that no service interval is credited while a required node is frozen, unpowered, or dust-disabled.

## Source starting points

- [NASA Lunar Surface Data Book, thermal environment and Diviner products](https://ntrs.nasa.gov/api/citations/20250002845/downloads/M2M-30044%20Lunar%20Surface%20Data%20Book%20Baseline_Final.pdf)
- [NASA lunar south pole environment](https://www.nasa.gov/reference/moonbase-environment/)
- [NASA lunar dust behavior and hardware effects](https://science.nasa.gov/solar-system/moon/phenomena-nasa-astronauts-will-encounter-at-moons-south-pole/)
- [NASA lunar atmosphere and surface dust transport](https://science.nasa.gov/moon/lunar-atmosphere/)
