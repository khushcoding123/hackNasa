# MOONMESH UI redesign plan

## Product outcome

MOONMESH should feel like a quiet lunar mission console: spacious, precise, minimal, and unmistakably space-oriented. A new visitor should understand the current mission, locate a direct-to-Earth blackout, see what terrain caused it, evaluate the screened relay recommendation, and test a relay failure without instruction. The interface is a planning prototype, so every claim must remain appropriately qualified.

This document specifies the complete frontend redesign. It does not change the science model, optimizer, API response shapes, or export data. The implementation owner should coordinate changes to `frontend/` and `web/` with the frontend owner named in `MOONMESH_DELIVERABLES.md`.

### Design principles

1. **Make the mission legible first.** Show the scenario, verdict, and next useful action before detailed readouts.
2. **Let data create the atmosphere.** Use the lunar terrain map, orbital geometry, coordinates, and timelines as the visual identity. Avoid stock space imagery and theatrical effects.
3. **Show why, not just what.** Connect a blackout to its time, terrain obstruction, Earth geometry, RF margin, and available relay paths.
4. **Be exact about uncertainty.** Missing terrain is uncertain, never successful coverage. Keep flat-horizon, terrain-aware, RF-feasible, relay-assisted, and uncertain states distinct.
5. **Keep expert depth available.** The primary journey stays calm and understandable while detailed settings, source data, and sensitivity analysis remain easy to reach.

## Information architecture

Use five primary navigation stops, in this order:

| Stop | User question | Existing material brought together |
| --- | --- | --- |
| **Mission** | What is the outcome for this scenario? | Overview, configuration, headline metrics, map, mission timeline, before/after |
| **Sites** | How do the landing regions compare? | Landing-site comparison and selected-point context |
| **Blackouts** | When does contact fail, and why? | Terrain analysis, communication analysis, outage table, horizon profile |
| **Relays** | Which screened surface relays help? | Relay planner, candidates, recommendation, power detail |
| **Resilience** | What happens when a relay fails? | Failure simulator, worst single failure, criticality |

Put **Sensitivity** and **Data & assumptions** in a secondary “Analysis” area that is always reachable from navigation. Keep JSON, CSV, and Print report together in a compact Export menu. Do not hide export until results exist; show disabled actions with an explanation while the first run is pending.

The app continues to open directly into Mission and automatically runs the existing default scenario. The existing in-page view state can power navigation; URL routing is not required. Navigation must preserve the current result, selected timestamp, selected candidate, and simulated disabled relays.

### Primary journey

1. Choose a landing region, UTC start/end dates, and sampling interval; run the mission.
2. Read the mission verdict and compare direct with relay-assisted communication.
3. Open Sites to compare Haworth, Nobile Rim 1, and Malapert Massif over the same period and assumptions.
4. Open Blackouts; select an outage or timeline time to connect the terrain map, horizon profile, and link readouts.
5. Open Relays to inspect the screened recommendation and individual candidates.
6. Open Resilience; disable a recommended relay and see the change in coverage and longest blackout.
7. Optionally run Sensitivity, inspect sources and assumptions, or export a report.

## Visual system

### Look and feel

The screen uses a nearly black canvas, restrained charcoal surfaces, fine cool borders, generous negative space, and crisp typography. A faint polar grid, orbital arc, or small coordinate marks may sit behind the Mission header at low contrast. These elements must never compete with charts or text. No large stock photograph, star field wallpaper, intense glow, or continuous animation.

| Token | Value | Use |
| --- | --- | --- |
| Canvas | `#070B12` | Main background |
| Raised surface | `#111B28` | Panels and menus |
| Subtle surface | `#0C1520` | Chart wells and nested controls |
| Border | `#294056` | Dividers and panel edges |
| Primary text | `#EDF5FF` | Main labels and results |
| Secondary text | `#A9BACD` | Explanations and metadata |
| Cyan | `#84DEFF` | Primary action, selection, lander, terrain profile |
| Green | `#73D8AE` | Available path, selected working relay, requirement met |
| Amber | `#F4BA72` | Sunlight, uncertainty, caution |
| Red | `#F2848C` | Blackout, failed relay, requirement unmet |
| Violet | `#BBA9FF` | Relay-assisted path |

