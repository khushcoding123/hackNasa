import {
 AmbientLight, BoxGeometry, BufferAttribute, BufferGeometry, Color, CylinderGeometry, DirectionalLight, Group, Line, LineBasicMaterial, LineDashedMaterial,
 Mesh, MeshBasicMaterial, MeshLambertMaterial, PerspectiveCamera, Scene, SphereGeometry, Vector3, WebGLRenderer, PCFSoftShadowMap,
} from "three";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls.js";
import {MOON_RADIUS_M} from "./terrain-mesh.js";
import type {Result} from "./types.js";

/** Three.js close-up of one mission region: a real-scale mesh of the 20 m LOLA terrain, sunlight from the mission's own
 * Sun geometry, and the lander and relay towers as 3D objects. Visual only: all mission values come from the mission API.
 * Everything is placed in a local East-North-Up frame at the site (x = east, y = up, z = south) so float32 stays precise. */

const TILES = 4, TILE_SIZE = 129, HALF_WIDTH_M = 7000;
const rad = (d: number) => d * Math.PI / 180;
const clamp = (x: number, a: number, b: number) => Math.min(b, Math.max(a, x));

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

interface Grid { size: number; heights: Float32Array; lats: Float64Array; lons: Float64Array; resolutionM: number; sources: string[] }

/** Fetch TILES×TILES terrain tiles that tile a rectangle around the site and stitch them into one height grid. */
async function loadGrid(lat: number, lon: number, signal: AbortSignal): Promise<Grid> {
 lon = ((lon + 540) % 360) - 180;                                          // the API wants -180…180 (no dateline crossing)
 const dLat = HALF_WIDTH_M / MOON_RADIUS_M * 180 / Math.PI;
 const poleward = Math.min(89.4, Math.abs(lat) + dLat);                 // longitude degrees are widest on the pole side
 const dLon = Math.min(80, HALF_WIDTH_M / (MOON_RADIUS_M * Math.cos(rad(poleward))) * 180 / Math.PI);
 const west = lon - dLon, east = lon + dLon, south = lat - dLat, north = lat + dLat;
 const span = TILE_SIZE - 1, size = TILES * span + 1, stride = TILE_SIZE + 2;
 const heights = new Float32Array(size * size);
 let resolutionM = Infinity; const sources = new Set<string>();
 await Promise.all(Array.from({length: TILES * TILES}, async (_, k) => {
  const i = Math.floor(k / TILES), j = k % TILES;                          // i: north→south, j: west→east
  const w = west + (east - west) * j / TILES, e = west + (east - west) * (j + 1) / TILES;
  const n = north - (north - south) * i / TILES, s = north - (north - south) * (i + 1) / TILES;
  const response = await fetch(`/api/terrain/tile?west=${w}&south=${s}&east=${e}&north=${n}&size=${TILE_SIZE}`, {signal});
  if (!response.ok) throw new Error(`Terrain tile failed (${response.status}).`);
  const padding = Number(response.headers.get("X-Terrain-Padding") ?? 1);
  const tile = new Float32Array(await response.arrayBuffer()), tstride = TILE_SIZE + 2 * padding;
  resolutionM = Math.min(resolutionM, Number(response.headers.get("X-Terrain-Resolution-M") ?? Infinity));
  (response.headers.get("X-Terrain-Sources") ?? "").split(",").filter(Boolean).forEach(x => sources.add(x));
  for (let a = 0; a < TILE_SIZE; a++) for (let b = 0; b < TILE_SIZE; b++)
   heights[(i * span + a) * size + j * span + b] = tile[(a + padding) * tstride + b + padding];
 }));
 void stride;
 const lats = new Float64Array(size), lons = new Float64Array(size);
 for (let r = 0; r < size; r++) lats[r] = north - (north - south) * r / (size - 1);
 for (let c = 0; c < size; c++) lons[c] = west + (east - west) * c / (size - 1);
 return {size, heights, lats, lons, resolutionM, sources: [...sources]};
}

export interface SiteOptions {
 lat: number; lon: number; name: string; result?: Result; index?: number; disabled?: Set<string>;
 onClose: () => void;
}

export class SiteView {
 private renderer: WebGLRenderer;
 private scene = new Scene();
 private camera = new PerspectiveCamera(50, 1, 5, 80_000);
 private controls: OrbitControls;
 private sun = new DirectionalLight(0xfff4e0, 3.2);
 private ambient = new AmbientLight(0x6f7a8c, 0.035);
 private terrain: Mesh | undefined;
 private markers = new Group();
 private labels: {el: HTMLElement; world: Vector3}[] = [];
 private basis: Basis;
 private grid: Grid | undefined;
 private result: Result | undefined;
 private index = 0;
 private disabled = new Set<string>();
 private az = 40; private el = 3;
 private scale = 8;
 private frame = 0;
 private disposed = false;
 private abort = new AbortController();
 private towers = new Map<string, {top: Vector3; mast: Mesh; head: Mesh; link?: Line}>();
 private lander: Vector3 | undefined;
 private el$: HTMLElement;

