import {
 BoundingSphere, Cartesian3, Cartographic, Credit, Ellipsoid, EllipsoidalOccluder, Event, GeographicTilingScheme, OrientedBoundingBox, Rectangle,
 QuantizedMeshTerrainData, TerrainProvider, type Request,
} from "cesium";
import {buildTileMesh, polarStereographicSouth, rectangleTouchesExtent, type TerrainRegion, type TileSamples} from "./terrain-mesh.js";

export interface TerrainInfo {
 unit: string; vertical_reference: string; reference_radius_m: number; padding: number; default_size: number;
 sources: Record<"global" | "far" | "near", {id: string; name: string; resolution_m: number; max_level: number}>;
 regions: TerrainRegion[];
}
export interface TileMeta { resolutionM: number; sources: string[]; level: number }

const MOON = Ellipsoid.MOON;
const deg = (r: number) => r * 180 / Math.PI;

/** Cesium terrain provider backed by the read-only /api/terrain/tile endpoint.
 * The server returns elevation samples only; vertex normals and the quantized mesh are built here. */
export class MoonTerrainProvider {
 readonly tilingScheme = new GeographicTilingScheme({ellipsoid: MOON});
 readonly errorEvent = new Event();
 readonly credit = new Credit("LOLA elevation · NASA / MIT", false);
 readonly hasWaterMask = false;
 readonly hasVertexNormals = true;
 readonly availability = undefined;
 readonly tileMeta = new Map<string, TileMeta>();
 failures = 0;
 successes = 0;
 private levelZeroError: number;
 constructor(private info: TerrainInfo, private size = info.default_size) {
  this.levelZeroError = TerrainProvider.getEstimatedLevelZeroGeometricErrorForAHeightmap(MOON, size, this.tilingScheme.getNumberOfXTilesAtLevel(0));
 }
 get maximumLevel() { return this.info.sources.near.max_level; }
 getLevelMaximumGeometricError(level: number) { return this.levelZeroError / 2 ** level; }
 private rectangleDegrees(x: number, y: number, level: number) {
  const r = this.tilingScheme.tileXYToRectangle(x, y, level);
  return {west: deg(r.west), south: deg(r.south), east: deg(r.east), north: deg(r.north)};
 }
 /** Source limits decide how deep each place may refine: no apparent detail beyond the data. */
 getTileDataAvailable(x: number, y: number, level: number): boolean {
  const {sources, regions} = this.info;
  if (level <= sources.global.max_level) return true;
  const rect = this.rectangleDegrees(x, y, level);
  return regions.some(region => {
   const centre: [number, number] = [region.lat, region.lon];
   return (level <= sources.far.max_level && region.far && rectangleTouchesExtent(rect, region.far, centre)) ||
    (level <= sources.near.max_level && region.near && rectangleTouchesExtent(rect, region.near, centre));
  });
 }
 loadTileDataAvailability() { return undefined; }
 /** Mission region whose 20 m terrain crop contains the point, if any. */
 regionAt(latDeg: number, lonDeg: number): TerrainRegion | undefined {
  if (latDeg > -80) return undefined;
  const [x, y] = polarStereographicSouth(latDeg, lonDeg);
  return this.info.regions.find(r => r.near && x >= r.near.x_min && x <= r.near.x_max && y >= r.near.y_min && y <= r.near.y_max);
 }
 /** Resolution (m) of the most detailed loaded tile under a point. */
 resolutionAt(latDeg: number, lonDeg: number): TileMeta | undefined {
  const position = Cartographic.fromDegrees(lonDeg, latDeg);
  for (let level = this.maximumLevel; level >= 0; level--) {
   const tile = this.tilingScheme.positionToTileXY(position, level);
   const meta = tile && this.tileMeta.get(`${level}/${tile.x}/${tile.y}`);
   if (meta) return meta;
  }
  return undefined;
 }
 async requestTileGeometry(x: number, y: number, level: number, request?: Request) {
  const rect = this.tilingScheme.tileXYToRectangle(x, y, level);
  const d = this.rectangleDegrees(x, y, level);
  const url = `/api/terrain/tile?west=${d.west}&south=${d.south}&east=${d.east}&north=${d.north}&size=${this.size}`;
  const controller = new AbortController();
  if (request) request.cancelFunction = () => controller.abort();
  let response: Response;
  try { response = await fetch(url, {signal: controller.signal}); }
  catch (error) { if (!controller.signal.aborted) this.failures++; throw error; }
  if (!response.ok) { this.failures++; throw new Error(`Terrain tile ${level}/${x}/${y} failed (${response.status}).`); }
  const buffer = await response.arrayBuffer();
  const padding = Number(response.headers.get("X-Terrain-Padding") ?? this.info.padding);
  const samples: TileSamples = {
   size: this.size, padding, heights: new Float32Array(buffer),
   resolutionM: Number(response.headers.get("X-Terrain-Resolution-M") ?? NaN),
   sources: (response.headers.get("X-Terrain-Sources") ?? "").split(",").filter(Boolean),
  };
  const mesh = buildTileMesh(samples, {west: rect.west, south: rect.south, east: rect.east, north: rect.north});
  const boundingSphere = BoundingSphere.fromVertices(Array.from(mesh.positions));
  const occluder = new EllipsoidalOccluder(MOON);
  const positions: Cartesian3[] = [];
  for (let i = 0; i < mesh.positions.length; i += 3) positions.push(new Cartesian3(mesh.positions[i], mesh.positions[i + 1], mesh.positions[i + 2]));
  // Large tiles cannot be summarised by one culling point. Cesium then derives one itself (or skips horizon
  // culling), so a placeholder is passed to satisfy the constructor and cleared afterwards.
  const computed = occluder.computeHorizonCullingPointPossiblyUnderEllipsoid(boundingSphere.center, positions, mesh.minimumHeight);
  const horizonOcclusionPoint = computed ?? new Cartesian3(0, 0, 0);
  const next = level + 1, bit = (cx: number, cy: number, mask: number) => this.getTileDataAvailable(cx, cy, next) ? mask : 0;
  const childTileMask = bit(2 * x, 2 * y + 1, 1) | bit(2 * x + 1, 2 * y + 1, 2) | bit(2 * x, 2 * y, 4) | bit(2 * x + 1, 2 * y, 8);
  this.tileMeta.set(`${level}/${x}/${y}`, {resolutionM: samples.resolutionM, sources: samples.sources, level});
  this.successes++;
  const data = new QuantizedMeshTerrainData({
   quantizedVertices: mesh.quantizedVertices, indices: mesh.indices,
   minimumHeight: mesh.minimumHeight, maximumHeight: mesh.maximumHeight,
   boundingSphere, orientedBoundingBox: OrientedBoundingBox.fromRectangle(new Rectangle(rect.west, rect.south, rect.east, rect.north), mesh.minimumHeight, mesh.maximumHeight, MOON), horizonOcclusionPoint, encodedNormals: mesh.encodedNormals,
   westIndices: mesh.westIndices, southIndices: mesh.southIndices, eastIndices: mesh.eastIndices, northIndices: mesh.northIndices,
   westSkirtHeight: skirt(level), southSkirtHeight: skirt(level), eastSkirtHeight: skirt(level), northSkirtHeight: skirt(level),
   childTileMask, credits: [this.credit],
  });
  if (!computed) (data as unknown as {_horizonOcclusionPoint?: Cartesian3})._horizonOcclusionPoint = undefined;
  return data;
 }
}
/** Skirts hide any residual boundary difference between tiles of different levels. */
const skirt = (level: number) => Math.max(40, 90_000 / 2 ** level);

export async function loadTerrainInfo(): Promise<TerrainInfo> {
 const response = await fetch("/api/terrain/info");
 if (!response.ok) throw new Error("Terrain service unavailable.");
 const info = await response.json() as TerrainInfo;
 if (info.unit !== "m" || info.reference_radius_m !== 1_737_400) throw new Error("Unexpected terrain units.");
 return info;
}
