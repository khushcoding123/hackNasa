# Step 06: RF labels (signal strength and dead zones)

Implement `training/src/moonmesh_train/rf.py`:

- `fspl_db(d_m, f_hz) = 20log10(d) + 20log10(f) - 147.55`, using the 3D chord distance.
- `received_power_dbm = tx_power_dbm + tx_gain_dbi + rx_gain_dbi - fixed_loss_db - path_loss_db`. `margin_db = prx - receiver_sensitivity_dbm`.
- ITU-R P.526 single knife-edge loss: `J(nu) = 6.9 + 20log10(sqrt((nu-0.1)^2 + 1) + nu - 0.1)` for nu > -0.78, else 0. Use the max nu from los.py. `path_loss_db = fspl + J(nu_max)`.
- Class layers (apply the threshold last and always keep the continuous margin):
  - rf_covered: margin >= required margin
  - terrain_dead_zone: valid and los == 0
  - rf_dead_zone: valid and margin < required margin
  - terrain_caused_rf_outage: rf_dead_zone, but the free-space-only margin passes
  - unknown: not valid

Write `training/scripts/04_label_scenario.py`:

- Input: a scenario YAML (tx position as x/y or lat/lon, h_tx, h_rx, radio profile name). Load the profile with config.py.
- Use los.py `mode="fast"` at 10 m spacing, with the wavelength from the profile frequency.
- Output: one GeoTIFF per scenario, cropped to the window that covers the tx's max_range disk, on the same 20 m master lattice. It has its own transform, but its pixel edges stay on the lattice. Use DEFLATE compression with a predictor.
  - uint8 bands: los, valid_mask, rf_covered, terrain_caused_rf_outage, quality_flags.
  - float32 bands: min_clearance_m, obstruction_x_m, obstruction_y_m, obstruction_distance_m, path_loss_db, free_space_margin_db, received_power_dbm, link_margin_db.
  - Put band descriptions and units in tags.
- Plus a JSON sidecar containing: a full copy of the radio profile, propagation_model "fspl+p526_single_edge (simulated)", the DEM path and sha256, sample spacing, tracer mode, code commit, threshold and the window transform.
- Expose the labeling as importable functions so later steps can reuse them.

Tests (pytest):

- FSPL at 8 km is about 118.1 dB at 2.4 GHz and about 115.6 dB at 1.8 GHz (within 0.1).
- Our free-space margin for moonmesh_2p4_ref equals `moonmesh.network.link_margin(Mission().surface_radio, d_km)` to within 0.01 dB at several distances, passing the same distance to both.
- J(0) is about 6.0 dB, and J(-1) = 0.
- The class and outage logic on hand-built arrays.
- A GeoTIFF round trip preserves georeferencing, alignment with the master lattice, dtypes and band names.

Then run one scenario: tx at the Haworth research point, h_tx 5, h_rx 2, moonmesh_2p4_ref (8 km).

- Automatically pick three paths: clear, ridge-blocked, and near-boundary (|min_clearance| < 2 m). Plot each terrain profile with the chord, the first Fresnel zone and the obstruction point to `reports/rf/profiles/`.
- Plot the link-margin map and the class map (covered / terrain dead zone / RF dead zone / unknown) over the hillshade to `reports/rf/`.
- Write `reports/rf/report.md` with the covered and dead areas in km², the GeoTIFF file size and the runtime.

## Gates

1. All the tests above pass.
2. The research-point scenario has exactly 503,434 valid pixels and 70,612 LOS-clear pixels. These match `reports/los/report.md` (same tracer, DEM and spacing).
3. Invariants on that scenario:
   - link_margin <= free_space_margin + 1e-6 on every valid pixel
   - rf_covered and rf_dead_zone are never both 1
   - terrain_caused_rf_outage is a subset of rf_dead_zone
   - every invalid pixel has NaN continuous values
4. The scenario GeoTIFF is at most 60 MB and lies on the master lattice.
5. The 3 profile plots, the margin map and the class map exist.

Status file: `reports/pipeline/step_06.json`.
