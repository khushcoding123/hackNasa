# Haworth mission scientific validation

The fixed Haworth mission passes the published calculation benchmark for its hypothetical 24-hour run on 1 November 2026 UTC. The simulator credits a contact only when all three hops are available in the same sample. The reference is calculated independently at 30-second intervals and compared with the app's five-minute samples.

| Check | Maximum difference or comparison | Acceptance limit |
| --- | --- | --- |
| Lunar orbiter position | 0.00019 m | 10 m |
| Earth receiver position | 1.26 m | 100 m |
| Range across the three hops | 1.08 m | 100 m |
| Relay and Earth receiver elevations | 0.000011° | 0.01° |
| RF margin across the three hops | 0.00222 dB | 0.02 dB |
| Contact onset and recovery | 210 seconds | 300 seconds |
| Availability | 9.375% app, 9.201% reference | 1 percentage point |
| Longest blackout | 7 h 45 min app, 7 h 42 min reference | 300 seconds |
| App-grid path state and hop blockers | Zero disagreements across 288 samples | Zero |
| Relay and satellite disabled separately | Zero service for both | Zero service |
| Lander-to-relay terrain clearance | Clear on PDS LOLA and PGDA 78, with minimum clearances of 1.51 m and 1.59 m respectively | Same clear/blocked result |

The angular and margin limits check numerical agreement under matched assumptions. The timing limit is one app sample and bounds the comparison for this run. Both approaches use DE440 Earth positions and the same lunar orientation and ephemeris kernels. The independent orbit uses numerical integration of two-body gravity. The Earth receiver uses USNO's approximate sidereal-time equation. Terrain is checked with a Cartesian chord and denser horizon rays at the actual bearing. The RF reference evaluates the SI form of the ITU free-space loss equation. [NAIF kernels](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/) · [USNO sidereal time](https://aa.usno.navy.mil/faq/GAST) · [ITU-R P.525](https://www.itu.int/rec/R-REC-P.525/en)

PGDA 78 is an independently processed 5 m terrain product from the same LOLA instrument. Its agreement on this chord is a cross-product check. Both minimum clearances are close to the surface. The reference keeps the nominal nodes operational for this test, while the app executes its cold + dust model. RF reception in lunar conditions, thermal survival, battery endurance, and dust response remain unverified. [NASA PGDA 78](https://pgda.gsfc.nasa.gov/products/78)

## Reproduce

Prepare the app's normal Haworth terrain and SPICE cache with `python -m moonmesh.data`. Use the training environment with Rasterio and the app requirements installed. Place the official `Haworth_final_adj_5mpp_surf.tif` from the PGDA 78 Haworth section at `raw/pgda78/haworth/` under the selected data root.

```powershell
& C:/moonmesh-data/venv/Scripts/python.exe -m training.scripts.validate_haworth_mission --data-root C:/moonmesh-data
```

The command writes the JSON comparison and a 30-second reference CSV under the data root's `reports/` directory. The JSON records the tolerances, contact windows in seconds from mission start, differences, source URLs, and fingerprints of the scenario, code, terrain, and kernels. The command exits nonzero when required evidence is missing or checks exceed the declared limits. [Published machine-readable results](figures/haworth_mission_validation.json)
