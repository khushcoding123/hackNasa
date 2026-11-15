import {
 Credit, Ellipsoid, GeographicTilingScheme, ImageryLayer, Rectangle, RequestState, SingleTileImageryProvider,
 WebMapServiceImageryProvider, type Request, type TileProviderError, type Viewer,
} from "cesium";
import {LayerStatus, trackRequests, type LayerState} from "./imagery-status.js";

/** LROC WAC layers on the public, CORS-enabled Lunaserv WMS (EPSG:4326 here means selenographic lon/lat). */
export const WMS_URL = "https://wms.im-ldi.com/";
export const MAP_LAYER = "luna_wac_global";
export const SHADING_LAYER = "luna_wac_normalized_reflectance";
/** Advertised ground sampling of luna_wac_global. Deeper zoom reuses these tiles and adds no detail. */
export const WAC_RESOLUTION_M = 100;
/** The bundled colour map is 4096 × 2048 px: about 2.7 km per pixel at the equator (finer east–west toward the poles). */
export const BUNDLED_RESOLUTION_M = 2665;
const MOON = Ellipsoid.MOON;

export type ImageryState = LayerState;
export type ImageryChoice = "auto" | "bundled";
type Mode = "map" | "3d";
export interface ImageryHandle {
 setMode(mode: Mode): void;
 setChoice(choice: ImageryChoice): void;
 /** Rebuild the live layers so tiles that failed earlier are requested again. */
 retry(): void;
 /** State of the live layer for the current mode. */
 readonly state: ImageryState;
 readonly counts: {ok: number; failed: number; cancelled: number; deferred: number};
 /** Name of the imagery currently contributing to the display. */
 describe(mode: Mode): string;
}

const scheme = () => new GeographicTilingScheme({ellipsoid: MOON});

/** Adds the bundled fallback (bottom) and both live WMS layers. Each live layer tracks its own health: a sustained
 * outage hides that layer (the bundled map shows through) and reports a state change; the camera and all other scene
 * state are untouched. Tiles that fail individually fall back to Cesium's coarser ancestor tile or the bundled map. */
export function addImagery(viewer: Viewer, onState: (state: ImageryState) => void): ImageryHandle {
 const layers = viewer.imageryLayers;
 const bundled = layers.addImageryProvider(new SingleTileImageryProvider({
  url: "/assets/moon-color-4k.jpg", rectangle: Rectangle.MAX_VALUE, tileWidth: 4096, tileHeight: 2048, ellipsoid: MOON,
  credit: new Credit("Bundled colour map · NASA SVS CGI Moon Kit", false),
 }));
 let mode: Mode = "map", choice: ImageryChoice = "auto", reported: ImageryState | undefined;
 const report = () => { const s = live[mode].status.state; if (s !== reported) { reported = s; onState(s); } apply(); };
 const make = (layer: string, title: string, index: number) => {
  const status = new LayerStatus(() => report());
  const provider = new WebMapServiceImageryProvider({
   url: WMS_URL, layers: layer, parameters: {format: "image/jpeg", transparent: false, version: "1.1.1"},
   tilingScheme: scheme(), ellipsoid: MOON, tileWidth: 256, tileHeight: 256, maximumLevel: 8,
   credit: new Credit(`${title} · NASA / GSFC / ASU via LROC QuickMap`, false),
  });
  const original = provider.requestImage.bind(provider);
  (provider as {requestImage: unknown}).requestImage = trackRequests<Request, unknown>(original as never, status, RequestState.CANCELLED);
  provider.errorEvent.addEventListener((error: TileProviderError) => { error.retry = status.shouldRetry(error.timesRetried); });
  return {status, layer: layers.addImageryProvider(provider, index), name: layer, title};
 };
 const live = {map: make(MAP_LAYER, "LROC WAC", 1), "3d": make(SHADING_LAYER, "LROC WAC normalized reflectance", 2)};
 const apply = () => {
  for (const m of ["map", "3d"] as const) live[m].layer.show = choice === "auto" && m === mode && live[m].status.state !== "fallback";
  bundled.show = true;
  viewer.scene.requestRender();
 };
 report();
 return {
  setMode(next) { mode = next; report(); },
  setChoice(next) { choice = next; apply(); reported = undefined; report(); },
  retry() {
   for (const m of ["map", "3d"] as const) {
    const old = live[m], index = layers.indexOf(old.layer);
    layers.remove(old.layer, true);
    live[m] = make(old.name, old.title, index);
   }
   reported = undefined; report();
  },
  get state() { return live[mode].status.state; },
  get counts() { return live[mode].status.counts; },
  describe(current) {
   const status = live[current].status;
   const bundledText = `Bundled color map · lower detail (about ${(BUNDLED_RESOLUTION_M / 1000).toFixed(1)} km/px)`;
   if (choice === "bundled" || status.state === "fallback") return bundledText;
   if (status.state === "loading") return `${bundledText} · LROC WAC loading`;
   const name = current === "3d" ? "LROC WAC normalized reflectance" : "LROC WAC global mosaic";
   return `${name} · ${WAC_RESOLUTION_M} m/px source`;
  },
 };
}
export type {ImageryLayer};
