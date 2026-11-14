import {MAP_LAYER, WMS_URL} from "./imagery.js";
import {MOON_RADIUS_M} from "./terrain-mesh.js";

/** A local orthographic WMS image uses metres east and north of the site, matching the Three.js tangent frame. */
export async function loadWmsPatch(lat: number, lon: number, half: number, size: number, layer: string, signal: AbortSignal): Promise<HTMLCanvasElement> {
 const params = new URLSearchParams({
  SERVICE: "WMS", VERSION: "1.1.1", REQUEST: "GetMap", LAYERS: layer, STYLES: "",
  SRS: `AUTO:42003,9001,${lon},${lat}`, BBOX: `${-half},${-half},${half},${half}`,
  WIDTH: String(size), HEIGHT: String(size), FORMAT: "image/png", TRANSPARENT: "TRUE",
 });
 const response = await fetch(`${WMS_URL}?${params}`, {signal});
 if (!response.ok || !response.headers.get("content-type")?.startsWith("image/")) throw new Error(`LROC imagery unavailable (${response.status}).`);
 const bitmap = await createImageBitmap(await response.blob());
 const canvas = document.createElement("canvas"); canvas.width = size; canvas.height = size;
 canvas.getContext("2d")!.drawImage(bitmap, 0, 0, size, size); bitmap.close();
 return canvas;
}

export const SOUTH_NAC_LAYER = "luna_nac_pole_control_south";
export const NORTH_NAC_LAYER = "luna_nac_np_mosaic";
export const nacLayerAt = (lat: number) => lat <= -84 ? SOUTH_NAC_LAYER : lat >= 84 ? NORTH_NAC_LAYER : undefined;
export const loadWacPatch = (lat: number, lon: number, half: number, size: number, signal: AbortSignal) =>
 loadWmsPatch(lat, lon, half, size, MAP_LAYER, signal);

export function withNac(base: HTMLCanvasElement, overlay: HTMLCanvasElement): {canvas: HTMLCanvasElement; centerCovered: boolean; coverage: number} {
 const canvas = document.createElement("canvas"); canvas.width = base.width; canvas.height = base.height;
 const ctx = canvas.getContext("2d", {willReadFrequently: true})!;
 ctx.drawImage(base, 0, 0);
 const source = overlay.getContext("2d", {willReadFrequently: true})!;
 const data = source.getImageData(0, 0, overlay.width, overlay.height).data;
 let visible = 0, total = 0, centerVisible = 0, centerTotal = 0;
 const step = Math.max(1, Math.floor(overlay.width / 64));
 for (let y = 0; y < overlay.height; y += step) for (let x = 0; x < overlay.width; x += step) {
  const solid = data[(y * overlay.width + x) * 4 + 3] > 96;
  visible += Number(solid); total++;
  if (Math.abs(x / overlay.width - 0.5) < 0.12 && Math.abs(y / overlay.height - 0.5) < 0.12) {
   centerVisible += Number(solid); centerTotal++;
  }
 }
 if (visible) ctx.drawImage(overlay, 0, 0, canvas.width, canvas.height);
 return {canvas, centerCovered: centerTotal > 0 && centerVisible / centerTotal > 0.55, coverage: visible / total};
}

let bundled: Promise<ImageData> | undefined;
async function bundledPixels(): Promise<ImageData> {
 bundled ??= (async () => {
  const response = await fetch("/assets/moon-color-4k.jpg");
  if (!response.ok) throw new Error("Bundled lunar imagery unavailable.");
  const bitmap = await createImageBitmap(await response.blob());
  const canvas = document.createElement("canvas"); canvas.width = bitmap.width; canvas.height = bitmap.height;
  const ctx = canvas.getContext("2d", {willReadFrequently: true})!;
  ctx.drawImage(bitmap, 0, 0); bitmap.close();
  return ctx.getImageData(0, 0, canvas.width, canvas.height);
 })();
 return bundled;
}

/** Reproject the checked-in global map onto the same local square as the DEM; works at seams and poles. */
export async function loadBundledPatch(lat: number, lon: number, half: number, signal: AbortSignal): Promise<HTMLCanvasElement> {
 const source = await bundledPixels();
 if (signal.aborted) throw new DOMException("Aborted", "AbortError");
 const size = 512, canvas = document.createElement("canvas"); canvas.width = size; canvas.height = size;
 const ctx = canvas.getContext("2d")!, output = ctx.createImageData(size, size);
 const p = lat * Math.PI / 180, l = lon * Math.PI / 180;
 const up = [Math.cos(p) * Math.cos(l), Math.cos(p) * Math.sin(l), Math.sin(p)];
 const east = [-Math.sin(l), Math.cos(l), 0];
 const north = [-Math.sin(p) * Math.cos(l), -Math.sin(p) * Math.sin(l), Math.cos(p)];
 for (let y = 0; y < size; y++) {
  const z = (y / (size - 1) * 2 - 1) * half / MOON_RADIUS_M;
  for (let x = 0; x < size; x++) {
   const xx = (x / (size - 1) * 2 - 1) * half / MOON_RADIUS_M;
   const rx = up[0] + xx * east[0] - z * north[0];
   const ry = up[1] + xx * east[1] - z * north[1];
   const rz = up[2] + xx * east[2] - z * north[2];
   const latitude = Math.asin(rz / Math.hypot(rx, ry, rz));
   const longitude = Math.atan2(ry, rx);
   const sx = Math.min(source.width - 1, Math.max(0, Math.floor((longitude / (2 * Math.PI) + 0.5) * source.width)));
   const sy = Math.min(source.height - 1, Math.max(0, Math.floor((0.5 - latitude / Math.PI) * source.height)));
   const src = (sy * source.width + sx) * 4, dst = (y * size + x) * 4;
   output.data[dst] = source.data[src]; output.data[dst + 1] = source.data[src + 1]; output.data[dst + 2] = source.data[src + 2]; output.data[dst + 3] = 255;
  }
 }
 ctx.putImageData(output, 0, 0);
 return canvas;
}
