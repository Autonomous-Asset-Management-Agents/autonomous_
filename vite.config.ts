import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  // Absolute base — Firebase Hosting rewrites all unknown paths to /index.html.
  // Relative "./assets/…" breaks for any nested route (e.g. /legal/imprint) because
  // the browser resolves them against the current URL, not the document root.
  base: "/",
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
        rewrite: (path) => path.replace(/^\/api/, ""),
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
