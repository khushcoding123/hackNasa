# Underground-to-surface radio link

## What the repo had (scan, November 2026)

Nothing for radio through rock or regolith. `ora/tunnel.py` models mines as tunnel graphs and says in its docstring that radio does not go through rock, so links follow the tunnels (free-space + waveguide loss + bend loss). No file in the repo has permittivity, loss tangent, basalt or regolith attenuation. `docs/ORA_TRAINING.md` lists mine-propagation papers but nothing about lunar materials.

## What was added (all labels SIMULATED)

| File | Role |
| --- | --- |
| `training/src/moonmesh_train/subsurface.py` | Deterministic physics. Complex permittivity, exact bulk attenuation, Fresnel interface loss at each boundary, free-space spreading over the slant path, layered void -> basalt -> regolith -> vacuum. `max_depth_m` gives the deepest single-material path that closes the link. |
| `training/src/moonmesh_train/subsurface_model.py` | Scenario sampler, labeller and LightGBM surrogate. Inputs are raw scenario parameters only; margin, absorption and the other label terms are rejected as features. |
| `training/configs/subsurface.yaml` | Radio, material ranges, sampling and split settings. |
| `training/scripts/20_train_subsurface.py` | CLI. Writes `reports/subsurface/subsurface_model_report.json` and `models/subsurface_margin.txt` under the data root. |
| `training/tests/test_subsurface.py` | 9 pytest tests. |

Run: `python training/scripts/20_train_subsurface.py --help`.

## First result (20,000 scenarios, seed 42, simulated)

| Test | MAE (dB) | Bias (dB) | False-covered | Dead recall |
| --- | --- | --- | --- | --- |
| In distribution (2,990) | 2.85 | +0.10 | 0.4% | 99.6% |
| Extrapolation: 40-80 m of basalt, training stopped at 40 m (2,000) | 7.49 | +6.50 | 7.0% | 93.0% |

The surrogate learns a formula it could just evaluate, so it only earns its place if the real labels get more complex (scattering, tube geometry). Beyond the training range it is optimistic (positive bias), the unsafe direction: fall back to the physics there. Training reached the 600-tree cap, so it may not have converged. The margin target is floored at -60 dB; about 70% of scenarios are dead links.

## Unverified inputs (TODO before anyone quotes a number)

- Regolith: `eps' = 1.919^density` and `tan_delta = 10^(0.038*(FeO+TiO2) + 0.312*density - 3.260)` are from memory of Carrier et al. (1991), Lunar Sourcebook ch. 9. Confirm against the text.
- Basalt: eps' 6-9 and tan_delta 0.003-0.03 are assumed ranges, not looked-up values. Cite Apollo sample measurements.
- Permittivity is treated as frequency independent. The radio (30 dBm, 2 dBi antennas, -105 dBm sensitivity) is a placeholder, not hardware from this repo.
- Not modelled: rock scattering, surface roughness, refraction, lava-tube wall geometry, coherent multipath, temperature dependence.

## "LunarRF" backup

The user's note read "lunar one storms" for another model after LunarRF. I took that as a LunarRF-style simulated-label model for this link. LunarRF itself only covers surface links, so no LunarRF data feeds this model. If something else was meant, tell me.
