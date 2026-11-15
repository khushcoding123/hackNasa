/** A CAD-style view cube: a small 3D box that mirrors the camera, with clickable faces, edges and corners, a drag-to-orbit surface,
 * a coloured X / Y / Z axis triad, a Home button and a small menu of standard views.
 *
 * It knows nothing about the 3D engine. The host describes its own world frame (one entry per face) and supplies a few callbacks:
 *  - basis():  the camera's right / up / forward axes in that frame, read every frame so the cube always shows the true view;
 *  - orbit():  the cube was dragged by dx, dy pixels;
 *  - snap():   a face (normal), edge (two normals) or corner (three normals) was clicked; the vector points from the
 *              centre of the scene towards where the camera should go.
 */
export type Vec3 = [number, number, number];
export interface CubeFace { label: string; normal: Vec3; up: Vec3 }
export interface CubeBasis { right: Vec3; up: Vec3; forward: Vec3 }
export interface CubeHost {
  faces: CubeFace[];
  /** Directions of the X, Y and Z axes in the host's frame; defaults to the identity. */
  axes?: [Vec3, Vec3, Vec3];
  basis(): CubeBasis;
  orbit(dx: number, dy: number): void;
  snap(direction: Vec3): void;
  end?(): void;   // a drag finished
  home?(): void;
  menu?: {label: string; run(): void}[];
}