Use Geist for navigation, prose, and large figures; use IBM Plex Mono for UTC times, coordinates, units, and compact instrument labels. Base text should be 14–16 px; supporting text should not fall below 12 px. Use a small number of type sizes, with 32–48 px headline figures and 20–28 px page headings. Keep a consistent 8 px spacing scale, 12–16 px panel radius, and subtle 1 px borders. Buttons should have clear primary, secondary, quiet, disabled, hover, active, and focus states. Icons must carry labels unless their meaning is unambiguous and available to assistive technology.

Motion is limited to 150–250 ms fades or position changes for menus, selection, and loading feedback. Respect `prefers-reduced-motion`. Avoid animated metric counts because they obscure exact values.

### Shared application shell

- **Desktop, 1200 px and wider:** A labeled 208–224 px left rail contains the five primary stops, Analysis links, and the prototype label. The content area uses a 12-column grid, with a maximum readable width around 1500 px.
- **Tablet, 768–1199 px:** Reduce the rail width and collapse multi-column content to two or one columns based on chart readability. Keep navigation labels visible where possible.
- **Phone, below 768 px:** Replace the rail with a keyboard-accessible menu button and a visible current-page title. Stack charts and panels; keep controls full width where needed. Never rely on horizontal page scrolling. Wide tables can scroll inside a labeled table region.
- A compact scenario bar shows the site, date range, UTC, sampling interval, and run state. Changing a form value marks the displayed results **Changes not applied** until Run Mission completes. The prior result remains visible with this label during the next computation.
- Give each view a one-sentence purpose and one clear primary action. Place technical explanations alongside the relevant chart or metric, with deeper detail in Data & assumptions.

## Screen specifications

### 1. Mission

Desktop opening composition: a wide terrain/map visualization occupies about three fifths of the hero area; a right-hand summary holds the mission verdict, active scenario, and three dominant readouts. The verdict uses the current active network's requirement status, not an invented score. A concise annotation explains whether relays improve direct contact and how long the longest remaining blackout lasts.

Below the hero, show five consistent metric cards: direct-to-Earth availability, sunlight availability, longest direct blackout, active-network availability, and worst single-relay-failure availability. Keep units, sample period, and definitions visible. Follow with the synchronized seven-track timeline, then a direct-versus-active-network comparison and a compact network diagram. Use a single primary next action: **Inspect blackouts** when direct outages exist, otherwise **Explore relays**. Do not imply that a relay recommendation is feasible when `result.feasible` is false.

### 2. Sites

Lead with a side-by-side comparison of the three supported regions. Show region name, coordinates, sunlight, direct RF availability, longest direct blackout, longest darkness, DEM coverage, and relay-assisted availability, all for the current date range and assumptions. Use aligned numbers and explicit units; indicate the current mission region. The existing Compare endpoint remains an intentional action because it performs additional computation. Show loading progress and per-run errors without clearing the current mission result.

Selecting a comparison row sets the region in the mission form and marks results as unapplied; it does not silently recompute. A selected-point panel shows location, elevation, slope, grid spacing, terrain coverage, and the map. State that the points are approximate research locations, as described in the project documentation.

### 3. Blackouts

Unify terrain and communication into a diagnostic workspace. The outage list and seven-track timeline share one selected timestamp with the terrain map and 360° horizon profile. Selecting an outage moves to its first sampled interval; dragging or using the timeline control updates all views. A clear status readout presents Earth azimuth/elevation, terrain angle, clearance after allowance, obstruction distance when known, RF margin, and the modeled reason. Show the Sun position and sunlight state as context.

The timeline legend and row labels must distinguish flat horizon, terrain visibility, direct RF, sunlight, relay path, combined path, and direct blackout. Uncertain samples need a distinct pattern or outline in addition to amber color. The horizon profile needs axis labels and a marker for the selected Earth and Sun positions. When there are no direct blackouts, show that result clearly and keep the timeline available. Never turn a missing obstruction distance into zero.

### 4. Relays

Lead with the map and recommendation summary. The summary states whether the constraints are met, the selected relay IDs, the number of eligible candidates, the required availability, the maximum blackout, and whether N−1 resilience was requested. Use **minimum among screened candidates** for a feasible result and **best evaluated configuration; requirements unmet** for an infeasible result. Do not use “global optimum.”

