import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./index.css";
import "./lib/i18n";
import { initDesktopBridge } from "./lib/desktopBridge";
import { installTraceparentFetch } from "./lib/traceparentFetch";

// OBS-5 (#2638): attach a W3C traceparent to every engine request (global
// window.fetch patch, engine-origin gated) so UI-click ↔ engine is one trace.
// Installed before the first render so even the earliest console fetch carries it.
installTraceparentFetch();

// Warm the desktop bridge (engine port + X-Engine-Key from the Electron
// preload IPC) BEFORE the first render, so the very first console fetch already
// carries the key. No-op in the browser — the cloud build resolves to render
// immediately (initDesktopBridge returns early when not in the desktop shell).
initDesktopBridge().finally(() => {
  const root = document.getElementById("root")!;
  // Prerendered markup from the `edge` function is for crawlers only (index.html hides it):
  // remove it so React renders into an empty, visible root.
  if (root.hasAttribute("data-prerendered")) {
    root.replaceChildren();
    root.removeAttribute("data-prerendered");
  }
  createRoot(root).render(<App />);
});
