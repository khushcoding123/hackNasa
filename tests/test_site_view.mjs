// Regression tests for lunar imagery requests (Cesium) and the Three.js Site view geometry and resampling.
// Run with `npm run test:site` (bundles the pure TypeScript modules into tmp/site-test first).
import assert from "node:assert/strict";
import {pathToFileURL} from "node:url";
import path from "node:path";
import {Event} from "@cesium/core";
import {ImageryLayer, ImageryState, RequestState, Request} from "@cesium/engine";

const dir = path.resolve(process.argv[2] ?? "tmp/site-test");
const load = name => import(pathToFileURL(path.join(dir, `${name}.mjs`)).href);
const status = await load("imagery-status"), proj = await load("site-projection"), surf = await load("site-surface");
const tick = () => new Promise(r => setTimeout(r, 0));
let passed = 0;
const test = async (name, fn) => { await fn(); passed++; console.log(`ok  ${name}`); };

// ---------------------------------------------------------------- Cesium imagery requests
/** Drive Cesium's own ImageryLayer._requestImagery state machine with a provider whose requestImage we control. */
async function cesiumRequest(requestImage, {retry} = {}) {
 const errorEvent = new Event();
 if (retry) errorEvent.addEventListener(retry);
 const layer = {_imageryProvider: {requestImage, errorEvent}, _requestImageError: undefined};
 const imagery = {x: 1, y: 2, level: 3, state: ImageryState.UNLOADED};
 ImageryLayer.prototype._requestImagery.call(layer, imagery);
 await tick(); await tick();
 return imagery;
}

await test("throttled request stays UNLOADED (retried later), not FAILED", async () => {
 const s = new status.LayerStatus();
 const throttled = () => undefined;
 const wrapped = status.trackRequests(throttled, s, RequestState.CANCELLED);
 assert.equal(wrapped(0, 0, 0, new Request()), undefined, "synchronous undefined must pass straight through");
 assert.equal((await cesiumRequest(wrapped)).state, ImageryState.UNLOADED);
 assert.equal(s.counts.failed, 0); assert.equal(s.state, "loading");
 // The previous async wrapper turned the same throttle into a permanent failure.
 const asyncWrapper = async (...args) => throttled(...args);
 assert.equal((await cesiumRequest(asyncWrapper, {retry: e => { e.retry = false; }})).state, ImageryState.FAILED, "old behaviour reproduced");
});

await test("successful tile reaches RECEIVED and marks the layer live", async () => {
 const s = new status.LayerStatus(), image = {width: 256};
 const imagery = await cesiumRequest(status.trackRequests(() => Promise.resolve(image), s, RequestState.CANCELLED));
 assert.equal(imagery.state, ImageryState.RECEIVED); assert.equal(imagery.image, image);
 assert.equal(s.state, "live"); assert.equal(s.counts.ok, 1);
});

await test("cancelled requests are not failures and never trigger the fallback", async () => {
 const s = new status.LayerStatus();
 const wrapped = status.trackRequests((x, y, l, request) => { request.state = RequestState.CANCELLED; return Promise.reject(new Error("cancelled")); }, s, RequestState.CANCELLED);
 for (let i = 0; i < 30; i++) await wrapped(0, 0, 0, new Request()).catch(() => {});
 assert.equal(s.counts.cancelled, 30); assert.equal(s.counts.failed, 0); assert.notEqual(s.state, "fallback");
 // RequestState.RECEIVED (3) was previously mistaken for CANCELLED.
 assert.equal(RequestState.CANCELLED, 4);
});

await test("sustained genuine failures switch to fallback; isolated ones do not", async () => {
 const changes = [], s = new status.LayerStatus(state => changes.push(state));
 const fail = status.trackRequests(() => Promise.reject(new Error("503")), s, RequestState.CANCELLED);
 const ok = status.trackRequests(() => Promise.resolve({}), s, RequestState.CANCELLED);
 await ok(0, 0, 0, new Request());
 for (let i = 0; i < status.FALLBACK_AFTER - 1; i++) await fail(0, 0, 0, new Request()).catch(() => {});
 await ok(0, 0, 0, new Request());                       // a success resets the streak
 for (let i = 0; i < status.FALLBACK_AFTER - 1; i++) await fail(0, 0, 0, new Request()).catch(() => {});
 assert.equal(s.state, "live");
 await fail(0, 0, 0, new Request()).catch(() => {});
 assert.equal(s.state, "fallback"); assert.deepEqual(changes, ["live", "fallback"]);
 assert.equal(s.shouldRetry(0), false, "no tile retries once the layer is in fallback");
 s.reset(); assert.equal(s.state, "loading"); assert.equal(s.counts.failed, 0);
});