 constructor(private root: HTMLElement, private options: SiteOptions) {
  this.result = options.result; this.index = options.index ?? 0; this.disabled = new Set(options.disabled ?? []);
  this.basis = basisAt(options.lat, options.lon, 0);
  this.el$ = document.createElement("div");
  this.el$.className = "site3d";
  this.el$.innerHTML = `
   <canvas class="site3d-canvas" tabindex="0" aria-label="3D terrain close-up of ${options.name}. Drag to orbit, scroll to zoom, right-drag to pan."></canvas>
   <div class="site3d-labels" aria-hidden="true"></div>
   <div class="site3d-bar">
    <div><strong>${options.name}</strong> <span class="site3d-sub">Site 3D · prototype</span></div>
    <button data-close>← Back to Moon</button>
   </div>
   <details class="site3d-panel" ${root.clientWidth >= 700 ? "open" : ""}><summary>Sun, scale &amp; camera</summary>
    <label>Sun azimuth <output data-az-out></output><input data-az type="range" min="0" max="359" step="1" value="${this.az}"></label>
    <label>Sun elevation <output data-el-out></output><input data-el type="range" min="-2" max="20" step="0.25" value="${this.el}"></label>
    <label class="site3d-time" hidden>Mission time <output data-time-out></output><input data-time type="range" min="0" max="0" step="1" value="0"></label>
    <label>Tower size <output data-scale-out></output><input data-scale type="range" min="1" max="30" step="1" value="${this.scale}"></label>
    <label>Height exaggeration <output data-ex-out></output><input data-ex type="range" min="1" max="4" step="0.25" value="1"></label>
    <div class="site3d-buttons"><button data-view="top">Overview</button><button data-view="lander">From lander</button><select data-stand aria-label="View from a tower"><option value="">From tower…</option></select></div>
    <p class="site3d-note" data-note>Loading 20 m terrain…</p>
   </details>`;
  root.append(this.el$);
  const canvas = this.el$.querySelector<HTMLCanvasElement>("canvas")!;
  this.renderer = new WebGLRenderer({canvas, antialias: true, powerPreference: "high-performance"});
  this.renderer.setPixelRatio(Math.min(2, devicePixelRatio));
  this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.type = PCFSoftShadowMap;
  this.scene.background = new Color(0x000000);
  this.scene.add(this.ambient, this.sun, this.sun.target, this.markers);
  this.sun.castShadow = true;
  this.sun.shadow.mapSize.set(4096, 4096);
  const cam = this.sun.shadow.camera; cam.left = -12000; cam.right = 12000; cam.top = 12000; cam.bottom = -12000; cam.near = 100; cam.far = 60_000;
  this.sun.shadow.bias = -0.0004; this.sun.shadow.normalBias = 2;
  this.controls = new OrbitControls(this.camera, canvas);
  this.controls.enableDamping = true; this.controls.maxPolarAngle = Math.PI / 2 - 0.01; this.controls.minDistance = 20; this.controls.maxDistance = 40_000;
  this.bind();
  this.syncResultUi();
  this.updateSun();
  this.resize();
  new ResizeObserver(() => this.resize()).observe(root);
  void this.load();
  const loop = () => { if (this.disposed) return; this.controls.update(); this.renderer.render(this.scene, this.camera); this.placeLabels(); this.frame = requestAnimationFrame(loop); };
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
  range("[data-az]", "[data-az-out]", v => `${v}°`, v => { this.az = v; this.updateSun(); });
  range("[data-el]", "[data-el-out]", v => `${v.toFixed(1)}°`, v => { this.el = v; this.updateSun(); });
  range("[data-scale]", "[data-scale-out]", v => `×${v}`, v => { this.scale = v; this.applyScale(); });
  range("[data-ex]", "[data-ex-out]", v => `×${v}`, v => { if (this.terrain) this.terrain.scale.y = v; this.applyScale(); });
  const time = this.q<HTMLInputElement>("[data-time]");
  time.oninput = () => this.setIndex(Number(time.value), true);
  this.q("[data-close]").onclick = () => this.options.onClose();
  this.q("[data-view='top']").onclick = () => this.overview();
  this.q("[data-view='lander']").onclick = () => this.stand(this.lander);
  this.q<HTMLSelectElement>("[data-stand]").onchange = e => {
   const id = (e.target as HTMLSelectElement).value; const t = this.towers.get(id);
   if (t) this.stand(t.top, this.lander);
   (e.target as HTMLSelectElement).value = "";
  };
  this.q("canvas").addEventListener("keydown", e => { if (e.key === "Escape") this.options.onClose(); });
 }

