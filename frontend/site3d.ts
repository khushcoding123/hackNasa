import {
 ACESFilmicToneMapping, AmbientLight, BoxGeometry, BufferAttribute, BufferGeometry, CanvasTexture, Color, ConeGeometry, CylinderGeometry, DataTexture, DirectionalLight, FrontSide, Group, Line, LineBasicMaterial, LineDashedMaterial,
 Mesh, MeshBasicMaterial, MeshStandardMaterial, PerspectiveCamera, RepeatWrapping, Scene, SphereGeometry, Vector2, Vector3, WebGLRenderer, PCFSoftShadowMap, SRGBColorSpace,
} from "three";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls.js";
import {MOON_RADIUS_M} from "./terrain-mesh.js";
import {
 BUNDLED_RESOLUTION_M, NAC_RESOLUTION_M, WAC_RESOLUTION_M, blendCentre, cropCentre, loadBundledPatch, loadWacPatch, loadWmsMosaic, nacLayerAt, withNac,
} from "./site-imagery.js";
import {imageryLabel, nacSteps, requestSize} from "./site-projection.js";
import {MAP_LAYER} from "./imagery.js";
import {buildSurface, surfaceHeight} from "./site-surface.js";
import type {Result} from "./types.js";

/** Three.js close-up of any lunar location: a real-scale mesh of the best available LOLA terrain, aligned LROC imagery,
 * adjustable inspection lighting, and mission objects where available. Visual only: all mission values come from the mission API.
 * Everything is placed in a local East-North-Up frame at the site (x = east, y = up, z = south) so float32 stays precise. */

/** Detailed centre imagery fades into the wide imagery over this fraction of its half-width; the finer terrain blends
 * into the wide grid over the same band, so geometry and texture hand over together. */
const BLEND = 0.12;
/** Screen height of the site pin in CSS pixels. */
const PIN_PX = 34;
const PATCHES = {
 near: {half: 7000, size: 513, inner: 2000, innerSize: 257, nacHeight: 650, wacHeight: 2800},
 far: {half: 45_000, size: 385, inner: 10_000, innerSize: 257, nacHeight: 1800, wacHeight: 4800},
 global: {half: 175_000, size: 257, inner: 20_000, innerSize: 257, nacHeight: 5000, wacHeight: 9000},
} as const;
const rad = (d: number) => d * Math.PI / 180;
const clamp = (x: number, a: number, b: number) => Math.min(b, Math.max(a, x));
const escapeHtml = (s: string) => s.replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&#39;"}[c]!));

type Basis = {origin: Vector3; east: Vector3; north: Vector3; up: Vector3};
function basisAt(lat: number, lon: number, height: number): Basis {
 const p = rad(lat), l = rad(lon);
 const up = new Vector3(Math.cos(p) * Math.cos(l), Math.cos(p) * Math.sin(l), Math.sin(p));
 return {up, east: new Vector3(-Math.sin(l), Math.cos(l), 0), north: new Vector3(-Math.sin(p) * Math.cos(l), -Math.sin(p) * Math.sin(l), Math.cos(p)), origin: up.clone().multiplyScalar(MOON_RADIUS_M + height)};
}
/** Body-fixed lat/lon/height to local scene coordinates, including the Moon's curvature. */
function toLocal(b: Basis, lat: number, lon: number, height: number, out = new Vector3()) {
 const p = rad(lat), l = rad(lon), r = MOON_RADIUS_M + height;
 const d = new Vector3(r * Math.cos(p) * Math.cos(l), r * Math.cos(p) * Math.sin(l), r * Math.sin(p)).sub(b.origin);
 return out.set(d.dot(b.east), d.dot(b.up), -d.dot(b.north));
}

interface Grid { size: number; half: number; heights: Float32Array; resolutionM: number; source: string; sources: string[] }
type ImageryKind = "bundled" | "wac" | "nac";
const RANK: Record<ImageryKind, number> = {bundled: 0, wac: 1, nac: 2};
/** One region's best imagery so far: the canvas plus what it really resolves. */
interface Imagery { canvas: HTMLCanvasElement; kind: ImageryKind; name: string; sourceM: number; texelM: number }
type Label = {el: HTMLElement; anchor: (out: Vector3) => Vector3};

/** A local tangent grid avoids longitude seams and works directly across either pole. */
async function loadGrid(lat: number, lon: number, half: number, size: number, signal: AbortSignal): Promise<Grid> {
 const query = new URLSearchParams({lat: String(lat), lon: String(lon), half_width_m: String(half), size: String(size)});
 const response = await fetch(`/api/terrain/site?${query}`, {signal});
 if (!response.ok) throw new Error(`Site terrain failed (${response.status}).`);
 const heights = new Float32Array(await response.arrayBuffer());
 if (heights.length !== size * size || heights.some(h => !Number.isFinite(h))) throw new Error("Invalid site terrain grid.");
 return {size, half, heights, resolutionM: Number(response.headers.get("X-Terrain-Resolution-M")), source: response.headers.get("X-Terrain-Source") ?? "global",
  sources: (response.headers.get("X-Terrain-Sources") ?? "").split(",").filter(Boolean)};
}

export interface SiteOptions {
 lat: number; lon: number; name: string; detail: "near" | "far" | "global"; result?: Result; index?: number; disabled?: Set<string>;
 onClose: () => void;
}

