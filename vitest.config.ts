import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { readFileSync, existsSync } from "fs";

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

// #2270 "prod-only gate": mirror the vite.config.ts define EXACTLY (BORA — no test-vs-build
// drift). The public download link tracks the last PROD release (desktop/stable-release.json),
// not the installed build (desktop/package.json). Defaults to APP_VERSION if absent.
let STABLE_DESKTOP_VERSION = APP_VERSION;
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


export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(APP_VERSION),
    __STABLE_DESKTOP_VERSION__: JSON.stringify(STABLE_DESKTOP_VERSION),
  },
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}", "scripts/**/*.{test,spec}.ts"],
    exclude: ["src/test/e2e/**"],
    testTimeout: 20000,
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
});
