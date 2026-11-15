import {MOON_R, gnomonicToOrtho} from "./site-projection.js";

/** One continuous Site view terrain surface from a wide grid and an optional finer centre grid.
 *
 * Both grids are uniform gnomonic samples of the same DEM (row 0 north, metres east = x, metres south = z).
 * They are merged on a single non-uniform tensor grid: the axis is dense inside the centre square and keeps the wide
 * spacing outside it. The result is one indexed mesh, so nothing overlaps or z-fights, there are no T-junctions or
 * cracks, and vertex normals are continuous everywhere. Inside a narrow band at the centre square's edge the finer
 * heights blend into the wide grid's, so detail fades out instead of stepping at a visible square. */

export interface HeightGrid { size: number; half: number; heights: Float32Array }

/** Bilinear height on a uniform grid at gnomonic (x, z); clamps at the edges. */
export function gridHeight(g: HeightGrid, x: number, z: number): number {
 const n = g.size, step = 2 * g.half / (n - 1);
 const fc = Math.min(n - 1, Math.max(0, (x + g.half) / step)), fr = Math.min(n - 1, Math.max(0, (z + g.half) / step));
 const c = Math.min(n - 2, Math.floor(fc)), r = Math.min(n - 2, Math.floor(fr)), tx = fc - c, tz = fr - r, h = g.heights;
 const top = h[r * n + c] + (h[r * n + c + 1] - h[r * n + c]) * tx;
 const bottom = h[(r + 1) * n + c] + (h[(r + 1) * n + c + 1] - h[(r + 1) * n + c]) * tx;
 return top + (bottom - top) * tz;
}

const smoothstep = (t: number) => { t = Math.min(1, Math.max(0, t)); return t * t * (3 - 2 * t); };

/** Height of the merged surface at (x, z): what both the mesh and the markers are anchored to. */
export function surfaceHeight(outer: HeightGrid, inner: HeightGrid | undefined, x: number, z: number, blendM: number): number {
 const base = gridHeight(outer, x, z);
 if (!inner) return base;
 const inside = inner.half - Math.max(Math.abs(x), Math.abs(z));
 if (inside < 0) return base;
 const w = blendM > 0 ? smoothstep(inside / blendM) : 1;
 return w === 0 ? base : base + (gridHeight(inner, x, z) - base) * w;
}

/** Merged axis: the inner grid's coordinates plus the outer coordinates outside the inner square. Outer samples within
 * 40 % of a wide step of the inner edge are dropped so the transition has no sliver triangles. */
export function mergedAxis(outer: HeightGrid, inner?: HeightGrid): {axis: Float64Array; innerFrom: number; innerTo: number} {
 const so = 2 * outer.half / (outer.size - 1);
 const out: number[] = [];
 if (!inner) { for (let i = 0; i < outer.size; i++) out.push(-outer.half + i * so); return {axis: Float64Array.from(out), innerFrom: -1, innerTo: -1}; }
 const si = 2 * inner.half / (inner.size - 1), gap = inner.half + 0.4 * so;
 for (let i = 0; i < outer.size; i++) { const v = -outer.half + i * so; if (v < -gap) out.push(v); }
 const innerFrom = out.length;
 for (let j = 0; j < inner.size; j++) out.push(-inner.half + j * si);
 const innerTo = out.length - 1;
 for (let i = 0; i < outer.size; i++) { const v = -outer.half + i * so; if (v > gap) out.push(v); }
 return {axis: Float64Array.from(out), innerFrom, innerTo};
}

export interface Surface {
 positions: Float32Array; uv: Float32Array; uv1: Float32Array; index: Uint32Array;
 /** Triangles [0, innerCount) lie inside the centre square and use the detailed texture (uv1); the rest use uv. */
 innerCount: number; columns: number;
}

/** Build the mesh. `centre` is the elevation of the scene origin, so the site sits at y = 0. */
export function buildSurface(outer: HeightGrid, inner: HeightGrid | undefined, centre: number, blendM: number): Surface {
 const {axis, innerFrom, innerTo} = mergedAxis(outer, inner), n = axis.length;
 const positions = new Float32Array(n * n * 3), uv = new Float32Array(n * n * 2), uv1 = new Float32Array(n * n * 2);
 const innerHalf = inner?.half ?? outer.half;
 for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
  const i = r * n + c, x = axis[c], z = axis[r];
  const norm = Math.hypot(MOON_R, x, z), radius = MOON_R + surfaceHeight(outer, inner, x, z, blendM);
  positions[i * 3] = x * radius / norm; positions[i * 3 + 1] = MOON_R * radius / norm - MOON_R - centre; positions[i * 3 + 2] = z * radius / norm;
  // Texture space is orthographic (WMS BBOX in east/north metres); v grows northward because textures are flipped.
  const [e, nn] = gnomonicToOrtho(x, z);
  uv[i * 2] = 0.5 + e / (2 * outer.half); uv[i * 2 + 1] = 0.5 + nn / (2 * outer.half);
  uv1[i * 2] = 0.5 + e / (2 * innerHalf); uv1[i * 2 + 1] = 0.5 + nn / (2 * innerHalf);
 }
 const index = new Uint32Array((n - 1) * (n - 1) * 6);
 const isInner = (r: number, c: number) => inner !== undefined && r >= innerFrom && r < innerTo && c >= innerFrom && c < innerTo;
 let k = 0, innerCount = 0;
 const quad = (r: number, c: number) => {
  const a = r * n + c, b = a + n, cc = a + 1, d = b + 1;
  index[k++] = a; index[k++] = b; index[k++] = cc; index[k++] = cc; index[k++] = b; index[k++] = d;
 };
 for (let r = 0; r < n - 1; r++) for (let c = 0; c < n - 1; c++) if (isInner(r, c)) { quad(r, c); innerCount += 2; }
 for (let r = 0; r < n - 1; r++) for (let c = 0; c < n - 1; c++) if (!isInner(r, c)) quad(r, c);
 return {positions, uv, uv1, index, innerCount, columns: n};
}
