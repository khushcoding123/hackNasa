import {
 ACESFilmicToneMapping, AmbientLight, BoxGeometry, BufferAttribute, BufferGeometry, CanvasTexture, Color, ConeGeometry, CylinderGeometry, DataTexture, DirectionalLight, DoubleSide, Group, Line, LineBasicMaterial, LineDashedMaterial,
 Mesh, MeshBasicMaterial, MeshStandardMaterial, PerspectiveCamera, RepeatWrapping, Scene, SphereGeometry, Spherical, Vector2, Vector3, WebGLRenderer, PCFSoftShadowMap, SRGBColorSpace,
} from "three";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls.js";
import {ViewCube, type Vec3} from "./viewcube.js";
import {MOON_RADIUS_M} from "./terrain-mesh.js";
import {loadBundledPatch, loadWacPatch, loadWmsPatch, nacLayerAt, withNac} from "./site-imagery.js";
import type {Result} from "./types.js";

/** Three.js close-up of any lunar location: a real-scale mesh of the best available LOLA terrain, aligned LROC imagery,
 * adjustable inspection lighting, and mission objects where available. Visual only: all mission values come from the mission API.
 * Everything is placed in a local East-North-Up frame at the site (x = east, y = up, z = south) so float32 stays precise. */

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
 private cube: ViewCube | undefined;
 private sun = new DirectionalLight(0xfff4e0, 3.2);
 private ambient = new AmbientLight(0xc6cbd1, 0.9);
 private terrain: Mesh | undefined;
 private innerTerrain: Mesh | undefined;
 private markers = new Group();
 private labels: {el: HTMLElement; world: Vector3}[] = [];
 private basis: Basis;
 private half: number;
 private grid: Grid | undefined;
 private imagery: "bundled" | "wac" | "nac" = "bundled";
 private moved = false;
 private lightMode: "inspection" | "mission" = "inspection";
 private inspectionAz = 225; private inspectionEl = 26;
 private result: Result | undefined;
 private index = 0;
 private disabled = new Set<string>();
 private az = 225; private el = 26;
 private scale = 8;
 private frame = 0;
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
  this.sun.shadow.bias = -0.0004; this.sun.shadow.normalBias = 2;
  this.controls = new OrbitControls(this.camera, canvas);
  this.controls.enableDamping = true; this.controls.maxPolarAngle = Math.PI / 2 - 0.01; this.controls.minDistance = 20; this.controls.maxDistance = this.half * 5;
  this.controls.addEventListener("start", () => { this.moved = true; });
  this.controls.addEventListener("change", () => this.updateShadowCamera());
  this.bind();
  this.syncResultUi();
  this.updateSun();
  this.resize();
  this.observer = new ResizeObserver(() => this.resize()); this.observer.observe(root);
  void this.load();
  this.setupCube();
  const loop = () => { if (this.disposed) return; this.controls.update(); this.renderer.render(this.scene, this.camera); this.cube?.update(); this.placeLabels(); this.frame = requestAnimationFrame(loop); };
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
  range("[data-ex]", "[data-ex-out]", v => `×${v}`, v => { if (this.terrain) this.terrain.scale.y = v; if (this.innerTerrain) this.innerTerrain.scale.y = v; this.applyScale(); });
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
   if (t) this.stand(t.top, this.lander);
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
   const mid = (grid.size - 1) / 2, h0 = grid.heights[Math.round(mid) * grid.size + Math.round(mid)];
   this.basis = basisAt(this.options.lat, this.options.lon, h0);
   this.buildTerrain(grid);
   this.buildMarkers();
   this.flyover();
   const m = grid.resolutionM;
   const source = grid.source === "near20km" ? "20 m regional LOLA" : grid.source === "far" ? "240 m regional LOLA" : "Global LOLA";
   this.q("[data-quality]").textContent = `${source} terrain · ${Number.isFinite(m) ? Math.round(m).toLocaleString() : "?"} m source`;
   this.note(`${(grid.half * 2 / 1000).toFixed(0)} km terrain context · source ${grid.sources.join(" + ")} · true scale`);
   void this.loadOuterImagery(grid);
   void this.loadInner();
  } catch (error) {
   if (this.disposed || (error as Error).name === "AbortError") return;
   console.error(error);
   this.note("Terrain could not be loaded for this site.");
  }
 }

 private buildTerrain(g: Grid) {
  this.terrain = this.createTerrain(g, false);
  this.scene.add(this.terrain);
 }

 private createTerrain(g: Grid, inner: boolean): Mesh {
  const n = g.size, pos = new Float32Array(n * n * 3), uv = new Float32Array(n * n * 2);
  const centre = this.basis.origin.length() - MOON_RADIUS_M;
  const step = 2 * g.half / (n - 1);
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
   const i = r * n + c, x = (c - (n - 1) / 2) * step, z = (r - (n - 1) / 2) * step;
   const norm = Math.hypot(MOON_RADIUS_M, x, z), radius = MOON_RADIUS_M + g.heights[i];
   const px = x * radius / norm, pz = z * radius / norm;
   pos.set([px, MOON_RADIUS_M * radius / norm - MOON_RADIUS_M - centre, pz], i * 3);
   // The WMS AUTO:42003 orthographic axes are projected east and north metres, not grid indices.
   uv.set([clamp(0.5 + px / (2 * g.half), 0, 1), clamp(0.5 - pz / (2 * g.half), 0, 1)], i * 2);
  }
  const index = new Uint32Array((n - 1) * (n - 1) * 6);
  let k = 0;
  for (let r = 0; r < n - 1; r++) for (let c = 0; c < n - 1; c++) {
   const a = r * n + c, b = a + n, cc = a + 1, d = b + 1;
   index[k++] = a; index[k++] = b; index[k++] = cc; index[k++] = cc; index[k++] = b; index[k++] = d;
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(pos, 3));
  geometry.setAttribute("uv", new BufferAttribute(uv, 2));
  geometry.setIndex(new BufferAttribute(index, 1));
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  const material = new MeshStandardMaterial({color: 0x888b8c, roughness: 1, metalness: 0, side: DoubleSide,
   normalMap: this.grainNormalMap(g.half), normalScale: new Vector2(0.12, 0.12)});
  if (inner) { material.transparent = true; material.depthWrite = false; material.alphaMap = this.edgeMask(); material.polygonOffset = true; material.polygonOffsetFactor = -1; }
  const mesh = new Mesh(geometry, material);
  mesh.position.y = inner ? 0.75 : 0;
  mesh.castShadow = !inner && this.options.detail !== "global"; mesh.receiveShadow = true;
  mesh.visible = !inner;
  return mesh;
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

 private edgeMask() {
  const size = 128, canvas = document.createElement("canvas"); canvas.width = size; canvas.height = size;
  const ctx = canvas.getContext("2d")!, image = ctx.createImageData(size, size);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
   const edge = Math.max(Math.abs(x / (size - 1) * 2 - 1), Math.abs(y / (size - 1) * 2 - 1));
   const a = clamp((1 - edge) / 0.13, 0, 1), i = (y * size + x) * 4;
   image.data[i] = image.data[i + 1] = image.data[i + 2] = Math.round(a * a * (3 - 2 * a) * 255);
   image.data[i + 3] = 255;
  }
  ctx.putImageData(image, 0, 0);
  return new CanvasTexture(canvas);
 }

 private applyImage(mesh: Mesh | undefined, canvas: HTMLCanvasElement) {
  if (!mesh || this.disposed) return;
  const material = mesh.material as MeshStandardMaterial;
  material.map?.dispose();
  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  texture.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
  material.map = texture; material.color.set(0xffffff); material.needsUpdate = true;
  mesh.visible = true;
 }

 private setImagery(kind: "bundled" | "wac" | "nac", source: string) {
  if (this.disposed) return;
  const rank = {bundled: 0, wac: 1, nac: 2};
  if (rank[kind] < rank[this.imagery]) return;
  this.imagery = kind;
  this.q("[data-imagery]").textContent = `Imagery · ${source}`;
  if (!this.moved) this.flyover();
 }

 private async loadOuterImagery(g: Grid) {
  const signal = this.abort.signal, {lat, lon} = this.options;
  const imageSize = this.options.detail === "global" ? 2048 : 1024;
  const wac = loadWacPatch(lat, lon, g.half, imageSize, signal).then(canvas => ({canvas}), error => ({error}));
  try { this.applyImage(this.terrain, await loadBundledPatch(lat, lon, g.half, signal));
   this.setImagery("bundled", "bundled NASA map · coarse fallback"); }
  catch (error) { if (!signal.aborted) console.warn("Bundled site image failed", error); }
  const response = await wac;
  if (signal.aborted) return;
  if (!("canvas" in response) || !response.canvas) {
   this.note("Live LROC imagery unavailable · showing bundled NASA imagery and measured terrain.");
   return;
  }
  this.applyImage(this.terrain, response.canvas);
  this.setImagery("wac", "LROC WAC · 100 m/px source");
  const layer = nacLayerAt(lat); if (!layer) return;
  try {
   const overlay = await loadWmsPatch(lat, lon, g.half, 1024, layer, signal);
   const result = withNac(response.canvas, overlay);
   if (result.coverage > 0.02) this.applyImage(this.terrain, result.canvas);
  } catch (error) { if (!signal.aborted) console.warn("Outer NAC imagery unavailable", error); }
 }

 private async loadInner() {
  const signal = this.abort.signal, {lat, lon, detail} = this.options, patch = PATCHES[detail];
  try {
   const grid = await loadGrid(lat, lon, patch.inner, patch.innerSize, signal);
   if (signal.aborted || this.disposed) return;
   this.innerTerrain = this.createTerrain(grid, true);
   this.innerTerrain.scale.y = this.terrain?.scale.y ?? 1;
   this.scene.add(this.innerTerrain);
   await this.loadInnerImagery(grid);
  } catch (error) { if (!signal.aborted) console.warn("Close-up terrain unavailable", error); }
 }

 private async loadInnerImagery(g: Grid) {
  const signal = this.abort.signal, {lat, lon} = this.options;
  const wac = loadWacPatch(lat, lon, g.half, 1024, signal).then(canvas => ({canvas}), error => ({error}));
  try { this.applyImage(this.innerTerrain, await loadBundledPatch(lat, lon, g.half, signal)); }
  catch (error) { if (!signal.aborted) console.warn("Close-up fallback imagery unavailable", error); }
  const response = await wac;
  if (signal.aborted) return;
  if (!("canvas" in response) || !response.canvas) return;
  this.applyImage(this.innerTerrain, response.canvas);
  this.setImagery("wac", "LROC WAC · 100 m/px source");
  const layer = nacLayerAt(lat); if (!layer) return;
  try {
   const overlay = await loadWmsPatch(lat, lon, g.half, 2048, layer, signal);
   const base = document.createElement("canvas"); base.width = overlay.width; base.height = overlay.height;
   base.getContext("2d")!.drawImage(response.canvas, 0, 0, base.width, base.height);
   const result = withNac(base, overlay);
   if (result.coverage > 0.02) this.applyImage(this.innerTerrain, result.canvas);
   if (result.centerCovered) this.setImagery("nac", `LROC NAC ${lat < 0 ? "south-pole" : "north-pole"} · 1 m/px source`);
  } catch (error) { if (!signal.aborted) console.warn("Close-up NAC imagery unavailable", error); }
 }

 /** Mission objects. Lander and towers are real-metre objects; "Tower size" scales them so they stay findable at 14 km. */
 private buildMarkers() {
  this.markers.traverse(o => { if (o instanceof Mesh || o instanceof Line) { o.geometry.dispose(); (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => m.dispose()); } });
  this.markers.clear(); this.towers.clear(); this.labels.forEach(l => l.el.remove()); this.labels = [];
  const select = this.q<HTMLSelectElement>("[data-stand]");
  select.length = 1;
  const labelLayer = this.q(".site3d-labels");
  const label = (text: string, world: Vector3, cls = "") => { const el = document.createElement("span"); el.className = `site3d-label ${cls}`; el.textContent = text; labelLayer.append(el); this.labels.push({el, world}); };
  const pinSize = clamp(this.half / 7000, 1, 10);
  const pin = new Group(); pin.name = "site-pin";
  const red = new MeshStandardMaterial({color: 0xe94232, roughness: 0.42, metalness: 0.08});
  const white = new MeshStandardMaterial({color: 0xf6f9ff, roughness: 0.7});
  const head = new Mesh(new SphereGeometry(4 * pinSize, 24, 16), red); head.position.y = 11 * pinSize;
  const tip = new Mesh(new ConeGeometry(3.5 * pinSize, 8 * pinSize, 24), red); tip.rotation.z = Math.PI; tip.position.y = 5 * pinSize;
  const eye = new Mesh(new SphereGeometry(1.6 * pinSize, 16, 12), white); eye.position.set(0, 11 * pinSize, 3.6 * pinSize);
  pin.add(head, tip, eye); this.markers.add(pin);
  label(this.options.name.toUpperCase(), new Vector3(0, 20 * pinSize, 0), "site");
  const r = this.result; if (!r) { this.lander = undefined; return; }
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
  label("LANDER", lander.clone().add(new Vector3(0, 12, 0)), "lander");
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
   label(c.id, top.clone().add(new Vector3(0, 14, 0)));
   select.append(new Option(c.id, c.id));
  }
  this.applyScale(); this.paint();
 }
 private applyScale() {
  const ex = this.terrain?.scale.y ?? 1;
  // Scale about each object's own base so markers stay planted on the (possibly exaggerated) surface.
  const grow = (mesh: Mesh, anchor: Vector3, lift: number) => { mesh.scale.setScalar(this.scale); mesh.position.set(anchor.x, anchor.y * ex + lift * this.scale, anchor.z); };
  const l = this.lander; const lander = this.markers.getObjectByName("lander") as Mesh | undefined;
  if (l && lander) grow(lander, l, 1.25);
  const r = this.result;
  const items = r ? r.candidates.filter(c => r.recommended.includes(c.id)) : [];
  for (const c of items) {
   const t = this.towers.get(c.id); if (!t) continue;
   const base = new Vector3(t.top.x, t.top.y - 30, t.top.z);
   grow(t.mast, base, 15);
   t.head.scale.setScalar(this.scale);
   t.head.position.set(base.x, base.y * ex + 30 * this.scale, base.z);
   if (t.link && l) {
    const from = new Vector3(l.x, l.y * ex + 2.5 * this.scale, l.z);
    t.link.geometry.setFromPoints([from, t.head.position.clone()]);
    t.link.computeLineDistances?.();
   }
  }
  const tops = new Map<string, Vector3>();
  for (const c of items) { const t = this.towers.get(c.id); if (t) tops.set(c.id, t.head.position); }
  this.labels.forEach(lab => {
   const id = lab.el.textContent ?? "";
   const p = tops.get(id); if (p) lab.world.copy(p).add(new Vector3(0, 12 * this.scale, 0));
   else if (lab.el.classList.contains("lander") && l) lab.world.set(l.x, l.y * ex + 8 * this.scale, l.z);
  });
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
    t.link.material = up || off ? new LineBasicMaterial({color}) : new LineDashedMaterial({color, dashSize: 60, gapSize: 40});
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
 private groundAt(x: number, z: number) {
  const g = this.grid; if (!g) return 0;
  const col = clamp((x / g.half + 1) * (g.size - 1) / 2, 0, g.size - 1);
  const row = clamp((z / g.half + 1) * (g.size - 1) / 2, 0, g.size - 1);
  const c = Math.min(g.size - 2, Math.floor(col)), r = Math.min(g.size - 2, Math.floor(row));
  const fx = col - c, fz = row - r, h = g.heights;
  const elevation = h[r * g.size + c] * (1 - fx) * (1 - fz) + h[r * g.size + c + 1] * fx * (1 - fz)
   + h[(r + 1) * g.size + c] * (1 - fx) * fz + h[(r + 1) * g.size + c + 1] * fx * fz;
  const radius = MOON_RADIUS_M + elevation, norm = Math.hypot(MOON_RADIUS_M, x, z);
  return MOON_RADIUS_M * radius / norm - this.basis.origin.length();
 }
 private flyover() {
  const patch = PATCHES[this.options.detail];
  const height = (this.imagery === "nac" ? patch.nacHeight : patch.wacHeight) * (this.imagery === "bundled" ? 1.25 : 1);
  const x = -height * 1.7, z = height * 1.9;
  this.camera.position.set(x, Math.max(this.groundAt(x, z), this.groundAt(0, 0)) + height, z);
  this.controls.target.set(0, 0, 0);
  this.controls.update();
  this.updateSun();
 }

 /** CAD-style view cube in the site's local frame: x east, y up, z south. */
 private setupCube() {
  const parent = this.el$, camera = this.camera, controls = this.controls;
  const holder = document.createElement("div"); holder.className = "site3d-cube"; parent.append(holder);
  const east: Vec3 = [1, 0, 0], up: Vec3 = [0, 1, 0], south: Vec3 = [0, 0, 1], neg = (v: Vec3): Vec3 => [-v[0], -v[1], -v[2]];
  const look = (direction: Vec3) => {
   // Move the camera to the given side of the target, keeping its distance, never below the horizon.
   const distance = camera.position.distanceTo(controls.target), d = new Vector3(...direction); d.y = Math.max(d.y, 0.04); d.normalize();
   camera.position.copy(controls.target).addScaledVector(d, distance); controls.update();
  };
  this.cube = new ViewCube(holder, {
   faces: [{label: "TOP", normal: up, up: neg(south)}, {label: "BOTTOM", normal: neg(up), up: south}, {label: "FRONT", normal: south, up},
    {label: "BACK", normal: neg(south), up}, {label: "RIGHT", normal: east, up}, {label: "LEFT", normal: neg(east), up}],
   axes: [east, neg(south), up],
   basis: () => { const f = (x: number, y: number, z: number): Vec3 => { const v = new Vector3(x, y, z).applyQuaternion(camera.quaternion); return [v.x, v.y, v.z]; }; return {right: f(1, 0, 0), up: f(0, 1, 0), forward: f(0, 0, -1)}; },
   orbit: (dx, dy) => {
    const offset = camera.position.clone().sub(controls.target), spherical = new Spherical().setFromVector3(offset);
    spherical.theta += dx * 0.012; spherical.phi = Math.min(Math.PI / 2 - 0.01, Math.max(0.02, spherical.phi - dy * 0.01));
    camera.position.copy(controls.target).add(offset.setFromSpherical(spherical)); controls.update();
   },
   snap: direction => look(direction),
   home: () => this.overview(),
   menu: [{label: "Home view", run: () => this.overview()}, {label: "Top", run: () => look([0, 1, 0])}, {label: "Front · south", run: () => look([0, 0, 1])},
    {label: "Back · north", run: () => look([0, 0, -1])}, {label: "Right · east", run: () => look([1, 0, 0])}, {label: "Left · west", run: () => look([-1, 0, 0])}],
  });
 }
 private overview() {
  this.camera.position.set(-this.half * 0.9, this.half * 0.75, this.half * 1.1);
  this.controls.target.set(0, 0, 0);
  this.controls.update();
 }
 /** Stand a person-height above a point and look toward another (or north-ish), to judge line of sight by eye. */
 private stand(at?: Vector3, toward?: Vector3) {
  if (!at) return;
  const ex = this.terrain?.scale.y ?? 1;
  const eye = new Vector3(at.x, at.y * ex + 4, at.z);
  const look = toward ? new Vector3(toward.x, toward.y * ex + 4, toward.z) : eye.clone().add(new Vector3(0, 0, -500));
  const dir = look.clone().sub(eye).setY(0).normalize();
  this.camera.position.copy(eye);
  this.controls.target.copy(eye).addScaledVector(dir, 30);
  this.controls.update();
 }
 private placeLabels() {
  const w = this.root.clientWidth, h = this.root.clientHeight, v = new Vector3();
  for (const {el, world} of this.labels) {
   v.copy(world).project(this.camera);
   const visible = v.z < 1 && Math.abs(v.x) < 1 && Math.abs(v.y) < 1;
   el.style.display = visible ? "" : "none";
   if (visible) el.style.transform = `translate(${(v.x * 0.5 + 0.5) * w}px, ${(-v.y * 0.5 + 0.5) * h}px) translate(-50%, -100%)`;
  }
 }
 private resize() {
  const w = this.root.clientWidth, h = this.root.clientHeight; if (!w || !h) return;
  this.renderer.setSize(w, h, false); this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
 }

 dispose() {
  this.disposed = true; this.cube?.dispose(); cancelAnimationFrame(this.frame); this.abort.abort(); this.controls.dispose(); this.observer.disconnect();
  this.scene.traverse(o => { if (o instanceof Mesh || o instanceof Line) { o.geometry.dispose(); (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => { if (m instanceof MeshStandardMaterial) { m.map?.dispose(); m.normalMap?.dispose(); m.alphaMap?.dispose(); } m.dispose(); }); } });
  this.renderer.dispose(); this.el$.remove();
 }
}
