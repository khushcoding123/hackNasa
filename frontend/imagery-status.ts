/** Request bookkeeping for Cesium imagery providers, kept free of Cesium imports so it can be unit tested.
 *
 * Cesium's ImageryProvider.requestImage contract has three outcomes, which must stay distinct:
 *  - `undefined` returned synchronously: the request was throttled (too many in flight). Cesium marks the tile
 *    UNLOADED and asks again on a later frame. Returning a Promise that resolves to undefined instead makes Cesium
 *    mark the tile FAILED, and it is never requested again.
 *  - a rejected promise whose request was cancelled (the tile went out of view): Cesium retries it later; it is not
 *    a service failure.
 *  - any other rejection: a genuine failure. */

export type LayerState = "loading" | "live" | "fallback";
export type Outcome = "ok" | "failed" | "cancelled" | "deferred";

/** Consecutive genuine failures, with no success in between, before a live layer is treated as unavailable. */
export const FALLBACK_AFTER = 8;
/** Immediate per-error retries Cesium may make before leaving a tile on its coarser ancestor. */
export const TILE_RETRIES = 2;

export class LayerStatus {
 state: LayerState = "loading";
 counts = {ok: 0, failed: 0, cancelled: 0, deferred: 0};
 private streak = 0;
 constructor(private onChange: (state: LayerState) => void = () => {}) {}
 record(outcome: Outcome) {
  this.counts[outcome]++;
  if (outcome === "ok") { this.streak = 0; this.set("live"); }
  else if (outcome === "failed" && ++this.streak >= FALLBACK_AFTER && this.state !== "fallback") this.set("fallback");
 }
 /** Whether Cesium should immediately re-request a failed tile. */
 shouldRetry(timesRetried: number) { return this.state !== "fallback" && timesRetried < TILE_RETRIES; }
 reset() { this.counts = {ok: 0, failed: 0, cancelled: 0, deferred: 0}; this.streak = 0; this.set("loading"); }
 private set(next: LayerState) { if (next !== this.state) { this.state = next; this.onChange(next); } }
}

type RequestImage<R, T> = (x: number, y: number, level: number, request?: R) => Promise<T> | undefined;

/** Wrap requestImage without changing its contract: synchronous undefined passes straight through. */
export function trackRequests<R extends {state?: number}, T>(original: RequestImage<R, T>, status: LayerStatus, cancelledState: number): RequestImage<R, T> {
 return (x, y, level, request) => {
  const promise = original(x, y, level, request);
  if (promise === undefined) { status.record("deferred"); return undefined; }
  return promise.then(image => {
   status.record(image ? "ok" : "failed");
   return image;
  }, (error: unknown) => {
   status.record(request?.state === cancelledState ? "cancelled" : "failed");
   throw error;
  });
 };
}
