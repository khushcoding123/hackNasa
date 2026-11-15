import {
 ArcType,
 BoundingSphere, CallbackPositionProperty, CallbackProperty, Cartesian2, Cartesian3, Cartographic, Color, ColorMaterialProperty, ConstantPositionProperty, ConstantProperty, DirectionalLight, Ellipsoid, GeographicProjection,
 Globe, HeadingPitchRange, HeightReference, LabelStyle, Math as CesiumMath, Matrix3, Matrix4, Quaternion, PolylineDashMaterialProperty, PolylineGlowMaterialProperty, Ion,
 ScreenSpaceEventHandler, ScreenSpaceEventType, SceneMode, SceneTransforms, ShadowMode, VerticalOrigin, Viewer, type Entity,
} from "cesium";
import {LANDMARKS, coordinate, type Landmark} from "./landmarks.js";
import {addImagery, type ImageryHandle, type ImageryState} from "./imagery.js";
import {MoonTerrainProvider, loadTerrainInfo} from "./terrain.js";
import {SiteView, detailForReach} from "./site3d.js";
import {LoadingCover} from "./loading.js";
import {horizonDeg, loadHorizons, type HorizonSet} from "./horizon.js";
import {icon} from "./icons.js";
import type {AreaResult, Candidate, ReferenceFailure, ReferenceMission, Result} from "./types.js";
import {boresightHit, orbitPeriod, satelliteAccess, satellitePosition, satelliteState, surfacePosition} from "./orbital-preview.js";

const MOON = Ellipsoid.MOON, R = MOON.maximumRadius;
Ion.defaultAccessToken = "";               // no Cesium ion: all data comes from this app or the public WMS
Ellipsoid.default = MOON;

type Mode = "map" | "3d";
interface View { lat: number; lon: number; extent: number; heading: number; pitch: number }
const FULL_EXTENT = 2 * R * 1.06, MIN_EXTENT = 2000, MAX_EXTENT = FULL_EXTENT * 3, ORTHO_DISTANCE = 60 * R;
const DEFAULT_VIEW: View = {lat: 8, lon: 12, extent: FULL_EXTENT, heading: 0, pitch: -Math.PI / 2};
const LANDING_VIEW: View = {...DEFAULT_VIEW, extent: 4.4 * R};
const MISSION_REGIONS: Record<string, string> = {haworth: "haworth", nobile: "nobile-rim-1", malapert: "malapert"};
const RESEARCH = new Set(Object.keys(MISSION_REGIONS));
const deg = CesiumMath.toDegrees, rad = CesiumMath.toRadians;
const SATELLITE_MODEL = "/assets/cassini-satellite.glb", SATELLITE_PIXELS = 220;
/** The supplied Cassini model is about 700 units across; scaled so the spacecraft body is roughly 7 m, its real size. */
const CASSINI_SCALE = 0.0135;
/** Live satellite pass: simulated seconds per real second (a 500 km orbit then loops in about two minutes). */
const PASS_SPEED = 60;
const ORBIT_COLOR = Color.fromCssColorString("#ffd479"), SATELLITE_LINK_COLOR = Color.fromCssColorString("#ff5ad0");
const satelliteModel = () => ({uri: SATELLITE_MODEL, minimumPixelSize: SATELLITE_PIXELS, scale: CASSINI_SCALE, lightColor: new Color(1.8, 1.8, 1.8, 1), maximumScale: 4_000_000, shadows: ShadowMode.DISABLED});
/** Cassini's attitude. Cesium turns a glTF model's +Z into the entity's +X and its +Y into +Z (Y-up, Z-forward glTF convention).
 * In the supplied model the white high-gain dish sits at the -Z end and opens along -Z, and the long boom runs along +Y.
 * So the dish (entity -X) is aimed along ``look`` (by default straight down at the Moon) and the boom stays as close to the
 * flight direction as the dish axis allows. ``radial`` is straight up from the Moon. The 3D site views use the same axes. */
function satelliteAttitude(radial: Cartesian3, tangent: Cartesian3, result = new Quaternion(), look?: Cartesian3) {
 const aim = look ? Cartesian3.normalize(look, new Cartesian3()) : Cartesian3.negate(radial, new Cartesian3());
 const boom = Cartesian3.subtract(tangent, Cartesian3.multiplyByScalar(aim, Cartesian3.dot(tangent, aim), new Cartesian3()), new Cartesian3());
 Cartesian3.normalize(boom, boom);
 const forward = Cartesian3.negate(aim, new Cartesian3());           // entity +X: the dish faces the opposite way
 const side = Cartesian3.cross(boom, forward, new Cartesian3()), rotation = new Matrix3();
 Matrix3.setColumn(rotation, 0, forward, rotation); Matrix3.setColumn(rotation, 1, side, rotation); Matrix3.setColumn(rotation, 2, boom, rotation);
 return Quaternion.fromRotationMatrix(rotation, result);
}
const wrap180 = (lon: number) => ((lon + 540) % 360) - 180;
const escapeHtml = (s: string) => s.replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]!));
const metres = (m: number) => m >= 1000 ? `${(m / 1000).toFixed(m >= 10000 ? 0 : 1)} km` : `${Math.round(m)} m`;
const angular = (a: Pick<Landmark, "lat" | "lon">, lat: number, lon: number) => {
 const p = rad(a.lat), q = rad(lat), d = rad(a.lon - lon);
 return Math.acos(Math.min(1, Math.max(-1, Math.sin(p) * Math.sin(q) + Math.cos(p) * Math.cos(q) * Math.cos(d)))) * R;
};
const smooth = (a: number, b: number, x: number) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

interface Pin { id: number; lat: number; lon: number; h: number; name: string }
const MAX_PINS = 12;
/** Points of a circle of `radius` metres around a surface point, as a closed polyline at the given height. */
function ring(lat: number, lon: number, radius: number, height: number) {
 const p = rad(lat), l = rad(lon), d = radius / R, out: Cartesian3[] = [];
 for (let i = 0; i <= 72; i++) {
  const b = (i / 72) * 2 * Math.PI;
  const q = Math.asin(Math.sin(p) * Math.cos(d) + Math.cos(p) * Math.sin(d) * Math.cos(b));
  const m = l + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p), Math.cos(d) - Math.sin(p) * Math.sin(q));
  out.push(Cartesian3.fromRadians(m, q, height, MOON));
 }
 return out;
}

/** Map-style red teardrop marker with a white centre; its tip sits on the surface point. */
let pinCache = "";
function pinImage() {
 if (pinCache) return pinCache;
 const c = document.createElement("canvas"); c.width = 68; c.height = 96;
 const g = c.getContext("2d")!;
 g.shadowColor = "rgba(0,0,0,.55)"; g.shadowBlur = 6; g.shadowOffsetY = 3;
 g.beginPath(); g.moveTo(34, 92); g.bezierCurveTo(34, 92, 6, 56, 6, 33); g.arc(34, 33, 28, Math.PI, 0); g.bezierCurveTo(62, 56, 34, 92, 34, 92); g.closePath();
 g.fillStyle = "#ea4335"; g.fill(); g.shadowColor = "transparent"; g.lineWidth = 3; g.strokeStyle = "#b31412"; g.stroke();
 g.beginPath(); g.arc(34, 33, 11, 0, 2 * Math.PI); g.fillStyle = "#fff"; g.fill();
 return pinCache = c.toDataURL();
}

export interface MoonHooks {
 onOpenExplorer?: () => void;
 /** The user chose to test a dropped pin as the mission location. `site` is the region whose terrain covers it. */
 onUsePin?: (lat: number, lon: number, site: string) => void;
 onChooseRegion?: (siteId: string) => void;
 onSelectCandidate?: (candidate: Candidate) => void;
 /** A square or free-form area was drawn: vertices as [latitude, east longitude]. */
 onArea?: (polygon: [number, number][]) => void;
 /** The live satellite pass moved: seconds after the overhead pass, a few times a second. */
 onSatelliteTime?: (seconds: number) => void;
}

/** CesiumJS Moon workspace. Visual and geographic only: all mission values come from the mission API. */
/** Circular navigation dial: the outer ring is split into four pan wedges, the inner disc into zoom in (top) and zoom out (bottom),
 * and the centre button resets the view. Every active part carries data-nav, handled by MoonView.navigate. */
function navigationDial() {
 const C = 66, OUTER = 64, RING = 41, HUB = 15, GAP = 1.6;
 const at = (r: number, deg: number) => { const t = rad(deg); return [(C + r * Math.sin(t)).toFixed(2), (C - r * Math.cos(t)).toFixed(2)]; };
 const wedge = (centre: number) => {
  const a0 = centre - 45 + GAP, a1 = centre + 45 - GAP, [x0, y0] = at(OUTER, a0), [x1, y1] = at(OUTER, a1), [x2, y2] = at(RING + 2, a1), [x3, y3] = at(RING + 2, a0);
  return `M${x0} ${y0}A${OUTER} ${OUTER} 0 0 1 ${x1} ${y1}L${x2} ${y2}A${RING + 2} ${RING + 2} 0 0 0 ${x3} ${y3}Z`;
 };
 const half = (centre: number) => {
  const a0 = centre - 90 + GAP, a1 = centre + 90 - GAP, [x0, y0] = at(RING - 2, a0), [x1, y1] = at(RING - 2, a1), [x2, y2] = at(HUB + 3, a1), [x3, y3] = at(HUB + 3, a0);
  return `M${x0} ${y0}A${RING - 2} ${RING - 2} 0 0 1 ${x1} ${y1}L${x2} ${y2}A${HUB + 3} ${HUB + 3} 0 0 0 ${x3} ${y3}Z`;
 };
 const part = (nav: string, label: string, path: string, glyph: string, x: number, y: number, size = 15) =>
  `<g class="vw-dial-part" data-nav="${nav}" role="button" tabindex="0" aria-label="${label}"><title>${label}</title><path d="${path}"/><text x="${x}" y="${y}" font-size="${size}" text-anchor="middle" dominant-baseline="central">${glyph}</text></g>`;
 const g = (deg: number) => at(52, deg).map(Number) as [number, number];
 const [ux, uy] = g(0), [rx, ry] = g(90), [dx, dy] = g(180), [lx, ly] = g(270);
 return `<div class="vw-dial-wrap"><svg class="vw-dial" viewBox="0 0 ${2 * C} ${2 * C}" width="${2 * C}" height="${2 * C}" role="group" aria-label="Navigation">
  ${part("up", "Pan north", wedge(0), "▲", ux, uy, 13)}${part("right", "Pan east", wedge(90), "▶", rx, ry, 13)}${part("down", "Pan south", wedge(180), "▼", dx, dy, 13)}${part("left", "Pan west", wedge(270), "◀", lx, ly, 13)}
  ${part("in", "Zoom in", half(0), "+", C, C - 26, 20)}${part("out", "Zoom out", half(180), "−", C, C + 26, 20)}
  <g class="vw-dial-part vw-dial-hub" data-nav="north" role="button" tabindex="0" aria-label="Reset north and view"><title>Reset view</title><circle cx="${C}" cy="${C}" r="${HUB}"/><circle class="vw-dial-dot" cx="${C}" cy="${C}" r="3.2"/></g>
 </svg>
 <div class="vw-tilt vw-3d" hidden><button data-nav="tilt-up" aria-label="Tilt up" title="Tilt up">▲</button><button data-nav="tilt-down" aria-label="Tilt down" title="Tilt down">▼</button></div></div>`;
}