export class SiteView {
 private renderer: WebGLRenderer;
 private scene = new Scene();
 private camera = new PerspectiveCamera(50, 1, 5, 80_000);
 private controls: OrbitControls;
 private sun = new DirectionalLight(0xfff4e0, 3.2);
 private ambient = new AmbientLight(0xc6cbd1, 0.9);
 private terrain: Mesh<BufferGeometry, MeshStandardMaterial[]> | undefined;
 private markers = new Group();
 private pin = new Group();
 private labels: Label[] = [];
 private basis: Basis;
 private half: number;
 private grid: Grid | undefined;
 private innerGrid: Grid | undefined;
 private centreHeight = 0;
 private exaggeration = 1;
 private imagery: ImageryKind = "bundled";
 private outerImagery: Imagery | undefined;
 private innerImagery: Imagery | undefined;
 private imageryAbort: AbortController | undefined;
 private grain: DataTexture | undefined;
 private moved = false;
 private lightMode: "inspection" | "mission" = "inspection";
 private inspectionAz = 225; private inspectionEl = 26;
 private result: Result | undefined;
 private index = 0;
 private disabled = new Set<string>();
 private az = 225; private el = 26;
 private scale = 8;
 private frame = 0;
 private labelFrame = 0;
 private terrainNote = "";
 private disposed = false;
 private abort = new AbortController();
 private towers = new Map<string, {top: Vector3; mast: Mesh; head: Mesh; link?: Line}>();
 private lander: Vector3 | undefined;
 private el$: HTMLElement;
 private observer: ResizeObserver;

 constructor(private root: HTMLElement, private options: SiteOptions) {
  this.result = options.result; this.index = options.index ?? 0; this.disabled = new Set(options.disabled ?? []);
  this.half = PATCHES[options.detail].half;
  this.camera.far = this.half * 12; this.camera.updateProjectionMatrix();
  this.basis = basisAt(options.lat, options.lon, 0);
  this.el$ = document.createElement("div");
  this.el$.className = "site3d";
  this.el$.innerHTML = `
   <canvas class="site3d-canvas" tabindex="0" aria-label="3D terrain close-up of ${escapeHtml(options.name)}. Drag to orbit, scroll to zoom, right-drag to pan."></canvas>
   <div class="site3d-labels" aria-hidden="true"></div>
   <div class="site3d-bar">
    <div><span class="site3d-kicker">LUNAR SURFACE / SITE EXPLORER</span><strong>${escapeHtml(options.name)}</strong><span class="site3d-sub">${Math.abs(options.lat).toFixed(4)}°${options.lat < 0 ? "S" : "N"} · ${((options.lon % 360) + 360) % 360 < 180 ? (((options.lon % 360) + 360) % 360).toFixed(4) + "°E" : (360 - (((options.lon % 360) + 360) % 360)).toFixed(4) + "°W"}</span></div>
    <button data-close>← Back to Moon</button>
   </div>
   <div class="site3d-hud"><span data-imagery>Imagery · loading</span><span data-quality>Terrain · loading</span><span>1× measured relief · close-up grain is illustrative</span><span>Drag to orbit · scroll to zoom</span></div>
   <details class="site3d-panel" ${root.clientWidth >= 700 ? "open" : ""}><summary>Light &amp; camera</summary>
    <label class="site3d-light-mode" hidden>Lighting <select data-light-mode><option value="inspection">Inspection light</option><option value="mission">Mission Sun</option></select></label>
    <label>Sun azimuth <output data-az-out></output><input data-az type="range" min="0" max="359" step="1" value="${this.az}"></label>
    <label>Sun elevation <output data-el-out></output><input data-el type="range" min="-2" max="35" step="0.25" value="${this.el}"></label>
    <label class="site3d-time" hidden>Mission time <output data-time-out></output><input data-time type="range" min="0" max="0" step="1" value="0"></label>
    <label>Tower size <output data-scale-out></output><input data-scale type="range" min="1" max="30" step="1" value="${this.scale}"></label>
    <label>Height exaggeration <output data-ex-out></output><input data-ex type="range" min="1" max="4" step="0.25" value="1"></label>
    <button data-retry-imagery hidden>Retry live imagery</button>
    <div class="site3d-buttons"><button data-camera="site">At site</button><button data-camera="top">Overview</button><button data-camera="lander">From lander</button><select data-stand aria-label="View from a tower"><option value="">From tower…</option></select></div>
    <p class="site3d-note" data-note>Loading lunar terrain…</p>
   </details>`;
  root.append(this.el$);
  const canvas = this.el$.querySelector<HTMLCanvasElement>("canvas")!;
  this.renderer = new WebGLRenderer({canvas, antialias: true, powerPreference: "high-performance"});
  this.renderer.setPixelRatio(Math.min(2, devicePixelRatio));
  this.renderer.outputColorSpace = SRGBColorSpace; this.renderer.toneMapping = ACESFilmicToneMapping; this.renderer.toneMappingExposure = 1.45;
  this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.type = PCFSoftShadowMap;
  this.scene.background = new Color(0x03060b);
  this.scene.add(this.ambient, this.sun, this.sun.target, this.markers);
  this.sun.castShadow = true;
  this.sun.shadow.mapSize.set(2048, 2048);
  const cam = this.sun.shadow.camera; cam.left = -this.half; cam.right = this.half; cam.top = this.half; cam.bottom = -this.half; cam.near = 100; cam.far = this.half * 5;
  this.sun.shadow.bias = -0.0002; this.sun.shadow.normalBias = 1;
  this.controls = new OrbitControls(this.camera, canvas);
  this.controls.enableDamping = true; this.controls.maxPolarAngle = Math.PI / 2 - 0.01; this.controls.minDistance = 20; this.controls.maxDistance = this.half * 5;
  this.controls.addEventListener("start", () => { this.moved = true; });
  // The shadow frustum follows the orbit target; otherwise panning walks out of it and shadows end at a square edge.
  this.controls.addEventListener("change", () => this.updateSun());
  this.pin.name = "site-pin";
  this.bind();
  this.syncResultUi();
  this.updateSun();
  this.resize();
  this.observer = new ResizeObserver(() => this.resize()); this.observer.observe(root);
  void this.load();
  const loop = () => {
   if (this.disposed) return;
   this.controls.update(); this.keepAboveGround(); this.sizePin();
   this.renderer.render(this.scene, this.camera); this.placeLabels();
   this.frame = requestAnimationFrame(loop);
  };
  loop();
 }

