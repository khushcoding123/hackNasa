# MOONMESH communication architecture

## Product target

MOONMESH plans a communication path from a lunar lander through a surface relay and a lunar communications satellite to Earth:

**Lander → surface relay → lunar communications satellite → Earth receiver**

The product helps a mission planner choose surface relay locations and evaluate satellite access over the mission period. It seeks the smallest feasible relay configuration among screened candidates under availability, maximum-blackout, deployment, power, and resilience constraints. It does not promise a global optimum or continuous communication.

The satellite orbit or constellation remains to be selected. A hypothetical constellation must be labeled as a scenario assumption, with explicit orbital inputs and provenance; it must not be presented as an available operational service.

## Current implementation

The current MOONMESH planner calculates direct lander-to-Earth service and independent **lander → surface relay → Earth** paths. It does not yet calculate satellite orbits, satellite access, satellite radio links, or satellite failures. Existing coverage, blackout, and N−1 figures describe that baseline and must not be relabeled as satellite-assisted results.

The ORA lab also retains its current surface-network and Earth-visible gateway model. Its published measurements do not validate the proposed orbital architecture.

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

Satellite features are planned until the orbit, geometry, RF, power, optimization, API, visualization, and failure analysis are implemented and verified together. The local prototype continues to display its existing baseline in the meantime.
