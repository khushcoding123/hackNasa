# ORA: Open Relay repeater network for lunar mining

ORA is the relay-placement and resilience lab inside MOONMESH. It decides where to put repeaters on real lunar terrain, tests the network against node failures and the lunar night, and extends the same graph engine underground.

Open it at `/ora.html` in the running app, or use the command line (`python -m ora --help`).

This page maps every idea in `ORA_Implementation_Plan.md` to what was built, what was measured, and what is still open.

## Status of every idea

| # | Idea | Status | Where | What it does now |
| --- | --- | --- | --- | --- |
| I1 | Open repeater network | Built | `ora/`, `scenarios/`, this repo | Python package, YAML scenarios, tests, a command line and a web lab. No licence file yet (team decision). |
| I2 | Designed for space mining | Built | `scenarios/shackleton_rim_to_floor.yaml`, `ora/scenario.py` | Mining sites on the permanently shadowed floor of Shackleton crater, relayed to gateways on the sunlit rim. |
| I3 | Model trained on Earth networks that predicts where relays go | Built and trained | `ora/train.py`, `ora/features.py`, `ora/model_card.json` | Siting prior trained on 276 registered tower sites in six mountain regions. Scores every lunar candidate. The line-of-sight optimizer makes the final choice. |
| I4 | Digital twin | Built | `ora/twin.py`, Digital twin view | Hour-by-hour sunlight, battery, temperature, mode and link state per node. What-ifs: kill a node, regional failure, forced lunar night, laser power, optical trunks with dust. 2D map, not a 3D globe. |
| I5 | Electronics survive the lunar night | Built | `ora/thermal.py`, Lunar night view | Lumped thermal model, survival energy budget, hibernation and duty cycling, radioisotope heat, smallest-battery search. |
| I6 | Underground mining | Built | `ora/tunnel.py`, Underground view | Mines as tunnel graphs. Radio follows tunnels with distance and bend loss. |
| I7 | Novel research | Built as experiments | `ora/resilience.py`, Resilience lab | Five failure modes, three null models, Monte Carlo with 95% intervals, Schneider R. Results below. |
| I8 | Dig twin for testing | Built | `ora/breadcrumb.py` | A robot follows the dig front and drops repeaters. Failures and collapses are applied afterwards. |
| I9 | Mines are the biggest challenge | Built | `ora/breadcrumb.py` | Three repeater policies compared, including a resilience-aware one. |
| I10 | Lasers for power and packets | Built | `ora/links.py`, Lasers view | Optical links as a second edge type and a stage-by-stage power-beam budget, both usable in the twin. |
| I11 | Help mining on Earth | Built as a case study | `ora/tunnel.py` (`room_and_pillar`, `Mine.from_survey`), Earth mines view | Room-and-pillar panel on the same engine, and a loader for real cave or mine surveys. No real mine map loaded yet. |
| I12 | Blank bullet | Open | — | See open questions. |

Module A (terrain to graph) is `ora/los.py` and `ora/graph.py`. Module B is `ora/resilience.py`. Module C is `ora/optimize.py` with `ora/train.py`.

## Run it

```bash
python -m pip install -r requirements-dev.txt   # or: make setup
python -m moonmesh.data          # NASA terrain and SPICE kernels (about 540 MB)
python -m ora.data --earth       # Shackleton terrain window, FCC towers, six Earth elevation tiles (about 120 MB)
python -m ora.lunar_windows      # 102 lunar windows with exact viewshed labels (a few minutes)
python -m ora.train              # trains the prior, surrogate and gain model, writes ora/model_card.json
python -m ora stress             # nine stress evals on sealed data
python -m pytest tests -q
```

| Command | Result |
| --- | --- |
| `python -m ora network shackleton_rim_to_floor` | The chosen relays and what survives a single failure |
| `python -m ora resilience shackleton_rim_to_floor --seeds 200` | Robustness table, terrain against a terrain-free network |
| `python -m ora twin --beam` | Twin summary with laser power on |
| `python -m ora run scenarios/mine_tunnel_demo.yaml` | Underground policy comparison over 10 mine layouts |
| `python -m ora run scenarios/earth_cascades_validation.yaml` | Earth check of the terrain graph engine |
| `python -m ora stress` | Stress report, written to `docs/figures/ora_stress.json` |
| `python -m ora evaluate` | Saved models on sealed Earth tiles and lunar windows |
| `python -m ora benchmark` | Relay selection strategies against exact greedy on sealed windows |
| `python -m ora tall-towers` | Tall Lunar Towers viewshed benchmark |
| `python -m ora.literature "tunnel radio propagation"` | OpenAlex literature and dataset search |

## Measured results

All numbers come from the commands above with seed 0. They describe these scenarios, not lunar networks in general.

### Terrain changes how networks break (Shackleton, 223 sites, 12 km range, 200 repeats)

| | Real terrain | No terrain, same mean degree |
| --- | ---: | ---: |
| Single points of failure | 11 | 1 |
| R, single points of failure removed first | 0.221 | 0.324 |
| R, busiest relay removed first | 0.161 | 0.168 |
| R, random failures | 0.385 | 0.351 |
| R, regional failures | 0.374 | 0.345 |
| R, link degradation | 0.711 | 0.670 |

R is Schneider robustness: the mean share of the network still connected across the removal sequence. Higher is better.

The plan's hypothesis was that terrain graphs have more cut vertices and collapse earlier than idealized graphs of the same mean degree. The first part holds: 11 single points of failure against 1. The second part holds only for targeted attacks. Under random and regional failures the terrain graph does slightly better, because rim sites form dense clusters that survive random losses. The practical reading: on real terrain the danger is a few critical relays, not general fragility.

