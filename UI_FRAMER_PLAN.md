# MOONMESH UI plan: Framer-style redesign

> Status: proposal. Supersedes the visual direction in [UI_REDESIGN_PLAN.md](UI_REDESIGN_PLAN.md). The guided journey in [PRODUCT_FLOW.md](PRODUCT_FLOW.md) stays. This plan changes how the journey looks, moves and is navigated. It does not change the science model, optimizer, API shapes or exports.

## 1. Direction

Framer's marketing and product style is a black canvas, very large tightly tracked type, one saturated accent, big rounded "bento" cards, pill buttons, and motion that feels springy and physical. We borrow that language and apply it to a lunar mission planner.

The Moon is the hero. Everything else is quiet, so the globe, the terrain and the blackout timeline carry the visual interest.

**What we keep from the current app:** the dark palette, the mono eyebrow labels, the Moon-first landing, the 7-step journey and every assumption label.

**What changes:** a heavy left rail becomes a floating nav; a text-dense welcome becomes one headline and one action; stacked panels become a bento grid; stepped pages gain continuous, scroll- and spring-driven transitions.

## 2. Principles

1. **One idea per screen.** One headline, one primary action, one hero visual.
2. **Type does the work.** Large, tight display type for the headline and the key number. Small, calm body text.
3. **One accent.** A single blue for primary actions and active states. Green, amber and red are reserved for link status and never decorative.
4. **Cards, not panels.** Content lives in rounded cards with a hairline border and a soft inner gradient.
5. **Motion explains.** Animation shows where something came from or went. Nothing moves just to move.
6. **Honest by default.** The "simulated / assumption / insufficient evidence" labels stay visible. A stylish UI must not make a planning estimate look like a measurement.

## 3. Design tokens

Put these in one `web/tokens.css` imported by `style.css`, `ui.css` and the ORA lab. This also removes the current overlap between those files.

### Color

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `#000000` | Page canvas |
| `--surface-1` | `#0b0b0c` | Cards |
| `--surface-2` | `#141416` | Raised cards, inputs |
| `--border` | `rgba(255,255,255,.08)` | Hairlines |
| `--border-strong` | `rgba(255,255,255,.16)` | Hover, focus |
| `--text` | `#ffffff` | Headlines, key numbers |
| `--text-2` | `rgba(255,255,255,.64)` | Body |
| `--text-3` | `rgba(255,255,255,.44)` | Labels, captions (AA on `--bg`; verify) |
| `--accent` | `#0099ff` | Primary actions, active step, focus ring |
| `--accent-glow` | `rgba(0,153,255,.35)` | Button and card glow |
| `--ok` / `--warn` / `--bad` | `#34d399` / `#fbbf24` / `#f87171` | Link and assessment status only |

Cards get a subtle top-lit gradient: `linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,0))` over `--surface-1`.

### Type

- Display: Inter Display, or Inter with `font-feature-settings: "ss01","cv11"` as a fallback. Weight 500–600.
- Mono: keep the existing mono for eyebrows, coordinates and timestamps.
- Scale (desktop / phone):

| Role | Size | Tracking | Line height |
| --- | --- | --- | --- |
| Hero | 88 / 44 px | -0.05em | 0.98 |
| H1 | 56 / 34 px | -0.04em | 1.02 |
| H2 | 32 / 24 px | -0.03em | 1.1 |
| Key number | 64 / 40 px, tabular | -0.04em | 1 |
| Body | 16 px | -0.01em | 1.55 |
| Small / label | 12–13 px | 0 | 1.4 |
| Eyebrow (mono) | 11 px, uppercase | +0.12em | 1.4 |

Rule: no text under 11 px anywhere. The rail and cards currently use 9–10 px.

### Shape and space

- Radius: cards 24 px, inner cards and inputs 14 px, buttons and chips fully rounded (pill).
- Spacing: 4 px base. Card padding 24–32 px. Section gap 96–160 px on the landing page.
- Max content width 1200 px, 16 px gutter on phones.

### Motion

- Spring for movement: stiffness 260, damping 28 (about 0.4 s settle). Use `cubic-bezier(.22,1,.36,1)` where a CSS curve is needed.
- Fades 160–240 ms. Hover lift 2 px with a border brighten.
- Scroll reveals: translateY 24 px to 0 plus fade, staggered 60 ms per item, once only.
- Honour `prefers-reduced-motion`: remove movement and keep fades.

