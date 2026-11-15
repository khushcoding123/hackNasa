# Landing page: Moon gets cut off when zooming (planned change)

Status: **PLAN ONLY. No code has been changed.**
Files involved: `web/ui.css` (main change), possibly `web/app.js` (camera framing), `web/index.html` (no change needed).

---

## 1. The problem

On the landing page ("Your next mission. A clearer connection."), the headline block sits on a solid black area and the Moon starts below it. When the user scrolls/zooms into the Moon, the surface is chopped off by a hard horizontal line right behind the "Create a mission / Explore the south pole" buttons (see the screenshot: black above, lunar terrain starting abruptly at the line). The Moon can never pass behind or above the title, so it looks clipped.

## 2. Root cause (verified in the code)

The black band is not a background on the title section. `.welcome` (`web/ui.css:204`) has no background at all. The cut comes from how the Moon canvas is **sized and positioned** on the landing step:

```css
/* web/ui.css:222 */
.planner[data-step=landing].panel-collapsed:not(.exploring) #moon {
  top: var(--hero-split);   /* 54vh (60vh / 58vh on smaller screens) */
  bottom: auto;
  height: 130vh;
}
```

- `#moon` (the Cesium canvas container, `web/index.html:33`) is pushed down to start at `--hero-split` (54vh). Its top edge is where the Moon is clipped. Anything the camera renders above that line simply does not exist.
- Everything above that line shows the body background, `body.planner { background: #000 }` (`web/ui.css:149`), plus `.planner #moon { background: #000 }` (`web/ui.css:187`) and the `.topbar` black gradient (`web/ui.css:154`). That is the "black title section".
- Zooming moves the camera closer, so the globe grows and hits the container's top edge, producing the visible cut.

So "make the title section background transparent" really means: **stop shrinking the Moon canvas to the lower part of the screen, and let it fill the whole viewport behind the title.**

## 3. Proposed change

### 3.1 Make the Moon canvas full-screen on the landing step (core fix)

Replace the rule at `web/ui.css:222` so the landing canvas uses the same box as every other step instead of a lowered, oversized one:

```css
/* BEFORE */
.planner[data-step=landing].panel-collapsed:not(.exploring) #moon {
  top: var(--hero-split); bottom: auto; height: 130vh;
}

/* AFTER */
.planner[data-step=landing].panel-collapsed:not(.exploring) #moon {
  top: 0; bottom: 0; height: auto;   /* full viewport, nothing to clip against */
}
```

(`bottom: 0` rather than `var(--strip)` so the Moon also runs behind the status strip; the strip already has its own black background and sits at z-index 28.)

### 3.2 Make the layers above the Moon transparent

| Selector | Location | Change |
|---|---|---|
| `.planner #moon` | `ui.css:187` | `background: #000` -> `background: transparent` for the landing step only (keep black elsewhere to avoid flashes while Cesium loads; or keep black and rely on the canvas being opaque, since the sky behind the Moon is black anyway). |
| `.planner .topbar` | `ui.css:154` | Keep the gradient but lighten it, e.g. `linear-gradient(rgba(0,0,0,.55), transparent)`, so the Moon is not hidden behind a solid black strip at the top. |
| `.welcome` | `ui.css:204` | Already transparent. No change. |
| `body.planner` | `ui.css:149` | Stays `#000`; it is only visible if the canvas is not loaded yet. |

### 3.3 Keep the Moon framed in the lower half (the only tricky part)

With a full-screen canvas, Cesium centres the globe in the middle of the screen, which would put it behind the headline. Previously the offset came for free from shifting the container down. Two options:

1. **Camera/frustum offset (recommended).** After the viewer is created, shift the render centre down by about the old split (~27% of the viewport height) while on the landing step, and clear it when leaving it:
   ```js
   // landing only
   const f = viewer.camera.frustum;           // PerspectiveFrustum
   f.xOffset = 0;
   f.yOffset = -(viewer.canvas.clientHeight * 0.27);
   ```
   (Cesium's `PerspectiveOffCenterFrustum` supports this. If the app uses a custom camera through `lookAtTransform`, apply the same offset where the landing camera is set; `app.js` has several `lookAtTransform` / `setView` / `flyTo` call sites around lines 11952, 13267, 15378, 17701-17738, 21629, so the landing one needs to be located first.)
2. **Simple fallback.** Keep `#moon` full-screen and just change the landing camera's pitch/destination so the Moon's visual centre sits lower. Less exact across screen sizes, but no frustum maths.

Reset the offset (`yOffset = 0`) when `body.exploring` is set or the step changes, so the explorer and mission views stay centred as they are today.

### 3.4 Layering / interaction checks

- Title block `.welcome` is `position: fixed; z-index: 8` and has `pointer-events: none` on the container, `auto` on its children. With a full-screen canvas underneath, dragging/zooming the Moon on empty areas still works; the buttons still capture their own clicks. No z-index change needed.
- Cards (`.explore-card`, z-index 9) and `.hero-glow` (z-index 1) stay above/below as they are today.
- Text readability: the headline is white on a mostly black sky, but when the lit surface is zoomed in behind it, contrast could drop. Add a soft top scrim (see 3.2 topbar) or a faint `text-shadow` on `.welcome h2` / `.welcome-copy` if testing shows it.
- `.hero-glow` uses `top: calc(var(--hero-split) - 18vh)`; it is tied to the old split. Leave as is, since the Moon's rim still sits in the same place with the offset from 3.3.
- Responsive: `--hero-split` is overridden at `ui.css:348` (60vh) and `ui.css:355` (58vh). If option 1 is used, derive the offset from the same variable (`(splitFraction - 0.5) * height`) so mobile keeps its layout.
- The WebGL-less fallback (`.vw-fallback`, `ui.css:225-227`) is separate and should be left alone.

## 4. What does NOT change

- `web/index.html` markup.
- Behaviour after "Create a mission" / "Explore the south pole" (those rules already set `#moon { top: 0 }`, `ui.css:243`).
- Any Python/model/training code.

## 5. Test plan

1. Load `/` at desktop size. The Moon should look the same as now at rest (rim under the buttons), but with no hard black/terrain seam.
2. Scroll-zoom and drag in toward the surface. Terrain should extend up behind the headline and top bar with no horizontal cut.
3. Click "Explore the south pole" and "Create a mission"; the transitions and `.exploring` animation should be unchanged.
4. Check 1280x720, 1920x1080, tablet and phone widths (`--hero-split` at 54/58/60vh).
5. Confirm headline, subtitle and buttons stay readable and clickable while zoomed in.
6. Check the no-WebGL fallback still renders.
7. Existing tests (`python -m unittest discover -s tests -v`) are unaffected, since this is CSS/JS front-end only.

## 6. Risks

- Camera offset is the one place a visual regression could appear (Moon off-centre or distorted on some aspect ratios). Hence the explicit reset on step change and per-breakpoint testing.
- A full-height canvas means the globe is rendered at larger pixel size than the old 130vh box in some cases: negligible performance change.
- Cache-bust: `index.html` loads `/ui.css?ui=11` and `/app.js?ui=11`; bump to `ui=12` when the change is applied.

## 7. Estimated effort

Small: one CSS rule + a few CSS tweaks, plus ~5-10 lines of JS for the offset. About 30 minutes including cross-size testing.