await test("failed tiles get a bounded number of Cesium retries, then use the ancestor", async () => {
 const s = new status.LayerStatus();
 let calls = 0;
 const wrapped = status.trackRequests(() => { calls++; return Promise.reject(new Error("500")); }, s, RequestState.CANCELLED);
 const imagery = await cesiumRequest(wrapped, {retry: e => { e.retry = s.shouldRetry(e.timesRetried); }});
 for (let i = 0; i < 6; i++) await tick();
 assert.equal(calls, 1 + status.TILE_RETRIES); assert.equal(imagery.state, ImageryState.FAILED);
});

// ---------------------------------------------------------------- Site view projection and resampling
const R = proj.MOON_R, deg = r => r * 180 / Math.PI;

await test("Site view imagery uses the lunar orthographic SRS, not Earth-radius AUTO:42003", async () => {
 assert.equal(proj.orthoSrs(-86.8, 337.2), "IAU2000:30166,9001,337.2,-86.8");
});

/** A smooth synthetic global map (a function of 3D direction), so the correct answer is known everywhere. */
function syntheticMap(w = 360, h = 180) {
 const data = new Uint8ClampedArray(w * h * 4), value = (lat, lon) => {
  const x = Math.cos(lat) * Math.cos(lon), y = Math.cos(lat) * Math.sin(lon), z = Math.sin(lat);
  return 128 + 60 * x + 40 * y + 25 * z;
 };
 for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) {
  const lat = Math.PI / 2 - (r + 0.5) / h * Math.PI, lon = (c + 0.5) / w * 2 * Math.PI - Math.PI, i = (r * w + c) * 4;
  data[i] = data[i + 1] = data[i + 2] = Math.round(value(lat, lon)); data[i + 3] = 255;
 }
 return {raster: {width: w, height: h, data}, value};
}

await test("bundled reprojection is bilinear, continuous across the longitude seam and through the poles", async () => {
 const {raster, value} = syntheticMap();
 for (const [lat, lon, half] of [[0, 180, 400_000], [0, -180, 400_000], [-89.9, 0, 300_000], [89.95, 137, 300_000], [-30, 40, 200_000]]) {
  const size = 64, out = proj.reprojectEquirect(raster, lat, lon, half, size), f = proj.frameAt(lat, lon);
  let worst = 0;
  for (let row = 0; row < size; row++) for (let col = 0; col < size; col++) {
   const ll = proj.orthoToLatLon(f, ((col + 0.5) / size * 2 - 1) * half, (1 - (row + 0.5) / size * 2) * half);
   worst = Math.max(worst, Math.abs(out[(row * size + col) * 4] - value(...ll)));
  }
  // One source pixel is about 30 km; nearest-pixel sampling errs by up to ~0.7 grey levels per pixel here plus
  // seam/pole jumps of tens of levels. Bilinear stays within rounding.
  assert.ok(worst <= 1.5, `max error ${worst.toFixed(2)} at ${lat},${lon}`);
 }
});

await test("bilinear output is smooth where nearest-pixel sampling is blocky", async () => {
 const w = 64, h = 32, data = new Uint8ClampedArray(w * h * 4);
 for (let i = 0; i < w * h; i++) { data[i * 4] = (i % w) * 4; data[i * 4 + 3] = 255; }
 const out = proj.reprojectEquirect({width: w, height: h, data}, 0, 0, 1_500_000, 128);   // spans ~18 source pixels
 const row = Array.from({length: 128}, (_, c) => out[(64 * 128 + c) * 4]);
 const distinct = new Set(row).size;
 assert.ok(distinct > 40, `only ${distinct} distinct values across a ramp: blocky`);
});

await test("orthographic frame: north is up, east is right, and points round-trip", async () => {
 const f = proj.frameAt(-43.3, -11.2);
 const [latN] = proj.orthoToLatLon(f, 0, 10_000), [latS] = proj.orthoToLatLon(f, 0, -10_000);
 assert.ok(latN > latS);
 const [, lonE] = proj.orthoToLatLon(f, 10_000, 0), [, lonW] = proj.orthoToLatLon(f, -10_000, 0);
 assert.ok(lonE > lonW);
 const [lat, lon] = proj.orthoToLatLon(f, 0, 0);
 assert.ok(Math.abs(deg(lat) + 43.3) < 1e-9 && Math.abs(deg(lon) + 11.2) < 1e-9);
 assert.equal(proj.orthoToLatLon(f, R, R), undefined, "beyond the visible hemisphere");
});

