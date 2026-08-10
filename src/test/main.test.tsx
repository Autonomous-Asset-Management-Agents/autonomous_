import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * G3 (#1050) review P0: the bootstrap MUST call initDesktopBridge() before the
 * first render, otherwise the desktop console has no engine port / X-Engine-Key
 * and every fetch 503s. This pins the wiring so it can't silently regress.
 */
const render = vi.fn();
const initDesktopBridge = vi.fn().mockResolvedValue(undefined);

vi.mock("react-dom/client", () => ({ createRoot: vi.fn(() => ({ render })) }));
vi.mock("../App.tsx", () => ({ default: () => null }));
vi.mock("../index.css", () => ({}));
vi.mock("../lib/desktopBridge", () => ({ initDesktopBridge }));

describe("app bootstrap", () => {
  beforeEach(() => {
    vi.resetModules();
    render.mockClear();
    initDesktopBridge.mockClear();
    document.body.innerHTML = '<div id="root"></div>';
  });

  it("warms the desktop bridge before mounting React", async () => {
    await import("../main.tsx");
    expect(initDesktopBridge).toHaveBeenCalledOnce();
    // render happens in the .finally() after the bridge resolves
    await Promise.resolve();
    await Promise.resolve();
    expect(render).toHaveBeenCalledOnce();
  });
});
