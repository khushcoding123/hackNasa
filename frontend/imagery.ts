import {
 Credit, Ellipsoid, GeographicTilingScheme, ImageryLayer, Rectangle, SingleTileImageryProvider,
 WebMapServiceImageryProvider, type Request, type Viewer,
} from "cesium";

/** LROC WAC layers on the public, CORS-enabled Lunaserv WMS (EPSG:4326 here means selenographic lon/lat). */
export const WMS_URL = "https://wms.im-ldi.com/";
export const MAP_LAYER = "luna_wac_global";
export const SHADING_LAYER = "luna_wac_normalized_reflectance";
/** Advertised ground sampling of luna_wac_global. Deeper zoom reuses these tiles and adds no detail. */
export const WAC_RESOLUTION_M = 100;
/** 8192-px bundled colour map: about 1.3 km per pixel at the equator, coarser near the poles. */
export const BUNDLED_RESOLUTION_M = 1330;
const MOON = Ellipsoid.MOON;

export type ImageryState = "loading" | "live" | "fallback";
export type ImageryChoice = "auto" | "bundled";
export interface ImageryHandle {
 setMode(mode: "map" | "3d"): void;
 setChoice(choice: ImageryChoice): void;
 retry(): void;
 readonly state: ImageryState;
 readonly counts: {ok: number; failed: number};
 /** Name of the imagery currently contributing to the display. */
 describe(mode: "map" | "3d"): string;
}

const scheme = () => new GeographicTilingScheme({ellipsoid: MOON});

/** Adds the bundled fallback (bottom) and both live WMS layers. Failure of the live service hides those layers
 * and reports a state change; the camera and all other scene state are untouched. */
export function addImagery(viewer: Viewer, onState: (state: ImageryState) => void): ImageryHandle {
 const layers = viewer.imageryLayers;
 const bundled = layers.addImageryProvider(new SingleTileImageryProvider({
  url: "/assets/moon-color-4k.jpg", rectangle: Rectangle.MAX_VALUE, tileWidth: 4096, tileHeight: 2048, ellipsoid: MOON,
  credit: new Credit("Bundled colour map · NASA SVS CGI Moon Kit", false),
 }));
 const live = (layer: string, title: string) => {
  const provider = new WebMapServiceImageryProvider({
   url: WMS_URL, layers: layer, parameters: {format: "image/jpeg", transparent: false, version: "1.1.1"},
   tilingScheme: scheme(), ellipsoid: MOON, tileWidth: 256, tileHeight: 256, maximumLevel: 8, minimumLevel: 0,
   credit: new Credit(`${title} · NASA / GSFC / ASU via LROC QuickMap`, false),
  });
  return {provider, layer: layers.addImageryProvider(provider)};
 };
 const map = live(MAP_LAYER, "LROC WAC"), shading = live(SHADING_LAYER, "LROC WAC normalized reflectance");
 const counts = {ok: 0, failed: 0};
 let state: ImageryState = "loading", mode: "map" | "3d" = "map", choice: ImageryChoice = "auto";
 const set = (next: ImageryState) => { if (next !== state) { state = next; onState(next); } apply(); };
 const apply = () => {
  const showLive = state !== "fallback" && choice === "auto";
  map.layer.show = showLive && mode === "map";
  shading.layer.show = showLive && mode === "3d";
  bundled.show = true;
  viewer.scene.requestRender();
 };
 for (const {provider} of [map, shading]) {
  const original = provider.requestImage.bind(provider);
  (provider as {requestImage: unknown}).requestImage = async (x: number, y: number, level: number, request?: Request) => {
   try {
    const image = await original(x, y, level, request);
    if (image) { counts.ok++; if (state === "loading") set("live"); }
    return image;
   } catch (error) {
    if (request?.state === 3 /* CANCELLED */) throw error;
    counts.failed++;
    if (choice === "auto" && counts.failed >= 4 && counts.failed > counts.ok) set("fallback");
    throw error;
   }
  };
  provider.errorEvent.addEventListener(error => { error.retry = false; });
 }
 apply();
 return {
  setMode(next) { mode = next; apply(); },
  setChoice(next) { choice = next; apply(); onState(state); },
  retry() { counts.ok = 0; counts.failed = 0; state = "loading"; onState(state); apply(); },
  get state() { return state; }, counts,
  describe(current) {
   if (choice === "bundled" || state === "fallback") return `Bundled color map · lower detail (about ${(BUNDLED_RESOLUTION_M / 1000).toFixed(1)} km/px)`;
   return current === "3d" ? `LROC WAC normalized reflectance · ${WAC_RESOLUTION_M} m/px` : `LROC WAC global mosaic · ${WAC_RESOLUTION_M} m/px`;
  },
 };
}
export type {ImageryLayer};
