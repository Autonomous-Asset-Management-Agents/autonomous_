import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { readFileSync, existsSync } from "fs";
import type { ConnectProxy } from "./scripts/viteProxyInjector";

let attachEngineKey: ((proxy: ConnectProxy, key: string | undefined) => void) | undefined;
const injectorPath = path.resolve(__dirname, "./scripts/viteProxyInjector.ts");
if (existsSync(injectorPath)) {
  void import("./scripts/viteProxyInjector").then((mod) => {
    attachEngineKey = mod.attachEngineKey;
  }).catch(() => {
    // optional dev helper
  });
}

let APP_VERSION = "0.0.0";
const pkgPath = path.resolve(__dirname, "./desktop/package.json");
if (existsSync(pkgPath)) {
  try {
    APP_VERSION = (
      JSON.parse(readFileSync(pkgPath, "utf-8")) as { version: string }
    ).version;
  } catch (e) {
    console.error("Error reading/parsing desktop/package.json:", e);
  }
}

// #2270 "prod-only gate": the PUBLIC download link tracks the last PROD release
// (desktop/stable-release.json), NOT the installed build (desktop/package.json). Bumping
// package.json for a test/rc build must not move the website link — that follows this file,
// which a human bumps only on a clean PROD release. Defaults to APP_VERSION if absent.
let STABLE_DESKTOP_VERSION = "0.0.0";
const stablePath = path.resolve(__dirname, "./desktop/stable-release.json");
if (existsSync(stablePath)) {
  try {
    STABLE_DESKTOP_VERSION = (
      JSON.parse(readFileSync(stablePath, "utf-8")) as { version: string }
    ).version;
  } catch (e) {
    console.error("Error reading/parsing desktop/stable-release.json:", e);
  }
} else {
  STABLE_DESKTOP_VERSION = APP_VERSION;
}


// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  // Absolute base — Firebase Hosting rewrites all unknown paths to /index.html.
  // Relative "./assets/…" breaks for any nested route (e.g. /legal/imprint) because
  // the browser resolves them against the current URL, not the document root.
  base: "/",
  define: {
    __APP_VERSION__: JSON.stringify(APP_VERSION),
    __STABLE_DESKTOP_VERSION__: JSON.stringify(STABLE_DESKTOP_VERSION),
  },
  server: {
    host: "::",
    port: 8082,
    headers: {
      "Cross-Origin-Opener-Policy": "same-origin-allow-popups"
    },
    proxy: {
      "/api": {
        target: "http://localhost:8001",
        changeOrigin: true,
        ws: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
        // #2647: authenticate the browser→engine hop server-side. mac-quickstart.sh exports
        // ENGINE_API_KEY; attachEngineKey adds X-Engine-Key to every proxied req (no-op when unset).
        configure: (proxy) => attachEngineKey?.(proxy, process.env.ENGINE_API_KEY),
      },
    },
    hmr: {
      overlay: false,
    },
  },
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    chunkSizeWarningLimit: 1000, // avoid "chunk > 500 kB" warning in CI (can add code-split later)
  },
}));