 private q<T extends HTMLElement>(s: string) { return this.el$.querySelector<T>(s)!; }
 private note(text: string) { this.q("[data-note]").textContent = text; }

 private bind() {
  const range = (sel: string, out: string, fmt: (v: number) => string, apply: (v: number) => void) => {
   const input = this.q<HTMLInputElement>(sel), output = this.q(out);
   const go = () => { const v = Number(input.value); output.textContent = fmt(v); apply(v); };
   input.oninput = go; output.textContent = fmt(Number(input.value));
  };
  range("[data-az]", "[data-az-out]", v => `${v}°`, v => { this.lightMode = "inspection"; this.q<HTMLSelectElement>("[data-light-mode]").value = "inspection"; this.az = this.inspectionAz = v; this.updateSun(); });
  range("[data-el]", "[data-el-out]", v => `${v.toFixed(1)}°`, v => { this.lightMode = "inspection"; this.q<HTMLSelectElement>("[data-light-mode]").value = "inspection"; this.el = this.inspectionEl = v; this.updateSun(); });
  range("[data-scale]", "[data-scale-out]", v => `×${v}`, v => { this.scale = v; this.applyScale(); });
  range("[data-ex]", "[data-ex-out]", v => `×${v}`, v => { this.exaggeration = v; if (this.terrain) this.terrain.scale.y = v; this.applyScale(); });
  this.q("[data-retry-imagery]").onclick = () => { if (this.grid) void this.loadImagery(); };
  const time = this.q<HTMLInputElement>("[data-time]");
  time.oninput = () => this.setIndex(Number(time.value), true);
  this.q<HTMLSelectElement>("[data-light-mode]").onchange = e => {
   this.lightMode = (e.target as HTMLSelectElement).value as "inspection" | "mission";
   if (this.lightMode === "mission") this.setIndex(this.index);
   else { this.az = this.inspectionAz; this.el = this.inspectionEl; this.syncLightSliders(); this.updateSun(); }
  };
  this.q("[data-close]").onclick = () => this.options.onClose();
  this.q("[data-camera='top']").onclick = () => { this.moved = true; this.overview(); };
  this.q("[data-camera='site']").onclick = () => { this.moved = true; this.flyover(); };
  this.q("[data-camera='lander']").onclick = () => this.stand(this.lander);
  this.q<HTMLSelectElement>("[data-stand]").onchange = e => {
   const id = (e.target as HTMLSelectElement).value; const t = this.towers.get(id);
   if (t) this.stand(new Vector3(t.top.x, t.top.y - 30, t.top.z), this.lander, 30);
   (e.target as HTMLSelectElement).value = "";
  };
  this.q("canvas").addEventListener("keydown", e => { if (e.key === "Escape") this.options.onClose(); });
 }

 private async load() {
  try {
   const patch = PATCHES[this.options.detail];
   const grid = await loadGrid(this.options.lat, this.options.lon, patch.half, patch.size, this.abort.signal);
   if (this.disposed) return;
   this.grid = grid;
   const mid = (grid.size - 1) / 2;
   this.centreHeight = grid.heights[mid * grid.size + mid];
   this.basis = basisAt(this.options.lat, this.options.lon, this.centreHeight);
   this.buildTerrain();
   this.buildMarkers();
   this.flyover();
   const m = grid.resolutionM;
   const source = grid.source === "near20km" ? "20 m regional LOLA" : grid.source === "far" ? "240 m regional LOLA" : "Global LOLA";
   this.q("[data-quality]").textContent = `${source} terrain · ${Number.isFinite(m) ? Math.round(m).toLocaleString() : "?"} m source`;
   this.terrainNote = `${(grid.half * 2 / 1000).toFixed(0)} km terrain context · source ${grid.sources.join(" + ")} · true scale`;
   this.note(this.terrainNote);
   void this.loadImagery();
   void this.loadInner();
  } catch (error) {
   if (this.disposed || (error as Error).name === "AbortError") return;
   console.error(error);
   this.note("Terrain could not be loaded for this site.");
  }
 }

