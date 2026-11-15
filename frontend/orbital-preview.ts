/** Hypothetical circular orbit around the Moon, not an operational ephemeris. Every aspect below can be changed:
 * altitude (orbital radius), the strength of gravity, the orbital speed that follows from them, the direction the
 * ground track runs in, and which way the antenna faces (pitch and roll) with how wide its beam is.
 * GM source: https://ssd.jpl.nasa.gov/astro_par.html, converted to m³/s².
 * The default is the original preview: polar orbit, 500 km, real lunar gravity, antenna straight down at the Moon,
 * passing over the area at t=0, with uniform approximate lunar rotation.
 */
export const MOON_RADIUS = 1_737_400;
export const ORBIT_ALTITUDE = 500_000;   // default altitude in metres
export const ELEVATION_MASK = 10;        // default mask in degrees
const MU = 4902.800118e9, ROTATION_PERIOD = 27.321661 * 86400;

export interface OrbitSettings {
 altitudeKm: number;   // height above the mean lunar radius
 gravity: number;      // multiple of real lunar gravity (1 = the Moon); a what-if
 trackDeg: number;     // ground-track heading at the area: 0 = polar (north-south), 90 = east-west
 pitchDeg: number;     // antenna tilt along the flight direction (+ = forward)
 rollDeg: number;      // antenna tilt across the flight direction (+ = to the satellite's left)
 beamDeg: number;      // antenna half-beam width around its pointing direction; 180 = not limited
 maskDeg: number;      // minimum elevation of the satellite above a node's horizon
}
export const DEFAULT_ORBIT: Readonly<OrbitSettings> = {altitudeKm: ORBIT_ALTITUDE / 1000, gravity: 1, trackDeg: 0, pitchDeg: 0, rollDeg: 0, beamDeg: 180, maskDeg: ELEVATION_MASK};
export const ORBIT_LIMITS: Record<keyof OrbitSettings, [number, number]> = {
 altitudeKm: [50, 10000], gravity: [0.1, 5], trackDeg: [0, 90], pitchDeg: [-90, 90], rollDeg: [-90, 90], beamDeg: [5, 180], maskDeg: [0, 45],
};
let orbit: OrbitSettings = {...DEFAULT_ORBIT};
const clamp = (v: number, [lo, hi]: [number, number]) => Math.min(hi, Math.max(lo, Number.isFinite(v) ? v : lo));

export const getOrbit = (): OrbitSettings => ({...orbit});
export function setOrbit(change: Partial<OrbitSettings>): OrbitSettings {
 for (const key of Object.keys(change) as (keyof OrbitSettings)[]) orbit[key] = clamp(change[key] as number, ORBIT_LIMITS[key]);
 return getOrbit();
}
export const resetOrbit = () => { orbit = {...DEFAULT_ORBIT}; return getOrbit(); };

const mu = () => MU * orbit.gravity;
export const orbitRadius = () => MOON_RADIUS + orbit.altitudeKm * 1000;
/** Seconds for one revolution, and speed in m/s, of a circular orbit: T = 2π√(r³/μ), v = √(μ/r). */
export const orbitPeriod = () => 2 * Math.PI * Math.sqrt(orbitRadius() ** 3 / mu());
export const orbitSpeed = () => Math.sqrt(mu() / orbitRadius());
/** Gravitational acceleration at the satellite's altitude and on the surface, m/s². */
export const gravityAtOrbit = () => mu() / orbitRadius() ** 2;
export const surfaceGravity = () => mu() / MOON_RADIUS ** 2;
/** Altitude in km that gives a circular orbit with this speed (km/s) under the current gravity, kept inside the allowed range. */
export function altitudeForSpeed(kmPerSecond: number) {
 const r = mu() / (kmPerSecond * 1000) ** 2;
 return clamp((r - MOON_RADIUS) / 1000, ORBIT_LIMITS.altitudeKm);
}
/** The orbit's inclination to the equator where it crosses the area, from the ground-track heading and latitude. */
export function inclinationDeg(lat: number) {
 const h = rad(orbit.trackDeg);
 return Math.acos(Math.max(-1, Math.min(1, Math.sin(h) * Math.cos(rad(lat))))) * 180 / Math.PI;
}
/** Kept for callers that only want the default preview values. */
export const ORBIT_PERIOD = 2 * Math.PI * Math.sqrt((MOON_RADIUS + ORBIT_ALTITUDE) ** 3 / MU);

