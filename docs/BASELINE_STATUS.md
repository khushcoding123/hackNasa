# Baseline status of this checkout

Phase 0 of the reliability repair plan (14 November 2026). This report describes what this checkout contains and what its tests show. It is not a validation certificate: a passing test shows the code does what the test checks, not that a mission will work.

## Code and environment

| Item | Value |
| --- | --- |
| Base commit | `18ca6c749c04af259d789864e090a8bafa034b25` (`jeswanth/ora-lab`, same as `origin/main`) |
| Phase 0 changes | branch `repair/phase0-baseline`, uncommitted when this report was written; `python -m ora preflight` prints the commit and a SHA-256 of any uncommitted diff, and saved reports now record both |
| Python | 3.13.7 in `.moonmesh-data/venv` |
| Key libraries | numpy 2.5.4, scipy 1.18.1, scikit-learn 1.9.1, spiceypy 8.4.0, fastapi 0.162.7, starlette 1.10.0, networkx 3.7, joblib 1.6.0, pytest 9.2.0 |
| Test client | `httpx` was missing, so one test failed (below). It is now in `requirements-dev.txt` and installed (0.28.1). Starlette 1.10 prints a deprecation warning that prefers `httpx2`; `httpx` still works. |

## How this differs from the audit

The repair plan was written from an audit of a different working copy. In this checkout:

- **All three trained models are present and load cleanly** (`ora_siting_prior`, `ora_coverage_surrogate`, `ora_gain_model`). scikit-learn raised no version-mismatch warning on load. The plan's premise that the models are missing does not apply here; restoring them (phase 1) is not needed, but binding them to the model card is (see open items).
- **All required and optional data is present**: SPICE kernels, both LOLA polar products, terrain crops for all five sites (including Shackleton and Connecting Ridge), global LDEM_64, the FCC tower registry, all 20 training tiles, and 102 lunar windows with 408 variants.
- **There was no uncommitted work** to preserve.
- `moonmesh/environment.py`, `tests/test_environment.py` and `docs/COMMUNICATION_ARCHITECTURE.md`, which the plan names, were not on any branch when this baseline was taken. They arrived later the same day in `origin/main` commit `44bf663` ("Add lunar environment modeling…"), together with the plan itself (`docs/RELIABILITY_REPAIR_PLAN.md`) and a new thermal solver in `ora/thermal.py`. The audit was evidently made on that working copy. This branch has since been fast-forwarded to `44bf663` with the phase 0 and accuracy work re-applied on top; the test results below predate that merge.

## Inputs present (preflight)

`python -m ora preflight` reports **ready**: every required input is present and every trained model loaded.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `ora_siting_prior.joblib` (HistGradientBoostingClassifier) | 299,312 | `9898338e3155af9b22cafa1d75d7eeeee0c2d24bd8917689d6994cae9ca0cad6` |
| `ora_coverage_surrogate.joblib` (HistGradientBoostingRegressor) | 1,119,256 | `b40ffa53ded4fc1aaa552fbf54a1b631e87eef8b23ef59bf16d41d9d1b10129e` |
| `ora_gain_model.joblib` (HistGradientBoostingRegressor) | 1,121,080 | `112fb8da59547396c0590036519aaa6df191a9a776c0c90daeabb5e00ed72d80` |
| `LDEM_85S_20M.IMG` | 460,136,448 | `d900ac0aa97fbb983f9898873ead759d870fdb33978afc17fb4e1fa526aa49b2` |
| `LDEM_75S_240M.IMG` | 29,062,688 | `d031efe2611b17dfbf3c158f5e15dc04cd5719349dcbf6384bb21fd97697c490` |
| `ldem_64.img` | 530,841,600 | `1c4958699d4cffd7e777d51421b6044bba300aa0bef286a544309efb082cdbd6` |
| `de440s.bsp` | 32,726,016 | `c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2` |
| `moon_pa_de440_200625.bpc` | 12,863,488 | `60cd55aa401ea2ea97360636f567554bfe4e37bb829f901b4460a455dfaf783f` |

Run `python -m ora preflight --json` for every file, including site crops and Earth tiles.

## Test baseline

Before any phase 0 change (base commit, `python -m pytest tests -q -rA`):

| Result | Count | Detail |
| --- | ---: | --- |
| Passed | 93 | plus 51 subtests |
| Failed | 1 | `tests/test_terrain_tiles.py::TileTests::test_endpoint_headers`: Starlette's test client raised `RuntimeError` because neither `httpx` nor `httpx2` was installed. The test's skip guard only catches `ImportError`, so this was a dependency gap, not a terrain bug. |
| Skipped | 0 | All data-dependent tests ran because every input is present. |
| Wall time | 73 s | Slowest: drawn-area layout comparison 27 s, Shackleton scenario setup 15 s, lunar split check 8 s, topology table 7 s |

After phase 0 (same machine, `httpx` installed, 9 new tests in `tests/test_status.py`):

| Run | Result |
| --- | --- |
| `python -m pytest tests -q` | 103 passed, 0 failed, 0 skipped, 51 subtests (95 s) |
| `MOONMESH_REQUIRE_DATA=1 python -m pytest tests -q` | 103 passed, 0 failed, 0 skipped (83 s). Verified separately that a data skip, a plain `pytest.skip` and a `unittest.skipUnless` all fail in this mode. |
| `npm run build` (TypeScript type-check and bundle) | passes |
| `npm run test:terrain` | passes |

The one warning is Starlette's deprecation notice for `httpx`.

## Start-up time

The audit reported a full test run interrupted during an import. It did not reproduce here.

