import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  isDesktop,
  initDesktopBridge,
  getEngineKey,
  getEnginePort,
  minimizeWindow,
  toggleMaximizeWindow,
  closeWindow,
  __resetDesktopBridge,
} from "../lib/desktopBridge";

/**
 * G3 (#1050): the desktop edition serves the same console from the Electron
 * shell. Every engine call must carry the per-session X-Engine-Key
 * (require_engine_key is 503-fail-closed) and target the shell's loopback
 * port — both delivered by the `engine:get-connection` IPC (G2 preload).
 * The cloud path must be entirely unaffected when not in the desktop app.
 */

const setBridge = (impl: Record<string, unknown> | undefined) => {
  (window as unknown as { aaagents?: unknown }).aaagents = impl;
};

describe("desktopBridge", () => {
  beforeEach(() => {
    __resetDesktopBridge();
    setBridge(undefined);
  });

  afterEach(() => {
    __resetDesktopBridge();
    setBridge(undefined);
  });

  it("isDesktop() is false in a plain browser (no bridge)", () => {
    expect(isDesktop()).toBe(false);
  });

  it("isDesktop() is true when the Electron preload bridge is present", () => {
    setBridge({ isDesktop: true });
    expect(isDesktop()).toBe(true);
  });

  it("initDesktopBridge() caches the engine connection from the IPC", async () => {
    const getEngineConnection = vi
      .fn()
      .mockResolvedValue({ port: 8001, apiKey: "k-abc" });
    setBridge({ isDesktop: true, getEngineConnection });

    await initDesktopBridge();

    expect(getEngineConnection).toHaveBeenCalledOnce();
    expect(getEnginePort()).toBe(8001);
    expect(getEngineKey()).toBe("k-abc");
  });

  it("initDesktopBridge() is a no-op in the browser (getters stay null)", async () => {
    await initDesktopBridge();
    expect(getEnginePort()).toBeNull();
    expect(getEngineKey()).toBeNull();
  });

  it("initDesktopBridge() swallows an IPC failure (renderer still loads)", async () => {
    const getEngineConnection = vi.fn().mockRejectedValue(new Error("ipc down"));
    setBridge({ isDesktop: true, getEngineConnection });

    await expect(initDesktopBridge()).resolves.toBeUndefined();
    expect(getEngineKey()).toBeNull();
  });

  it("ignores a malformed IPC payload", async () => {
    const getEngineConnection = vi
      .fn()
      .mockResolvedValue({ port: "nope", apiKey: 123 });
    setBridge({ isDesktop: true, getEngineConnection });

    await initDesktopBridge();
    expect(getEnginePort()).toBeNull();
    expect(getEngineKey()).toBeNull();
  });

  it("window controls route through the bridge (and are no-ops in the browser)", () => {
    // browser: no bridge → must not throw
    expect(() => {
      minimizeWindow();
      toggleMaximizeWindow();
      closeWindow();
    }).not.toThrow();

    // desktop: each control calls the matching preload method exactly once
    const min = vi.fn();
    const max = vi.fn();
    const close = vi.fn();
    setBridge({ isDesktop: true, minimizeWindow: min, toggleMaximizeWindow: max, closeWindow: close });
    minimizeWindow();
    toggleMaximizeWindow();
    closeWindow();
    expect(min).toHaveBeenCalledOnce();
    expect(max).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
  });
});
