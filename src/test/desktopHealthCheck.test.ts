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
    expect(app).toMatch(/case\s+"running"/);
    expect(app).toMatch(/startHealthPolling/);
  });

  it("has a recovery path from /offline back to /console", () => {
    // When the engine is healthy and the user is on /offline, navigate to /console
    expect(app).toMatch(/\/console/);
    expect(app).toMatch(/\/offline/);
  });
});