| Measurement | Time |
| --- | ---: |
| `import moonmesh.server` (warm) | 2.0 s, of which FastAPI 0.51 s, SciPy `ndimage` 0.46 s, NetworkX 0.37 s, SpiceyPy 0.20 s, joblib 0.14 s |
| `pytest --collect-only` (103 tests) | 1.7 s |
| Data download during tests | none (all inputs already present) |

Start-up is dominated by third-party imports, not project code or data. The interruption was most likely a first run on a cold machine (bytecode compilation, antivirus scanning of the venv) or a missing-data download; neither was observed, so this is not confirmed. No optimisation is warranted on this evidence.

## Documentation reconciled

| Document | Was | Now |
| --- | --- | --- |
| `README.md` | Test command was `unittest discover`, which misses the pytest-style tests; no way to check data or model state | `pytest`, release mode `MOONMESH_REQUIRE_DATA=1`, `python -m ora preflight`, and the three result states |
| `docs/ORA.md` I3 | "276 registered tower sites in six mountain regions" | 491 tower cells in 14 of 20 tiles, from the model card |
| `docs/ORA.md` setup | "six Earth elevation tiles (about 120 MB)" | 20 tiles plus the FCC registry (about 280 MB) |
| `docs/ORA.md` results | Numbers without a date or model reference | Marked which numbers were re-run here and which are copied from records that predate model hashes; twin results labelled as scenario outcomes |
| `EARTH_NETWORK_DATA_PLAN.md` | "no Earth data has been imported" | The three-source case study is still not built; ORA separately uses FCC ASR and SRTM to train the siting prior |
| `MOONMESH_DELIVERABLES.md` | Unticked checklist | Points here; boxes left for the team |

### Documented numbers rechecked

| Claim in `docs/ORA.md` | Rechecked |
| --- | --- |
| Shackleton placement: 7 relays (4 chain, 3 spare), 2 of 3 mines after the worst failure, 47% ground covered, 223 sites | Reproduced exactly |
| Twin, default node: 43% linked to a gateway, 24% to Earth, 2 relays frozen | Reproduced exactly |
| Twin, laser power: 100%, 53%, 1 frozen | Reproduced exactly |
| Siting prior AUC 0.85, 43% in top 10%; surrogate R² 0.69, rank 0.83 | Match `ora/model_card.json`; not retrained |
| Sealed Earth tiles AUC 0.88, 48.5%; sealed lunar R² 0.71, rank 0.82, error 5.7 | Match `docs/figures/ora_evaluation.json`; not re-run |
| Stress suite 23 of 24 | Matches `docs/figures/ora_stress.json`; not re-run, and the file records no date or model hash |
| Tall Lunar Towers 2.9% / 10.4% | Matches `docs/figures/ora_tall_towers.json` (240 m terrain, window centre; phase 7 item) |

## What phase 0 changed

- **Preflight** (`ora/status.py`, `python -m ora preflight`, `GET /api/status`, `GET /api/ora/status`): code revision and diff hash, every input with size and checksum, library versions against `requirements*.txt`, and the state of each model. Exit code 1 when required inputs are missing.
- **Honest model status**: model loading moved to one place. A failed load is recorded with its error and the fallback used, never silently skipped. Loading is keyed on file size and modification time, so a replaced artifact is reloaded and cached networks are rebuilt. Every network response and GeoJSON export carries `score_evidence` (method, models used, their hashes, fallback). `GET /api/ora/config` returns `models` (loaded now) beside `model_card` (training record). The ORA *Models & limits* tab shows the loaded models first and labels the card as historical.
- **Three result states** (`moonmesh/evidence.py`): mission results, relay plans, drawn-area analyses and twin runs carry an `assessment` with `requirements_met`, `requirements_failed` or `insufficient_evidence`, the subject it covers, the checks, evidence gaps, hazards evaluated and not evaluated, and assumed parameters. Mission checks are decided only when the verdict holds with marginal samples lost and with unknown samples available. Twin runs were `insufficient_evidence` at this baseline; after merging the environment model the rule became: every status is decided under the assumptions listed with it (uncalibrated thermal and dust values flagged as such), and `insufficient_evidence` means the data cannot decide. Shown in the mission overview, data tab, region comparison, sensitivity sweep, layout view, ORA placement and twin views, and in JSON, CSV, GeoJSON and printable exports.
- **Release mode for tests**: `MOONMESH_REQUIRE_DATA=1` turns every skip into a failure.
- **Saved reports** (`ora_headline.json`, `ora_benchmark.json`, `ora_evaluation.json`, `ora_stress.json`, `ora_tall_towers.json`) now get a `provenance` block (date, commit, diff hash, model hashes) when regenerated. Training writes artifact hashes and library versions into the model card.

Sample statuses with default inputs, at this baseline (before the environment model was merged): Haworth mission **requirements failed** (68% availability against 95%, 229.5 h blackout against 24 h); Malapert mission **requirements met**; Shackleton relay placement **requirements met** for static links, with gateways seeing Earth 54% of the period; default twin **insufficient evidence**.

## Open items

- The model card has no artifact hashes, so it cannot be confirmed to describe the loaded files. Retraining (`python -m ora.train`) writes them; alternatively, regenerate `ora_evaluation.json` and `ora_stress.json` against the current files so the saved results carry hashes.
- The saved evaluation, stress, benchmark and tall-towers reports predate provenance stamping and should be regenerated before they are quoted.
- No CI job runs the release mode yet (phase 8).
- Placeholder hardware values (`ora/modules.yaml`, `ora/thermal.py`) are unchanged; they are phase 3.