 private async load() {
  try {
   const grid = await loadGrid(this.options.lat, this.options.lon, this.abort.signal);
   if (this.disposed) return;
   this.grid = grid;
   const mid = (grid.size - 1) / 2, h0 = grid.heights[Math.round(mid) * grid.size + Math.round(mid)];
   this.basis = basisAt(this.options.lat, this.options.lon, h0);
   this.buildTerrain(grid);
   this.buildMarkers();
   this.overview();
   const m = grid.resolutionM;
   this.note(`${(HALF_WIDTH_M * 2 / 1000).toFixed(0)} × ${(HALF_WIDTH_M * 2 / 1000).toFixed(0)} km · ${grid.size}² samples · ${Number.isFinite(m) ? Math.round(m) : "?"} m/px LOLA · real scale`);
  } catch (error) {
   if (this.disposed || (error as Error).name === "AbortError") return;
   console.error(error);
   this.note("Terrain could not be loaded for this site.");
  }
 }

 private buildTerrain(g: Grid) {
  const n = g.size, pos = new Float32Array(n * n * 3), tmp = new Vector3();
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
   toLocal(this.basis, g.lats[r], g.lons[c], g.heights[r * n + c], tmp);
   pos.set([tmp.x, tmp.y, tmp.z], (r * n + c) * 3);
  }
  const index = new Uint32Array((n - 1) * (n - 1) * 6);
  let k = 0;
  for (let r = 0; r < n - 1; r++) for (let c = 0; c < n - 1; c++) {
   const a = r * n + c, b = a + n, cc = a + 1, d = b + 1;
   index[k++] = a; index[k++] = b; index[k++] = cc; index[k++] = cc; index[k++] = b; index[k++] = d;
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(pos, 3));
  geometry.setIndex(new BufferAttribute(index, 1));
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  const material = new MeshLambertMaterial({color: 0x9a9893});
  this.terrain?.geometry.dispose();
  if (this.terrain) this.scene.remove(this.terrain);
  this.terrain = new Mesh(geometry, material);
  this.terrain.castShadow = true; this.terrain.receiveShadow = true;
  this.scene.add(this.terrain);
 }

 /** Mission objects. Lander and towers are real-metre objects; "Tower size" scales them so they stay findable at 14 km. */
 private buildMarkers() {
  this.markers.clear(); this.towers.clear(); this.labels.forEach(l => l.el.remove()); this.labels = [];
  const select = this.q<HTMLSelectElement>("[data-stand]");
  select.length = 1;
  const r = this.result; if (!r) { this.lander = undefined; return; }
  const labelLayer = this.q(".site3d-labels");
  const label = (text: string, world: Vector3, cls = "") => { const el = document.createElement("span"); el.className = `site3d-label ${cls}`; el.textContent = text; labelLayer.append(el); this.labels.push({el, world}); };
  const lander = toLocal(this.basis, r.site.lat, r.site.lon, r.site.elevation);
  this.lander = lander.clone();
  const body = new Mesh(new BoxGeometry(3, 2.5, 3), new MeshLambertMaterial({color: 0xdfe8f2}));
  body.position.copy(lander).add(new Vector3(0, 1.25, 0)); body.castShadow = true; body.name = "lander";
  this.markers.add(body);
  label("LANDER", lander.clone().add(new Vector3(0, 12, 0)), "lander");
  for (const c of r.candidates.filter(c => r.recommended.includes(c.id))) {
   const base = toLocal(this.basis, c.lat, c.lon, c.elevation);
   const top = base.clone().add(new Vector3(0, 30, 0));
   const mast = new Mesh(new CylinderGeometry(0.5, 0.8, 30, 10), new MeshLambertMaterial({color: 0xcfd6df}));
   mast.position.copy(base).add(new Vector3(0, 15, 0)); mast.castShadow = true;
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
  this.sun.position.copy(dir).multiplyScalar(25_000);
  this.sun.target.position.set(0, 0, 0);
  this.sun.intensity = this.el <= 0 ? 0 : 3.2 * clamp(this.el / 1.5, 0.15, 1);
  this.sun.target.updateMatrixWorld();
 }
 private syncResultUi() {
  const r = this.result, row = this.q<HTMLElement>(".site3d-time");
  row.hidden = !r;
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
  this.az = (s.sun_az[i] + 360) % 360; this.el = s.sun_el[i];
  const set = (sel: string, v: number) => { this.q<HTMLInputElement>(sel).value = String(v); };
  set("[data-az]", Math.round(this.az)); set("[data-el]", clamp(this.el, -2, 20)); if (!fromSlider) set("[data-time]", i);
  this.q("[data-az-out]").textContent = `${this.az.toFixed(0)}°`; this.q("[data-el-out]").textContent = `${this.el.toFixed(1)}°`;
  this.q("[data-time-out]").textContent = r.times[i].replace("T", " ").slice(0, 16) + " UTC";
  this.updateSun(); this.paint();
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
 private overview() {
  const ex = this.terrain?.scale.y ?? 1; void ex;
  this.camera.position.set(-HALF_WIDTH_M * 0.9, HALF_WIDTH_M * 0.75, HALF_WIDTH_M * 1.1);
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
  this.disposed = true; cancelAnimationFrame(this.frame); this.abort.abort(); this.controls.dispose();
  this.scene.traverse(o => { if (o instanceof Mesh || o instanceof Line) { o.geometry.dispose(); (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => m.dispose()); } });
  this.renderer.dispose(); this.el$.remove();
 }
}
