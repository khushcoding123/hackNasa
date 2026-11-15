# Communication outage and relay-choice evidence

Implemented scope: the dated mission planner, including optional simulated satellite paths. ORA area-layout results remain separate. No new candidate search strategy, training pipeline, radio defaults, or environmental calibration is introduced.

## Outage investigation

The mission response adds `diagnostics.version = 1`, with explicit node, link, and complete-path identities. Links retain numerical geometry, configured RF thresholds and all gate states; `null` means unknown evidence. Unknown terrain blocks service credit. Missing satellite radios preserve the existing geometry-only upper bound and remain visible as unknown RF.

Every existing failure state adds `outages` for the primary cold/dust case and `cases` for all four environmental cases. Each case contains combined, assisted and lander timelines, metrics, network outage intervals and individual selected-path outage intervals. These describe the same applied relay set, including compound relay/satellite failures; they are not re-optimized after failure.

Outages use half-open `[start, end)` UTC intervals. Cause segments retain simultaneous component conditions and changes within an interval. Start/end boundary flags distinguish mission clipping from sampled transitions. Sample resolution is the configured 5–60 minutes; transitions between samples are unresolved. No exact physical transition-time guarantee is made.

Node coordinates and link endpoints locate failures. Horizon evidence identifies adjacent sampled rays, interpolation weights, controlling terrain samples and approximate coordinates. These are not exact intersections along the interpolated bearing. Surface-link evidence retains the minimum sampled signed clearance and its approximate location. Missing terrain locations remain null with a TODO. The thermal solver exposes irreversible freeze and interval service state; independent electrical shortfalls after freeze or unknown environment remain unknown rather than being inferred from SOC.

The default blackout UI is Active network outages. Users can inspect direct-to-Earth or other selected paths, change the environmental case, toggle failed nodes and click a blackout or cause segment to synchronize the timeline, selected relay, map, scene and numerical inspector. A blocked path does not become a network outage when another usable path rescues it.

## Relay-choice evidence

The production environmental optimizer can collect evidence while enumerating combinations. Selection behavior is preserved: choose the first affordable relay count passing all four cases, then maximize worst-case availability and minimize worst-case longest blackout. Resilient missions include single relay and shared-satellite loss. If no configuration passes, retain the existing best evaluated fallback.

Feasible score ties preserve the existing lexicographically greatest candidate-index tuple; fallback score ties preserve the first enumerated tuple. Equal scores are disclosed rather than attributed to invented physical advantages. The evidence pass continues through the allowed affordable counts to produce comparisons without changing the selected configuration.

`optimization_evidence.version = 1` contains the objective, catalog scope, screening rules/counts, eligibility reasons, evaluated/feasible counts, ties, selected configuration, no-relay baseline, best configuration at each allowed count, and up to five distinct same-count alternatives. Each selected relay has a removal comparison and best eligible single replacement. Conditional coverage gains and recovered hours are measured against the remaining selected set; overlapping relay coverage is not credited twice. Marginal summaries explicitly use the cold/dust case; comparison reports retain all cases.

Displayed configurations include nominal case timelines, availability, longest/total blackout, worst single failure, installed relay cost and violations. Alternative preview locations and timelines appear in violet and do not replace the applied recommendation. Failure toggles apply to the recommendation, not the nominal alternative preview. Claims remain minimum within the screened catalog, not global terrain optimality.

## Export and provenance

The Excel request adds `environment_case` and optional `preview_config_id`; invalid cases/previews are rejected. New sheets are Network Outages, Outage Causes, Link Evidence, Node Evidence, Relay Alternatives and Marginal Gains. Active-case timelines, disabled nodes and preview identity are exported alongside configuration, source provenance and code revision/working-tree hash. JSON includes the complete evidence snapshot and current view selection. Legacy fields remain readable for older consumers and snapshots.

## Acceptance and verification

- Synthetic tests cover rescue by another path, simultaneous blockers, cause changes, unknown terrain/RF, injected failures, sampled locations, and mission-boundary intervals.
- A separately implemented enumerator checks minimum count, ranking, budget limits, fallback, all environmental cases and shared failures against production selection, with and without evidence collection.
- Overlap/tie tests check removal and replacement evidence and prevent double-counted gains.
- Cached-terrain integration tests reconstruct production timelines from explicit paths for every failure state/environmental case and verify metrics/outage totals and export sheets.
- Frontend tests cover environmental cases, S1 failure gating, outage scope, unknown evidence, HTML escaping, and preview isolation.
- Run `.venv/bin/python -m pytest tests -q`, `.venv/bin/python -m unittest discover -s tests -v`, `npm run build`, `npm run test:evidence`, `npm run test:orbital`, and `npm run test:terrain` in this macOS app checkout. The Windows training interpreter is not used for app-only changes.
- Browser-check a cached research-point mission, recovered direct outage, compound loss, cause inspection, alternative overlay and Excel download.

Open evidence gaps: independently checked satellite trajectories, supplied satellite RF/costs, calibrated hardware/environmental response, and Earth station assumptions. These remain explicit limitations; no weights, new terrain downloads or invented metadata are used.
