/** Pure geometry and resampling for the Site view. No DOM, Three.js or Cesium, so it runs under the Node tests.
 *
 * Two local square frames share one centre (lat0, lon0):
 *  - the terrain grid from /api/terrain/site is GNOMONIC: sample (x, z) lies on the ray up + (x·east − z·north)/R;
 *  - imagery is ORTHOGRAPHIC: pixel (u, v) is the point whose east/north components on the sphere are (u, v).
 * A vertex's texture coordinate is therefore its orthographic coordinate, R·x/|ray|, not its gnomonic one. */

export const MOON_R = 1_737_400;

/** WMS spatial reference for a local orthographic image. Lunaserv evaluates AUTO:42003 on Earth's radius (a 3.67×
 * zoom-in on the Moon), so the lunar IAU2000 orthographic code is required for imagery to match the terrain. */
export const orthoSrs = (lat: number, lon: number) => `IAU2000:30166,9001,${lon},${lat}`;

type V3 = [number, number, number];
export interface Frame { up: V3; east: V3; north: V3 }
export function frameAt(latDeg: number, lonDeg: number): Frame {
 const p = latDeg * Math.PI / 180, l = lonDeg * Math.PI / 180;
 return {
  up: [Math.cos(p) * Math.cos(l), Math.cos(p) * Math.sin(l), Math.sin(p)],
  east: [-Math.sin(l), Math.cos(l), 0],
  north: [-Math.sin(p) * Math.cos(l), -Math.sin(p) * Math.sin(l), Math.cos(p)],
 };
}

/** Orthographic east/north metres → [lat, lon] in radians, or undefined beyond the visible hemisphere. */
export function orthoToLatLon(f: Frame, eastM: number, northM: number): [number, number] | undefined {
 const a = eastM / MOON_R, b = northM / MOON_R, c2 = 1 - a * a - b * b;
 if (c2 < 0) return undefined;
 const c = Math.sqrt(c2);
 const x = c * f.up[0] + a * f.east[0] + b * f.north[0];
 const y = c * f.up[1] + a * f.east[1] + b * f.north[1];
 const z = c * f.up[2] + a * f.east[2] + b * f.north[2];
 return [Math.asin(Math.max(-1, Math.min(1, z))), Math.atan2(y, x)];
}

/** Orthographic coordinates (metres east, north) of the gnomonic terrain sample at scene (x, z), z positive south. */
export function gnomonicToOrtho(x: number, z: number): [number, number] {
 const norm = Math.hypot(MOON_R, x, z);
 return [MOON_R * x / norm, -MOON_R * z / norm];
}

export interface Raster { width: number; height: number; data: Uint8ClampedArray | Uint8Array }

/** Bilinear sample of a global equirectangular RGBA raster (x = 0 at 180°W, row 0 at 90°N, pixel-centred).
 * Longitude wraps; a row beyond a pole continues on the opposite meridian, so the poles have no seam. */
export function sampleEquirect(src: Raster, latRad: number, lonRad: number, out: number[] | Float32Array, offset = 0) {
 const {width: w, height: h, data} = src;
 const fx = (lonRad / (2 * Math.PI) + 0.5) * w - 0.5, fy = (0.5 - latRad / Math.PI) * h - 0.5;
 const x0 = Math.floor(fx), y0 = Math.floor(fy), tx = fx - x0, ty = fy - y0;
 const at = (row: number, col: number) => {
  if (row < 0) { row = -1 - row; col += w / 2; } else if (row >= h) { row = 2 * h - 1 - row; col += w / 2; }
  col = ((Math.round(col) % w) + w) % w;
  return (row * w + col) * 4;
 };
 const a = at(y0, x0), b = at(y0, x0 + 1), c = at(y0 + 1, x0), d = at(y0 + 1, x0 + 1);
 for (let k = 0; k < 4; k++) {
  const top = data[a + k] + (data[b + k] - data[a + k]) * tx, bottom = data[c + k] + (data[d + k] - data[c + k]) * tx;
  out[offset + k] = top + (bottom - top) * ty;
 }
}

/** Resample a global equirectangular raster onto the local orthographic square [−half, half]² (row 0 = north),
 * with the same pixel-edge convention as a WMS BBOX. */
export function reprojectEquirect(src: Raster, latDeg: number, lonDeg: number, half: number, size: number): Uint8ClampedArray<ArrayBuffer> {
 const f = frameAt(latDeg, lonDeg), out = new Uint8ClampedArray(size * size * 4), px = [0, 0, 0, 0];
 for (let row = 0; row < size; row++) {
  const north = (1 - (row + 0.5) / size * 2) * half;
  for (let col = 0; col < size; col++) {
   const east = ((col + 0.5) / size * 2 - 1) * half, i = (row * size + col) * 4;
   const ll = orthoToLatLon(f, east, north);
   if (!ll) continue;
   sampleEquirect(src, ll[0], ll[1], px);
   out[i] = px[0]; out[i + 1] = px[1]; out[i + 2] = px[2]; out[i + 3] = 255;
  }
 }
 return out;
}

/** Image size (pixels) for a square of half-width `half`: about two pixels per source pixel, so the server's
 * nearest-neighbour resampling does not alias, within [minSize, maxSize]. Upsampling beyond that adds no detail. */
export function requestSize(half: number, sourceM: number, maxSize: number, minSize = 256): number {
 return Math.max(minSize, Math.min(maxSize, Math.ceil(2 * half / (sourceM / 2))));
}

const metres = (m: number) => m >= 1000 ? `${(m / 1000).toFixed(1)} km` : m >= 10 ? `${Math.round(m)} m` : `${m.toFixed(1)} m`;
/** What the eye actually gets: the coarser of the source product and the texture pixel. Never claims finer than the source. */
export function imageryLabel(name: string, sourceM: number, texelM: number): string {
 if (texelM <= sourceM * 1.15) return `${name} · ${metres(sourceM)}/px source (closer zoom is interpolated, not extra detail)`;
 return `${name} · shown at ${metres(texelM)}/px (${metres(sourceM)}/px source)`;
}
