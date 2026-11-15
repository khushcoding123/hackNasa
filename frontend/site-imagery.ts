import {MAP_LAYER, WAC_RESOLUTION_M, BUNDLED_RESOLUTION_M, WMS_URL} from "./imagery.js";
import {orthoSrs, reprojectEquirect, tileBoxes} from "./site-projection.js";

export {WAC_RESOLUTION_M, BUNDLED_RESOLUTION_M};
/** LROC NAC polar mosaics (controlled south: 85°–90°S; uncontrolled north: 85.5°–90°N), both 1 m/px. */
export const NAC_RESOLUTION_M = 1;
export const SOUTH_NAC_LAYER = "luna_nac_pole_control_south";
export const NORTH_NAC_LAYER = "luna_nac_np_mosaic";
export const nacLayerAt = (lat: number) => lat <= -84 ? SOUTH_NAC_LAYER : lat >= 84 ? NORTH_NAC_LAYER : undefined;

const sleep = (ms: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
 const t = setTimeout(resolve, ms);
 signal.addEventListener("abort", () => { clearTimeout(t); reject(new DOMException("Aborted", "AbortError")); }, {once: true});
});

/** Abort `signal`'s work after `ms` too. The timeout rejects with a TimeoutError so it can be told apart from a cancel. */
function withTimeout(signal: AbortSignal, ms: number) {
 const control = new AbortController();
 const timer = setTimeout(() => control.abort(new DOMException(`LROC imagery request timed out after ${Math.round(ms / 1000)} s`, "TimeoutError")), ms);
 const cancel = () => control.abort(signal.reason);
 if (signal.aborted) cancel(); else signal.addEventListener("abort", cancel, {once: true});
 return {signal: control.signal, done: () => { clearTimeout(timer); signal.removeEventListener("abort", cancel); }};
}

/** A local orthographic WMS image in east/north metres about the site, matching the terrain's texture space.
 * Transient failures (network, 429, 5xx) are retried with backoff. Aborts, timeouts (the server's render time grows
 * with image size, so asking again does not help) and other HTTP errors are not. */
export async function loadWmsPatch(lat: number, lon: number, half: number, size: number, layer: string, signal: AbortSignal,
 options: {transparent?: boolean; attempts?: number; timeoutMs?: number; bbox?: [number, number, number, number]} = {}): Promise<HTMLCanvasElement> {
 const transparent = options.transparent ?? true;
 const params = new URLSearchParams({
  SERVICE: "WMS", VERSION: "1.1.1", REQUEST: "GetMap", LAYERS: layer, STYLES: "",
  SRS: orthoSrs(lat, lon), BBOX: (options.bbox ?? [-half, -half, half, half]).join(","),
  WIDTH: String(size), HEIGHT: String(size), FORMAT: transparent ? "image/png" : "image/jpeg", TRANSPARENT: transparent ? "TRUE" : "FALSE",
 });
 const attempts = options.attempts ?? 3;
 for (let attempt = 1; ; attempt++) {
  let transient = true;
  const current = withTimeout(signal, options.timeoutMs ?? 60_000);
  try {
   const response = await fetch(`${WMS_URL}?${params}`, {signal: current.signal});
   transient = response.status === 429 || response.status >= 500;
   if (!response.ok || !response.headers.get("content-type")?.startsWith("image/")) throw new Error(`LROC imagery unavailable (${response.status}).`);
   const bitmap = await createImageBitmap(await response.blob());
   const canvas = document.createElement("canvas"); canvas.width = size; canvas.height = size;
   canvas.getContext("2d")!.drawImage(bitmap, 0, 0, size, size); bitmap.close();
   return canvas;
  } catch (error) {
   const timedOut = !signal.aborted && current.signal.aborted;
   if (timedOut) throw current.signal.reason;
   if (signal.aborted || (error as Error).name === "AbortError" || !transient || attempt >= attempts) throw error;
   await sleep(700 * 2 ** (attempt - 1), signal);
  } finally { current.done(); }
 }
}

/** A large orthographic image fetched as tiles of at most `maxTile` px and stitched. If any tile fails, the rest are
 * cancelled and the whole image fails, so a partial mosaic is never shown. */
export async function loadWmsMosaic(lat: number, lon: number, half: number, size: number, layer: string, signal: AbortSignal,
 options: {transparent?: boolean; maxTile: number; attempts?: number; timeoutMs?: number}): Promise<HTMLCanvasElement> {
 const boxes = tileBoxes(half, size, options.maxTile);
 if (boxes.length === 1) return loadWmsPatch(lat, lon, half, size, layer, signal, options);
 const control = new AbortController(), stop = () => control.abort(signal.reason);
 if (signal.aborted) stop(); else signal.addEventListener("abort", stop, {once: true});
 try {
  const tiles = await Promise.all(boxes.map(box => loadWmsPatch(lat, lon, half, box.px, layer, control.signal, {...options, bbox: box.bbox})
   .catch(error => { control.abort(error); throw error; })));
  const canvas = document.createElement("canvas"); canvas.width = size; canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  tiles.forEach((tile, i) => ctx.drawImage(tile, boxes[i].x, boxes[i].y));
  return canvas;
 } finally { signal.removeEventListener("abort", stop); }
}

