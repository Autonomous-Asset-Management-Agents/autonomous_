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


export default defineConfig({
  define: { __APP_VERSION__: JSON.stringify(APP_VERSION) },
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["src/test/e2e/**"],
    testTimeout: 20000,
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
});
