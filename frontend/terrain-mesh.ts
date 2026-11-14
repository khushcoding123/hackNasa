/** Pure (Cesium-free) construction of quantized-mesh terrain tiles from elevation samples.
 * Heights are metres above the lunar reference sphere. Sample grids carry one padding sample on every side
 * so that normals on both sides of a tile edge use identical neighbours, which avoids visible seams. */
export const MOON_RADIUS_M = 1_737_400;
const MAX_SHORT = 32767;

export interface TileSamples {
 /** Interior samples per side (without padding). */
 size: number;
 padding: number;
 /** (size + 2·padding)² metres, row 0 = north, column 0 = west. */
 heights: Float32Array;
 resolutionM: number;
 sources: string[];
}
export interface RectangleRadians { west: number; south: number; east: number; north: number }
export interface TileMesh {
 quantizedVertices: Uint16Array;
 indices: Uint16Array;
 minimumHeight: number;
 maximumHeight: number;
 westIndices: number[];
 southIndices: number[];
 eastIndices: number[];
 northIndices: number[];
 encodedNormals: Uint8Array;
 /** Body-fixed unit normals, xyz per vertex, for tests and bounding volumes. */
 normals: Float32Array;
 /** Body-fixed vertex positions in metres, xyz per vertex. */
 positions: Float64Array;
}

const position = (lat: number, lon: number, height: number, out: number[], offset: number) => {
 const r = MOON_RADIUS_M + height, c = Math.cos(lat);
 out[offset] = r * c * Math.cos(lon); out[offset + 1] = r * c * Math.sin(lon); out[offset + 2] = r * Math.sin(lat);
};

/** Same oct encoding as Cesium's AttributeCompression.octEncodeInRange(v, 255). */
export function octEncode(x: number, y: number, z: number): [number, number] {
 const norm = Math.abs(x) + Math.abs(y) + Math.abs(z);
 let a = x / norm, b = y / norm;
 if (z < 0) {
  const ax = a, by = b;
  a = (1 - Math.abs(by)) * (ax < 0 ? -1 : 1);
  b = (1 - Math.abs(ax)) * (by < 0 ? -1 : 1);
 }
 const snorm = (v: number) => Math.round((Math.min(1, Math.max(-1, v)) * 0.5 + 0.5) * 255);
 return [snorm(a), snorm(b)];
}
export function octDecode(a: number, b: number): [number, number, number] {
 let x = a / 255 * 2 - 1, y = b / 255 * 2 - 1;
 const z = 1 - Math.abs(x) - Math.abs(y);
 if (z < 0) { const ox = x; x = (1 - Math.abs(y)) * (ox < 0 ? -1 : 1); y = (1 - Math.abs(ox)) * (y < 0 ? -1 : 1); }
 const length = Math.hypot(x, y, z);
 return [x / length, y / length, z / length];
}

/** Longitude/latitude (radians) of interior sample (row, column); padding extends beyond the rectangle. */
export const sampleLatLon = (rect: RectangleRadians, size: number, row: number, column: number): [number, number] => [
 rect.north - row * (rect.north - rect.south) / (size - 1),
 rect.west + column * (rect.east - rect.west) / (size - 1),
];

