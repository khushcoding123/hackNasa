/** Hypothetical circular polar orbit, not an operational ephemeris.
 * GM source: https://ssd.jpl.nasa.gov/astro_par.html, converted to m³/s².
 * Passes over the area at t=0, with uniform approximate lunar rotation.
 */
export const MOON_RADIUS = 1_737_400;
export const ORBIT_ALTITUDE = 500_000;
export const ELEVATION_MASK = 10;
const MU = 4902.800118e9, ROTATION_PERIOD = 27.321661 * 86400;
const RADIUS = MOON_RADIUS + ORBIT_ALTITUDE;
export const ORBIT_PERIOD = 2 * Math.PI * Math.sqrt(RADIUS ** 3 / MU);
type Vector = [number, number, number];
const rad = (v: number) => v * Math.PI / 180;
export function surfacePosition(lat: number, lon: number, height = 0): Vector {
 const p = rad(lat), l = rad(lon), r = MOON_RADIUS + height;
 return [r * Math.cos(p) * Math.cos(l), r * Math.cos(p) * Math.sin(l), r * Math.sin(p)];
}
export function satellitePosition(lat: number, lon: number, seconds: number): Vector {
 const u = rad(lat) + seconds * 2 * Math.PI / ORBIT_PERIOD;
 const l = rad(lon) - seconds * 2 * Math.PI / ROTATION_PERIOD;
 return [RADIUS * Math.cos(u) * Math.cos(l), RADIUS * Math.cos(u) * Math.sin(l), RADIUS * Math.sin(u)];
}
export function satelliteAccess(node: {lat: number; lon: number; elevation: number}, satellite: Vector) {
 const at = surfacePosition(node.lat, node.lon, node.elevation + 10);
 const up = surfacePosition(node.lat, node.lon).map(v => v / MOON_RADIUS);
 const delta = satellite.map((v, i) => v - at[i]);
 const rangeM = Math.hypot(...delta);
 const elevationDeg = Math.asin(Math.max(-1, Math.min(1, delta.reduce((sum, v, i) => sum + v * up[i], 0) / rangeM))) * 180 / Math.PI;
 return {rangeM, elevationDeg, visible: elevationDeg >= ELEVATION_MASK};
}
