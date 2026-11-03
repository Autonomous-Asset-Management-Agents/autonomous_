// #4467 (ADR-026 §6): GA4 must not start just because a page imports src/lib/firebase.ts.
// The landing imports it for auth; with VITE_ENABLE_FIREBASE=true the eager getAnalytics() at
// module load pulled gtag.js from googletagmanager.com onto the landing. GA4 now starts on the
// first trackVariantImpression() call, which only the console variants make.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getAnalytics = vi.fn(() => ({}));
const logEvent = vi.fn();

vi.mock("firebase/app", () => ({ initializeApp: vi.fn(() => ({})) }));
vi.mock("firebase/auth", () => ({ getAuth: vi.fn(), GoogleAuthProvider: vi.fn() }));
vi.mock("firebase/app-check", () => ({ initializeAppCheck: vi.fn(), ReCaptchaV3Provider: vi.fn() }));
vi.mock("firebase/firestore", () => ({ getFirestore: vi.fn() }));
vi.mock("firebase/remote-config", () => ({
  getRemoteConfig: vi.fn(() => ({ settings: {}, defaultConfig: {} })),
  fetchAndActivate: vi.fn(() => Promise.resolve(true)),
}));
vi.mock("firebase/analytics", () => ({
  getAnalytics,
  logEvent,
  isSupported: vi.fn(() => Promise.resolve(true)),
}));

beforeEach(() => {
  vi.resetModules();
  getAnalytics.mockClear();
  logEvent.mockClear();
  vi.stubEnv("VITE_ENABLE_FIREBASE", "true");
});
afterEach(() => vi.unstubAllEnvs());

describe("firebase.ts GA4 initialisation", () => {
  it("does not initialise GA4 on import", async () => {
    await import("../firebase");
    await new Promise((r) => setTimeout(r, 0));
    expect(getAnalytics).not.toHaveBeenCalled();
  });

  it("initialises GA4 and logs on the first variant impression", async () => {
    const { trackVariantImpression } = await import("../firebase");
    trackVariantImpression("v1");
    await vi.waitFor(() =>
      expect(logEvent).toHaveBeenCalledWith(expect.anything(), "experience_impression", { exp_variant: "v1" }),
    );
    expect(getAnalytics).toHaveBeenCalledTimes(1);
  });
});