export function buildTileMesh(samples: TileSamples, rect: RectangleRadians): TileMesh {
 const {size, padding, heights} = samples, stride = size + 2 * padding;
 if (heights.length !== stride * stride) throw new Error("Terrain tile has an unexpected sample count.");
 if (size < 2 || size * size > 65535) throw new Error("Unsupported terrain tile size.");
 const at = (row: number, column: number) => heights[(row + padding) * stride + column + padding];
 let minimumHeight = Infinity, maximumHeight = -Infinity;
 for (let row = 0; row < size; row++) for (let column = 0; column < size; column++) {
  const h = at(row, column);
  if (!Number.isFinite(h)) throw new Error("Terrain tile contains missing elevation.");
  if (h < minimumHeight) minimumHeight = h;
  if (h > maximumHeight) maximumHeight = h;
 }
 const count = size * size, range = maximumHeight - minimumHeight;
 const quantized = new Uint16Array(count * 3), encodedNormals = new Uint8Array(count * 2);
 const normals = new Float32Array(count * 3), positions = new Float64Array(count * 3), scratch = [0, 0, 0];
 const p = (row: number, column: number, into: number[]) => { // padded neighbours use the true (unfolded) lat/lon
  const [lat, lon] = sampleLatLon(rect, size, row, column);
  position(lat, lon, at(row, column), into, 0);
  return into;
 };
 const east: number[] = [0, 0, 0], west: number[] = [0, 0, 0], north: number[] = [0, 0, 0], south: number[] = [0, 0, 0];
 for (let row = 0; row < size; row++) for (let column = 0; column < size; column++) {
  const i = row * size + column, h = at(row, column);
  quantized[i] = Math.round(column / (size - 1) * MAX_SHORT);
  quantized[count + i] = Math.round((1 - row / (size - 1)) * MAX_SHORT);
  quantized[2 * count + i] = range > 0 ? Math.round((h - minimumHeight) / range * MAX_SHORT) : 0;
  const [lat, lon] = sampleLatLon(rect, size, row, column);
  position(lat, lon, h, scratch, 0);
  positions.set(scratch, i * 3);
  // Central differences use the padded ring at tile edges, so adjacent tiles agree on edge normals.
  p(row, column + 1, east); p(row, column - 1, west); p(row - 1, column, north); p(row + 1, column, south);
  const ex = east[0] - west[0], ey = east[1] - west[1], ez = east[2] - west[2];
  const nx = north[0] - south[0], ny = north[1] - south[1], nz = north[2] - south[2];
  let x = ey * nz - ez * ny, y = ez * nx - ex * nz, z = ex * ny - ey * nx, length = Math.hypot(x, y, z);
  // Degenerate at the exact pole (all longitudes coincide): fall back to the radial direction.
  if (length < 1e-6) { x = scratch[0]; y = scratch[1]; z = scratch[2]; length = Math.hypot(x, y, z); }
  x /= length; y /= length; z /= length;
  if (x * scratch[0] + y * scratch[1] + z * scratch[2] < 0) { x = -x; y = -y; z = -z; }
  normals[i * 3] = x; normals[i * 3 + 1] = y; normals[i * 3 + 2] = z;
  const [a, b] = octEncode(x, y, z);
  encodedNormals[i * 2] = a; encodedNormals[i * 2 + 1] = b;
 }
 const indices = new Uint16Array((size - 1) * (size - 1) * 6);
 let k = 0;
 for (let row = 0; row < size - 1; row++) for (let column = 0; column < size - 1; column++) {
  const nw = row * size + column, ne = nw + 1, sw = nw + size, se = sw + 1;
  indices[k++] = nw; indices[k++] = sw; indices[k++] = ne;   // counter-clockwise seen from above
  indices[k++] = ne; indices[k++] = sw; indices[k++] = se;
 }
 const line = (index: (n: number) => number) => Array.from({length: size}, (_, n) => index(n));
 return {
  quantizedVertices: quantized, indices, minimumHeight, maximumHeight, encodedNormals, normals, positions,
  westIndices: line(n => n * size), eastIndices: line(n => n * size + size - 1),
  northIndices: line(n => n), southIndices: line(n => (size - 1) * size + n),
 };
}

/** Deepest level that adds detail: sample spacing of a tile at `level` must be no finer than the source. */
export const tileSpacingMetres = (level: number, size: number) => Math.PI * MOON_RADIUS_M / (size - 1) / 2 ** level;

export interface RegionExtent { x_min: number; x_max: number; y_min: number; y_max: number; pixel_m: number }
export interface TerrainRegion { id: string; name: string; lat: number; lon: number; near: RegionExtent | null; far: RegionExtent | null }

/** South polar stereographic, identical to lunar_core.terrain.polar_stereographic_south. */
export function polarStereographicSouth(latDeg: number, lonDeg: number): [number, number] {
 const rho = 2 * MOON_RADIUS_M * Math.tan((90 + latDeg) * Math.PI / 360), lon = lonDeg * Math.PI / 180;
 return [rho * Math.sin(lon), rho * Math.cos(lon)];
}

/** True when any part of the rectangle (degrees) can be covered by the extent. Conservative: samples a 9×9 grid
 * and also tests whether the extent's corners/centre fall inside the tile (handles tiles larger than the extent). */
export function rectangleTouchesExtent(rectDeg: {west: number; south: number; east: number; north: number}, extent: RegionExtent, centre: [number, number]): boolean {
 if (rectDeg.south > -60) return false;
 const inside = (lat: number, lon: number) => {
  if (lat > -60) return false;
  const [x, y] = polarStereographicSouth(lat, lon);
  return x >= extent.x_min && x <= extent.x_max && y >= extent.y_min && y <= extent.y_max;
 };
 for (let i = 0; i <= 8; i++) for (let j = 0; j <= 8; j++) {
  if (inside(rectDeg.south + (rectDeg.north - rectDeg.south) * i / 8, rectDeg.west + (rectDeg.east - rectDeg.west) * j / 8)) return true;
 }
 const [lat, lon] = [centre[0], centre[1] > 180 ? centre[1] - 360 : centre[1]];
 return lat >= rectDeg.south && lat <= rectDeg.north && lon >= rectDeg.west && lon <= rectDeg.east;
}