export class MoonView {
 private viewer: Viewer | undefined;
 private terrain: MoonTerrainProvider | undefined;
 private imagery: ImageryHandle | undefined;
 private mode: Mode = "map";
 private disposed = false;
 private labelsOn = true;
 private view: View = DEFAULT_VIEW;
 private cameraOffset = 0;
 private animation = 0;
 private labelButtons: {feature: Landmark; button: HTMLButtonElement}[] = [];
 private activeLandmark = "";
 private result: Result | undefined;
 private missionVisible = true;
 private showAll = false;
 private index = 0;
 private disabled = new Set<string>();
 private selected = "";
 private towers = new Map<string, {entity: Entity; link?: Entity}>();
 private lander: Entity | undefined;
 private observer: ResizeObserver;
 private handler: ScreenSpaceEventHandler | undefined;
 private hover = {x: 0, y: 0, frame: 0};
 private statusTimer = 0;
 private satelliteTimer = 0;
 private settled = false;
 private pins = new Map<number, Pin>();
 private pinSeq = 0;
 private selectedPin = 0;
 private pinRing: Entity | undefined;
 private planRing: Entity | undefined;
 private planHighlight = true;
 private lightYaw = 40;
 private lightElevation = 30;
 private site: SiteView | undefined;
 private loader: LoadingCover | undefined;
 private loadingDone = 0;
 private cameraMoving = false;
 private layoutResult: AreaResult | undefined;
 private layoutFormat = "";
 private satelliteSeconds = 0;
 private pass = {playing: !matchMedia("(prefers-reduced-motion: reduce)").matches, base: 0, startedAt: performance.now()};
 private live: {result: AreaResult; nodes: {node: AreaResult["nodes"][number]; at: Cartesian3}[]; visible: boolean[]; satellite: Cartesian3; aim: Cartesian3;
  orientation: Quaternion; linked: number; seconds: number; reported: number; horizons?: HorizonSet} | undefined;
 private drawKind: "rect" | "free" | undefined;
 private drawing: {start: Cartesian2; screen: Cartesian2[]; points: Cartesian3[]} | undefined;
 private draft: Cartesian3[] = [];
 private suppressClickUntil = 0;

 constructor(private root: HTMLElement, private hooks: MoonHooks = {}) {
  root.classList.add("moon-workspace");
  root.dataset.mode = "map";
  root.innerHTML = `
  <div class="vw-canvas" id="cesium-host"></div>
  <div class="vw-labels" aria-label="Lunar feature labels"></div>
  <div class="vw-left">
   <div class="vw-search" role="search"><span class="vw-search-icon">${icon("search")}</span><input id="vw-search" type="search" placeholder="Find a landmark or region…" autocomplete="off" role="combobox" aria-expanded="false" aria-controls="vw-results" aria-label="Search lunar landmarks and mission regions"><ul id="vw-results" role="listbox" hidden></ul></div>
   <div class="vw-layers"><button class="vw-chip" data-layers aria-expanded="false" aria-controls="vw-layer-menu">${icon("layers")} Layers</button>
    <div id="vw-layer-menu" class="vw-menu" hidden>
     <label><input type="checkbox" data-labels checked> Feature labels</label>
     <label class="vw-mission-only" hidden><input type="checkbox" data-mission checked> Mission overlay</label>
     <label class="vw-mission-only" hidden><input type="checkbox" data-plan checked> Highlight mission plan area</label>
     <label class="vw-mission-only" hidden><input type="checkbox" data-candidates> Screened candidates</label>
     <div class="vw-pins"><strong>Pins</strong><ul data-pinlist></ul></div>
     <label>Imagery <select data-imagery><option value="auto">LROC WAC (live)</option><option value="bundled">Bundled (lower detail)</option></select></label>
     <p class="vw-note">Terrain is shown at true 1× scale.</p><p class="vw-note">Click the Moon to drop a pin, then open its 3D site view.</p>
    </div></div>
   <div class="vw-draw" role="group" aria-label="Draw an area to analyse"><span class="vw-tool-label">PLAN AN AREA</span><button class="vw-chip" data-draw="rect" aria-pressed="false" title="Drag across the Moon to draw a square or rectangle">${icon("rectangle")} Rectangle</button><button class="vw-chip" data-draw="free" aria-pressed="false" title="Press and drag to draw any shape">${icon("pen")} Freeform</button><button class="vw-chip" data-draw="clear" hidden>Clear area</button><button class="vw-chip" data-clear-pins hidden title="Remove every pin from the Moon">Clear pins</button><button class="vw-chip" data-pole title="Zoom to the south polar cap, where terrain detail is sharpest">${icon("target")} South pole</button></div>
  </div>
  <div class="vw-right" role="group" aria-label="Map controls">
   ${navigationDial()}
   <div class="vw-modes" role="group" aria-label="View mode"><button data-mode="map" aria-pressed="true">Map</button><button data-mode="3d" aria-pressed="false">3D</button></div>
   <button class="vw-site-btn" data-site3d title="Open the selected pin or current Moon location in three dimensions">${icon("terrain")} Site view</button>
  </div>
  <div class="vw-pass" hidden role="group" aria-label="Satellite pass"><button type="button" data-pass>❚❚</button><span data-pass-text aria-live="off"></span></div>
  <button class="vw-detail-btn" data-detail hidden title="Open a close-up 3D cut-out of the calculated area with its nodes, links and relay satellite">${icon("terrain")}<span><strong>Detailed view</strong><small>3D close-up of the calculated area</small></span></button>
  <div class="vw-inspector" role="region" aria-label="Location inspector" aria-live="polite" hidden></div>
  <div class="vw-toast" role="status" aria-live="polite"></div>
  <div class="vw-bottom">
   <span class="vw-readout" data-coords>Hover or click the Moon</span>
   <span class="vw-scale"><i data-scalebar></i><span data-scaletext></span></span>
   <span class="vw-res" data-res></span>
   <span class="vw-light vw-3d" hidden title="Drag to change the inspection light. It does not change mission dates, solar calculations, or communication results.">Interactive lighting</span>
   <span class="vw-credit">Imagery: <a href="https://quickmap.lroc.im-ldi.com/" target="_blank" rel="noopener noreferrer">LROC QuickMap / NASA GSFC / ASU</a> · Elevation: NASA LOLA · Rendered with <a href="https://cesium.com/platform/cesiumjs/" target="_blank" rel="noopener noreferrer">CesiumJS</a></span>
  </div>`;
  this.observer = new ResizeObserver(() => { if (this.viewer && !this.animation) this.apply(this.view); this.positionLabels(); this.trackBottomBar(); });
  this.loader = new LoadingCover(root, "vw-loader");
  try { this.create(); }
  catch (error) { console.error("Moon viewer failed to start", error); this.fallback(); return; }
  this.bindControls();
  this.observer.observe(root);
  const bar = root.querySelector<HTMLElement>(".vw-bottom"); if (bar) this.observer.observe(bar);
  void this.attachTerrain();
 }

 // ---------- scene ----------
 private create() {
  const host = this.root.querySelector<HTMLElement>("#cesium-host")!;
  const viewer = new Viewer(host, {
   baseLayer: false, baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false, navigationHelpButton: false,
   animation: false, timeline: false, fullscreenButton: false, infoBox: false, selectionIndicator: false, vrButton: false,
   globe: new Globe(MOON), skyBox: false, skyAtmosphere: false, scene3DOnly: true, requestRenderMode: true, maximumRenderTimeChange: Infinity,
   mapProjection: new GeographicProjection(MOON), sceneMode: SceneMode.SCENE3D, msaaSamples: 4,
   contextOptions: {webgl: {alpha: false, antialias: true, powerPreference: "high-performance"}},
  });
  this.viewer = viewer;
  // Cesium normally renders at CSS-pixel resolution. Raise the drawing buffer on
  // high-density displays so the Moon and labels stay crisp without exceeding 2×.
  viewer.resolutionScale = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
  const scene = viewer.scene;
  (viewer.cesiumWidget.creditContainer as HTMLElement).style.display = "none";
  scene.backgroundColor = Color.BLACK;
  if (scene.sun) scene.sun.show = false; if (scene.moon) scene.moon.show = false; scene.fog.enabled = false;
  scene.globe.showGroundAtmosphere = false; scene.globe.baseColor = Color.fromCssColorString("#26272a");
  scene.globe.depthTestAgainstTerrain = true; scene.globe.maximumScreenSpaceError = 2.6;
  scene.globe.lightingFadeInDistance = 1e12; scene.globe.lightingFadeOutDistance = 1e12; scene.globe.dynamicAtmosphereLighting = false;
  scene.light = new DirectionalLight({direction: new Cartesian3(1, 0, 0), intensity: 2});
  scene.shadowMap.softShadows = true; scene.shadowMap.size = 4096; scene.shadowMap.darkness = 0.42; scene.shadowMap.maximumDistance = 60_000;
  scene.shadowMap.enabled = false; scene.globe.shadows = ShadowMode.DISABLED;
  const camera = scene.camera;
  camera.percentageChanged = 0.0005;
  const control = scene.screenSpaceCameraController;
  control.enableCollisionDetection = true; control.minimumZoomDistance = 300; control.maximumZoomDistance = 8 * R;
  control.inertiaSpin = 0.7; control.inertiaZoom = 0.6;
  this.imagery = addImagery(viewer, state => this.onImageryState(state));
  this.setupSatellite();
  // Camera motion and the orbit both request frames as needed.
  camera.moveStart.addEventListener(() => { scene.requestRenderMode = false; this.cameraMoving = true; });
  camera.moveEnd.addEventListener(() => { this.cameraMoving = false; scene.requestRenderMode = true; scene.requestRender(); this.view = this.readView(); this.clampZoom(); this.refreshReadouts(); });
  camera.changed.addEventListener(() => { this.clampZoom(); });
  scene.preRender.addEventListener(() => { if (this.mode === "3d") this.updateLight(); });
  scene.preUpdate.addEventListener(() => { if (this.live) this.updateLive(); });
  scene.postRender.addEventListener(() => { this.positionLabels(); if (!scene.globe.tilesLoaded) scene.requestRender(); });
  scene.globe.tileLoadProgressEvent.addEventListener(count => { this.root.dataset.loading = String(count); scene.requestRender(); if (count === 0) this.refreshReadouts(); });
  this.setupLabels();
  this.setupPicking();
  this.apply(this.isLanding() ? LANDING_VIEW : this.view);
  this.applyMode();
  // The first frames lay out the canvas; re-apply the initial view once it has a real size, then trust the camera.
  requestAnimationFrame(() => requestAnimationFrame(() => { if (this.viewer && !this.disposed) { this.apply(this.view); this.settled = true; this.refreshReadouts(); } }));
 }
 /** NASA's Cassini spacecraft (glTF, as supplied). It is drawn at least SATELLITE_PIXELS wide so it stays visible at whole-Moon scale. */