## 4. Components

- **Floating nav.** A centered pill, blurred glass (`backdrop-filter: blur(20px)`, `rgba(11,11,12,.6)`), holding logo, the 5 journey stops, and a primary "Create mission" button. It replaces the 242 px left rail and the separate topbar. On phones it collapses to a logo plus a menu sheet.
- **Buttons.** Primary: white fill, black text, pill. Accent: blue fill with glow. Secondary: transparent with a hairline border. Hover lifts 2 px and brightens the border.
- **Cards.** Hairline border, top-lit gradient, 24 px radius. Interactive cards lift and show a cursor-following spotlight (radial gradient at the pointer).
- **Chips and tabs.** Pill segmented control for Globe / Terrain, Basic / Expert, and map layers. The active segment slides under a shared spring-animated indicator.
- **Status chip.** Pill with a colored dot: "Requirements met", "Requirements failed", "Insufficient evidence". It is always visible in the nav once a mission exists, with the reason on hover or tap.
- **Key-number tile.** Large tabular number, a mono label above, a delta below ("-1.8 h vs. baseline"). Numbers count up once on reveal.
- **Inputs.** `--surface-2`, 14 px radius, 44 px tall, accent ring on focus, inline validation text below.
- **Tooltips and locked states.** Locked steps stay clickable and open a popover: "Calculate a mission first", with a button that does it.
- **Skeletons.** Shimmering card placeholders for globe, terrain tiles and results. The calculate step shows a progress label and a Cancel button.
- **Command palette (⌘K).** Go to step, jump to Haworth / Nobile / Malapert, export, toggle layers.

## 5. Screens

### 5.1 Landing: "Explore the Moon"

```
[ floating nav ───────────────────────────────────── Create mission ]

                  FROM LUNAR TERRAIN TO A WORKING NETWORK
              See what stands between your
                    mission and Earth.
        Real NASA terrain. Explicit assumptions. Relay layouts.

              [ Create a mission ]   [ Explore the south pole → ]

        ┌──────────────────── full-bleed globe ────────────────────┐
        │   (drag to rotate · region chips float on the globe)    │
        │      ◉ Haworth    ◉ Nobile Rim 1    ◉ Malapert          │
        └──────────────────────────────────────────────────────────┘

   ── bento: how it works ──
   ┌────────────┬────────────┬──────────────────┐
   │ 01 Pick a  │ 02 Build   │ 03 Go deeper:    │
   │ place      │ a network  │ ORA lab          │
   └────────────┴────────────┴──────────────────┘
```

- One headline, one primary and one secondary action. The three region shortcuts live only on the globe, not twice.
- The globe fills the width and parallaxes slightly on scroll. Region chips are pins with a pulse ring.
- The three cards become the bento row. Each card hosts a small looping preview (terrain mesh, relay links, a power chart).

### 5.2 Mission setup

- Two-column layout: a form card on the left, a live preview card on the right (region on the globe, date range, chosen relay count).
- Basic fields first: region, dates, coverage goal, max relays, budget. "Expert settings" is a segmented Basic / Expert tab, not a collapsed section.
- The long explanatory paragraph becomes helper text under each field.
- "Calculate network" is the single accent button. While running it morphs into a progress pill with a Cancel button.
- Editing after a result shows a slim banner: "Results are out of date. Recalculate." Result steps dim until then.

### 5.3 Network (3D terrain)

- The terrain view goes full-bleed under the nav. Controls are floating pill groups at the edges: layers (top left), Globe / Terrain (top right), view presets (bottom right).
- A floating glass card on the left lists the recommended relays. Selecting one flies the camera there with a spring ease and opens its detail card: coverage it adds, what fails without it, the terrain reason.
- A bottom timeline scrubber (see 5.4) is shared by every analysis step.
- Imagery and terrain source and resolution move into a small "Data" chip that expands on click. The labels stay and are only tucked into a chip.

### 5.4 Blackouts and the shared timeline

- A persistent bottom dock holds a play and pause button, a speed control, and the blackout windows shaded on the time axis. Scrubbing updates the 3D view, horizon profile and RF margin together.
- Selecting an outage opens a bento of four cards: terrain clearance, Earth geometry, RF margin, link status. Each shows a key number and a plain-language cause.

