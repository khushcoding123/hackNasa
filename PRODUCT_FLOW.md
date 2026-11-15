# MOONMESH: guided mission flow and 3D product direction

## Target communication architecture

The intended mission path is **lander → surface relay → lunar communications satellite → Earth**. Setup will include an explicit satellite scenario; relay selection will use time-dependent feasibility of all three links and node power. Network inspection will show satellite positions and the active path, blackout explanations will identify the failed hop or node, and resilience will include satellite failures and shared dependencies. See [Communication architecture](docs/COMMUNICATION_ARCHITECTURE.md).

These satellite capabilities are planned. The implemented journey below currently evaluates direct-to-Earth and lander → relay → Earth baseline paths. Existing results and exported metrics describe that baseline.

## Product diagnosis

The previous interface exposed the science model before explaining the task. It automatically calculated a default mission, kept a large configuration form on every page, and asked visitors to navigate between analysis categories. A visitor could not easily tell what to do first, which settings produced the visible result, or why a relay was useful.

The product's strongest existing capability is its connection between real terrain, time-dependent communication outages, and a physically constrained screened relay recommendation. The interface should teach that relationship through a single mission journey.

## Implemented journey

1. **Explore the Moon.** A dedicated interactive 3D globe opens first, using NASA SVS imagery. Drag, zoom, and keyboard camera controls work before any mission calculation. There are no mission towers or network connections on this page.
2. **Set up mission.** Choose the supported research region, UTC dates, sampling, coverage target, maximum blackout, tower count, and deployment radius. Deeper RF, terrain, battery, and resilience assumptions stay in Expert settings. Calculate network is the explicit submission action; startup only retrieves configuration.
3. **See your network.** After a successful API calculation, show the local LOLA terrain mesh with the lander and recommended relay towers. Whole Moon gives geographic context. Tower and link colors follow the selected sample and simulated relay failures. A select control offers a keyboard equivalent to clicking a tower. Screened candidates are an optional layer; they are not presented as recommendations.
4. **Understand blackouts.** Explain the before/after result using the existing synchronized timeline, outage list, horizon profile, and 2D map. Selecting a blackout identifies its terrain or RF cause. Continue leads to the failure test.
5. **Test resilience.** Disable a recommended relay and inspect changes in coverage and longest blackout. Export the applied scenario when finished, or return to setup to revise it.

Back and Continue controls repeat the same path. Analysis steps are unavailable until a mission succeeds. Editing parameters marks results as unapplied, disables result navigation and exports, and requires recalculation. Compare sites, detailed candidate tables, sensitivity, and source data remain under Optional analysis tools.

## 3D view contract

- Browser-native Three.js renders the scene. The Blender MCP ZIP has not been installed or activated.
- The global color texture comes from NASA's Scientific Visualization Studio CGI Moon Kit: https://svs.gsfc.nasa.gov/4720/ (2025 color map, 2048 × 1024 JPEG). This imagery is for illustration, not scientific terrain measurement.
- The local mesh comes from `Result.map.elevation`, including the API's cell-centered polar grid. Missing terrain cells remain gaps. Vertical relief is exaggerated ×3 and tower symbols are enlarged, both labeled in the UI.
- Lander and tower positions come from the applied API result. The local window is 20 km wide. Whole-Moon markers necessarily overlap at mission scale; local terrain is the default after calculation.
- In the current baseline, each selected relay represents a separate lander → relay → Earth path. The target path adds lunar satellite backhaul; satellite paths and positions are not yet rendered or calculated. Local Earth rays indicate direction; they do not depict an Earth station or a propagated radio beam. Scene lighting is illustrative and does not replace calculated sunlight.
- Green tower paths are available at the selected sample, amber paths are unavailable, and red towers are disabled. Dashed links are unavailable. Availability is taken from `temporal_coverage`, not inferred from rendered line intersections. Unknown data can contribute to unavailability; consult the diagnostic timeline for uncertainty.
- Camera controls and tower inspection have keyboard controls. When WebGL is unavailable, mission setup, 2D maps, textual readouts, and tables remain usable.
- Graphics are rendered on demand. Route changes dispose GPU resources and listeners. Rendering never changes the science calculation.

