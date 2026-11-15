import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const O = await import(pathToFileURL(process.argv[2]));
const {MOON_RADIUS, ORBIT_ALTITUDE, ORBIT_PERIOD, satellitePosition, satelliteAccess} = O;
assert.ok(ORBIT_PERIOD > 9000 && ORBIT_PERIOD < 10000, '500 km lunar orbit has a physical period near 158 minutes');
for (const [lat, lon] of [[0,23],[-89,120],[90,0],[45,180],[45,-180]]) {
 const node = {lat, lon, elevation: 0};
 const overhead = satellitePosition(lat,lon,0);
 assert.ok(Math.abs(Math.hypot(...overhead) - MOON_RADIUS - ORBIT_ALTITUDE) < 1e-6);
 const start = satelliteAccess(node, overhead);
 assert.ok(start.visible && Math.abs(start.elevationDeg - 90) < 1e-5);
 assert.ok(Math.abs(start.rangeM - (ORBIT_ALTITUDE - 10)) < 1e-5);
 const opposite = satelliteAccess(node, satellitePosition(lat,lon,ORBIT_PERIOD/2));
 assert.ok(!opposite.visible && opposite.elevationDeg < -80, 'Moon blocks the far half of the pass');
 const horizon = satelliteAccess(node, satellitePosition(lat,lon,ORBIT_PERIOD/4));
 assert.ok(!horizon.visible, 'quarter-orbit satellite is below the surface horizon');
}
const east=satellitePosition(45,180,0), west=satellitePosition(45,-180,0);
assert.ok(east.every((v,i)=>Math.abs(v-west[i])<1e-6));

// Orbit parameters. The default must still be the original preview.
const dot = (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
assert.ok(Math.abs(O.orbitPeriod() - ORBIT_PERIOD) < 1e-9, 'default period unchanged');
assert.ok(Math.abs(O.orbitSpeed() - 1480) < 5, 'a 500 km lunar orbit moves at about 1.48 km/s');
O.setOrbit({gravity: 4});
assert.ok(Math.abs(O.orbitPeriod() - ORBIT_PERIOD / 2) < 1e-6, 'four times the pull halves the period');
assert.ok(Math.abs(O.orbitSpeed() - 2 * Math.sqrt(4902.800118e9 / (MOON_RADIUS + ORBIT_ALTITUDE))) < 1e-6, 'and doubles the speed');
O.resetOrbit();
O.setOrbit({altitudeKm: 1500});
assert.ok(O.orbitPeriod() > ORBIT_PERIOD && O.orbitSpeed() < 1480, 'a higher orbit is slower with a longer period');
assert.ok(Math.abs(Math.hypot(...satellitePosition(-86, 10, 1234)) - (MOON_RADIUS + 1_500_000)) < 1e-6, 'radius follows the altitude');
const v = O.orbitSpeed() / 1000;
assert.ok(Math.abs(O.altitudeForSpeed(v) - 1500) < 1e-6, 'speed and altitude convert both ways');
assert.equal(O.setOrbit({altitudeKm: 1e9}).altitudeKm, 10000, 'altitude is clamped');
O.resetOrbit();
// Track heading: any heading still passes straight overhead at t=0 and keeps the radius.
for (const heading of [0, 30, 90]) {
 O.setOrbit({trackDeg: heading});
 const node = {lat: -87, lon: 40, elevation: 0};
 const pass = satelliteAccess(node, satellitePosition(-87, 40, 0));
 assert.ok(Math.abs(pass.elevationDeg - 90) < 1e-4, 'overhead at t=0 for heading ' + heading);
 assert.ok(Math.abs(Math.hypot(...satellitePosition(-87, 40, 700)) - MOON_RADIUS - ORBIT_ALTITUDE) < 1e-6);
}
assert.ok(Math.abs(O.inclinationDeg(0) - 0) < 1e-9, 'east-west at the equator is an equatorial orbit');
O.resetOrbit();
assert.ok(Math.abs(O.inclinationDeg(-87) - 90) < 1e-9, 'polar heading is a 90 degree inclination');
// Facing: straight down by default; pitch and roll swing the antenna axis.
let st = O.satelliteState(-87, 40, 500);
assert.ok(dot(st.boresight, st.radial) < -1 + 1e-9, 'default antenna looks straight down');
O.setOrbit({pitchDeg: 90});
st = O.satelliteState(-87, 40, 500);
assert.ok(dot(st.boresight, st.tangent) > 1 - 1e-9, 'pitch 90 points along the flight direction');
assert.equal(O.boresightHit(st), null, 'and then misses the Moon');
O.resetOrbit(); O.setOrbit({rollDeg: 20});
st = O.satelliteState(-87, 40, 500);
assert.ok(Math.abs(Math.acos(-dot(st.boresight, st.radial)) * 180 / Math.PI - 20) < 1e-6, 'roll 20 tilts the axis 20 degrees off nadir');
// A narrow beam drops a node that sits outside it while the satellite is still above its horizon.
O.resetOrbit();
st = O.satelliteState(-87, 40, 0);
const off = {lat: -87 + 6, lon: 40, elevation: 0};
const wide = satelliteAccess(off, st.position, st.boresight);
assert.ok(wide.visible && wide.offAxisDeg > 0);
O.setOrbit({beamDeg: 5});
const narrow = satelliteAccess(off, st.position, st.boresight);
assert.ok(!narrow.visible && !narrow.inBeam && narrow.elevationDeg >= 10, 'outside the beam, not below the mask');
O.resetOrbit();
// Terrain horizon: two nodes see the same low satellite; the one with a ridge in that direction loses it first.
{
 const lat = -86.8, lon = 337.2, node = {lat, lon, elevation: 0};
 let t = 0; while (satelliteAccess(node, satellitePosition(lat, lon, t)).elevationDeg > 14) t += 10;   // the satellite sinks toward 14°
 const low = satelliteAccess(node, satellitePosition(lat, lon, t));
 assert.ok(low.visible && low.aboveTerrain && low.azimuthDeg >= 0 && low.azimuthDeg < 360, 'no horizon given: only the mask applies');
 const ridge = satelliteAccess(node, satellitePosition(lat, lon, t), undefined, az => Math.abs(((az - low.azimuthDeg + 540) % 360) - 180) < 20 ? 20 : 0);
 assert.ok(!ridge.visible && ridge.aboveMask && !ridge.aboveTerrain && ridge.horizonDeg === 20, 'a 20° ridge toward the satellite blocks it while it is above the 10° mask');
 const behind = satelliteAccess(node, satellitePosition(lat, lon, t), undefined, az => Math.abs(((az - low.azimuthDeg + 540) % 360) - 180) > 160 ? 20 : 0);
 assert.ok(behind.visible, 'a ridge on the opposite side does not block it');
}
console.log('Orbital preview: overhead, occultation, range, polar and longitude seam checks passed; orbit radius, gravity, speed, heading, pointing, beam and terrain-horizon checks passed');