await test("resolution labels never claim more than the source resolves", async () => {
 assert.match(proj.imageryLabel("LROC WAC", 100, 171), /shown at 171 m\/px \(100 m\/px source\)/);
 assert.match(proj.imageryLabel("LROC WAC", 100, 50), /100 m\/px source \(closer zoom is interpolated/);
 assert.match(proj.imageryLabel("LROC NAC", 1, 4000 / 2048), /shown at 2\.0 m\/px \(1\.0 m\/px source\)/);
 assert.doesNotMatch(proj.imageryLabel("LROC WAC", 100, 3.9), /3\.9/);
 assert.equal(proj.requestSize(7000, 100, 2048), 280);        // ~2 px per 100 m source pixel over 14 km
 assert.equal(proj.requestSize(175_000, 100, 2048), 2048);    // capped: the label then reports 171 m/px
 assert.equal(proj.requestSize(2000, 100, 2048), 256);
});

await test("NAC requests: preview then full size, only where they beat WAC", async () => {
 assert.deepEqual(proj.nacSteps(2000, 2048, 100), [512, 2048]);     // 20 m site centre: 7.8 m preview, 2 m full
 assert.deepEqual(proj.nacSteps(7000, 2048, 100), [512, 2048]);
 assert.deepEqual(proj.nacSteps(20_000, 2048, 100), [1024, 2048]);  // global centre: 512 px (78 m) would not beat WAC
 assert.deepEqual(proj.nacSteps(45_000, 2048, 100), [2048]);        // 240 m patch: only the full size helps
 assert.deepEqual(proj.nacSteps(175_000, 2048, 100), []);           // global patch: NAC would be coarser than WAC
});

await test("large images split into whole tiles that stitch exactly, north row first", async () => {
 const one = proj.tileBoxes(7000, 280, 2048);
 assert.deepEqual(one, [{x: 0, y: 0, px: 280, bbox: [-7000, -7000, 7000, 7000]}]);
 const four = proj.tileBoxes(175_000, 4096, 2048);
 assert.equal(four.length, 4);
 assert.deepEqual(four[0], {x: 0, y: 0, px: 2048, bbox: [-175_000, 0, 0, 175_000]});        // north-west first
 assert.deepEqual(four[3], {x: 2048, y: 2048, px: 2048, bbox: [0, -175_000, 175_000, 0]});  // south-east last
 const sixteen = proj.tileBoxes(2000, 2048, 512);
 assert.equal(sixteen.length, 16);
 // Same metres per pixel in every tile as in the whole image, and tiles cover the square exactly once.
 let area = 0;
 for (const t of sixteen) { assert.equal((t.bbox[2] - t.bbox[0]) / t.px, 4000 / 2048); area += (t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1]); }
 assert.ok(Math.abs(area - 4000 ** 2) < 1e-6);
 assert.throws(() => proj.tileBoxes(2000, 1000, 384), /whole tiles/);
});

// ---------------------------------------------------------------- Site view terrain surface
function grid(half, size, fn) {
 const heights = new Float32Array(size * size), step = 2 * half / (size - 1);
 for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) heights[r * size + c] = fn(-half + c * step, -half + r * step);
 return {half, size, heights};
}
const relief = (x, z) => 300 * Math.sin(x / 900) * Math.cos(z / 700) + 0.02 * x;

await test("merged surface is one non-overlapping, gap-free, upward-facing mesh", async () => {
 const outer = grid(7000, 129, relief), inner = grid(2000, 65, (x, z) => relief(x, z) + 5 * Math.sin(x / 40));
 const s = surf.buildSurface(outer, inner, 0, 240), n = s.columns, {axis} = surf.mergedAxis(outer, inner);
 for (let i = 1; i < axis.length; i++) assert.ok(axis[i] > axis[i - 1], "axis strictly increasing");
 assert.ok(s.index.every(i => i < n * n));
 // Sum of signed triangle areas in the horizontal plane equals the patch area: no overlap, no holes.
 let area = 0, flipped = 0;
 for (let t = 0; t < s.index.length; t += 3) {
  const [a, b, c] = [s.index[t], s.index[t + 1], s.index[t + 2]].map(i => [axis[i % n], axis[Math.floor(i / n)]]);
  const cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  // x east, z south: counter-clockwise seen from above has a negative x–z cross product.
  area += -cross / 2; if (cross > 0) flipped++;
 }
 assert.equal(flipped, 0);
 assert.ok(Math.abs(area - 14_000 ** 2) < 1, `area ${area}`);
 // Inner group triangles all lie in the centre square; the rest outside or on its edge.
 for (let t = 0; t < s.innerCount * 3; t++) {
  const i = s.index[t]; assert.ok(Math.abs(axis[i % n]) <= 2000 + 1e-6 && Math.abs(axis[Math.floor(i / n)]) <= 2000 + 1e-6);
 }
 assert.equal(s.innerCount, 64 * 64 * 2);
});

