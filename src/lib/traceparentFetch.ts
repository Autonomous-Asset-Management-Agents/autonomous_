// INF-13 OBS-5 (#2638): cross-service W3C trace context — frontend egress.
//
// The console/desktop ship no OpenTelemetry-JS SDK, and 6+ call sites use raw
// `fetch` (App.tsx, StockRecommendations, useSnapshotPolling, useEntitlementPolling,
// UpgradeModal, chatClient) that bypass the typed `fetchJson` wrapper. So the only
// regression-proof single choke point is a global `window.fetch` patch, gated to
// the engine origin, that attaches a manually-generated `traceparent`. The engine
// middleware (OtelSpanMiddleware) continues that trace id → UI-click ↔ engine is
// one trace. Non-engine requests (firebase, external) are left untouched.
import { getApiBase } from "./api";

/** Generate a valid W3C `traceparent` (version 00, sampled). No OTel-JS SDK,
 *  no dependency — `crypto.getRandomValues` is available in browser + Electron
 *  renderer. */
export function genTraceparent(): { header: string; traceId: string } {
  const hex = (n: number): string =>
    Array.from(crypto.getRandomValues(new Uint8Array(n)))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  const traceId = hex(16); // 128-bit
  const spanId = hex(8); // 64-bit
  return { header: `00-${traceId}-${spanId}-01`, traceId };
}

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

let _installed = false;

/** Idempotently wrap `window.fetch` so every engine-origin request carries a
 *  `traceparent`. Call once at app bootstrap. Never throws; a failure here must
 *  never break a request. */
export function installTraceparentFetch(): void {
  if (_installed || typeof window === "undefined" || !window.fetch) return;
  _installed = true;
  const orig = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    try {
      const engine = getApiBase();
      if (engine && urlOf(input).startsWith(engine)) {
        const headers = new Headers(
          init?.headers ??
            (input instanceof Request ? input.headers : undefined),
        );
        if (!headers.has("traceparent")) {
          headers.set("traceparent", genTraceparent().header);
        }
        init = { ...(init ?? {}), headers };
      }
    } catch {
      // instrumentation must never break a request
    }
    return orig(input as RequestInfo, init);
  };
}