The map distinguishes all screened candidates, selected relays, disabled relays, lander, Earth direction, and the local scale. Selecting a marker or table row opens the same candidate detail. Candidate detail explains the governing reason in plain language, then shows range, slope, surface line of sight, RF margins, sunlight, usable power, full-path coverage, recovered direct-outage time, uncertainty count, and battery timeline. Keep the candidate table sortable by at least recovered coverage and deployment range; sorting is purely client-side and never changes the optimizer's recommendation.

### 5. Resilience

Place selected-relay disable controls beside the network diagram. Every toggle immediately updates active-network availability, longest blackout, requirement status, and timeline using existing `failure_states`; do not recompute a mission merely to simulate a failure. Show nominal and simulated results together, with the difference in percentage points and hours. Below, show worst single failure and the per-relay criticality table. If the recommendation uses no relays, explain that the direct path is the only active path and disable controls are unavailable. Keep the distinction between a user-simulated combination of failures and the precomputed single-failure table.

### Secondary analysis and exports

Sensitivity keeps its existing one-assumption-at-a-time sweep, but uses a scannable comparison table plus a clearly labeled network-availability visualization. Show variant errors in place, without implying the entire sweep failed. Data & assumptions groups provenance, terrain and ephemeris limitations, two-hop RF assumptions, battery assumptions, and meanings of uncertain samples. Link each source from the existing provenance data.

Retain JSON, CSV, and Print report actions. The printable report adopts the new visual hierarchy but remains ink-friendly and includes scenario configuration, key metrics, charts, relay feasibility, failures, assumptions, and provenance. Preserve the existing export data fields and filenames unless a functional defect is found.

## Interaction, data, and accessibility rules

- Keep `GET /api/config` and the existing mission, compare, and sensitivity API calls and response shapes. Reuse the `Result`, `Comparison`, `Candidate`, and `Sensitivity` data already typed in `frontend/types.ts`. Backend work is out of scope unless a missing field blocks a required display; document that need before changing the contract.
- Store one current timestamp and one selected candidate for the active result. Switching views does not reset either. A new successful mission run resets them to the first sample and first recommended candidate, as the current app does.
- A mission request disables duplicate submission and announces progress. On failure, preserve the last successful result, keep its scenario label, and show the error next to the form. Initial configuration failure provides a retry action.
- Use semantic headings, labeled controls, `aria-current` for navigation, an announced status region, logical focus after view changes, and visible focus outlines. Canvas interactions must have equivalent keyboard controls and adjacent textual readouts or tables. Do not convey uncertainty, outage, or success by color alone.
- Keep UTC explicit everywhere. Use consistent percentage precision and `h`, `km`, `m`, `°`, and `dB` units. Avoid wording that suggests flight readiness, guaranteed links, official landing coordinates, or guaranteed optimality.

## Implementation sequence

1. Define CSS tokens and shared components, rebuild the shell and navigation, and add responsive layouts. Preserve the existing API calls and exports while moving views.
2. Build Mission and the shared timestamp/map/timeline interactions; verify each headline figure against the current result fields.
3. Build Sites and Blackouts, including comparison loading and synchronized diagnostics.
4. Build Relays and Resilience, including candidate selection, infeasible results, and failure toggles.
5. Restyle Sensitivity, Data & assumptions, and Print report; finish loading, error, empty, and stale-result states.
6. Review keyboard access, contrast, reduced motion, mobile layouts, and the three-minute demo path with the frontend owner.

## Acceptance checklist

- [ ] A first-time user can complete the primary journey without verbal instruction.
- [ ] All five primary stops and both secondary tools are usable at 1440 px, 900 px, and 390 px viewport widths.
- [ ] Map, timeline, horizon, outage list, and numeric readouts agree at the selected timestamp.
- [ ] Flat-horizon, terrain-aware, direct RF, relay-assisted, blackout, and uncertain states remain distinct in both legend and display.
- [ ] Feasible and infeasible recommendations use accurate wording and show the relevant constraints.
- [ ] Disabling each selected relay updates the displayed metrics and timeline consistently with the API's failure state.
- [ ] Initial load, long computation, comparison error, no outages, no relays, incomplete DEM, and failed mission request all have understandable states.
- [ ] Navigation, forms, map selection alternatives, timeline controls, and relay toggles work by keyboard; focus remains visible.
- [ ] JSON, CSV, and print output still work and include configuration and provenance.
- [ ] `npm run build` passes, and the existing Python tests pass if implementation touches integration behavior.

The finished experience should feel visually memorable because the mission data is presented with clarity and restraint. Scientific qualification is part of the design, not a footnote.