export const loadWacPatch = (lat: number, lon: number, half: number, size: number, signal: AbortSignal) =>
 loadWmsPatch(lat, lon, half, size, MAP_LAYER, signal, {transparent: false});

/** Overlay a partially transparent image (NAC coverage) on a base; reports how much of it, and of the centre, is covered. */
export function withNac(base: HTMLCanvasElement, overlay: HTMLCanvasElement): {canvas: HTMLCanvasElement; centerCovered: boolean; coverage: number} {
 const canvas = document.createElement("canvas"); canvas.width = overlay.width; canvas.height = overlay.height;
 const ctx = canvas.getContext("2d", {willReadFrequently: true})!;
 ctx.imageSmoothingQuality = "high";
 ctx.drawImage(base, 0, 0, canvas.width, canvas.height);
 const data = overlay.getContext("2d", {willReadFrequently: true})!.getImageData(0, 0, overlay.width, overlay.height).data;
 let visible = 0, total = 0, centerVisible = 0, centerTotal = 0;
 const step = Math.max(1, Math.floor(overlay.width / 64));
 for (let y = 0; y < overlay.height; y += step) for (let x = 0; x < overlay.width; x += step) {
  const solid = data[(y * overlay.width + x) * 4 + 3] > 96;
  visible += Number(solid); total++;
  if (Math.abs(x / overlay.width - 0.5) < 0.12 && Math.abs(y / overlay.height - 0.5) < 0.12) { centerVisible += Number(solid); centerTotal++; }
 }
 if (visible) ctx.drawImage(overlay, 0, 0);
 return {canvas, centerCovered: centerTotal > 0 && centerVisible / centerTotal > 0.55, coverage: visible / total};
}

/** The centre square [−inner, inner] of an image covering [−outer, outer], resampled to `size` px. Both are
 * orthographic about the same point, so this crop is exact. */
export function cropCentre(image: HTMLCanvasElement, outer: number, inner: number, size: number): HTMLCanvasElement {
 const canvas = document.createElement("canvas"); canvas.width = size; canvas.height = size;
 const ctx = canvas.getContext("2d")!, w = image.width * inner / outer, h = image.height * inner / outer;
 ctx.imageSmoothingQuality = "high";
 ctx.drawImage(image, (image.width - w) / 2, (image.height - h) / 2, w, h, 0, 0, size, size);
 return canvas;
}

/** Detailed centre imagery over the wide imagery's crop, fading to the crop at the edges so the two textures meet
 * without a visible square. `fade` is the edge band as a fraction of the half-width. */
export function blendCentre(base: HTMLCanvasElement, detail: HTMLCanvasElement, fade = 0.12): HTMLCanvasElement {
 const size = detail.width, canvas = document.createElement("canvas"); canvas.width = size; canvas.height = size;
 const ctx = canvas.getContext("2d")!;
 ctx.imageSmoothingQuality = "high";
 ctx.drawImage(base, 0, 0, size, size);
 const layer = document.createElement("canvas"); layer.width = size; layer.height = size;
 const lctx = layer.getContext("2d")!, mask = lctx.createImageData(size, size);
 for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
  const edge = Math.max(Math.abs((x + 0.5) / size * 2 - 1), Math.abs((y + 0.5) / size * 2 - 1));
  const t = Math.min(1, Math.max(0, (1 - edge) / fade));
  mask.data[(y * size + x) * 4 + 3] = Math.round(t * t * (3 - 2 * t) * 255);
 }
 lctx.putImageData(mask, 0, 0);
 lctx.globalCompositeOperation = "source-in";
 lctx.drawImage(detail, 0, 0, size, size);
 ctx.drawImage(layer, 0, 0);
 return canvas;
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
 bundled.catch(() => { bundled = undefined; });
 return bundled;
}

/** Reproject the checked-in global map onto the same orthographic square as the WMS imagery, with bilinear
 * interpolation, longitude wrap and pole continuity. It stays at the map's ~2.7 km/px: smooth, not sharper. */
export async function loadBundledPatch(lat: number, lon: number, half: number, signal: AbortSignal, size = 256): Promise<HTMLCanvasElement> {
 const source = await bundledPixels();
 if (signal.aborted) throw new DOMException("Aborted", "AbortError");
 const canvas = document.createElement("canvas"); canvas.width = size; canvas.height = size;
 const ctx = canvas.getContext("2d")!;
 ctx.putImageData(new ImageData(reprojectEquirect(source, lat, lon, half, size), size, size), 0, 0);
 return canvas;
}