 private setupSatellite() {
  const viewer = this.viewer!;
  const radius = R * 1.28, inclination = rad(32), started = performance.now();
  const normal = new Cartesian3(0, -Math.sin(inclination), Math.cos(inclination));
  const radial = new Cartesian3(), tangent = new Cartesian3(), center = new Cartesian3();
  const orientation = new Quaternion();
  const around = (angle: number, result = new Cartesian3()) => Cartesian3.fromElements(Math.cos(angle), Math.sin(angle) * Math.cos(inclination), Math.sin(angle) * Math.sin(inclination), result);
  // The orbit ring is fixed; the bright arc is the stretch the satellite is about to fly.
  const ring = Array.from({length: 181}, (_, i) => Cartesian3.multiplyByScalar(around(i / 180 * 2 * Math.PI), radius, new Cartesian3()));
  const ahead = Array.from({length: 49}, () => new Cartesian3());
  const update = () => {
   const angle = (performance.now() - started) / 1000 * (2 * Math.PI / 75) + rad(65);
   around(angle, radial);
   Cartesian3.fromElements(-Math.sin(angle), Math.cos(angle) * Math.cos(inclination), Math.cos(angle) * Math.sin(inclination), tangent);
   Cartesian3.multiplyByScalar(radial, radius, center);
   satelliteAttitude(radial, tangent, orientation);
   ahead.forEach((point, i) => Cartesian3.multiplyByScalar(around(angle + i / 48 * rad(100), point), radius, point));
  };
  update();
  viewer.scene.preUpdate.addEventListener(update);
  viewer.entities.add({id: "satellite:orbit", name: "Satellite orbit", polyline: {positions: ring, arcType: ArcType.NONE, width: 2,
   material: new PolylineDashMaterialProperty({color: ORBIT_COLOR.withAlpha(0.55), dashLength: 14})}});
  viewer.entities.add({id: "satellite:ahead", name: "Satellite path ahead", polyline: {positions: new CallbackProperty(() => ahead, false), arcType: ArcType.NONE, width: 6,
   material: new PolylineGlowMaterialProperty({color: ORBIT_COLOR, glowPower: 0.25})}});
  viewer.entities.add({id: "satellite:model", name: "Satellite · Cassini spacecraft model (orbit illustrative)",
   position: new CallbackPositionProperty((_time, result) => Cartesian3.clone(center, result), false),
   orientation: new CallbackProperty((_time, result) => Quaternion.clone(orientation, result), false),
   model: satelliteModel()});
  this.satelliteTimer = window.setInterval(() => { if (!document.hidden && this.root.dataset.site !== "open" && !viewer.isDestroyed()) viewer.scene.requestRender(); }, 33);
 }
 private fallback() {
  this.root.innerHTML = `<div class="vw-fallback"><span class="fallback-moon" aria-hidden="true"></span><p>The 3D Moon needs WebGL, which is unavailable in this browser. You can still configure a mission and use the 2D maps and tables in the analysis.</p></div>`;
 }
 private async attachTerrain() {
  try {
   const info = await loadTerrainInfo();
   if (this.disposed || !this.viewer) return;
   this.terrain = new MoonTerrainProvider(info);
   this.viewer.scene.terrainProvider = this.terrain as unknown as typeof this.viewer.scene.terrainProvider;
   this.viewer.scene.requestRender();
   this.root.dataset.terrain = "ready";
  } catch {
   this.root.dataset.terrain = "unavailable";
   this.toast("Elevation service unavailable. The Moon is shown without terrain relief.", true);
  }
 }

