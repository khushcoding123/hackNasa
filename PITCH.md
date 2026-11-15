# MOONMESH 3-minute pitch (Space Apps Seattle)

| Part | Time | Question it answers |
| --- | --- | --- |
| 1. Person + Problem | 35 s | Who needs help? |
| 2. Solution + Proof | 90 s | What you built, demo, NASA data |
| 3. Possibility | 40 s | What could change? |
| 4. Close | 15 s | What should we remember? |
| Total | 180 s | |

Speaking pace is about 150 words per minute. Part 2 is shorter in words because the demo fills the time.

---

## 1. Person + Problem (35 s, ~85 words)

You've all been there. You're hiking, one bar of signal, then none. Annoying.

Now make that hill a crater rim at the Moon's south pole. The person who just lost signal is an astronaut with a damaged suit, a few kilometers from the lander. There's no tower. No second try. Just a ridge nobody ever mapped for radio.

NASA is sending crews to exactly that region. And today, planners choose where to land without seeing where the radio will die.

## 2. Solution + Proof (90 s, ~175 words plus the demo)

We built **MOONMESH**. Pick a landing region and a date range, and it shows where communication works, where it breaks, and where to put relays.

It runs on real NASA data. Terrain is LOLA laser-altimeter data at 20 meters, for three candidate regions. Sun and Earth positions come from JPL's ephemerides, and we checked them against JPL Horizons: elevations agree within a thousandth of a degree. Where terrain data is missing, we say "unknown." We never count it as coverage.

**[DEMO, ~40 s]** Here is Haworth in November 2026 with a 5 meter mast. Green is covered. Red is a dead zone. Gray is unknown. Click this red spot. A ridge rises right through the line between the antennas. Now raise the mast to 50 meters. Same spot, now green.

Planners see the *cause*, not just the result. And if we disable a relay, coverage and blackouts update, so the network is tested against failure.

**Proof:** we scored our coverage calculation against 73 million dense line-of-sight checks, on terrain we never trained or tuned on. It agrees at about 99% R-squared. Every radio result is labeled simulated.

## 3. Possibility (40 s, ~100 words)

What could change? Lunar missions are planned with slides and spreadsheets. With a tool like this, a team compares sites and dates in an afternoon and finds the weak link before anything is built.

The optional satellite hop is now simulated: lander, relay, one lunar satellite, Earth. It uses a two-body orbit, terrain access and lunar occultation, with shared-satellite failure analysis. Missing radio profiles give geometry-only upper bounds; independent spacecraft ephemeris validation remains open.

The same engine also runs on Earth terrain, for places like remote mines where towers face the same problem.

## 4. Close (15 s, ~35 words)

You've all lost signal behind a hill. On the Moon, that can cost a life.

MOONMESH shows where the call will fail, before anyone gets there.

---

## Before you go on stage

- **Build the offline demo** (see `docs/DEMO.md`): run `training/scripts/17_demo.py`, then `--verify-only`. It needs no internet.
- **Rehearse the demo path in 40 s:** open page, point at green / red / gray, press **Inspect ridge path**, press **Raise mast to 50 m**.
- **Do not say** the results are validated against measured lunar RF. No such data exists. Say: "checked against an independent dense line-of-sight calculation on the same LOLA terrain."
- **Do not claim** operational satellite coverage or validated thermal/dust survival. Satellite results are simulated; missing RF, satellite hardware assumptions and independent ephemeris validation are explicit gaps.
- **Numbers to know cold:** 20 m terrain, 3 regions, 1,452 Horizons samples within 0.001°, 73M line-of-sight checks, ~99% R-squared on held-out terrain.
- **Open question:** the "person" is a generic planner. If you have a real user, name them in Part 1.