### Earth check (Cascades tile N47W122, 300 sites, 8 km range)

Terrain cuts mean degree from 54.5 to 22.1, splits the graph into 9 components against 1, and isolates 6 sites. This reproduces the qualitative TerrainLOS result that terrain graphs have lower degree and more disconnection than unit-disk graphs.

### Placement on Shackleton

Seven relays link all three floor mining sites to two rim gateways: four to connect and three spare. After the worst single failure, two of three mines stay linked. The chosen network covers 47% of the ground within 14 km for a 2 m receiver; the rest is mapped as dead zone.

### Twin: shadowed relays are the problem, laser power fixes the local link

| Run (30 days from 2026-11-01) | Mines linked to a gateway | Linked to Earth | Relays frozen |
| --- | ---: | ---: | --- |
| Default node (600 Wh, 40 W solar) | 43% | 24% | 2 |
| Laser power to shadowed relays | 100% | 53% | 1 |

Two chain relays sit in shadow and freeze once their batteries drain. One 500 W laser feed from a sunlit relay 5.6 km away delivers about 82 W and keeps the chain alive. Earth contact is then limited by the gateways' own Earth view (about 54% of the month), which no surface relay can raise.

### Lunar night

A relay that draws 6 W needs 2,124 Wh for a 354 hour night. Hibernating and waking 10% of each hour cuts that by more than half. In the twin, keeping every mine linked through a forced night on Shackleton takes a 3,200 Wh battery per relay when always on, 1,000 Wh at a 20% wake share and 700 Wh at 10% (the smallest sizes that passed in a scan of nine battery sizes). A relay that never wakes saves the most energy and drops the network.

### Underground (10 mine layouts, mean ± 95% interval)

| Policy | Repeaters | Mine reachable after the worst single repeater failure | After a collapse (mean) |
| --- | ---: | ---: | ---: |
| Drop when the link weakens | 6.7 ± 0.4 | 30% | 76% |
| Drop every 70 m | 24.1 ± 0.2 | 100% | 92% |
| Resilience-aware | 13.7 ± 0.6 | 100% | 80% |

The plan's hypothesis was that the resilience-aware policy would win on collapse robustness at modest extra repeaters. It was partly supported. The resilience-aware policy survives any single repeater failure with 43% fewer repeaters than fixed spacing. For collapses it is only slightly better than the simple threshold policy, and fixed spacing does best because it leaves more radios on both sides of a break. A collapse on the only way in cuts off the mine under every policy (9% reachable). Only a second way out helps.

### Models

| Model | Validation | Result |
| --- | --- | --- |
| Siting prior (Earth towers) | Leave one tile out, 14 tiles with 15 or more towers | AUC 0.85; 43% of real towers fall in the top 10% of cells (chance 10%). On eight sealed tiles: AUC 0.88, 48.5% in the top 10% |
| Coverage surrogate (lunar) | 5 folds of 60 km blocks over 86 lunar windows; 16 more windows sealed as a test | CV R² 0.69, rank correlation 0.83. Sealed test: R² 0.71, rank 0.82, error 5.7 points |
| Marginal-gain model | 16 sealed lunar windows, 8 relays | 83% of exact greedy coverage, beats random in 100% of windows |

The surrogate screens candidates; the app still computes exact line of sight for the final set. Siting scores in the app are 80% surrogate and 20% Earth prior, because the prior alone does not rank lunar cells by visibility. The stress suite (`python -m ora stress`) passes 23 of 24 checks; `docs/ORA_TRAINING.md` lists the one that fails and the weak spots.

### Drawn areas and layouts (main app, step 3)

The main MOONMESH app no longer starts from fixed landing regions. Draw a square or a free-form shape anywhere between 85 S and the pole (about 2 to 24 km across); `POST /api/ora/area` crops 20 m LOLA terrain for it, plans relays, gateways and mining sites inside the shape, and compares three layouts over the same sites: a tree, a tree with loops, and a full mesh. The best layout survives the most single relay or link failures with the fewest links. If nothing inside the shape sees Earth or links up, sites in a 6 km ring around it are used and marked as outside. Every ORA endpoint also accepts `polygon`. Drawn terrain is cached under `.moonmesh-data/terrain/area-*` (the 12 most recent are kept).

## What ORA does not claim

- No placement is optimal. The search is a heuristic over screened candidates.
- The siting prior is trained on Earth, where towers follow roads, power and people as well as terrain. On the Moon it is a ranking prior, never a decision.
- The twin uses simulated telemetry on 20 m terrain. Thermal constants are illustrative.
- Tunnel loss numbers are placeholders until fitted from the bench test. The bench validates the method, not lunar values.
- Laser power is evaluated as a technology. It is not presented as flight heritage.
- Prior work was searched, not exhausted: "not found" is not "does not exist".

## Still open

| Item | Why it is open |
| --- | --- |
| Bench test with ESP32 boards | Needs hardware. Firmware is in `hardware/` but has not been compiled or flashed. |
| 3D twin view (CesiumJS or Three.js) | Not built. The twin uses a 2D map. |
| Real Earth mine or cave map | The loader exists; no survey file has been loaded. Candidate datasets are in `docs/ORA_TRAINING.md`. |
| Tall Lunar Towers benchmark | Run on a 36 km window at its centre: 2.9% / 10.4% against the paper's 14.7% / 25.9% at 10 m / 50 m. The full-region numbers need the whole Connecting Ridge tile. |
| Licence, Zenodo release, LunaNet terminology review | Team decisions. |
| Items marked "verify" in the plan | Night temperatures, ISS optical terminal dates and rates, beam efficiency range, lava-tube temperatures. None are used as inputs. |
| The blank bullet, the challenge statement, the Earth customer | Questions for the team. |
