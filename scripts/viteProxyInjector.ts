/**
 * Node-side Vite dev-server proxy helper (#2647). Lives in `scripts/`, NOT `src/`, so it is never
 * bundled into the client `dist/` — the engine key must never reach the browser.
 *
 * The from-source browser web-console talks to the local engine through the Vite `/api` proxy. The
 * engine requires an `X-Engine-Key` header (core/auth.py). `mac-quickstart.sh` exports `ENGINE_API_KEY`
 * into the dev-server env; this attaches it to every proxied HTTP + WebSocket request, server-side, so
 * the browser stays keyless while the engine stays secured.
 */
export interface ConnectProxyReq {
  setHeader(name: string, value: string): void;
}

export interface ConnectProxy {
  on(event: string, callback: (proxyReq: ConnectProxyReq) => void): void;
}

export function attachEngineKey(proxy: ConnectProxy, key: string | undefined): void {
  if (!key) return; // frontend-only dev / no local engine → inject nothing
  proxy.on("proxyReq", (proxyReq: ConnectProxyReq) => proxyReq.setHeader("X-Engine-Key", key));
  proxy.on("proxyReqWs", (proxyReq: ConnectProxyReq) => proxyReq.setHeader("X-Engine-Key", key));
}
