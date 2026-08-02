import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./index.css";
import { initDesktopBridge } from "./lib/desktopBridge";

// Warm the desktop bridge (engine port + X-Engine-Key from the Electron
// preload IPC) BEFORE the first render, so the very first console fetch already
// carries the key. No-op in the browser — the cloud build resolves to render
// immediately (initDesktopBridge returns early when not in the desktop shell).
initDesktopBridge().finally(() => {
  createRoot(document.getElementById("root")!).render(<App />);
});
