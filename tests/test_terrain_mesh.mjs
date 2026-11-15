import assert from "node:assert/strict";
import {pathToFileURL} from "node:url";
import path from "node:path";

const m = await import(pathToFileURL(path.resolve(process.argv[2])).href);
const rad = d => d * Math.PI / 180;
const size = 9, padding = 1, stride = size + 2;

// A synthetic, continuous terrain defined on lat/lon so neighbouring tiles can be sampled consistently.
const terrain = (lat, lon) => 800 * Math.sin(rad(lat) * 40) * Math.cos(rad(lon) * 25);
function samples(rect, fn) {
 const heights = new Float32Array(stride * stride);
 for (let r = 0; r < stride; r++) for (let c = 0; c < stride; c++) {
  const [lat, lon] = m.sampleLatLon(rect, size, r - padding, c - padding);
  heights[r * stride + c] = fn(lat * 180 / Math.PI, lon * 180 / Math.PI);
 }
 return {size, padding, heights, resolutionM: 20, sources: ["near20km"]};
}
const rect = (w, s, e, n) => ({west: rad(w), south: rad(s), east: rad(e), north: rad(n)});

// Oct encoding round-trips to within quantisation.
for (const v of [[0, 0, 1], [0, 0, -1], [1, 0, 0], [-.6, .3, -.74], [.1, -.9, .42]]) {
 const n = Math.hypot(...v), u = v.map(x => x / n), [a, b] = m.octEncode(...u), d = m.octDecode(a, b);
 assert.ok(u[0] * d[0] + u[1] * d[1] + u[2] * d[2] > 0.9995, `oct round trip ${v}`);
}

// Flat terrain: normals are radial, heights quantise to zero range, indices are valid and edges complete.
const flatRect = rect(10, -50, 10.5, -49.5);
const flat = m.buildTileMesh(samples(flatRect, () => 100), flatRect);
assert.equal(flat.minimumHeight, 100); assert.equal(flat.maximumHeight, 100);
assert.equal(flat.quantizedVertices.length, size * size * 3);
assert.equal(flat.indices.length, (size - 1) ** 2 * 6);
assert.ok(flat.indices.every(i => i < size * size));
for (const edge of [flat.westIndices, flat.eastIndices, flat.northIndices, flat.southIndices]) assert.equal(edge.length, size);
for (let i = 0; i < size * size; i++) {
 const p = [0, 1, 2].map(k => flat.positions[i * 3 + k]), n = [0, 1, 2].map(k => flat.normals[i * 3 + k]), len = Math.hypot(...p);
 assert.ok((p[0] * n[0] + p[1] * n[1] + p[2] * n[2]) / len > 0.9999, "flat normal is radial");
}

// Height units: 1 unit is 1 metre above the 1,737,400 m sphere.
const sphere = m.buildTileMesh(samples(flatRect, () => 0), flatRect);
assert.ok(Math.abs(Math.hypot(sphere.positions[0], sphere.positions[1], sphere.positions[2]) - 1_737_400) < 1e-3);
assert.ok(Math.abs(Math.hypot(flat.positions[0], flat.positions[1], flat.positions[2]) - 1_737_500) < 1e-3);

// Slopes tilt normals; a ramp rising to the east must tilt the normal toward the west.
const ramp = m.buildTileMesh(samples(flatRect, (lat, lon) => (lon - 10) * 1000), flatRect);
const mid = Math.floor(size / 2) * size + Math.floor(size / 2);
const p = [0, 1, 2].map(k => ramp.positions[mid * 3 + k]), n = [0, 1, 2].map(k => ramp.normals[mid * 3 + k]);
const eastDir = [-Math.sin(rad(10.25)), Math.cos(rad(10.25)), 0];
assert.ok(n[0] * eastDir[0] + n[1] * eastDir[1] < -0.01, "normal leans away from rising ground");

// Tile boundaries: shared edge vertices and normals agree to the last bit across neighbouring tiles.
const a = rect(10, -50, 10.5, -49.5), b = rect(10.5, -50, 11, -49.5);
const ma = m.buildTileMesh(samples(a, terrain), a), mb = m.buildTileMesh(samples(b, terrain), b);
for (let row = 0; row < size; row++) {
 const ia = row * size + size - 1, ib = row * size;
 for (let k = 0; k < 3; k++) {
  assert.ok(Math.abs(ma.positions[ia * 3 + k] - mb.positions[ib * 3 + k]) < 1e-6, "edge positions match");
  assert.ok(Math.abs(ma.normals[ia * 3 + k] - mb.normals[ib * 3 + k]) < 1e-6, "edge normals match");
 }
 assert.equal(ma.encodedNormals[ia * 2], mb.encodedNormals[ib * 2]);
}

// The pole row collapses to one point and must not produce NaN normals.
const polar = rect(-180, -90, -90, -80), pm = m.buildTileMesh(samples(polar, () => 0), polar);
assert.ok(pm.normals.every(Number.isFinite));

// Missing elevation is refused rather than rendered as a spike.
const bad = samples(flatRect, () => 0); bad.heights[stride * 3 + 3] = NaN;
assert.throws(() => m.buildTileMesh(bad, flatRect), /missing/);

// Source-resolution limits: tile spacing at the deepest levels.
assert.ok(Math.abs(m.tileSpacingMetres(5, 33) - 5330) < 10);
assert.ok(Math.abs(m.tileSpacingMetres(13, 33) - 20.8) < 0.2);

// Regional footprints: a tile over Haworth touches its 20 m extent; one on the far side does not.
const [hx, hy] = m.polarStereographicSouth(-86.8, 337.2);
const extent = {x_min: hx - 20000, x_max: hx + 20000, y_min: hy - 20000, y_max: hy + 20000, pixel_m: 20};
assert.ok(m.rectangleTouchesExtent({west: -23.2, south: -86.85, east: -22.6, north: -86.75}, extent, [-86.8, 337.2]));
assert.ok(!m.rectangleTouchesExtent({west: 100, south: -60, east: 110, north: -50}, extent, [-86.8, 337.2]));
assert.ok(!m.rectangleTouchesExtent({west: 0, south: 10, east: 10, north: 20}, extent, [-86.8, 337.2]));
console.log("terrain mesh tests passed");
