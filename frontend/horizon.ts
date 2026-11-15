/** Terrain horizon of every node in a planned area, from the same LOLA terrain the app serves.
 *
 * As the relay satellite rises or sets, each node keeps sight of it until it drops below that node's own horizon:
 * crater rims and hills block one node earlier than another, so links open and close one by one. The map, the 3D
 * views and the pass table all read the same profiles, so they always agree on which node is linked.
 * Geometry only: radio diffraction over the horizon is not modelled. */
import {MOON_RADIUS} from "./orbital-preview.js";
import type {AreaResult} from "./types.js";

const STEP_DEG = 2;               // azimuth resolution of each horizon profile
const REACH_M = 20_000;           // terrain beyond this is below the elevation mask for any reasonable mask
const ANTENNA_M = 10;             // the same 10 m antenna height the access check uses
const GRID = 257;

export interface HorizonSet { key: string; stepDeg: number; byNode: Map<number, Float32Array>; resolutionM: number }

const cache = new Map<string, HorizonSet>();
const pending = new Map<string, Promise<HorizonSet | undefined>>();
const rad = (d: number) => d * Math.PI / 180;

export const horizonKey = (L: AreaResult) => `${L.site.key}|${L.site.lat.toFixed(5)},${L.site.lon.toFixed(5)}|${L.nodes.map(n => n.id).join(",")}`;
export const cachedHorizons = (L: AreaResult | undefined) => L ? cache.get(horizonKey(L)) : undefined;

/** Highest terrain elevation angle (degrees) a node sees toward `azimuthDeg` (0 = north, 90 = east). */
export function horizonDeg(set: HorizonSet | undefined, nodeId: number, azimuthDeg: number) {
 const profile = set?.byNode.get(nodeId); if (!profile) return -90;
 const f = (((azimuthDeg % 360) + 360) % 360) / set!.stepDeg, i = Math.floor(f) % profile.length, j = (i + 1) % profile.length, t = f - Math.floor(f);
 return profile[i] * (1 - t) + profile[j] * t;
}

/** Fetch the terrain around the area once and trace every node's horizon. Resolves undefined if terrain is unavailable. */
export function loadHorizons(L: AreaResult): Promise<HorizonSet | undefined> {
 const key = horizonKey(L);
 const done = cache.get(key); if (done) return Promise.resolve(done);
 const running = pending.get(key); if (running) return running;
 const job = build(L, key).then(set => { if (set) cache.set(key, set); pending.delete(key); return set; },
  error => { console.warn("Terrain horizons unavailable; links use the elevation mask only", error); pending.delete(key); return undefined; });
 pending.set(key, job);
 return job;
}

async function build(L: AreaResult, key: string): Promise<HorizonSet | undefined> {
 const {lat, lon} = L.site, p = rad(lat), l = rad(lon);
 const up = [Math.cos(p) * Math.cos(l), Math.cos(p) * Math.sin(l), Math.sin(p)];
 const east = [-Math.sin(l), Math.cos(l), 0], north = [-Math.sin(p) * Math.cos(l), -Math.sin(p) * Math.sin(l), Math.cos(p)];
 const offset = (la: number, lo: number) => {   // tangent-plane east/north metres of a point from the area centre
  const q = rad(la), m = rad(lo), v = [Math.cos(q) * Math.cos(m), Math.cos(q) * Math.sin(m), Math.sin(q)];
  const d = v.map((x, i) => (x - up[i]) * MOON_RADIUS);
  return {e: d[0] * east[0] + d[1] * east[1] + d[2] * east[2], n: d[0] * north[0] + d[1] * north[1] + d[2] * north[2]};
 };
 const nodes = L.nodes.map(node => ({id: node.id, ...offset(node.lat, node.lon)}));
 const spread = nodes.reduce((m, n) => Math.max(m, Math.hypot(n.e, n.n)), 0);
 const half = Math.min(200_000, Math.max(5000, Math.ceil(spread + REACH_M)));
 const query = new URLSearchParams({lat: String(lat), lon: String(lon), half_width_m: String(half), size: String(GRID)});
 const response = await fetch(`/api/terrain/site?${query}`);
 if (!response.ok) throw new Error(`Horizon terrain failed (${response.status}).`);
 const heights = new Float32Array(await response.arrayBuffer());
 if (heights.length !== GRID * GRID) throw new Error("Invalid horizon terrain grid.");
 const px = 2 * half / (GRID - 1);
 // Bilinear height at tangent-plane metres (east, north); rows run north to south as the server builds them.
 const at = (e: number, n: number) => {
  const c = Math.min(GRID - 1.001, Math.max(0, (e / half + 1) * (GRID - 1) / 2)), r = Math.min(GRID - 1.001, Math.max(0, (-n / half + 1) * (GRID - 1) / 2));
  const c0 = Math.floor(c), r0 = Math.floor(r), fc = c - c0, fr = r - r0, i = r0 * GRID + c0;
  const h = heights[i] * (1 - fc) * (1 - fr) + heights[i + 1] * fc * (1 - fr) + heights[i + GRID] * (1 - fc) * fr + heights[i + GRID + 1] * fc * fr;
  return Number.isFinite(h) ? h : NaN;
 };
 const byNode = new Map<number, Float32Array>(), bins = Math.round(360 / STEP_DEG);
 for (const node of nodes) {
  const eye = at(node.e, node.n) + ANTENNA_M, profile = new Float32Array(bins).fill(-90);
  if (!Number.isFinite(eye)) { byNode.set(node.id, profile); continue; }
  for (let b = 0; b < bins; b++) {
   const az = rad(b * STEP_DEG), de = Math.sin(az), dn = Math.cos(az);
   let best = -90;
   for (let s = px; s <= REACH_M; s += px) {
    const e = node.e + de * s, n = node.n + dn * s;
    if (Math.abs(e) > half || Math.abs(n) > half) break;
    const h = at(e, n); if (!Number.isFinite(h)) continue;
    // Curvature: a point s metres away sits s²/2R lower relative to the node's horizontal plane.
    const angle = Math.atan2(h - eye - s * s / (2 * MOON_RADIUS), s) * 180 / Math.PI;
    if (angle > best) best = angle;
   }
   profile[b] = best;
  }
  byNode.set(node.id, profile);
 }
 return {key, stepDeg: STEP_DEG, byNode, resolutionM: px};
}
