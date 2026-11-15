# Earth network data for MOONMESH

## Purpose and current status

MOONMESH explains why a lunar landing point loses communication and compares four responses: choose a better site, move a short distance, raise the antenna, or deploy a surface relay. The November 2026 lunar study found that a relay helped at the tested Haworth point, while other sites needed no relay or had no reachable single-hop relay candidate. The study therefore supports a decision process for **site-specific mitigation**, not a general claim that the Moon needs a multi-relay mesh. See [`analysis/moonmesh_relay_gate.json`](analysis/moonmesh_relay_gate.json) and [`analysis/moonmesh_haworth_refinement.json`](analysis/moonmesh_haworth_refinement.json).

This document selects three Earth datasets for the **next validation phase**. They have been identified, but no Earth data has been imported into the application yet. Earth examples will help test terrain line of sight, endpoint placement, and network-path reasoning. They will not be used to calculate lunar availability percentages or to train a model that overrides lunar geometry.

## The three sources and their roles

| Source | What we obtain | Where we use it | Why it was selected |
| --- | --- | --- | --- |
| [FCC Universal Licensing System (ULS) microwave records](https://opendata.fcc.gov/Wireless/FCC-Universal-Licensing-System-ULS-/x28i-i4z4) | Public records of licensed fixed microwave paths, including transmitter and receiver locations and path technical details. The FCC [describes the location and path fields](https://wireless.fcc.gov/auctions/59/resources/03_AKnerr.pdf); its [public-access guide](https://wireless.fcc.gov/uls/documentation/pa_intro24.pdf) describes bulk license files. | Earth link examples: endpoint separation, mast or antenna assumptions when available, and recorded path layout. | Fixed point-to-point links are a closer analog to a lander-to-surface-relay hop than ordinary cellular coverage polygons. |
| [USGS 3D Elevation Program (3DEP)](https://www.usgs.gov/3d-elevation-program/about-3dep-products-services) | Digital elevation models (DEMs), including broad approximately 10 m coverage and finer data where available. | Terrain beneath the selected FCC links; horizon profiles, ridge attribution, slope, line of sight, and height/position sensitivity. | It lets us check whether the terrain calculations produce intelligible explanations on real landscapes. The data is free and has no use restrictions according to USGS. |
| [FunkFeuer wireless-mesh topology snapshots](https://zenodo.org/records/2635054) | A downloadable historical record of network topology collected from 15–28 January 2016 for link-quality research. | Separate graph/path experiments: which nodes and links are connected, how paths change across snapshots, and what losing a link does to connectivity. | It supplies actual network relationships and changing link quality, which isolated tower coordinates cannot provide. |

**Priority:** implement FCC + 3DEP as one small Earth case study first. Add FunkFeuer after that case can produce and explain terrain results. The third source is research input for path reasoning; it does not justify adding a multi-relay recommendation to the lunar product by itself.

## How the Earth case study works

1. **Choose a bounded, terrain-rich area.** Select a small set of active FCC fixed microwave paths with usable coordinates. Record the extract date, service codes, license status, frequency, endpoint elevations, and antenna heights where those fields are present. Keep incomplete records visibly incomplete; do not fill missing heights with unlabelled guesses.
2. **Load matching 3DEP tiles.** Convert the endpoints and DEM to a consistent horizontal coordinate system and vertical datum. Record tile identifiers, resolution, acquisition date, and gaps. A bare-earth DEM does not represent every tree or structure that may affect a real link.
3. **Reconstruct each terrestrial path.** Sample the elevation profile between the endpoints, account for **Earth** curvature, and calculate terrain clearance at the recorded antenna heights. Earth atmosphere and refraction assumptions, if used, must be explicit. The existing lunar-curvature calculation cannot simply be run on Earth with its current radius and projection.
4. **Test the same four mitigation questions.** For an obstructed test point, compare an alternate endpoint, short location shifts, antenna-height changes, and screened intermediate sites. Report clearance and deployment distance before reporting an estimated RF margin. For each option, state which terrain obstruction it changes.
5. **Review against the recorded network.** A licensed path is evidence that an operator registered that configuration. Agreement with an open modeled path is a useful plausibility check. Disagreement is a reason to inspect DEM quality, endpoint data, antenna height, refraction, and record status. A license alone does not prove measured uptime, throughput, or optimal placement.
6. **Save a reproducible Earth report.** Include source versions, coordinate and height assumptions, terrain profiles, candidate list, and the reasons each option passed or failed. Keep these results separate from the lunar mission report.

## How FunkFeuer fits later

FunkFeuer is for a second experiment after the terrain case study. Convert each snapshot into a graph of nodes and observed links, then ask: if one link disappears, is another path available? Which nodes are critical? Does a proposed added link create a useful path? Preserve timestamps and distinguish observed links from hypothetical ones.

Those questions can exercise the reasoning behind `moonmesh/network.py`, which computes path availability and failure outcomes. The dataset is historical and comes from a terrestrial community network. Its link changes cannot be treated as lunar Earth-visibility windows, and its performance numbers cannot be copied into a lunar RF budget. The current lunar evidence favors simpler, often single-relay decisions.

## Relationship to the current application

| Current component | Earth validation work | Lunar application afterward |
| --- | --- | --- |
| `lunar_core/horizon.py` and `moonmesh/science.py` | Check the *method* of terrain-horizon and obstruction explanations with 3DEP, using separate Earth geometry. | Continue using LOLA terrain, lunar curvature, and SPICE Earth/Sun positions for mission timelines. |
| `moonmesh/science.py` candidate and `surface_path` logic | Compare screened intermediate sites and terrain-clear paths with FCC endpoint examples. | Continue screening reachable lunar relay sites around the selected landing point. |
| `moonmesh/network.py` | Use FunkFeuer snapshots to exercise path and link-loss reasoning if the lunar product expands to multiple paths. | Recommend a relay only when it improves the stated mission requirements more meaningfully than the simpler options. |
| User interface and exports | Show an Earth validation report as a clearly labeled example or development artifact if useful. | Keep lunar recommendations based on NASA data and visible mission assumptions. |

The Earth workflow should live in a separate analysis area, for example `analysis/earth_validation/`, rather than loading FCC or USGS data in the lunar mission API. Shared, body-independent calculations can be reused only after Earth and Moon coordinate, curvature, and propagation assumptions are explicit.

## What success looks like

The first Earth case should produce at least one clearly open path and one path whose result needs investigation or mitigation, with inspectable terrain profiles for both. For each example, MOONMESH should explain the controlling terrain, show what a site shift or antenna change does, and recommend an intermediate relay only if it provides a meaningful improvement under stated constraints. The report must also show when the available records or DEM are too uncertain for a confident conclusion.

This validates whether the *decision process* is understandable and technically consistent. The lunar claim still rests on the NASA LOLA and JPL SPICE analysis, with its own uncertainty and mission-specific assumptions.