 // ---------- camera ----------
 private isLanding() {
  return document.body.matches(".planner[data-step=landing].panel-collapsed:not(.exploring)");
 }
 private aspect() { const scene = this.viewer!.scene; return scene.drawingBufferWidth / Math.max(1, scene.drawingBufferHeight); }
 private canvasCenter() { const c = this.viewer!.scene.canvas; return new Cartesian2(c.clientWidth / 2, c.clientHeight / 2); }
 private surfacePoint(position: Cartesian2) {
  const scene = this.viewer!.scene, ray = scene.camera.getPickRay(position);
  if (!ray) return undefined;
  return scene.globe.pick(ray, scene) ?? scene.camera.pickEllipsoid(position, MOON);
 }
 /** Current location, zoom (vertical extent in metres) and orientation, whichever projection is active. */
 private readView(): View {
  const camera = this.viewer!.scene.camera;
  const center = this.canvasCenter();
  center.y += this.cameraOffset * this.viewer!.canvas.clientHeight;
  const point = this.surfacePoint(center);
  if (!point) return this.view;
  const c = Cartographic.fromCartesian(point, MOON)!;
  let extent: number;
  const frustum = camera.frustum as unknown as {width?: number; aspectRatio: number; fovy?: number};
  if (this.mode === "map") extent = (frustum.width ?? FULL_EXTENT) / this.aspect();
  else extent = 2 * Cartesian3.distance(camera.positionWC, point) * Math.tan((frustum.fovy ?? Math.PI / 4) / 2);
  return {lat: deg(c.latitude), lon: wrap180(deg(c.longitude)), extent: Math.min(MAX_EXTENT, Math.max(MIN_EXTENT, extent)), heading: camera.heading, pitch: this.mode === "map" ? -Math.PI / 2 : Math.min(-0.15, camera.pitch)};
 }
 private apply(view: View) {
  const viewer = this.viewer; if (!viewer) return;
  const camera = viewer.scene.camera;
  view = {...view, extent: Math.min(MAX_EXTENT, Math.max(MIN_EXTENT, view.extent))};
  const target = Cartesian3.fromDegrees(view.lon, view.lat, 0, MOON);
  const wasLanding = this.cameraOffset !== 0;
  const landing = this.isLanding();
  this.cameraOffset = landing
   ? Number(getComputedStyle(document.body).getPropertyValue("--hero-moon-offset"))
   : 0;
  if (landing !== wasLanding) {
   for (const id of this.pins.keys()) {
    const pin = viewer.entities.getById(`pin:${id}`);
    if (pin) pin.show = !landing;
   }
   if (this.pinRing) this.pinRing.show = !landing;
  }
  if (this.mode === "map") {
   camera.switchToOrthographicFrustum();
   viewer.resize();
   const frustum = camera.frustum as unknown as {width: number; aspectRatio: number};
   frustum.aspectRatio = viewer.scene.drawingBufferWidth / viewer.scene.drawingBufferHeight;
   const width = view.extent * frustum.aspectRatio;
   // Cesium derives the orthographic width from camera distance on wheel zoom.
   camera.lookAt(target, new HeadingPitchRange(view.heading, -Math.PI / 2, this.cameraOffset ? width : ORTHO_DISTANCE));
   frustum.width = width;
  } else {
   camera.switchToPerspectiveFrustum();
   const frustum = camera.frustum as unknown as {fov: number; fovy: number};
   frustum.fov = Math.PI / 4;
   const distance = view.extent / 2 / Math.tan(frustum.fovy / 2);
   camera.lookAt(target, new HeadingPitchRange(view.heading, view.pitch, distance));
  }
  camera.lookAtTransform(Matrix4.IDENTITY);
  if (this.cameraOffset) camera.moveUp(view.extent * this.cameraOffset);
  this.view = view;
  viewer.scene.requestRender();
 }
 showLanding() {
  if (!this.viewer) return;
  cancelAnimationFrame(this.animation);
  this.animation = 0;
  this.mode = "map";
  this.applyMode();
  this.apply(LANDING_VIEW);
 }
 private clampZoom() {
  if (this.mode !== "map" || !this.viewer) return;
  const frustum = this.viewer.scene.camera.frustum as unknown as {width?: number; aspectRatio: number};
  if (!frustum.width) return;
  const extent = frustum.width / this.aspect();
  if (extent < MIN_EXTENT) frustum.width = MIN_EXTENT * this.aspect();
  else if (extent > MAX_EXTENT) frustum.width = MAX_EXTENT * this.aspect();
 }
 private defaultPitch(extent: number) { return -Math.PI / 2 + rad(42) * smooth(1.8 * R, 0.35 * R, extent); }
 /** Smoothly move to a view without discarding mission state; imagery and terrain continue to stream meanwhile. */
 flyTo(target: Partial<View>, ms = 1100, arcOut = false) {
  if (!this.viewer) return;
  cancelAnimationFrame(this.animation);
  const from = this.readView(), to: View = {...from, ...target};
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const a = Cartesian3.fromDegrees(from.lon, from.lat, 0, MOON), b = Cartesian3.fromDegrees(to.lon, to.lat, 0, MOON);
  const angle = Cartesian3.angleBetween(a, b);
  let dh = to.heading - from.heading; dh = Math.atan2(Math.sin(dh), Math.cos(dh));
  const started = performance.now(), scene = this.viewer.scene;
  const done = () => { this.animation = 0; scene.requestRenderMode = true; scene.requestRender(); this.view = this.readView(); this.refreshReadouts(); };
  if (reduce || ms <= 0) { this.apply(to); done(); return; }
  scene.requestRenderMode = false;
  const step = (now: number) => {
   if (this.disposed) return;
   const t = Math.min(1, (now - started) / ms), e = t * t * (3 - 2 * t);
   const sin = Math.sin(angle), position = angle < 1e-6 ? b : Cartesian3.add(Cartesian3.multiplyByScalar(a, Math.sin((1 - e) * angle) / sin, new Cartesian3()), Cartesian3.multiplyByScalar(b, Math.sin(e * angle) / sin, new Cartesian3()), new Cartesian3());
   const c = Cartographic.fromCartesian(position, MOON)!;
   // Only jumps to a named place zoom out in the middle and back in; panning, tilting and the dial never do.
   const arc = arcOut ? 1 + Math.min(2.2, angle * 1.6) * Math.sin(Math.PI * e) : 1;
   this.apply({lat: deg(c.latitude), lon: wrap180(deg(c.longitude)), extent: Math.exp(Math.log(from.extent) * (1 - e) + Math.log(to.extent) * e) * arc,
    heading: from.heading + dh * e, pitch: from.pitch + (to.pitch - from.pitch) * e});
   if (t < 1) this.animation = requestAnimationFrame(step); else { this.apply(to); done(); }
  };
  this.animation = requestAnimationFrame(step);
 }
 flyToPlace(lat: number, lon: number, extent = 24_000) {
  const keep3d = this.mode === "3d";
  this.flyTo({lat, lon, extent, heading: 0, pitch: keep3d ? this.defaultPitch(extent) : -Math.PI / 2}, 1100, true);
 }
 /** Move to a mission region after a successful calculation. */
 flyToSite(lat: number, lon: number, extent = 16_000) { this.flyToPlace(lat, lon, extent); }
 setMode(mode: Mode) {
  if (!this.viewer || mode === this.mode) return;
  const view = this.readView();
  this.mode = mode;
  this.applyMode();
  // Preserve the location and zoom; 3D adds perspective and tilt.
  this.apply({...view, pitch: mode === "3d" ? this.defaultPitch(view.extent) : -Math.PI / 2});
  this.refreshReadouts();
 }
 get currentMode() { return this.mode; }
 private applyMode() {
  const viewer = this.viewer!, scene = viewer.scene, three = this.mode === "3d";
  this.root.dataset.mode = this.mode;
  scene.globe.enableLighting = three;
  scene.shadowMap.enabled = three; scene.globe.shadows = three ? ShadowMode.ENABLED : ShadowMode.DISABLED;
  const control = scene.screenSpaceCameraController;
  control.enableTilt = three; control.enableLook = three;
  this.imagery?.setMode(this.mode);
  this.root.querySelectorAll<HTMLButtonElement>("[data-mode]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.mode === this.mode)));
  this.root.querySelectorAll<HTMLElement>(".vw-3d").forEach(node => { node.hidden = !three; });
  scene.requestRender();
 }
 /** Directional light offset above and beside the camera, so dragging sweeps light across slopes. */
 private updateLight() {
  const scene = this.viewer!.scene, camera = scene.camera;
  const e = rad(this.lightElevation), a = rad(this.lightYaw);
  const toLight = new Cartesian3();
  Cartesian3.multiplyByScalar(camera.directionWC, -Math.cos(e) * Math.cos(a), toLight);
  Cartesian3.add(toLight, Cartesian3.multiplyByScalar(camera.upWC, Math.sin(e), new Cartesian3()), toLight);
  Cartesian3.add(toLight, Cartesian3.multiplyByScalar(camera.rightWC, Math.cos(e) * Math.sin(a), new Cartesian3()), toLight);
  Cartesian3.negate(toLight, toLight);
  Cartesian3.normalize(toLight, (scene.light as DirectionalLight).direction);
  const height = Math.max(this.view.extent, 1000);
  scene.shadowMap.maximumDistance = Math.min(8e5, Math.max(8000, height * 3));
 }

 // ---------- UI ----------
 private bindControls() {
  const q = <T extends HTMLElement>(s: string) => this.root.querySelector<T>(s)!;
  this.root.querySelectorAll<HTMLButtonElement>("[data-mode]").forEach(b => b.onclick = () => this.setMode(b.dataset.mode as Mode));
  this.root.querySelectorAll<HTMLElement>("[data-nav]").forEach(b => {
   b.onclick = () => this.navigate(b.dataset.nav!);
   b.onkeydown = event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); this.navigate(b.dataset.nav!); } };
  });
  q<HTMLButtonElement>("[data-site3d]").onclick = () => this.openSite();
  q<HTMLButtonElement>("[data-detail]").onclick = () => this.openDetail();
  q<HTMLButtonElement>("[data-pass]").onclick = () => this.setPassPlaying(!this.pass.playing);
  const layers = q<HTMLButtonElement>("[data-layers]"), menu = q("#vw-layer-menu");
  layers.onclick = () => { menu.hidden = !menu.hidden; layers.setAttribute("aria-expanded", String(!menu.hidden)); };
  this.root.querySelectorAll<HTMLButtonElement>("[data-draw]").forEach(b => b.onclick = () => {
   const kind = b.dataset.draw as "rect" | "free" | "clear";
   if (kind === "clear") { this.showArea(undefined); this.setLayout(undefined, ""); this.hooks.onArea?.([]); return; }
   this.setDrawMode(this.drawKind === kind ? undefined : kind);
  });
  q<HTMLButtonElement>("[data-pole]").onclick = () => this.flyToPlace(-89.6, 0, 160_000);
  q<HTMLInputElement>("[data-labels]").onchange = e => { this.labelsOn = (e.target as HTMLInputElement).checked; this.positionLabels(); };
  q<HTMLInputElement>("[data-mission]").onchange = e => this.setMissionVisible((e.target as HTMLInputElement).checked);
  q<HTMLInputElement>("[data-plan]").onchange = e => this.setPlanHighlight((e.target as HTMLInputElement).checked);
  this.renderPinList();
  q<HTMLInputElement>("[data-candidates]").onchange = e => { this.showAll = (e.target as HTMLInputElement).checked; this.refreshMission(); };
  q<HTMLSelectElement>("[data-imagery]").onchange = e => this.imagery?.setChoice((e.target as HTMLSelectElement).value as "auto" | "bundled");
  const canvas = this.viewer!.scene.canvas;
  canvas.tabIndex = 0;
  canvas.setAttribute("aria-label", "Interactive Moon. Drag to rotate, scroll or pinch to zoom, click to drop a pin, then select it to open the 3D site. Arrow keys rotate; plus and minus zoom; 0 resets.");
  canvas.addEventListener("keydown", e => {
   const map: Record<string, string> = {ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down", "+": "in", "=": "in", "-": "out", "0": "north", Home: "north"};
   if (map[e.key]) { e.preventDefault(); this.navigate(map[e.key]); }
  });
  canvas.addEventListener("pointerdown", () => { this.activeLandmark = ""; });
  this.bindSearch();
 }
 private navigate(action: string) {
  if (!this.viewer) return;
  const v = this.readView(), pan = v.extent / 5 / R * 180 / Math.PI;
  const cos = Math.max(0.05, Math.cos(rad(v.lat)));
  switch (action) {
   case "in": return this.flyTo({extent: v.extent * 0.55}, 350);
   case "out": return this.flyTo({extent: v.extent / 0.55}, 350);
   case "left": return this.flyTo({lon: wrap180(v.lon - pan / cos)}, 250);
   case "right": return this.flyTo({lon: wrap180(v.lon + pan / cos)}, 250);
   case "up": return this.flyTo({lat: Math.min(89.9, v.lat + pan)}, 250);
   case "down": return this.flyTo({lat: Math.max(-89.9, v.lat - pan)}, 250);
   case "tilt-up": return this.flyTo({pitch: Math.max(-Math.PI / 2, v.pitch - rad(10))}, 250);
   case "tilt-down": return this.flyTo({pitch: Math.min(-0.15, v.pitch + rad(10))}, 250);
   default: { const full = FULL_EXTENT / Math.min(1, this.aspect()); return this.flyTo({lat: 8, lon: 12, extent: full, heading: 0, pitch: this.mode === "3d" ? this.defaultPitch(full) : -Math.PI / 2}, 700); }
  }
 }
 private onImageryState(state: ImageryState) {
  this.root.dataset.imagery = state;
  if (state === "fallback") this.toast("Live lunar imagery is unavailable. Showing the bundled lower-detail color map. Your view and mission are unchanged.", true, true);
  else if (state === "live") this.toast("");
  this.refreshReadouts();
 }
 private toast(message: string, error = false, retry = false) {
  const node = this.root.querySelector<HTMLElement>(".vw-toast")!;
  clearTimeout(this.statusTimer);
  node.classList.toggle("error", error);
  node.innerHTML = message ? `<span>${escapeHtml(message)}</span>${retry ? '<button data-retry>Retry live imagery</button>' : ""}` : "";
  node.hidden = !message;
  node.querySelector<HTMLButtonElement>("[data-retry]")?.addEventListener("click", () => { this.imagery?.retry(); this.toast(""); });
  if (message && !retry) this.statusTimer = window.setTimeout(() => { node.hidden = true; }, 9000);
 }

 // ---------- readouts ----------
 private refreshReadouts() {
  if (!this.viewer) return;
  const v = this.view, scene = this.viewer.scene;
  const point = this.surfacePoint(this.canvasCenter());
  let perPixel = v.extent / scene.canvas.clientHeight;
  if (point) {
   const size = scene.camera.getPixelSize(new BoundingSphere(point, 1), scene.drawingBufferWidth, scene.drawingBufferHeight);
   if (Number.isFinite(size) && size > 0) perPixel = size * scene.drawingBufferWidth / scene.canvas.clientWidth;
  }
  const target = perPixel * 110, magnitude = 10 ** Math.floor(Math.log10(target));
  const nice = [1, 2, 5, 10].map(k => k * magnitude).find(m => m >= target * 0.6) ?? magnitude;
  this.root.querySelector<HTMLElement>("[data-scalebar]")!.style.width = `${Math.max(24, nice / perPixel)}px`;
  this.root.querySelector<HTMLElement>("[data-scaletext]")!.textContent = metres(nice);
  const terrain = this.terrain?.resolutionAt(v.lat, v.lon);
  const imagery = this.imagery?.describe(this.mode) ?? "";
  const display = perPixel;
  const terrainText = terrain ? `${terrain.resolutionM >= 1000 ? `${(terrain.resolutionM / 1000).toFixed(1)} km` : `${Math.round(terrain.resolutionM)} m`}/px LOLA` : this.root.dataset.terrain === "unavailable" ? "unavailable" : "loading";
  this.root.querySelector<HTMLElement>("[data-res]")!.innerHTML = `<span>Imagery: ${escapeHtml(imagery)}</span><span>Terrain: ${terrainText}</span><span>Screen: ${display >= 1000 ? (display / 1000).toFixed(1) + " km" : display.toFixed(display < 10 ? 1 : 0) + " m"}/px</span>`;
  this.root.dataset.extentM = String(Math.round(v.extent));
  this.root.dataset.centerLat = v.lat.toFixed(3); this.root.dataset.centerLon = v.lon.toFixed(3);
  this.root.dataset.screenMpp = perPixel.toFixed(1);
  this.root.dataset.terrainMpp = terrain ? String(Math.round(terrain.resolutionM)) : "";
  this.root.dataset.tileFailures = String(this.terrain?.failures ?? 0);
 }

 // ---------- picking, inspector ----------
 private setupPicking() {
  const viewer = this.viewer!, scene = viewer.scene;
  this.handler = new ScreenSpaceEventHandler(scene.canvas);
  this.handler.setInputAction((event: {position: Cartesian2}) => this.drawDown(event.position), ScreenSpaceEventType.LEFT_DOWN);
  this.handler.setInputAction((event: {position: Cartesian2}) => this.drawUp(event.position), ScreenSpaceEventType.LEFT_UP);
  this.handler.setInputAction((event: {position: Cartesian2}) => {
   if (this.drawKind || performance.now() < this.suppressClickUntil) return;
   if (this.isLanding()) {
    if (!this.surfacePoint(event.position)) return;
    const view = this.readView();
    this.hooks.onOpenExplorer?.();
    this.apply({...view, extent: FULL_EXTENT / Math.min(1, this.aspect())});
    return;
   }
   const picked = scene.pick(event.position);
   const id = picked?.id && typeof picked.id === "object" && "id" in picked.id ? String((picked.id as Entity).id) : "";
   if (id.startsWith("pin:")) { this.selectPin(Number(id.slice(4))); return; }
   if (id.startsWith("tower:")) { const c = this.result?.candidates.find(c => c.id === id.slice(6)); if (c) { this.hooks.onSelectCandidate?.(c); return; } }
   const point = this.surfacePoint(event.position);
   if (point) this.dropPin(point);
  }, ScreenSpaceEventType.LEFT_CLICK);
  const drop = (position: Cartesian2) => { if (this.drawKind) return; const point = this.surfacePoint(position); if (point) this.dropPin(point); };
  this.handler.setInputAction((event: {position: Cartesian2}) => drop(event.position), ScreenSpaceEventType.RIGHT_CLICK);
  scene.canvas.addEventListener("contextmenu", e => e.preventDefault());
  let press = 0, start = [0, 0];   // touch: long-press drops a pin
  scene.canvas.addEventListener("pointerdown", e => { if (e.pointerType !== "touch") return; start = [e.clientX, e.clientY]; const r = scene.canvas.getBoundingClientRect(); press = window.setTimeout(() => drop(new Cartesian2(start[0] - r.left, start[1] - r.top)), 650); });
  for (const type of ["pointerup", "pointercancel"]) scene.canvas.addEventListener(type, () => clearTimeout(press));
  scene.canvas.addEventListener("pointermove", e => { if (e.pointerType === "touch" && Math.hypot(e.clientX - start[0], e.clientY - start[1]) > 8) clearTimeout(press); });
  this.handler.setInputAction((event: {endPosition: Cartesian2}) => {
   if (this.drawing) this.drawMove(event.endPosition);
   this.hover.x = event.endPosition.x; this.hover.y = event.endPosition.y;
   if (this.hover.frame) return;
   this.hover.frame = requestAnimationFrame(() => {
    this.hover.frame = 0;
    const point = this.viewer && this.surfacePoint(new Cartesian2(this.hover.x, this.hover.y));
    const out = this.root.querySelector<HTMLElement>("[data-coords]");
    if (!out) return;
    if (!point) { out.textContent = "Off the Moon"; return; }
    const c = Cartographic.fromCartesian(point, MOON)!;
    out.textContent = `${coordinate(deg(c.latitude), deg(c.longitude))} · ${(c.height / 1000).toFixed(2)} km`;
   });
  }, ScreenSpaceEventType.MOUSE_MOVE);
 }
 private inspect(point: Cartesian3) {
  const c = Cartographic.fromCartesian(point, MOON)!, lat = deg(c.latitude), lon = wrap180(deg(c.longitude));
  const near = [...LANDMARKS].map(f => ({f, d: angular(f, lat, lon)})).sort((a, b) => a.d - b.d).slice(0, 3);
  const terrain = this.terrain?.resolutionAt(lat, lon);
  const node = this.root.querySelector<HTMLElement>(".vw-inspector")!;
  const region = near.find(n => RESEARCH.has(n.f.id) && n.d < 60_000)?.f;
  node.innerHTML = `<button class="vw-close" aria-label="Close inspector">×</button><span class="eyebrow">LOCATION</span><h3>${coordinate(lat, lon)}</h3>
   <dl><dt>Terrain elevation</dt><dd>${Math.round(c.height).toLocaleString()} m <small>above the 1,737.4 km reference sphere</small></dd>
   <dt>Elevation source</dt><dd>${terrain ? `LOLA · about ${Math.round(terrain.resolutionM).toLocaleString()} m/px` : "Loading terrain tiles…"}</dd>
   <dt>Nearby features</dt><dd><ul>${near.map(n => `<li><button data-place="${n.f.id}">${escapeHtml(n.f.name)}</button> <small>${n.d < 1000 ? "<1" : Math.round(n.d / 1000).toLocaleString()} km</small></li>`).join("")}</ul></dd></dl>
   ${region ? `<button class="primary" data-choose="${MISSION_REGIONS[region.id]}">Draw an area near ${escapeHtml(region.name)} →</button>` : ""}`;
  node.hidden = false;
  node.querySelector<HTMLButtonElement>(".vw-close")!.onclick = () => { node.hidden = true; };
  node.querySelectorAll<HTMLButtonElement>("[data-place]").forEach(b => b.onclick = () => { const f = LANDMARKS.find(f => f.id === b.dataset.place); if (f) this.focusLandmark(f); });
  node.querySelector<HTMLButtonElement>("[data-choose]")?.addEventListener("click", e => this.hooks.onChooseRegion?.((e.currentTarget as HTMLElement).dataset.choose!));
 }

 /** Drop a new pin anywhere on the Moon. */
 private dropPin(point: Cartesian3) {
  if (this.isLanding()) return;
  if (this.pins.size >= MAX_PINS) { this.toast(`You can keep up to ${MAX_PINS} pins. Remove one to add another.`, true); return; }
  const c = Cartographic.fromCartesian(point, MOON)!;
  const id = ++this.pinSeq;
  this.pins.set(id, {id, lat: deg(c.latitude), lon: wrap180(deg(c.longitude)), h: c.height, name: `Pin ${id}`});
  this.viewer!.entities.add({id: `pin:${id}`, position: Cartesian3.fromDegrees(wrap180(deg(c.longitude)), deg(c.latitude), c.height + 5, MOON),
   billboard: {image: pinImage(), width: 34, height: 48, verticalOrigin: VerticalOrigin.BOTTOM, disableDepthTestDistance: new CallbackProperty(() => this.mode === "map" ? Number.POSITIVE_INFINITY : 3e5, false), scale: new CallbackProperty(() => id === this.selectedPin ? 1.3 : 1, false)},
   label: {text: new CallbackProperty(() => this.pins.get(id)?.name ?? "", false), font: "600 12px IBM Plex Mono, monospace", fillColor: Color.WHITE, outlineColor: Color.BLACK, outlineWidth: 3, style: LabelStyle.FILL_AND_OUTLINE, verticalOrigin: VerticalOrigin.BOTTOM, pixelOffset: new Cartesian2(0, -56), disableDepthTestDistance: new CallbackProperty(() => this.mode === "map" ? Number.POSITIVE_INFINITY : 3e5, false)}});
  this.selectPin(id);
 }
 /** Highlight a pin: enlarge it, draw a ring around it, and open its card. */
 selectPin(id: number, fly = false) {
  const viewer = this.viewer, pin = this.pins.get(id); if (!viewer || !pin) return;
  this.selectedPin = id;
  if (this.pinRing) viewer.entities.remove(this.pinRing);
  const ringColor = Color.fromCssColorString("#ffd54a");
  this.pinRing = viewer.entities.add({id: "pinhl", polyline: {
   positions: new CallbackProperty(() => ring(pin.lat, pin.lon, Math.max(60, this.view.extent * 0.045), pin.h + 12), false),
   width: 4, material: ringColor, depthFailMaterial: ringColor}});
  if (fly) this.flyToPlace(pin.lat, pin.lon, Math.min(this.view.extent, 24_000));
  this.showPinCard(pin); this.renderPinList();
  viewer.scene.requestRender();
 }
 private showPinCard(pin: Pin) {
  const region = this.terrain?.regionAt(pin.lat, pin.lon);
  const node = this.root.querySelector<HTMLElement>(".vw-inspector")!;
  node.innerHTML = `<button class="vw-close" aria-label="Close pin card">×</button><span class="eyebrow">PIN · HIGHLIGHTED</span>
   <label class="vw-rename">Name<input data-pin-name type="text" maxlength="40" value="${escapeHtml(pin.name)}" aria-label="Pin name"></label>
   <h3>${coordinate(pin.lat, pin.lon)}</h3>
   <dl><dt>Terrain elevation</dt><dd>${Math.round(pin.h).toLocaleString()} m</dd></dl>
   <button class="primary" data-open-site>Explore this site in 3D →</button>
   <p class="vw-note">${region ? `Inside the ${escapeHtml(region.name)} 20 m terrain.` : "Global LOLA terrain (about 470 m per pixel) is used here."}</p>
   <button data-use-pin>Analyse a 6 km square here →</button>
   <button data-clear-pin>Remove pin</button>`;
  node.hidden = false;
  node.querySelector<HTMLButtonElement>(".vw-close")!.onclick = () => { node.hidden = true; this.deselectPin(); };
  node.querySelector<HTMLInputElement>("[data-pin-name]")!.oninput = e => { pin.name = (e.target as HTMLInputElement).value.trim() || `Pin ${pin.id}`; this.renderPinList(); this.viewer?.scene.requestRender(); };
  node.querySelector<HTMLButtonElement>("[data-clear-pin]")!.onclick = () => { this.removePin(pin.id); node.hidden = true; };
  node.querySelector<HTMLButtonElement>("[data-open-site]")!.onclick = () => this.openSite({lat: pin.lat, lon: pin.lon, name: pin.name});
  node.querySelector<HTMLButtonElement>("[data-use-pin]")?.addEventListener("click", () => this.hooks.onUsePin?.(pin.lat, pin.lon, region?.id ?? ""));
 }
 private deselectPin() {
  this.selectedPin = 0;
  if (this.pinRing) { this.viewer?.entities.remove(this.pinRing); this.pinRing = undefined; }
  this.renderPinList(); this.viewer?.scene.requestRender();
 }
 removePin(id: number) {
  this.viewer?.entities.removeById(`pin:${id}`); this.pins.delete(id);
  if (this.selectedPin === id) this.deselectPin(); else this.renderPinList();
  this.viewer?.scene.requestRender();
 }
 clearPin() { for (const id of [...this.pins.keys()]) this.removePin(id); }
 private renderPinList() {
  const list = this.root.querySelector<HTMLElement>("[data-pinlist]"); if (!list) return;
  list.innerHTML = this.pins.size ? [...this.pins.values()].map(p => `<li class="${p.id === this.selectedPin ? "on" : ""}"><button data-pin="${p.id}" aria-pressed="${p.id === this.selectedPin}">${escapeHtml(p.name)}</button><button data-pin-x="${p.id}" aria-label="Remove ${escapeHtml(p.name)}">×</button></li>`).join("") : '<li class="vw-note">No pins yet.</li>';
  const clear = this.root.querySelector<HTMLButtonElement>("[data-clear-pins]");
  if (clear) {   // only offered while at least one pin exists
   clear.hidden = this.pins.size === 0;
   clear.textContent = this.pins.size > 1 ? `Clear pins (${this.pins.size})` : "Clear pins";
   clear.onclick = () => { this.clearPin(); const n = this.root.querySelector<HTMLElement>(".vw-inspector"); if (n) n.hidden = true; this.toast("All pins removed."); };
  }
  list.querySelectorAll<HTMLButtonElement>("[data-pin]").forEach(b => b.onclick = () => this.selectPin(Number(b.dataset.pin), true));
  list.querySelectorAll<HTMLButtonElement>("[data-pin-x]").forEach(b => b.onclick = () => { this.removePin(Number(b.dataset.pinX)); const n = this.root.querySelector<HTMLElement>(".vw-inspector"); if (n && !this.selectedPin) n.hidden = true; });
 }
 /** Outline the area the mission plan covers: the lander and every recommended relay. */
 setPlanHighlight(on: boolean) {
  this.planHighlight = on;
  const box = this.root.querySelector<HTMLInputElement>("[data-plan]"); if (box) box.checked = on;
  this.refreshMission();
 }
 private updatePlanHighlight() {
  const viewer = this.viewer, r = this.result; if (!viewer) return;
  if (this.planRing) { viewer.entities.remove(this.planRing); this.planRing = undefined; }
  if (!r || !this.missionVisible || !this.planHighlight) return;
  const members = r.candidates.filter(c => r.recommended.includes(c.id));
  const reach = members.reduce((m, c) => Math.max(m, angular({id: "", name: "", kind: "", lat: c.lat, lon: c.lon} as Landmark, r.site.lat, r.site.lon)), 0);
  const radius = Math.max(1500, reach * 1.3), height = r.site.elevation + 20;
  const color = Color.fromCssColorString("#84deff");
  this.planRing = viewer.entities.add({id: "planhl", position: Cartesian3.fromDegrees(r.site.lon, r.site.lat, height, MOON),
   polyline: {positions: ring(r.site.lat, r.site.lon, radius, height), width: 3, material: new PolylineDashMaterialProperty({color, dashLength: 18}), depthFailMaterial: new PolylineDashMaterialProperty({color, dashLength: 18})},
   label: {text: `MISSION PLAN AREA · ${metres(radius)} radius`, font: "600 12px IBM Plex Mono, monospace", fillColor: color, outlineColor: Color.BLACK, outlineWidth: 3, style: LabelStyle.FILL_AND_OUTLINE, verticalOrigin: VerticalOrigin.TOP, pixelOffset: new Cartesian2(0, 12), disableDepthTestDistance: 2e5}});
 }

 // ---------- search, labels ----------
 private bindSearch() {
  const input = this.root.querySelector<HTMLInputElement>("#vw-search")!, list = this.root.querySelector<HTMLUListElement>("#vw-results")!;
  let items: Landmark[] = [], cursor = -1;
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); cursor = -1; };
  const pick = (feature: Landmark) => { input.value = feature.name; close(); this.focusLandmark(feature); };
  const render = () => {
   const query = input.value.trim().toLowerCase();
   items = query ? LANDMARKS.filter(f => (f.name + " " + f.kind).toLowerCase().includes(query)).slice(0, 7) : [];
   list.innerHTML = items.map((f, i) => `<li role="option" id="vw-opt-${i}" data-i="${i}" aria-selected="${i === cursor}"><strong>${escapeHtml(f.name)}</strong><small>${RESEARCH.has(f.id) ? "Mission region" : escapeHtml(f.kind)}</small></li>`).join("") || (query ? '<li class="empty">No matching landmark or region</li>' : "");
   list.hidden = !query; input.setAttribute("aria-expanded", String(!!query));
  };
  input.oninput = () => { cursor = -1; render(); };
  input.onkeydown = e => {
   if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); cursor = (cursor + (e.key === "ArrowDown" ? 1 : -1) + items.length) % Math.max(1, items.length); render(); input.setAttribute("aria-activedescendant", `vw-opt-${cursor}`); }
   else if (e.key === "Enter") { const f = items[Math.max(0, cursor)]; if (f) { e.preventDefault(); pick(f); } }
   else if (e.key === "Escape") close();
  };
  list.onmousedown = e => { const li = (e.target as HTMLElement).closest<HTMLElement>("li[data-i]"); if (li) { e.preventDefault(); pick(items[Number(li.dataset.i)]); } };
  input.onblur = () => setTimeout(close, 120);
 }
 private setupLabels() {
  const layer = this.root.querySelector(".vw-labels")!;
  this.labelButtons = LANDMARKS.map(feature => {
   const button = document.createElement("button"); button.className = "vw-label"; button.textContent = feature.name; button.hidden = true; button.tabIndex = -1;
   button.onclick = () => this.focusLandmark(feature); layer.append(button);
   return {feature, button};
  });
 }
 focusLandmark(feature: Landmark) {
  this.activeLandmark = feature.id;
  if (this.selectedPin) this.deselectPin();
  const extent = RESEARCH.has(feature.id) ? 24_000 : feature.kind.includes("pole") || feature.kind.includes("Hemisphere") ? 1_800_000 : feature.kind.includes("sea") ? 1_200_000 : 300_000;
  this.flyToPlace(feature.lat, feature.lon, extent);
  const point = Cartesian3.fromDegrees(feature.lon, feature.lat, 0, MOON);
  setTimeout(() => { if (!this.disposed) this.inspect(this.viewer ? (this.surfacePoint(this.canvasCenter()) ?? point) : point); }, 1250);
 }
 private positionLabels() {
  if (!this.viewer) return;
  const scene = this.viewer.scene, stage = this.root, w = stage.clientWidth, h = stage.clientHeight;
  const camera = scene.camera, boxes: {x: number; y: number; w: number; h: number}[] = [];
  const extent = this.view.extent, detailed = extent < 1.5 * R;
  const ordered = [...this.labelButtons].sort((a, b) => Number(b.feature.id === this.activeLandmark) - Number(a.feature.id === this.activeLandmark));
  for (const {feature, button} of ordered) {
   button.hidden = true;
   if (!this.labelsOn) continue;
   if (feature.minor && !detailed && feature.id !== this.activeLandmark) continue;
   const point = Cartesian3.fromDegrees(feature.lon, feature.lat, 0, MOON), normal = Cartesian3.normalize(point, new Cartesian3());
   const toCamera = Cartesian3.normalize(Cartesian3.subtract(camera.positionWC, point, new Cartesian3()), new Cartesian3());
   if (Cartesian3.dot(normal, toCamera) < 0.08) continue;                      // behind the Moon
   const screen = SceneTransforms.worldToWindowCoordinates(scene, point);
   if (!screen) continue;
   const width = Math.min(190, feature.name.length * 7 + 26), height = 26;
   if (screen.x < width / 2 + 8 || screen.x > w - width / 2 - 8 || screen.y < 60 || screen.y > h - 70) continue;
   if (boxes.some(b => Math.abs(screen.x - b.x) < (width + b.w) / 2 + 6 && Math.abs(screen.y - b.y) < (height + b.h) / 2 + 4)) continue;
   boxes.push({x: screen.x, y: screen.y, w: width, h: height});
   button.hidden = false; button.style.left = `${screen.x}px`; button.style.top = `${screen.y}px`;
   button.classList.toggle("active", feature.id === this.activeLandmark);
   button.classList.toggle("region", RESEARCH.has(feature.id));
  }
 }

 // ---------- mission overlay ----------
 /** Exploration-only until a mission run succeeds. */
 setMission(result: Result | undefined) {
  this.result = result; this.index = 0; this.disabled = new Set(); this.selected = result?.recommended[0] ?? "";
  this.site?.setMission(result, 0, this.disabled);
  this.root.querySelectorAll<HTMLElement>(".vw-mission-only").forEach(node => { node.hidden = !result; });
  this.refreshMission();
  this.updateDetailButton();
 }
 setMissionVisible(visible: boolean) {
  this.missionVisible = visible;
  const box = this.root.querySelector<HTMLInputElement>("[data-mission]"); if (box) box.checked = visible;
  this.refreshMission();
 }
 update(index: number, disabled: Set<string>, selected: string) {
  if (this.index === index && this.selected === selected && [...this.disabled].join() === [...disabled].join()) return;
  this.index = index; this.disabled = new Set(disabled); this.selected = selected; this.refreshMission();
  this.site?.update(index, this.disabled);
 }
 private refreshMission() {
  const viewer = this.viewer; if (!viewer) return;
  const r = this.result;
  if (!r || !this.missionVisible) { for (const e of [...viewer.entities.values]) if (!/^(pin|area|layout|satellite:|reference:)/.test(String(e.id))) viewer.entities.remove(e); this.towers.clear(); this.lander = undefined; this.planRing = undefined; viewer.scene.requestRender(); return; }
  if (!this.lander) {
   this.lander = viewer.entities.add({id: "lander", position: Cartesian3.fromDegrees(r.site.lon, r.site.lat, r.site.elevation + 4, MOON),
    point: {pixelSize: 12, color: Color.fromCssColorString("#84deff"), outlineColor: Color.BLACK, outlineWidth: 2, heightReference: HeightReference.NONE, disableDepthTestDistance: 2e5},
    label: {text: "LANDER", font: "600 12px IBM Plex Mono, monospace", fillColor: Color.fromCssColorString("#84deff"), outlineColor: Color.BLACK, outlineWidth: 3, style: LabelStyle.FILL_AND_OUTLINE, verticalOrigin: VerticalOrigin.BOTTOM, pixelOffset: new Cartesian2(0, -14), disableDepthTestDistance: 2e5}});
  }
  const wanted = new Set(r.candidates.filter(c => r.recommended.includes(c.id) || this.showAll || c.id === this.selected).map(c => c.id));
  for (const [id, parts] of this.towers) if (!wanted.has(id)) { viewer.entities.remove(parts.entity); if (parts.link) viewer.entities.remove(parts.link); this.towers.delete(id); }
  for (const c of r.candidates) {
   if (!wanted.has(c.id)) continue;
   const recommended = r.recommended.includes(c.id), off = this.disabled.has(c.id), available = !!c.temporal_coverage[this.index] && !off;
   const color = Color.fromCssColorString(off ? "#f2848c" : recommended ? (available ? "#73d8ae" : "#f4ba72") : "#8a9aaf");
   let parts = this.towers.get(c.id);
   const top = Cartesian3.fromDegrees(c.lon, c.lat, c.elevation + 30, MOON);
   if (!parts) {
    const entity = viewer.entities.add({id: `tower:${c.id}`, position: top,
     point: {pixelSize: 11, color, outlineColor: Color.WHITE, outlineWidth: c.id === this.selected ? 3 : 1, disableDepthTestDistance: 2e5},
     label: {text: c.id, font: "600 12px IBM Plex Mono, monospace", fillColor: color, outlineColor: Color.BLACK, outlineWidth: 3, style: LabelStyle.FILL_AND_OUTLINE, verticalOrigin: VerticalOrigin.BOTTOM, pixelOffset: new Cartesian2(0, -14), disableDepthTestDistance: 2e5}});
    const link = recommended ? viewer.entities.add({id: `link:${c.id}`, polyline: {positions: [this.lander.position!.getValue(viewer.clock.currentTime)!, top], width: 3, material: color, depthFailMaterial: color}}) : undefined;
    parts = {entity, link}; this.towers.set(c.id, parts);
   }
   parts.entity.point!.color = new ConstantProperty(color);
   parts.entity.point!.outlineWidth = new ConstantProperty(c.id === this.selected ? 3 : 1);
   parts.entity.label!.text = new ConstantProperty(c.id + (off ? " · OFF" : ""));
   parts.entity.label!.fillColor = new ConstantProperty(color);
   if (parts.link) {
    const material = () => available || off ? new ColorMaterialProperty(color) : new PolylineDashMaterialProperty({color, dashLength: 14}); // red (disabled) links stay solid; amber (unavailable now) links are dashed
    parts.link.polyline!.material = material();
    parts.link.polyline!.depthFailMaterial = material();
   }
  }
  this.updatePlanHighlight();
  viewer.scene.requestRender();
 }

 // ---------- three.js site view ----------
 /** Open a local Three.js close-up centered on a pin or the current Moon view. */
 openSite(target?: {lat: number; lon: number; name: string}) {
  if (this.site) return;
  if (!document.body.classList.contains("panel-collapsed")) document.querySelector<HTMLButtonElement>("#panel-close")?.click();
  const r = this.result;
  const pin = this.pins.get(this.selectedPin);
  const region = this.terrain?.regionAt(this.view.lat, this.view.lon);
  const landmark = LANDMARKS.find(feature => feature.id === this.activeLandmark && angular(feature, this.view.lat, this.view.lon) < 1000);
  // With no pin selected, a calculated plan is the whole setup the user wants to see: open on it, wherever the map is looking.
  const plan = this.layoutResult ? {lat: this.layoutResult.site.lat, lon: this.layoutResult.site.lon, name: `${this.layoutResult.site.name} · network`}
   : r ? {lat: r.site.lat, lon: r.site.lon, name: `${r.site.name} · mission`} : undefined;
  const place = target ?? (pin ? {lat: pin.lat, lon: pin.lon, name: pin.name} : plan ?? {lat: this.view.lat, lon: this.view.lon, name: region?.name ?? landmark?.name ?? "Moon site"});
  // Show the same layers as the map: the planned area network and the mission, whenever they are near this place.
  const L = this.layoutResult;
  const far = (list: {lat: number; lon: number}[]) => list.reduce((m, p) => Math.max(m, angular(p, place.lat, place.lon)), 0);
  const areaReach = L ? far([...L.nodes, ...L.polygon.map(([lat, lon]) => ({lat, lon}))]) : Infinity;
  const missionReach = r ? far([r.site, ...r.candidates.filter(c => r.recommended.includes(c.id))]) : Infinity;
  const near = (reach: number) => reach <= 30_000;
  const reach = Math.max(near(areaReach) ? areaReach : 0, near(missionReach) ? missionReach : 0);
  const detail = detailForReach(this.terrain?.detailAt(place.lat, place.lon) ?? "global", reach);
  this.root.dataset.site = "open";
  this.site = new SiteView(this.root, {...place, detail, result: near(missionReach) ? r : undefined, index: this.index, disabled: this.disabled, focus: !target && !pin && plan && L ? "area" : "site", heading: this.mapHeading(),
   area: L && near(areaReach) ? {result: L, format: this.layoutFormat || L.recommended, seconds: this.live ? this.passSeconds() : this.satelliteSeconds} : undefined, onClose: () => this.closeSite()});
 }
 /** Close-up of the calculated area: the terrain cut-out with the planned nodes, the chosen layout's links,
  * the lander and recommended relays, and the assumed relay satellite in its true direction. */
 openDetail() {
  if (this.site) return;
  const L = this.layoutResult, r = this.result;
  if (!L && !r) return;
  const centre = L ? {lat: L.site.lat, lon: L.site.lon} : {lat: r!.site.lat, lon: r!.site.lon};
  const points: {lat: number; lon: number}[] = [...(L?.nodes ?? []), ...(L?.polygon.map(([lat, lon]) => ({lat, lon})) ?? [])];
  const missionPoints = r ? [r.site, ...r.candidates.filter(c => r.recommended.includes(c.id))] : [];
  const reach = (list: {lat: number; lon: number}[]) => list.reduce((m, p) => Math.max(m, angular(p, centre.lat, centre.lon)), 500);
  const areaReach = reach(points), missionReach = reach(missionPoints);
  // The mission lander and relays join the close-up when they sit near the area; a far-off lander would only shrink the view.
  const withMission = !!r && (!L || missionReach <= Math.max(areaReach * 3, 8000));
  const detail = detailForReach(this.terrain?.detailAt(centre.lat, centre.lon) ?? "global", Math.max(areaReach, withMission ? missionReach : 0));
  if (!document.body.classList.contains("panel-collapsed")) document.querySelector<HTMLButtonElement>("#panel-close")?.click();
  this.root.dataset.site = "open";
  const name = L ? `${L.site.name} · mission area` : `${r!.site.name} · mission`;
  this.site = new SiteView(this.root, {...centre, name, detail, result: withMission ? r : undefined, index: this.index, disabled: this.disabled, focus: "area", heading: this.mapHeading(),
   area: L ? {result: L, format: this.layoutFormat || L.recommended, seconds: this.live ? this.passSeconds() : this.satelliteSeconds} : undefined, onClose: () => this.closeSite()});
 }
 /** The map camera's heading, so a close-up opens facing the same way. */
 private mapHeading() { const h = this.viewer?.scene.camera.heading; return h === undefined || !Number.isFinite(h) ? undefined : h; }
 /** Expose the readout bar's height so the Detailed view button can sit above it on narrow views. */
 private trackBottomBar() {
  const bar = this.root.querySelector<HTMLElement>(".vw-bottom");
  if (bar) this.root.style.setProperty("--vw-bottom-h", `${bar.offsetHeight}px`);
 }
 private updateDetailButton() {
  const button = this.root.querySelector<HTMLButtonElement>("[data-detail]"); if (!button) return;
  button.hidden = !(this.layoutResult || this.result);
 }
 /** Cover the view while a calculation runs. Call with a title to show or update it; call with none to remove it once the
  * Moon has finished flying to the result and loading its terrain tiles (capped, so a slow tile never traps the cover). */
 setLoading(title?: string, detail = "") {
  const loader = this.loader; if (!loader) return;
  clearTimeout(this.loadingDone); this.loadingDone = 0;
  if (title) { loader.show(title, detail); return; }
  if (!loader.visible) return;
  const viewer = this.viewer, started = performance.now();
  loader.show("Rendering the result", "Drawing the network and streaming terrain…");
  const check = () => {
   const waited = performance.now() - started;
   const settled = !viewer || viewer.isDestroyed() || (!this.animation && !this.cameraMoving && viewer.scene.globe.tilesLoaded);
   if ((settled && waited > 250) || waited > 9000) { this.loadingDone = 0; loader.hide(); return; }
   viewer?.scene.requestRender();
   // Timers, not animation frames: frames stop while the window is in the background, which would leave the cover up.
   this.loadingDone = window.setTimeout(check, 120);
  };
  this.loadingDone = window.setTimeout(check, 120);
 }
 closeSite() { this.site?.dispose(); this.site = undefined; delete this.root.dataset.site; this.viewer?.scene.requestRender(); }
 // ---------- drawn area ----------
 private lift(lat: number, lon: number, metresUp = 40) {
  const globe = this.viewer!.scene.globe, h = globe.getHeight(Cartographic.fromDegrees(lon, lat, 0)) ?? 0;
  return Cartesian3.fromDegrees(lon, lat, h + metresUp, MOON);
 }
 setDrawMode(kind: "rect" | "free" | undefined) {
  const viewer = this.viewer; if (!viewer) return;
  this.drawKind = kind; this.drawing = undefined; this.draft = [];
  viewer.scene.screenSpaceCameraController.enableInputs = !kind;
  this.root.classList.toggle("drawing", !!kind);
  this.root.querySelectorAll<HTMLButtonElement>("[data-draw]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.draw === kind)));
  viewer.entities.removeById("area-draft");
  if (kind) {
   viewer.entities.add({id: "area-draft", polyline: {positions: new CallbackProperty(() => this.draft.length > 1 ? [...this.draft, this.draft[0]] : [], false), width: 3, material: Color.fromCssColorString("#84deff"), depthFailMaterial: Color.fromCssColorString("#84deff")}});
   this.toast(kind === "rect" ? "Drag across the Moon to draw a square. Any size, anywhere on the Moon." : "Press and drag to draw a free-form shape of any size. Terrain detail is best near the south pole.");
  } else this.toast("");
  viewer.scene.requestRender();
 }
 private pickLatLon(position: Cartesian2) {
  const point = this.surfacePoint(position); if (!point) return undefined;
  const c = Cartographic.fromCartesian(point, MOON)!;
  return {lat: deg(c.latitude), lon: (deg(c.longitude) + 360) % 360, point};
 }
 private drawDown(position: Cartesian2) {
  if (!this.drawKind) return;
  const here = this.pickLatLon(position); if (!here) return;
  this.drawing = {start: position.clone(), screen: [position.clone()], points: [here.point]};
  this.draft = [here.point];
 }
 private drawMove(position: Cartesian2) {
  const d = this.drawing; if (!d || !this.viewer) return;
  if (this.drawKind === "rect") {
   const s = d.start, corners = [new Cartesian2(s.x, s.y), new Cartesian2(position.x, s.y), new Cartesian2(position.x, position.y), new Cartesian2(s.x, position.y)];
   const picks = corners.map(c => this.pickLatLon(c)); if (picks.some(p => !p)) return;
   d.screen = corners; d.points = picks.map(p => p!.point); this.draft = d.points;
  } else {
   const last = d.screen[d.screen.length - 1];
   if (Math.hypot(position.x - last.x, position.y - last.y) < 7) return;
   const p = this.pickLatLon(position); if (!p) return;
   d.screen.push(position.clone()); d.points.push(p.point); this.draft = d.points;
  }
  this.viewer.scene.requestRender();
 }
 private drawUp(position: Cartesian2) {
  const d = this.drawing; if (!d || !this.drawKind) return;
  this.drawMove(position);
  this.drawing = undefined;
  const reach = Math.max(...d.screen.map(p => Math.hypot(p.x - d.start.x, p.y - d.start.y)));
  if (d.points.length < 3 || reach < 14) { this.draft = []; this.toast("That shape is too small. Drag a larger area.", true); return; }
  let points = d.points;
  if (this.drawKind === "free" && points.length > 48) { const step = points.length / 48; points = Array.from({length: 48}, (_, i) => points[Math.floor(i * step)]); }
  const polygon = points.map(p => { const c = Cartographic.fromCartesian(p, MOON)!; return [deg(c.latitude), (deg(c.longitude) + 360) % 360] as [number, number]; });
  this.suppressClickUntil = performance.now() + 350;
  this.setDrawMode(undefined);
  this.showArea(polygon);
  this.hooks.onArea?.(polygon);
 }
 showArea(polygon: [number, number][] | undefined) {
  const viewer = this.viewer; if (!viewer) return;
  viewer.entities.removeById("area");
  this.root.querySelector<HTMLElement>("[data-draw=clear]")!.hidden = !polygon?.length;
  if (polygon?.length) {
   const color = Color.fromCssColorString("#84deff");
   viewer.entities.add({id: "area", polyline: {positions: [...polygon, polygon[0]].map(([lat, lon]) => this.lift(lat, lon)), width: 3, material: new PolylineDashMaterialProperty({color, dashLength: 16}), depthFailMaterial: new PolylineDashMaterialProperty({color, dashLength: 16})}});
  }
  viewer.scene.requestRender();
 }
 /** Draw the planned network for a drawn area: nodes by role, links of the chosen layout. Pass undefined to clear. */
 setLayout(result: AreaResult | undefined, format: string, seconds = 0) {
  const viewer = this.viewer; if (!viewer) return;
  for (const e of [...viewer.entities.values]) if (String(e.id).startsWith("layout")) viewer.entities.remove(e);
  this.layoutResult = result; this.layoutFormat = format; this.satelliteSeconds = seconds;
  this.updateDetailButton();
  if (result) {
   const colors = {gateway: "#84deff", relay: "#73d8ae", mine: "#f4ba72"} as const, letter = {gateway: "G", relay: "R", mine: "M"} as const;
   const at = new Map(result.nodes.map(n => [n.id, this.lift(n.lat, n.lon, 30)]));
   for (const [a, b] of result.layouts[format]?.edges ?? []) {
    const p = at.get(a), q = at.get(b); if (!p || !q) continue;
    const material = Color.fromCssColorString("#73d8ae").withAlpha(0.95);
    viewer.entities.add({id: `layout:link:${a}-${b}`, polyline: {positions: [p, q], width: 3, material, depthFailMaterial: material}});
   }
   for (const n of result.nodes) {
    const color = Color.fromCssColorString(colors[n.role]);
    viewer.entities.add({id: `layout:node:${n.id}`, position: at.get(n.id)!,
     point: {pixelSize: n.role === "mine" ? 13 : 12, color, outlineColor: n.outside ? Color.fromCssColorString("#bba9ff") : Color.BLACK, outlineWidth: n.outside ? 3 : 2, disableDepthTestDistance: 2e5},
     label: {text: `${letter[n.role]}${n.id}`, font: "600 12px IBM Plex Mono, monospace", fillColor: color, outlineColor: Color.BLACK, outlineWidth: 3, style: LabelStyle.FILL_AND_OUTLINE, verticalOrigin: VerticalOrigin.BOTTOM, pixelOffset: new Cartesian2(0, -14), disableDepthTestDistance: 2e5}});
   }
  }
  this.setSatellitePreview(result, seconds);
  viewer.scene.requestRender();
 }

 setReferenceMission(result?: ReferenceMission, index = 0, failure: ReferenceFailure = "nominal", frame = false) {
  const viewer = this.viewer; if (!viewer) return;
  for (const entity of [...viewer.entities.values]) {
   if (String(entity.id).startsWith("reference:")) viewer.entities.remove(entity);
   if (String(entity.id).startsWith("satellite:") && result) entity.show = false;
  }
  if (result) {
   this.closeSite();
   const sample = result.samples[index];
   const nodes = [result.lander_km, result.relay_km, sample.orbiter_km, sample.receiver_km]
    .map(([x,y,z]) => new Cartesian3(x*1000,y*1000,z*1000));
   const [lander, relay, satellite, receiver] = nodes;
   // Show the backhaul bearing at lunar scale. The ground receiver is far outside this view.
   const bearing = Cartesian3.normalize(Cartesian3.subtract(receiver, satellite, new Cartesian3()), new Cartesian3());
   const earthDirection = Cartesian3.add(satellite, Cartesian3.multiplyByScalar(bearing, 650_000, bearing), new Cartesian3());
   const points = [lander, relay, satellite, earthDirection];
   const online = result.states[failure].online[index];
   const color = Color.fromCssColorString(online ? "#73d8ae" : "#f4ba72");
   const off = Color.fromCssColorString("#f2848c");
   ["Lander", "Relay", "Satellite", "Earth receiver direction"].forEach((name, i) => {
    const disabled = name.toLowerCase() === failure || name in sample.node_failures;
    viewer.entities.add({id: `reference:node:${i}`, position: points[i],
     point: {pixelSize: i === 3 ? 6 : 11, color: disabled ? off : color, outlineWidth: 2, outlineColor: Color.BLACK},
     label: {text: name + (disabled ? " · OFF" : ""), font: "600 13px IBM Plex Mono, monospace",
      fillColor: disabled ? off : color, outlineColor: Color.BLACK, outlineWidth: 3, style: LabelStyle.FILL_AND_OUTLINE,
      pixelOffset: new Cartesian2(0, i === 0 ? 15 : -14)}});
   });
   Object.values(sample.hops).forEach((hop, i) => {
    const disabled = (failure === "relay" && i < 2) || (failure === "satellite" && i > 0);
    if (hop.status !== "available" || disabled) return;
    viewer.entities.add({id: `reference:hop:${i}`, polyline: {
     positions: [points[i], points[i+1]], arcType: ArcType.NONE, width: 3,
     material: online ? color : new PolylineDashMaterialProperty({color, dashLength: 14})}});
   });
   if (frame) {
    this.setMode("3d");
    cancelAnimationFrame(this.animation); this.animation = 0;
    const bounds = BoundingSphere.fromPoints(points);
    viewer.camera.flyToBoundingSphere(bounds, {duration: 1.2,
     offset: new HeadingPitchRange(0, rad(-35), Math.max(1_500_000, bounds.radius * 4)),
     complete: () => { this.view = this.readView(); this.refreshReadouts(); }});
   }
  }
  viewer.scene.requestRender();
 }

 /** Seconds after the overhead pass that the satellite is at now: the paused time, or that time plus the live clock. */
 passSeconds() {
  const p = this.pass, period = orbitPeriod();
  const t = p.playing ? p.base + (performance.now() - p.startedAt) / 1000 * PASS_SPEED : p.base;
  return ((t % period) + period) % period;
 }
 /** Play or pause the live pass. While it plays, the satellite flies its orbit and every link re-aims at it each frame. */
 setPassPlaying(playing: boolean) {
  const now = this.passSeconds();
  this.pass = {...this.pass, playing, base: now, startedAt: performance.now()};
  this.renderPassControl();
  this.viewer?.scene.requestRender();
 }
 /** Recompute the satellite, its antenna axis and every node's access once per frame; entity callbacks read this. */
 private updateLive() {
  const L = this.live; if (!L) return;
  const t = this.passSeconds();
  const state = satelliteState(L.result.site.lat, L.result.site.lon, t);
  const vec = (v: number[], out: Cartesian3) => Cartesian3.fromElements(v[0], v[1], v[2], out);
  vec(state.position, L.satellite);
  satelliteAttitude(vec(state.radial, new Cartesian3()), vec(state.tangent, new Cartesian3()), L.orientation, vec(state.boresight, new Cartesian3()));
  const hit = boresightHit(state), reach = Math.hypot(...state.position) - R;
  if (hit) vec(hit, L.aim); else Cartesian3.add(L.satellite, Cartesian3.multiplyByScalar(vec(state.boresight, new Cartesian3()), reach * 1.5, new Cartesian3()), L.aim);
  let linked = 0;
  L.nodes.forEach((n, i) => {
   const a = satelliteAccess(n.node, state.position, state.boresight, az => horizonDeg(L.horizons, n.node.id, az));
   L.visible[i] = a.visible; if (a.visible) linked++;
  });
  L.linked = linked; L.seconds = t;
  // Tell the panel (slider, table) a few times a second, not every frame.
  const now = performance.now();
  if (now - L.reported > 250) { L.reported = now; this.renderPassControl(); this.hooks.onSatelliteTime?.(t); }
 }
 private renderPassControl() {
  const box = this.root.querySelector<HTMLElement>(".vw-pass"); if (!box) return;
  const L = this.live; box.hidden = !L; if (!L) return;
  const playing = this.pass.playing, button = box.querySelector<HTMLButtonElement>("button")!;
  button.textContent = playing ? "❚❚" : "▶"; button.setAttribute("aria-label", playing ? "Pause the satellite pass" : "Play the satellite pass");
  box.querySelector<HTMLElement>("[data-pass-text]")!.innerHTML = `S1 ${playing ? "live pass" : "paused"} · +${(L.seconds / 60).toFixed(1)} min · <span class="on">${L.linked} of ${L.nodes.length} nodes linked</span>`;
 }

 /** Geometry-only preview: radio paths are straight and hidden below the elevation mask. The satellite flies its orbit
  * (PASS_SPEED times real time) and each node's link follows it until that node loses it, then returns on the next pass. */
 setSatellitePreview(result: AreaResult | undefined, seconds = 0) {
  const viewer = this.viewer; if (!viewer) return;
  for (const e of [...viewer.entities.values]) {
   if (String(e.id).startsWith("layout:orbital:")) viewer.entities.remove(e);
   if (String(e.id).startsWith("satellite:")) e.show = !result;
  }
  this.live = undefined;
  if (result) {
   this.satelliteSeconds = seconds;
   this.pass = {...this.pass, base: seconds, startedAt: performance.now()};
   const nodes = result.nodes.map(node => ({node, at: new Cartesian3(...surfacePosition(node.lat, node.lon, node.elevation + 10))}));
   const live = this.live = {result, nodes, visible: nodes.map(() => false), satellite: new Cartesian3(), aim: new Cartesian3(), orientation: new Quaternion(), linked: 0, seconds, reported: 0, horizons: undefined as HorizonSet | undefined};
   // Each node's own terrain horizon: links then close one by one as the satellite sinks behind rims and hills.
   void loadHorizons(result).then(set => { if (this.live === live) live.horizons = set; });
   this.updateLive();
   viewer.entities.add({id: "layout:orbital:satellite", position: new CallbackPositionProperty((_t, out) => Cartesian3.clone(live.satellite, out), false),
    orientation: new CallbackProperty((_t, out) => Quaternion.clone(live.orientation, out), false), model: satelliteModel(),
    label: {text: "S1 · assumed relay satellite", font: "600 13px IBM Plex Mono, monospace", fillColor: ORBIT_COLOR,
     outlineColor: Color.BLACK, outlineWidth: 3, style: LabelStyle.FILL_AND_OUTLINE, pixelOffset: new Cartesian2(0, -(SATELLITE_PIXELS / 2 + 12)), disableDepthTestDistance: Number.POSITIVE_INFINITY}});
   const period = orbitPeriod();
   const orbit = Array.from({length: 181}, (_, i) => new Cartesian3(...satellitePosition(result.site.lat, result.site.lon, i * period / 180)));
   viewer.entities.add({id: "layout:orbital:track", polyline: {positions: orbit, arcType: ArcType.NONE, width: 4,
    material: new PolylineGlowMaterialProperty({color: ORBIT_COLOR.withAlpha(0.8), glowPower: 0.2})}});
   // The antenna axis: where the satellite is facing. It ends on the Moon, or just past the limb when it points away.
   viewer.entities.add({id: "layout:orbital:boresight", name: "Antenna pointing", polyline: {positions: new CallbackProperty(() => [live.satellite, live.aim], false), arcType: ArcType.NONE, width: 2,
    material: new PolylineDashMaterialProperty({color: Color.fromCssColorString("#6ee7a8"), dashLength: 10})}});
   const dotted = () => new PolylineDashMaterialProperty({color: SATELLITE_LINK_COLOR, gapColor: Color.TRANSPARENT, dashLength: 12, dashPattern: 0b0011001100110011});
   nodes.forEach((n, i) => {
    viewer.entities.add({id: `layout:orbital:link:${n.node.id}`, polyline: {
     positions: new CallbackProperty(() => [n.at, live.satellite], false), show: new CallbackProperty(() => live.visible[i], false),
     arcType: ArcType.NONE, width: 3, material: dotted(), depthFailMaterial: dotted()}});
   });
  }
  this.renderPassControl();
  viewer.scene.requestRender();
 }
 showSatelliteOverview(result: AreaResult, seconds = 0) {
  const viewer = this.viewer; if (!viewer) return;
  this.setMode("3d");
  cancelAnimationFrame(this.animation); this.animation = 0;
  const points = result.nodes.map(n => new Cartesian3(...surfacePosition(n.lat, n.lon, n.elevation)));
  points.push(new Cartesian3(...satellitePosition(result.site.lat, result.site.lon, seconds)));
  const bounds = BoundingSphere.fromPoints(points);
  viewer.camera.flyToBoundingSphere(bounds, {duration: 1.2,
   offset: new HeadingPitchRange(0, rad(-35), Math.max(1_500_000, bounds.radius * 4)),
   complete: () => { this.view = this.readView(); this.refreshReadouts(); }});
 }

 /** For tests and diagnostics: what the viewer is currently showing. */
 diagnostics() {
  const terrain = this.terrain;
  return {mode: this.mode, view: this.view, imagery: this.imagery?.state, imageryCounts: this.imagery?.counts, terrainSuccess: terrain?.successes ?? 0, terrainFailures: terrain?.failures ?? 0};
 }
 dispose() {
  this.disposed = true; this.closeSite(); this.loader?.dispose(); clearTimeout(this.loadingDone); clearInterval(this.satelliteTimer); cancelAnimationFrame(this.animation); this.observer.disconnect(); this.handler?.destroy();
  if (this.viewer && !this.viewer.isDestroyed()) this.viewer.destroy();
  this.viewer = undefined;
 }
}
