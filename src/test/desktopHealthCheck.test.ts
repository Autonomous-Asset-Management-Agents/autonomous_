import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// Source-contract test: the desktop engine status monitor in App.tsx must:
// 1. Subscribe to engine status events (onEngineStatus) from the Electron IPC
//    and only start health-polling AFTER the engine reports "running" — never on
//    first render (the old code fired checkHealth() immediately, 5-15s before
//    the engine had bound its port → Connection Refused → /offline).
// 2. Use the correct OSS endpoint (/health on the engine's own loopback port
//    with X-Engine-Key), never the enterprise /api/v1/health on :8000.
// 3. Include a recovery path from /offline back to /console when the engine
//    becomes healthy.
const app = readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "App.tsx"),
  "utf8",
);

describe("desktop backend health check (docker-less OSS)", () => {
  it("checks the OSS engine's /health, never the enterprise /api/v1/health", () => {
    expect(app).not.toMatch(/\/api\/v1\/health/);
    expect(app).toMatch(/\/health`/);
  });

  it("uses the real engine connection (port + X-Engine-Key) from the desktop bridge", () => {
    expect(app).toMatch(/getEnginePort\(\)/);
    expect(app).toMatch(/getEngineKey\(\)/);
    expect(app).toMatch(/X-Engine-Key/);
  });

  it("never hardcodes the wrong :8000 port", () => {
    expect(app).not.toMatch(/127\.0\.0\.1:8000/);
  });

  it("subscribes to engine status before polling health", () => {
    expect(app).toMatch(/onEngineStatus/);
    expect(app).toMatch(/getEngineStatus/);
  });

  it("only starts health polling after engine status is 'running'", () => {
    // LSR R8 (#2261): the status→effect mapping moved into the pure
    // engineStatusEffect helper (unit-tested in engineStatusEffect.test.ts, where
    // "running" → polling:"start"). App.tsx delegates to it and only calls
    // startHealthPolling for the "running" effect.
    expect(app).toMatch(/engineStatusEffect/);
    expect(app).toMatch(/startHealthPolling/);
  });

  it("has a recovery path from /offline back to /console", () => {
    // When the engine is healthy and the user is on /offline, navigate to /console
    expect(app).toMatch(/\/console/);
    expect(app).toMatch(/\/offline/);
  });
});

// Operator finding #8: a deliberate Paper↔Live switch restarts the engine (an
// EXPECTED ~60s of refused connections). The health poll must route the offline
// decision through the pure shouldRouteOffline guard, passing the live modeSwitch
// state, so the red Backend-Offline screen never flashes during that switch. The
// routing logic itself is proven behaviourally in offlineRouting.test.ts (mirrors
// the engineStatusEffect pattern); here we assert App.tsx is actually wired to it.
describe("deliberate mode switch suppresses the /offline flash (#8)", () => {
  it("routes the offline decision through shouldRouteOffline", () => {
    expect(app).toMatch(/shouldRouteOffline/);
  });

  it("reads the live modeSwitch state and passes it as modeSwitchActive", () => {
    expect(app).toMatch(/useStore\.getState\(\)\.modeSwitch/);
    expect(app).toMatch(/modeSwitchActive/);
  });

  it("renders the calm ModeSwitchOverlay while a switch is in progress", () => {
    expect(app).toMatch(/ModeSwitchOverlay/);
  });

  it("guards the hard-error navigation with the switch state too (no full-reload teardown)", () => {
    // The engineStatusEffect navigateOffline path is also gated by !inModeSwitch().
    expect(app).toMatch(/effect\.navigateOffline && !inModeSwitch\(\)/);
  });
});
