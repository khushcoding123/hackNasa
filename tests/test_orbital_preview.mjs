import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {MOON_RADIUS, ORBIT_ALTITUDE, ORBIT_PERIOD, satellitePosition, satelliteAccess} = await import(pathToFileURL(process.argv[2]));
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
console.log('Orbital preview: overhead, occultation, range, polar and longitude seam checks passed');