### 5.5 Resilience

- Relays shown as toggle cards. Turning one off animates the coverage and longest-blackout numbers to their new values, with the delta in `--warn` or `--bad`.
- The worst single failure gets a highlighted card.

### 5.6 Cost, export, data and assumptions

- Cost is a tab in the Network view, not a separate numbered step. The bento shows total, per-relay cost and the Excel download.
- Export becomes one menu in the nav: Excel, JSON, CSV, Print. Disabled items show a reason on hover.
- "Data & assumptions" is a full card page with each assumption as a row and a clear simulated / planning-envelope tag.

## 6. Navigation

Cut the 7 numbered steps to 5 stops: **Explore, Setup, Network, Blackouts, Resilience**. Cost lives in Network. Compare sites, relay details and sensitivity sit under one "More" item in the nav. The ORA lab link moves into the bento on the landing page and into "More".

Moving between stops keeps the camera, selected time, selected relay and disabled relays. Transitions use a shared-element morph (the globe becomes the terrain view) where practical, and a 240 ms cross-fade otherwise.

## 7. Responsive

- ≥1200 px: full layout above.
- 768–1199 px: nav stays a pill; bento drops to two columns; the relay card becomes a bottom sheet.
- <768 px: nav collapses to logo plus menu sheet; bento is single column; timeline dock is a compact bar with a drag-up sheet; 3D HUD limited to two lines. No horizontal page scroll.

## 8. Accessibility

- Keep the skip link, keyboard camera controls and keyboard relay select.
- Contrast: all text at least AA on its surface. `--text-3` on `--surface-1` needs a measured check. The current `#8292a1` on `#0d131b` is the known risk.
- Focus ring: 2 px `--accent` with a 3 px offset on every interactive element.
- All motion respects `prefers-reduced-motion`.
- Status never relies on color alone. The chip carries a text label and an icon.

## 9. Implementation

Files to touch: `web/index.html`, `web/style.css`, `web/ui.css`, `web/ora*.css`, and `frontend/app.ts`, `moon.ts`, `site3d.ts` for the nav, HUD and timeline. No framework migration is required. Animation can be CSS plus the Web Animations API. If a spring library is wanted, add one small dependency (for example `motion`) rather than a framework.

| Phase | Work | Risk |
| --- | --- | --- |
| 1 | `tokens.css`, type scale, button, card, chip, input styles. Minimum 11 px text. Contrast pass. | Low |
| 2 | Floating nav replaces the rail and topbar. 7 steps to 5 stops. Locked-step popovers. Status chip. | Medium (navigation state) |
| 3 | New landing page: hero, globe with region pins, bento row, scroll reveals. | Low |
| 4 | Setup split layout, Basic / Expert tabs, progress and cancel, out-of-date banner. | Low |
| 5 | Full-bleed terrain view, floating controls, relay cards, shared bottom timeline. | High (touches 3D layout) |
| 6 | Resilience animations, key-number tiles, command palette, skeletons. | Medium |
| 7 | Mobile pass, reduced-motion pass, print stylesheet check. | Low |

## 10. Verification

- `npm run build` passes. The existing TypeScript and terrain tests pass: `npm run test:terrain`, `python -m unittest discover -s tests -v`.
- Journey still works end to end: Explore, Setup, Calculate, Network, Blackouts, Resilience, Export.
- Changing a parameter still locks results until recalculation. A failed calculation still leaves old results locked out.
- Screenshots at 1440, 1024, 768 and 375 px show no horizontal overflow.
- Keyboard-only pass: reach every control, move the camera, select a relay.
- Reduced-motion pass: no movement, content still readable.
- Every result still shows its status, assumptions and "simulated / planning envelope" labels.

## 11. Open questions

- Brand accent: Framer blue (`#0099ff`) as proposed, or a lunar tone (ice cyan, as now)?
- Font: is a licensed or self-hosted display font acceptable, or Inter only?
- Is the ORA lab restyled in the same pass, or later?
- Is a Framer-style marketing page (hero, bento, scroll reveals) wanted as a public landing page separate from the planner?
- Light theme: needed, or dark only?