export type Vector = [number, number, number];
const rad = (v: number) => v * Math.PI / 180;
const dot = (a: Vector, b: Vector) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vector, b: Vector): Vector => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a: Vector): Vector => { const l = Math.hypot(...a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
const turn = (a: Vector, angle: number): Vector => [a[0] * Math.cos(angle) + a[1] * Math.sin(angle), -a[0] * Math.sin(angle) + a[1] * Math.cos(angle), a[2]];

export function surfacePosition(lat: number, lon: number, height = 0): Vector {
 const p = rad(lat), l = rad(lon), r = MOON_RADIUS + height;
 return [r * Math.cos(p) * Math.cos(l), r * Math.cos(p) * Math.sin(l), r * Math.sin(p)];
}

export interface SatelliteState {
 position: Vector;
 radial: Vector;      // straight up from the Moon
 tangent: Vector;     // flight direction
 side: Vector;        // to the satellite's left
 boresight: Vector;   // where the antenna points
}
/** Position and orientation `seconds` after the satellite passes overhead of (lat, lon). */
export function satelliteState(lat: number, lon: number, seconds: number): SatelliteState {
 const phi = rad(lat), lam = rad(lon), h = rad(orbit.trackDeg);
 const p0: Vector = [Math.cos(phi) * Math.cos(lam), Math.cos(phi) * Math.sin(lam), Math.sin(phi)];
 const north: Vector = [-Math.sin(phi) * Math.cos(lam), -Math.sin(phi) * Math.sin(lam), Math.cos(phi)];
 const east: Vector = [-Math.sin(lam), Math.cos(lam), 0];
 const heading: Vector = [0, 1, 2].map(i => Math.cos(h) * north[i] + Math.sin(h) * east[i]) as Vector;
 const u = seconds * 2 * Math.PI / orbitPeriod(), spin = seconds * 2 * Math.PI / ROTATION_PERIOD;   // the Moon turns under the orbit
 const place = (a: number): Vector => [0, 1, 2].map(i => Math.cos(a) * p0[i] + Math.sin(a) * heading[i]) as Vector;
 const radial = turn(place(u), spin);
 const tangent = turn([0, 1, 2].map(i => -Math.sin(u) * p0[i] + Math.cos(u) * heading[i]) as Vector, spin);
 const down: Vector = [-radial[0], -radial[1], -radial[2]];
 const side = cross(tangent, down);
 const pitch = rad(orbit.pitchDeg), roll = rad(orbit.rollDeg);
 const boresight = unit([0, 1, 2].map(i => tangent[i] * Math.sin(pitch) + Math.cos(pitch) * (down[i] * Math.cos(roll) + side[i] * Math.sin(roll))) as Vector);
 const R = orbitRadius();
 return {position: [radial[0] * R, radial[1] * R, radial[2] * R], radial, tangent, side, boresight};
}
export function satellitePosition(lat: number, lon: number, seconds: number): Vector {
 return satelliteState(lat, lon, seconds).position;
}
/** Where the antenna axis meets the Moon, or null when it points past the limb. */
export function boresightHit(state: SatelliteState): Vector | null {
 const o = state.position, d = state.boresight, b = dot(o, d), c = dot(o, o) - MOON_RADIUS ** 2, disc = b * b - c;
 if (disc < 0) return null;
 const t = -b - Math.sqrt(disc);
 return t > 0 ? [o[0] + d[0] * t, o[1] + d[1] * t, o[2] + d[2] * t] : null;
}
/** Geometric access from a surface node to the satellite: above the elevation mask and, when a beam limit is set, inside the beam. */
export function satelliteAccess(node: {lat: number; lon: number; elevation: number}, satellite: Vector, boresight?: Vector) {
 const at = surfacePosition(node.lat, node.lon, node.elevation + 10);
 const up = surfacePosition(node.lat, node.lon).map(v => v / MOON_RADIUS);
 const delta = satellite.map((v, i) => v - at[i]) as Vector;
 const rangeM = Math.hypot(...delta);
 const elevationDeg = Math.asin(Math.max(-1, Math.min(1, delta.reduce((sum, v, i) => sum + v * up[i], 0) / rangeM))) * 180 / Math.PI;
 // Angle between the antenna axis and the direction from the satellite to the node.
 const offAxisDeg = boresight ? Math.acos(Math.max(-1, Math.min(1, -dot(delta, boresight) / rangeM))) * 180 / Math.PI : 0;
 const inBeam = !boresight || orbit.beamDeg >= 180 || offAxisDeg <= orbit.beamDeg;
 return {rangeM, elevationDeg, offAxisDeg, inBeam, visible: elevationDeg >= orbit.maskDeg && inBeam};
}