 /** One continuous mesh: the wide grid, refined in the centre once the finer grid arrives (see site-surface.ts).
  * Group 1 (centre square) draws with the detailed texture on uv1, group 0 with the wide texture on uv. */
 private buildTerrain() {
  const g = this.grid; if (!g) return;
  const inner = this.innerGrid;
  const surface = buildSurface(g, inner, this.centreHeight, inner ? inner.half * BLEND : 0);
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(surface.positions, 3));
  geometry.setAttribute("uv", new BufferAttribute(surface.uv, 2));
  geometry.setAttribute("uv1", new BufferAttribute(surface.uv1, 2));
  geometry.setIndex(new BufferAttribute(surface.index, 1));
  geometry.addGroup(0, surface.innerCount * 3, 1);
  geometry.addGroup(surface.innerCount * 3, surface.index.length - surface.innerCount * 3, 0);
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  if (this.terrain) { this.terrain.geometry.dispose(); this.terrain.geometry = geometry; return; }
  // Both groups share lighting, grain (on the wide uv, so its pattern is continuous) and shadow settings.
  this.grain = this.grainNormalMap(g.half);
  const material = () => new MeshStandardMaterial({color: 0x888b8c, roughness: 1, metalness: 0, side: FrontSide,
   normalMap: this.grain, normalScale: new Vector2(0.12, 0.12)});
  this.terrain = new Mesh(geometry, [material(), material()]);
  this.terrain.name = "terrain";
  this.terrain.scale.y = this.exaggeration;
  this.terrain.castShadow = this.options.detail !== "global"; this.terrain.receiveShadow = true;
  this.scene.add(this.terrain);
 }

 private grainNormalMap(half: number) {
  const size = 64, data = new Uint8Array(size * size * 4);
  const noise = (x: number, y: number) => Math.sin(x * 127.1 + y * 311.7) * Math.cos(x * 269.5 - y * 183.3);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
   const i = (y * size + x) * 4;
   data[i] = 128 + Math.round((noise(x + 1, y) - noise(x - 1, y)) * 15);
   data[i + 1] = 128 + Math.round((noise(x, y + 1) - noise(x, y - 1)) * 15);
   data[i + 2] = 250; data[i + 3] = 255;
  }
  const texture = new DataTexture(data, size, size);
  texture.wrapS = RepeatWrapping; texture.wrapT = RepeatWrapping;
  texture.repeat.set(2 * half / 80, 2 * half / 80);
  texture.needsUpdate = true;
  return texture;
 }

 private texture(canvas: HTMLCanvasElement, channel: number) {
  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace; texture.channel = channel;
  texture.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
  return texture;
 }
 /** Show the current best imagery: the wide image on group 0, and on group 1 the wide image's exact centre crop
  * with any detailed centre imagery faded in, so the two textures agree along the centre square's edge. */
 private paintImagery() {
  const mesh = this.terrain, outer = this.outerImagery; if (!mesh || !outer || this.disposed) return;
  const {half, inner} = PATCHES[this.options.detail];
  const crop = cropCentre(outer.canvas, half, inner, this.innerImagery?.canvas.width ?? clamp(Math.ceil(outer.canvas.width * inner / half), 64, 2048));
  const centre = this.innerImagery && RANK[this.innerImagery.kind] >= RANK[outer.kind] ? blendCentre(crop, this.innerImagery.canvas, BLEND) : crop;
  [outer.canvas, centre].forEach((canvas, i) => {
   const material = mesh.material[i];
   material.map?.dispose();
   material.map = this.texture(canvas, i); material.color.set(0xffffff); material.needsUpdate = true;
  });
  // Describe what is under the site: the detailed centre imagery when it is in use.
  const shown = this.innerImagery && RANK[this.innerImagery.kind] >= RANK[outer.kind] ? this.innerImagery : outer;
  this.q("[data-imagery]").textContent = `Imagery · ${imageryLabel(shown.name, shown.sourceM, shown.texelM)}`;
  if (RANK[shown.kind] > RANK[this.imagery]) { this.imagery = shown.kind; if (!this.moved) this.flyover(); }
 }
 private offer(region: "outer" | "inner", next: Imagery) {
  const current = region === "outer" ? this.outerImagery : this.innerImagery;
  if (current && RANK[next.kind] < RANK[current.kind]) return;
  if (region === "outer") this.outerImagery = next; else this.innerImagery = next;
  this.paintImagery();
 }

 /** Bundled map first (always available), then LROC WAC over the whole patch, then NAC near the poles. Each step
  * only ever upgrades what is shown, so a failed request leaves the previous imagery in place. The server's render
  * time grows with output size (a single 2048 px north-pole NAC image can take minutes, and slow responses are cut
  * off), so large images come as a quick preview, then full size, each fetched as tiles with a timeout. Rerun to retry. */
 private async loadImagery() {
  this.imageryAbort?.abort();
  const control = new AbortController(); this.imageryAbort = control;
  const signal = control.signal, stop = () => control.abort();
  this.abort.signal.addEventListener("abort", stop, {once: true});
  const retry = this.q<HTMLButtonElement>("[data-retry-imagery]"); retry.hidden = true;
  this.note(this.terrainNote);
  const {lat, lon, detail} = this.options, {half, inner} = PATCHES[detail];
  const maxTexture = this.renderer.capabilities.maxTextureSize;
  const maxSize = Math.min(2048, maxTexture), wacFull = requestSize(half, WAC_RESOLUTION_M, Math.min(4096, maxTexture));
  const wacSize = Math.min(wacFull, maxSize);
  const wac = loadWacPatch(lat, lon, half, wacSize, signal).then(canvas => ({canvas}), (error: Error) => ({error}));
  const failures: string[] = [];
  const failed = (what: string, error: unknown) => { if (!signal.aborted) { failures.push(what); console.warn(`${what} imagery unavailable`, error); } };
  try {
   if (!this.outerImagery) this.offer("outer", {canvas: await loadBundledPatch(lat, lon, half, signal), kind: "bundled",
    name: "Bundled NASA color map (fallback)", sourceM: BUNDLED_RESOLUTION_M, texelM: 2 * half / 256});
  } catch (error) { if (signal.aborted) return; console.warn("Bundled site image failed", error); }
  const response = await wac;
  if (signal.aborted) return;
  if ("canvas" in response) this.offer("outer", {canvas: response.canvas, kind: "wac", name: "LROC WAC", sourceM: WAC_RESOLUTION_M, texelM: 2 * half / wacSize});
  else failed("LROC WAC", response.error);
  const jobs: Promise<void>[] = [];
  // Global patches: a 4096 px WAC image reaches the 100 m source; it takes ~15 s, so it follows the 2048 px one.
  if ("canvas" in response && wacFull > wacSize) jobs.push(loadWmsMosaic(lat, lon, half, wacFull, MAP_LAYER, signal, {transparent: false, maxTile: 2048, attempts: 2, timeoutMs: 60_000})
   .then(canvas => this.offer("outer", {canvas, kind: "wac", name: "LROC WAC", sourceM: WAC_RESOLUTION_M, texelM: 2 * half / wacFull}), error => failed("full-resolution LROC WAC", error)));
  const layer = nacLayerAt(lat);
  if (layer) {
   const name = `LROC NAC ${lat < 0 ? "south-pole" : "north-pole"} mosaic`;
   // NAC only where it beats WAC by a clear margin: a preview size if one qualifies, then the full size.
   const nac = async (region: "outer" | "inner", h: number) => {
    let got = false;
    for (const size of nacSteps(h, maxSize, WAC_RESOLUTION_M)) {
     const full = size === maxSize;
     try {
      // 512 px tiles: polar NAC renders slowly (a single 2048 px north-pole image can take minutes).
      const overlay = await loadWmsMosaic(lat, lon, h, size, layer, signal, {maxTile: 512, attempts: 2, timeoutMs: 60_000});
      const wide = this.outerImagery;
      if (region === "outer") {
       if (!wide) continue;
       const result = withNac(wide.canvas, overlay);
       if (result.coverage > 0.02 && result.centerCovered) this.offer("outer", {canvas: result.canvas, kind: "nac", name, sourceM: NAC_RESOLUTION_M, texelM: 2 * h / size});
      } else {
       const result = withNac(wide ? cropCentre(wide.canvas, half, inner, size) : overlay, overlay);
       if (result.coverage > 0.02 && result.centerCovered) this.offer("inner", {canvas: result.canvas, kind: "nac", name, sourceM: NAC_RESOLUTION_M, texelM: 2 * h / size});
      }
      got = true;
     } catch (error) { failed(got ? "full-resolution LROC NAC" : "LROC NAC", error); return; }
    }
   };
   jobs.push(nac("outer", half), nac("inner", inner));
  }
  await Promise.all(jobs);
  if (signal.aborted || !failures.length) return;
  retry.hidden = false;
  this.note(`${[...new Set(failures)].join(" and ")} imagery unavailable · showing ${this.outerImagery ? this.outerImagery.name : "untextured terrain"} with measured terrain.`);
 }

 private async loadInner() {
  const signal = this.abort.signal, {lat, lon, detail} = this.options, patch = PATCHES[detail];
  try {
   const grid = await loadGrid(lat, lon, patch.inner, patch.innerSize, signal);
   if (signal.aborted || this.disposed) return;
   this.innerGrid = grid;
   this.buildTerrain();
   this.applyScale();
  } catch (error) { if (!signal.aborted) console.warn("Close-up terrain unavailable", error); }
 }

 /** Mission objects. Lander and towers are real-metre objects; "Tower size" scales them so they stay findable at 14 km. */
 private buildMarkers() {
  this.markers.traverse(o => { if (o instanceof Mesh || o instanceof Line) { o.geometry.dispose(); (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => m.dispose()); } });
  this.markers.clear(); this.towers.clear(); this.labels.forEach(l => l.el.remove()); this.labels = [];
  const select = this.q<HTMLSelectElement>("[data-stand]");
  select.length = 1;
  const labelLayer = this.q(".site3d-labels");
  const label = (text: string, anchor: Label["anchor"], cls = "") => { const el = document.createElement("span"); el.className = `site3d-label ${cls}`; el.textContent = text; labelLayer.append(el); this.labels.push({el, anchor}); };
  // The single site marker: a 3D pin whose tip touches the terrain at the site, resized every frame to a constant
  // on-screen height (sizePin) so it stays findable at any zoom. Its label sits just above the head.
  const red = new MeshStandardMaterial({color: 0xe94232, roughness: 0.42, metalness: 0.08, emissive: 0x3a0804});
  const white = new MeshStandardMaterial({color: 0xf6f9ff, roughness: 0.7});
  const head = new Mesh(new SphereGeometry(4, 24, 16), red); head.position.y = 11;
  const tip = new Mesh(new ConeGeometry(3.5, 8, 24), red); tip.rotation.z = Math.PI; tip.position.y = 4;
  const eye = new Mesh(new SphereGeometry(1.6, 16, 12), white); eye.position.set(0, 11, 3.6);
  this.pin.clear(); this.pin.add(head, tip, eye); this.markers.add(this.pin);
  label(this.options.name.toUpperCase(), out => out.copy(this.pin.position).setY(this.pin.position.y + 16 * this.pin.scale.y), "site");
  const r = this.result; if (!r) { this.lander = undefined; this.applyScale(); return; }
  const lander = toLocal(this.basis, r.site.lat, r.site.lon, r.site.elevation);
  this.lander = lander.clone();
  const body = new Mesh(new BoxGeometry(3, 2.5, 3), new MeshStandardMaterial({color: 0xc9d2d8, roughness: 0.48, metalness: 0.55}));
  body.position.copy(lander).add(new Vector3(0, 1.25, 0)); body.castShadow = true; body.name = "lander";
  const gold = new MeshStandardMaterial({color: 0xb8934b, roughness: 0.55, metalness: 0.55});
  const dark = new MeshStandardMaterial({color: 0x173148, roughness: 0.4, metalness: 0.2});
  const legMat = new MeshStandardMaterial({color: 0xe1e5dc, roughness: 0.45, metalness: 0.7});
  const lid = new Mesh(new CylinderGeometry(1.7, 1.7, 0.3, 8), gold); lid.position.y = 1.4; body.add(lid);
  for (const x of [-1, 1]) for (const z of [-1, 1]) {
   const leg = new Mesh(new CylinderGeometry(0.1, 0.13, 3.2, 8), legMat);
   leg.position.set(x * 1.5, -1.2, z * 1.5); leg.rotation.z = x * 0.45; leg.rotation.x = -z * 0.45; body.add(leg);
   const foot = new Mesh(new CylinderGeometry(0.52, 0.52, 0.12, 12), gold); foot.position.set(x * 2.1, -2.5, z * 2.1); body.add(foot);
  }
  for (const x of [-1, 1]) {
   const panel = new Mesh(new BoxGeometry(2.8, 0.12, 1.8), dark); panel.position.set(x * 3.1, 0.2, 0); body.add(panel);
   const frame = new Mesh(new BoxGeometry(3, 0.08, 2), legMat); frame.position.set(x * 3.1, 0.13, 0); body.add(frame);
  }
  const antenna = new Mesh(new CylinderGeometry(0.04, 0.05, 2.6, 8), legMat); antenna.position.set(0, 2.8, 0); body.add(antenna);
  const dish = new Mesh(new ConeGeometry(1.05, 0.45, 24, 1, true), legMat); dish.rotation.z = Math.PI; dish.position.set(0, 4.15, 0); body.add(dish);
  this.markers.add(body);
  // At the site itself the pin already marks the lander: one label instead of two overlapping ones.
  const siteLabel = this.labels.find(l => l.el.classList.contains("site"));
  if (siteLabel && Math.hypot(lander.x, lander.z) < 150) siteLabel.el.textContent = `${this.options.name.toUpperCase()} · LANDER`;
  else label("LANDER", out => out.set(lander.x, this.surfaceY(lander) + 8 * this.scale, lander.z), "lander");
  for (const c of r.candidates.filter(c => r.recommended.includes(c.id))) {
   const base = toLocal(this.basis, c.lat, c.lon, c.elevation);
   const top = base.clone().add(new Vector3(0, 30, 0));
   const mast = new Mesh(new CylinderGeometry(0.5, 0.8, 30, 10), new MeshStandardMaterial({color: 0xcfd6df, roughness: 0.48, metalness: 0.65}));
   mast.position.copy(base).add(new Vector3(0, 15, 0)); mast.castShadow = true;
   const platform = new Mesh(new CylinderGeometry(2.5, 2.5, 0.5, 12), gold); platform.position.y = -14.7; mast.add(platform);
   const collar = new Mesh(new CylinderGeometry(1.2, 1.2, 0.8, 12), dark); collar.position.y = 10; mast.add(collar);
   for (const side of [-1, 1]) {
    const panel = new Mesh(new BoxGeometry(3.5, 0.15, 2.2), dark); panel.position.set(side * 2.6, -9, 0); mast.add(panel);
   }
   const head = new Mesh(new SphereGeometry(2.2, 16, 12), new MeshBasicMaterial({color: 0x73d8ae}));
   head.position.copy(top);
   const link = new Line(new BufferGeometry().setFromPoints([lander.clone().add(new Vector3(0, 2.5, 0)), top]), new LineBasicMaterial({color: 0x73d8ae}));
   this.markers.add(mast, head, link);
   this.towers.set(c.id, {top, mast, head, link});
   label(c.id, out => out.copy(head.position).setY(head.position.y + 12 * this.scale));
   select.append(new Option(c.id, c.id));
  }
  this.applyScale(); this.paint();
 }
 private applyScale() {
  // Scale about each object's own base so markers stay planted on the (possibly exaggerated) surface.
  const grow = (mesh: Mesh, anchor: Vector3, lift: number) => { mesh.scale.setScalar(this.scale); mesh.position.set(anchor.x, this.surfaceY(anchor) + lift * this.scale, anchor.z); };
  this.pin.position.set(0, this.surfaceY(new Vector3()), 0);
  const l = this.lander; const lander = this.markers.getObjectByName("lander") as Mesh | undefined;
  if (l && lander) grow(lander, l, 1.25);
  for (const t of this.towers.values()) {
   const base = new Vector3(t.top.x, t.top.y - 30, t.top.z);
   grow(t.mast, base, 15);
   t.head.scale.setScalar(this.scale);
   t.head.position.set(base.x, this.surfaceY(base) + 30 * this.scale, base.z);
   if (t.link && l) {
    const from = new Vector3(l.x, this.surfaceY(l) + 2.5 * this.scale, l.z);
    t.link.geometry.setFromPoints([from, t.head.position.clone()]);
    t.link.computeLineDistances?.();
   }
  }
 }
 /** Colour each tower and its link by whether the mission path is available at the selected time. */
 private paint() {
  const r = this.result; if (!r) return;
  for (const c of r.candidates) {
   const t = this.towers.get(c.id); if (!t) continue;
   const off = this.disabled.has(c.id), up = !!c.temporal_coverage[this.index] && !off;
   const color = new Color(off ? 0xf2848c : up ? 0x73d8ae : 0xf4ba72);
   (t.head.material as MeshBasicMaterial).color.copy(color);
   if (t.link) {
    (t.link.material as LineBasicMaterial).dispose();
    t.link.material = up ? new LineBasicMaterial({color}) : new LineDashedMaterial({color, dashSize: 60, gapSize: 40});
    t.link.computeLineDistances();
   }
  }
 }

 // ---------- lighting ----------
 private updateSun() {
  const az = rad(this.az), el = rad(this.el);
  // ENU sun direction → scene coordinates (x east, y up, z south).
  const dir = new Vector3(Math.cos(el) * Math.sin(az), Math.sin(el), -Math.cos(el) * Math.cos(az)).normalize();
  this.sun.target.position.copy(this.controls?.target ?? new Vector3());
  this.sun.position.copy(this.sun.target.position).addScaledVector(dir, Math.max(25_000, this.half * 3));
  this.sun.intensity = this.el <= 0 ? 0 : 2.5 * clamp(this.el / 8, 0.25, 1);
  this.sun.target.updateMatrixWorld();
  this.updateShadowCamera();
 }
 private updateShadowCamera() {
  if (!this.controls || this.options.detail === "global") return;
  const cam = this.sun.shadow.camera;
  const extent = Math.min(this.half, Math.max(1800, this.camera.position.distanceTo(this.controls.target) * 1.1));
  cam.left = -extent; cam.right = extent; cam.top = extent; cam.bottom = -extent; cam.updateProjectionMatrix();
 }
 private syncLightSliders() {
  this.q<HTMLInputElement>("[data-az]").value = String(Math.round(this.az));
  this.q<HTMLInputElement>("[data-el]").value = String(clamp(this.el, -2, 35));
  this.q("[data-az-out]").textContent = `${this.az.toFixed(0)}°`;
  this.q("[data-el-out]").textContent = `${this.el.toFixed(1)}°`;
 }
 private syncResultUi() {
  const r = this.result, row = this.q<HTMLElement>(".site3d-time");
  row.hidden = !r;
  this.q<HTMLElement>(".site3d-light-mode").hidden = !r;
  this.q<HTMLInputElement>("[data-scale]").closest("label")!.hidden = !r;
  this.q<HTMLButtonElement>("[data-camera='lander']").hidden = !r;
  this.q<HTMLSelectElement>("[data-stand]").hidden = !r;
  if (!r) return;
  const slider = this.q<HTMLInputElement>("[data-time]");
  slider.max = String(r.times.length - 1);
  this.setIndex(this.index, false);
 }
 /** Drive the light from the mission's own Sun azimuth/elevation at the selected sample. */
 setIndex(i: number, fromSlider = false) {
  const r = this.result; if (!r) return;
  i = clamp(Math.round(i), 0, r.times.length - 1); this.index = i;
  const s = r.samples;
  if (this.lightMode === "mission") {
   this.az = (s.sun_az[i] + 360) % 360; this.el = s.sun_el[i];
   this.syncLightSliders(); this.updateSun();
  }
  if (!fromSlider) this.q<HTMLInputElement>("[data-time]").value = String(i);
  this.q("[data-time-out]").textContent = r.times[i].replace("T", " ").slice(0, 16) + " UTC";
  this.paint();
 }
 setMission(result: Result | undefined, index: number, disabled: Set<string>) {
  this.result = result; this.index = index; this.disabled = new Set(disabled);
  this.syncResultUi();
  if (this.terrain) this.buildMarkers();
 }
 update(index: number, disabled: Set<string>) {
  this.disabled = new Set(disabled);
  if (this.result) this.setIndex(index); else this.paint();
 }

 // ---------- camera ----------
 /** Exaggerated height of the drawn surface under a marker. Mission objects come with their own DEM elevation; their
  * feet go on the mesh actually shown (which interpolates a coarser grid away from the centre), so nothing floats.
  * Outside the terrain patch, the object's own elevation is used. */
 private surfaceY(p: Vector3) {
  const g = this.grid, inside = g && Math.abs(p.x) <= g.half && Math.abs(p.z) <= g.half;
  return (inside ? this.groundAt(p.x, p.z) : p.y) * this.exaggeration;
 }
 /** Unexaggerated scene height of the terrain surface at (x, z): the same merged surface the mesh is built from. */
 private groundAt(x: number, z: number) {
  const g = this.grid; if (!g) return 0;
  x = clamp(x, -g.half, g.half); z = clamp(z, -g.half, g.half);
  const inner = this.innerGrid, radius = MOON_RADIUS_M + surfaceHeight(g, inner, x, z, inner ? inner.half * BLEND : 0);
  return MOON_RADIUS_M * radius / Math.hypot(MOON_RADIUS_M, x, z) - MOON_RADIUS_M - this.centreHeight;
 }
 private flyover() {
  const patch = PATCHES[this.options.detail];
  const height = (this.imagery === "nac" ? patch.nacHeight : patch.wacHeight) * (this.imagery === "bundled" ? 1.25 : 1);
  const x = -height * 1.7, z = height * 1.9;
  this.camera.position.set(x, Math.max(this.groundAt(x, z), this.groundAt(0, 0)) * this.exaggeration + height, z);
  this.controls.target.set(0, 0, 0);
  this.controls.update();
  this.updateSun();
 }
 private overview() {
  this.camera.position.set(-this.half * 0.9, this.half * 0.75, this.half * 1.1);
  this.controls.target.set(0, 0, 0);
  this.controls.update();
 }
 /** Stand a person-height above a point and look toward another (or north-ish), to judge line of sight by eye. */
 private stand(at?: Vector3, toward?: Vector3, height = 0) {
  if (!at) return;
  const eye = new Vector3(at.x, this.surfaceY(at) + height + 4, at.z);
  const look = toward ? new Vector3(toward.x, this.surfaceY(toward) + 4, toward.z) : eye.clone().add(new Vector3(0, 0, -500));
  const dir = look.clone().sub(eye).setY(0).normalize();
  this.camera.position.copy(eye);
  this.controls.target.copy(eye).addScaledVector(dir, 30);
  this.controls.update();
 }
 /** Keep the pin the same size on screen, standing on the terrain and turned toward the viewer. */
 private sizePin() {
  const p = this.pin.position, cam = this.camera.position, h = this.root.clientHeight || 1;
  const metresPerPx = 2 * cam.distanceTo(p) * Math.tan(rad(this.camera.fov) / 2) / h;
  this.pin.scale.setScalar(Math.max(0.12, metresPerPx * PIN_PX / 15));
  this.pin.rotation.y = Math.atan2(cam.x - p.x, cam.z - p.z);
 }
 /** OrbitControls knows nothing of terrain: keep the eye above the (exaggerated) surface and the near plane tight. */
 private keepAboveGround() {
  if (!this.grid) return;
  const cam = this.camera.position, g = this.grid;
  const ground = Math.abs(cam.x) <= g.half && Math.abs(cam.z) <= g.half ? this.groundAt(cam.x, cam.z) * this.exaggeration : -Infinity;
  if (cam.y < ground + 2) cam.y = ground + 2;
  const altitude = Number.isFinite(ground) ? cam.y - ground : cam.y;
  const near = clamp(altitude * 0.3, 0.5, 50);
  if (Math.abs(near - this.camera.near) > this.camera.near * 0.2) { this.camera.near = near; this.camera.updateProjectionMatrix(); }
 }
 /** True when terrain lies between the camera and a world point (coarse march over the height field). */
 private hidden(world: Vector3) {
  const cam = this.camera.position, steps = 48, ex = this.exaggeration, p = new Vector3();
  for (let i = 1; i < steps; i++) {
   p.lerpVectors(cam, world, i / steps);
   if (p.y < this.groundAt(p.x, p.z) * ex - 1) return true;
  }
  return false;
 }
 private placeLabels() {
  const w = this.root.clientWidth, h = this.root.clientHeight, v = new Vector3(), world = new Vector3();
  this.labelFrame = (this.labelFrame + 1) % 6;
  for (const label of this.labels) {
   label.anchor(world);
   v.copy(world).project(this.camera);
   const visible = v.z < 1 && Math.abs(v.x) < 1.05 && Math.abs(v.y) < 1.05;
   label.el.style.display = visible ? "" : "none";
   if (!visible) continue;
   label.el.style.transform = `translate(${Math.round((v.x * 0.5 + 0.5) * w)}px, ${Math.round((-v.y * 0.5 + 0.5) * h)}px) translate(-50%, -100%)`;
   // Occlusion is checked a few times a second; labels behind a ridge dim rather than float in front of it.
   if (this.labelFrame === 0) label.el.classList.toggle("occluded", this.hidden(world));
  }
 }
 private resize() {
  const w = this.root.clientWidth, h = this.root.clientHeight; if (!w || !h) return;
  this.renderer.setSize(w, h, false); this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
 }

 dispose() {
  this.disposed = true; cancelAnimationFrame(this.frame); this.abort.abort(); this.controls.dispose(); this.observer.disconnect();
  this.scene.traverse(o => { if (o instanceof Mesh || o instanceof Line) { o.geometry.dispose(); (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => { if (m instanceof MeshStandardMaterial) m.map?.dispose(); m.dispose(); }); } });
  this.grain?.dispose();
  this.renderer.dispose(); this.el$.remove();
 }
}