await test("heights: fine data in the centre, wide data outside, blended band with no step at the edge", async () => {
 // A plane is reproduced exactly by bilinear interpolation on both grids, isolating the blend itself.
 const plane = (x, z) => 100 + 0.02 * x - 0.01 * z;
 const outer = grid(7000, 129, plane), inner = grid(2000, 65, (x, z) => plane(x, z) + 8);
 const blend = 240, h = (x, z) => surf.surfaceHeight(outer, inner, x, z, blend);
 assert.ok(Math.abs(h(0, 0) - (plane(0, 0) + 8)) < 1e-3, "centre uses the fine grid");
 assert.ok(Math.abs(h(1700, -300) - (plane(1700, -300) + 8)) < 1e-3, "inside the band's inner edge: fine grid");
 assert.ok(Math.abs(h(2000, 500) - surf.gridHeight(outer, 2000, 500)) < 1e-3, "edge equals the wide grid");
 assert.ok(Math.abs(h(4000, 4000) - surf.gridHeight(outer, 4000, 4000)) < 1e-6);
 // Walking outward across the band, the 8 m offset fades out monotonically.
 let last = Infinity;
 for (let x = 1700; x <= 2000; x += 10) { const d = h(x, 0) - surf.gridHeight(outer, x, 0); assert.ok(d <= last + 1e-6); last = d; }
});

await test("texture coordinates are orthographic and match the WMS/bundled image convention", async () => {
 const outer = grid(175_000, 33, () => 0), inner = grid(20_000, 17, () => 0);
 const s = surf.buildSurface(outer, inner, 0, 0), n = s.columns, {axis} = surf.mergedAxis(outer, inner);
 for (const [c, r] of [[0, 0], [n - 1, 0], [Math.floor(n / 2), Math.floor(n / 2)], [3, n - 5]]) {
  const x = axis[c], z = axis[r], i = r * n + c, norm = Math.hypot(R, x, z);
  assert.ok(Math.abs(s.uv[i * 2] - (0.5 + R * x / norm / 350_000)) < 1e-6);
  assert.ok(Math.abs(s.uv[i * 2 + 1] - (0.5 - R * z / norm / 350_000)) < 1e-6, "v grows northward (z is south)");
 }
 // At the patch corner the orthographic coordinate differs from the gnomonic one by ~1%, i.e. ~1.8 km here.
 assert.ok(s.uv[0] > 0.0049 && s.uv[0] < 0.006);
 const centre = Math.floor(n / 2) * n + Math.floor(n / 2);
 assert.ok(Math.abs(s.uv1[centre * 2] - 0.5) < 1e-9 && Math.abs(s.uv1[centre * 2 + 1] - 0.5) < 1e-9);
 for (let t = 0; t < s.innerCount * 3; t++) { const i = s.index[t]; assert.ok(s.uv1[i * 2] >= 0 && s.uv1[i * 2] <= 1 && s.uv1[i * 2 + 1] >= 0 && s.uv1[i * 2 + 1] <= 1); }
});

await test("site centre sits at y = 0 and positions follow the curved surface", async () => {
 const outer = grid(7000, 65, () => 1234);
 const s = surf.buildSurface(outer, undefined, 1234, 0), n = s.columns, mid = (n - 1) / 2;
 assert.ok(Math.abs(s.positions[(mid * n + mid) * 3 + 1]) < 1e-6);
 const cornerY = s.positions[1], drop = (R + 1234) * (1 - R / Math.hypot(R, 7000, 7000));
 assert.ok(Math.abs(cornerY + drop) < 0.01, "corner drops with lunar curvature");
});

console.log(`${passed} site view tests passed`);