const BOX = 62, HALF = BOX / 2, FRAME = 124, CENTRE = FRAME / 2, DEPTH = 300, DRAG_PIXELS = 4;
const AXIS_COLOURS = ["#e5322d", "#2fa84f", "#1f4fff"], AXIS_NAMES = ["X", "Y", "Z"];
const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const scale = (a: Vec3, s: number): Vec3 => [a[0] * s, a[1] * s, a[2] * s];
const norm = (a: Vec3): Vec3 => { const l = Math.hypot(...a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
const key = (n: Vec3) => n.join(",");
const SVG = "http://www.w3.org/2000/svg";

export class ViewCube {
  readonly element: HTMLElement;
  private faces: {element: HTMLElement; face: CubeFace; right: Vec3}[] = [];
  private lines: {line: SVGLineElement; text: SVGTextElement}[] = [];
  private menu: HTMLElement | undefined;
  private last = "";
  private drag: {x: number; y: number; moved: boolean; id: number} | undefined;
  private suppress = false;
  private axes: [Vec3, Vec3, Vec3];
  private dismiss = (event: Event) => { if (this.menu && !this.menu.contains(event.target as Node) && !(event.target as HTMLElement).closest?.(".vc-more")) this.closeMenu(); };

  constructor(parent: HTMLElement, private host: CubeHost, label = "View cube") {
    this.axes = host.axes ?? [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    const root = document.createElement("div");
    root.className = "viewcube";
    root.setAttribute("role", "group");
    root.setAttribute("aria-label", `${label}. Drag to rotate; click a face, edge or corner to look from there.`);
    root.title = "Drag to rotate the view · click a face, edge or corner to snap to it · double-click for home";
    const scene = document.createElement("div");
    scene.className = "viewcube-scene";
    for (const face of host.faces) {
      const right = cross(face.up, face.normal);
      const element = document.createElement("div");
      element.className = "viewcube-face";
      for (let row = -1; row <= 1; row++) for (let col = -1; col <= 1; col++) {
        // Row 0 of the grid is the top of the face (towards `up`).
        const cell = document.createElement("div");
        cell.setAttribute("role", "button");
        const direction: Vec3 = [0, 0, 0];
        for (let k = 0; k < 3; k++) direction[k] = face.normal[k] + col * right[k] + -row * face.up[k];
        cell.dataset.direction = key(direction);
        cell.className = `viewcube-cell ${row === 0 && col === 0 ? "face" : row !== 0 && col !== 0 ? "corner" : "edge"}`;
        cell.setAttribute("aria-label", this.describe(host.faces, direction));
        if (row === 0 && col === 0) cell.textContent = face.label;
        element.append(cell);
      }
      scene.append(element);
      this.faces.push({element, face, right});
    }
    root.append(scene);

    // Axis triad, projected with the same perspective as the cube.
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", "viewcube-axes");
    svg.setAttribute("viewBox", `0 0 ${FRAME} ${FRAME}`);
    svg.setAttribute("aria-hidden", "true");
    for (let i = 0; i < 3; i++) {
      const line = document.createElementNS(SVG, "line");
      line.setAttribute("stroke", AXIS_COLOURS[i]); line.setAttribute("stroke-width", "2.4"); line.setAttribute("stroke-linecap", "round");
      const text = document.createElementNS(SVG, "text");
      text.setAttribute("fill", AXIS_COLOURS[i]); text.textContent = AXIS_NAMES[i];
      svg.append(line, text);
      this.lines.push({line, text});
    }
    root.append(svg);

    const home = document.createElement("div");
    home.className = "vc-btn vc-home"; home.setAttribute("role", "button"); home.title = "Home view"; home.setAttribute("aria-label", "Home view");
    home.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M3.5 11.2 12 3.8l8.5 7.4M6 9.8V20h4.2v-5.6h3.6V20H18V9.8" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    home.addEventListener("click", event => { event.stopPropagation(); this.host.home?.(); });
    root.append(home);
    if (host.menu?.length) {
      const more = document.createElement("div");
      more.className = "vc-btn vc-more"; more.setAttribute("role", "button"); more.title = "Standard views"; more.setAttribute("aria-label", "Standard views"); more.setAttribute("aria-haspopup", "true");
      more.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M5 9h14l-7 8z" fill="currentColor"/></svg>`;
      more.addEventListener("click", event => { event.stopPropagation(); this.toggleMenu(); });
      root.append(more);
    }
    parent.prepend(root);
    this.element = root;
    root.addEventListener("pointerdown", event => this.down(event));
    root.addEventListener("pointermove", event => this.move(event));
    root.addEventListener("pointerup", event => this.up(event));
    root.addEventListener("pointercancel", event => this.up(event, true));
    root.addEventListener("click", event => this.click(event));
    root.addEventListener("dblclick", () => this.host.home?.());
    root.addEventListener("contextmenu", event => event.preventDefault());
    document.addEventListener("pointerdown", this.dismiss, true);
    this.update();
  }

  private describe(faces: CubeFace[], direction: Vec3) {
    const parts = faces.filter(f => dot(f.normal, direction) > 0.5).map(f => f.label.toLowerCase());
    return `Look from the ${parts.join(" / ")} ${parts.length === 1 ? "side" : parts.length === 2 ? "edge" : "corner"}`;
  }

  private toggleMenu() {
    if (this.menu) return this.closeMenu();
    const menu = document.createElement("div");
    menu.className = "vc-menu"; menu.setAttribute("role", "menu");
    for (const item of this.host.menu ?? []) {
      const entry = document.createElement("div");
      entry.className = "vc-item"; entry.setAttribute("role", "menuitem"); entry.textContent = item.label;
      entry.addEventListener("click", event => { event.stopPropagation(); this.closeMenu(); item.run(); });
      menu.append(entry);
    }
    this.element.append(menu);
    this.menu = menu;
  }
  private closeMenu() { this.menu?.remove(); this.menu = undefined; }

  /** Re-orient the cube to the camera. Cheap when nothing moved. */
  update() {
    const {right, up, forward} = this.host.basis();
    const stamp = [...right, ...up, ...forward].map(v => v.toFixed(4)).join(" ");
    if (stamp === this.last) return;
    this.last = stamp;
    // World vector -> screen (x right, y down, z towards the viewer) for the cube's CSS 3D space.
    const screen = (w: Vec3): Vec3 => [dot(w, right), -dot(w, up), -dot(w, forward)];
    for (const {element, face, right: faceRight} of this.faces) {
      const x = screen(faceRight), y = screen(scale(face.up, -1)), z = screen(face.normal), t = scale(z, HALF);
      element.style.transform = `matrix3d(${x[0]},${x[1]},${x[2]},0,${y[0]},${y[1]},${y[2]},0,${z[0]},${z[1]},${z[2]},0,${t[0]},${t[1]},${t[2]},1)`;
      // Brighter when the face turns towards the viewer; CSS hides the faces turned away.
      element.style.setProperty("--facing", Math.max(0, z[2]).toFixed(3));
    }
    // Axes start at the cube's lowest corner and run past it, as in CAD packages.
    const project = (p: Vec3): [number, number] => { const k = DEPTH / (DEPTH - p[2]); return [CENTRE + p[0] * k, CENTRE + p[1] * k]; };
    const origin = [0, 1, 2].reduce<Vec3>((sum, i) => { const s = screen(this.axes[i]); return [sum[0] - s[0] * HALF, sum[1] - s[1] * HALF, sum[2] - s[2] * HALF]; }, [0, 0, 0]);
    const [ox, oy] = project(origin);
    this.axes.forEach((axis, i) => {
      const s = screen(axis), end: Vec3 = [origin[0] + s[0] * BOX * 1.5, origin[1] + s[1] * BOX * 1.5, origin[2] + s[2] * BOX * 1.5];
      const [ex, ey] = project(end), {line, text} = this.lines[i];
      line.setAttribute("x1", ox.toFixed(1)); line.setAttribute("y1", oy.toFixed(1)); line.setAttribute("x2", ex.toFixed(1)); line.setAttribute("y2", ey.toFixed(1));
      text.setAttribute("x", (ex + (ex - ox) * 0.12 - 4).toFixed(1)); text.setAttribute("y", (ey + (ey - oy) * 0.12 + 4).toFixed(1));
    });
  }

  private down(event: PointerEvent) {
    if (event.button !== 0 && event.pointerType === "mouse") return;
    if ((event.target as HTMLElement).closest(".vc-btn, .vc-menu")) return;
    this.drag = {x: event.clientX, y: event.clientY, moved: false, id: event.pointerId};
  }
  private move(event: PointerEvent) {
    const drag = this.drag; if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < DRAG_PIXELS) return;
    if (!drag.moved) { this.element.setPointerCapture(event.pointerId); this.element.classList.add("pressed"); this.closeMenu(); }
    drag.moved = true;
    drag.x = event.clientX; drag.y = event.clientY;
    this.host.orbit(dx, dy);
    this.update();
  }
  private up(event: PointerEvent, cancelled = false) {
    const drag = this.drag; if (!drag || event.pointerId !== drag.id) return;
    this.drag = undefined;
    this.element.classList.remove("pressed");
    if (this.element.hasPointerCapture(event.pointerId)) this.element.releasePointerCapture(event.pointerId);
    if (drag.moved) this.host.end?.();
    // A drag must not also count as a click on whichever cell the pointer ended on.
    this.suppress = drag.moved || cancelled;
    if (this.suppress) setTimeout(() => { this.suppress = false; }, 0);
  }
  private click(event: MouseEvent) {
    if (this.suppress) { this.suppress = false; event.preventDefault(); return; }
    const cell = (event.target as HTMLElement).closest<HTMLElement>("[data-direction]");
    if (!cell?.dataset.direction) return;
    this.host.snap(norm(cell.dataset.direction.split(",").map(Number) as Vec3));
  }

  dispose() { document.removeEventListener("pointerdown", this.dismiss, true); this.element.remove(); }
}