## What would improve the product next

| Priority | Addition | User benefit | Required foundation |
| --- | --- | --- | --- |
| Next | Satellite-assisted communication architecture | Plan lander → relay → satellite → Earth connectivity and explain each link | Explicit satellite scenario, orbital geometry, three-link RF/power checks, optimization, shared-failure analysis, and API/UI/export support; see [architecture](docs/COMMUNICATION_ARCHITECTURE.md) |
| Next | Explicit unmet-constraint explanation | Understand whether coverage, blackout duration, power, or resilience is limiting the recommendation | Explain existing API metrics against configured thresholds; do not promise an unevaluated fix |
| Next | Save and compare named mission scenarios | Compare alternatives without losing the baseline | Versioned scenario/result persistence and clear source timestamps |
| Next | Selected-relay contribution breakdown | Explain how much each tower improves the mission and what fails without it | Reuse recovered coverage and single-failure results; account for overlapping coverage |
| Later | Deployment route feasibility | Assess whether a tower can actually be reached | Terrain-aware traversability and route planning; current straight-line distance is only an estimate |
| Later | Candidate-level time-specific uncertainty | Distinguish unknown links from known failed links directly in 3D | Additional per-candidate sample status from the science/API layer |
| Later | Wider lunar coverage | Explore regions beyond the prepared south-pole sites | Prepared DEMs, validation, data download/storage strategy, and compute budgeting |

A decorative model alone would not answer these mission decisions. The 3D scene is useful when the user can connect a location to a calculated result, inspect the reason, and make a supported change.

## Verification targets

- Startup requests configuration without submitting a mission; globe has no tower overlays.
- Calculating a valid scenario opens Step 03 and renders only returned recommendations by default.
- Switching Globe/Terrain, changing time, selecting candidates, and using camera controls preserves the applied mission.
- Changes in setup lock results until recalculation. Failed calculation retains the prior result but does not unlock stale analysis.
- Both feasible direct-only missions and infeasible relay recommendations remain clear.
- Desktop and phone layouts have no horizontal page overflow. The existing TypeScript build and scientific tests pass.

## Globe exploration update

The opening Moon now occupies a full-width primary panel; the introduction follows below it. The initial view shows major maria, with more landmarks revealed by zooming or clicking. A curated 14-place catalogue includes major seas, two impact craters, poles, a far-side orientation reference, and the three supported research points. It is an orientation guide, not the entire IAU gazetteer.

Labels remain anchored to lunar coordinates, hide behind the globe, and avoid overlapping each other. Clicking the surface reports the actual clicked coordinates and distance to the closest catalogued reference center; it does not claim that the clicked point lies within that feature's boundary. Clicking a label or using Find a place focuses the camera and opens a sourced description. Labels can be switched off.

The initial 2K map is replaced on zoom by an on-demand 8K map for larger desktop viewers or 4K for small viewers and constrained GPUs. Standard imagery stays visible during loading and on a high-detail download error. The maps are derived from one NASA source so geographic registration remains consistent during the swap.

## Physical topography update

The globe is now a displaced elevation mesh rather than an undeformed sphere with a color image. NASA LOLA global elevations change vertex radii and surface normals. Shaded topography is the default surface mode; elevation colors and lunar imagery over the same displaced mesh are optional. A labeled relief selector offers true scale or ×5, ×10 (default), and ×20 enhancement. An inspection-light control reveals terrain shape without implying simulated mission sunlight.

The browser mesh refines on zoom. Landmark positions and global mission markers use the rendered elevation surface; picking intersects the actual mesh, and the place inspector reports the sampled LOLA elevation. Global data, relief exaggeration, and mesh sampling are for visual inspection and do not change availability, RF, power, or optimization results. Run `npm run test:topography` to verify grid registration, units, periodic longitude, geometric displacement, normals, and relief scaling.
