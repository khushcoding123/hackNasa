# MOONMESH communication architecture

## Product target

MOONMESH plans a communication path from a lunar lander through a surface relay and a lunar communications satellite to Earth:

**Lander → surface relay → lunar communications satellite → Earth receiver**

The product helps a mission planner choose surface relay locations and evaluate satellite access over the mission period. It seeks the smallest feasible relay configuration among screened candidates under availability, maximum-blackout, deployment, power, and resilience constraints. It does not promise a global optimum or continuous communication.

The satellite orbit or constellation remains to be selected. A hypothetical constellation must be labeled as a scenario assumption, with explicit orbital inputs and provenance; it must not be presented as an available operational service.

## Current implementation

The configurable MOONMESH planner calculates direct lander-to-Earth service and independent **lander → surface relay → Earth** paths. Its coverage, blackout and N−1 figures describe that baseline. The separate Haworth reference scenario exercises the complete satellite architecture with one fixed relay and one hypothetical satellite.

### End-to-end Haworth reference simulation

Open **End-to-end simulation** in the workspace menu and select **Run Haworth mission**. The app calls `GET /api/mission/reference` and opens playback at the first connected sample if one exists. Inspect each hop, jump to a blackout, disable the relay or satellite, or download the JSON with the sampled geometry, hop results, baseline comparisons, failure states, and assumptions. The Moon view clips the Earth link to a direction marker at lunar scale.

The reference takes 288 samples at five-minute intervals on 1 November 2026 UTC. The 500 km two-body circular orbit starts northbound over the fixed relay. SPICE supplies the DE440 lunar frame rotations and Earth/Sun positions. The Earth receiver is hypothetical and uses a spherical Earth with approximate sidereal rotation. Access is screened using the relay's LOLA horizon and a 10° elevation mask. Backhaul checks lunar occultation and a 10° Earth receiver mask. The reference assumes ideal tracking, one simultaneous stream, external power at the receiver, and nominal satellite power. The lander and relay use the existing cold + dust model with fully charged batteries at the start. Results are simulated and sensitive to sampling and assumptions.

The two nominal baselines use the planner's Earth-center geometry and RF model on the same time grid. The satellite path uses the fixed receiver and its elevation mask. The reference milestone uses local LOLA Haworth terrain and SPICE kernels. Run `python -m moonmesh.data` to prepare them. The configurable planner and orbital relay placement remain separate work.

The ORA lab also retains its current surface-network and Earth-visible gateway model. Its published measurements do not validate the proposed orbital architecture.

### Satellite access preview

The Network layout step includes a geometry-only satellite preview. It draws straight blue paths from each planned surface node to an assumed satellite when the satellite is at least 10° above that node's local spherical horizon. The pass slider reports elevation and slant range for every node, including nodes with no access. The satellite marker is enlarged; positions and paths use metre coordinates.

The hypothetical scenario is one circular polar satellite at 500 km altitude, with its plane chosen to pass over the selected area's centre at preview time zero. Its approximately 158.3 minute period uses lunar GM = 4902.800118 km³/s² ([JPL](https://ssd.jpl.nasa.gov/astro_par.html)); uniform body rotation uses a 27.321661 day period. This is a relative pass demonstration, not a mission-date ephemeris or an operational constellation. Each node uses its terrain elevation plus an assumed 10 m antenna height. Terrain screening beyond the spherical elevation mask, RF, pointing, capacity, power and satellite-to-Earth access are not evaluated. The preview does not change placement, coverage, blackout or failure results.

Geometry checks: `node --experimental-strip-types tests/test_orbital_preview.mjs frontend/orbital-preview.ts` (Node 22.6+).

## Required satellite model

- Define each satellite's trajectory or orbital inputs, epoch, coordinate frame, mission-time coverage, and source. Earth/Sun ephemerides alone do not supply a communications satellite trajectory.
- At each mission sample, evaluate lander-to-relay surface line of sight and RF margin, relay-to-satellite terrain clearance and RF margin, and satellite-to-Earth occultation and RF margin.
- Require usable power and operational state at every participating node. Document satellite power and Earth receiver assumptions as well as surface thermal, battery, and dust assumptions.
- Count a live end-to-end path only when all three links and the participating nodes are available at the same sample. Missing geometry or link inputs remain uncertain and are excluded from credited availability.
- Evaluate every configured satellite and changes in which satellite provides service. State antenna pointing, tracking, handover, and any capacity assumptions; do not imply they are validated by visibility alone.
- Treat store-and-forward delivery, inter-satellite links, and chains of surface relays as separate extensions. The initial product target measures simultaneous end-to-end connectivity and does not assume those extensions.

## Optimization and resilience

Choose surface relays using the full time-dependent path to Earth through the configured satellite scenario. A relay need not see Earth directly to qualify for this architecture. Compare the orbital path with the existing direct and surface-to-Earth baselines using the same mission period, sampling, and constraints.

Report availability, longest blackout, recovered outage time, deployment constraints, power limits, and uncertainty. Identify whether each outage comes from the surface hop, satellite access, satellite-to-Earth link, or node power/state. Orbital backhaul may improve coverage, but the chosen orbit and links determine the result.

Test both individual surface-relay failures and individual satellite failures. Report them separately, including shared satellite dependencies: two surface relays using one satellite do not constitute two independent complete paths. Label baseline N−1 results as surface-relay failures only.

## Product flow and acceptance

1. Explore the Moon and select a landing region and mission period.
2. Configure communication goals, surface constraints, and an explicit satellite scenario.
3. Calculate relay placement against all three links and node availability.
4. Inspect the lander, relays, satellite positions, and active path at the selected time.
5. Explain blackouts by failed link or node and compare against the direct-to-Earth baseline.
6. Disable a surface relay or satellite and inspect the remaining complete paths.
7. Export the architecture, orbital inputs and sources, link and power assumptions, sampling, uncertainty, and results.

The Haworth reference covers the fixed-path integration milestone. The full acceptance flow also requires configurable orbital scenarios and relay placement against the three-hop path.
