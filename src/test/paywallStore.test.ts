// GTM-2 (#1809) T3: the console store carries the paywall flag polled from
// /api/entitlement/status, so the desktop cards can branch a Junior desktop from the
// free beta claim to the paid checkout/activation path. Default null (unknown until the
// first poll → cards fail-safe to the current free-beta behaviour).
import { describe, it, expect, beforeEach } from "vitest";
import { useStore } from "@/console/store/useStore";

describe("store: paywallEnabled", () => {
  beforeEach(() => {
    useStore.setState({ paywallEnabled: null });
  });

  it("defaults to null (unknown until the first entitlement poll)", () => {
    expect(useStore.getState().paywallEnabled).toBeNull();
  });

  it("setPaywallEnabled writes the flag", () => {
    useStore.getState().setPaywallEnabled(true);
    expect(useStore.getState().paywallEnabled).toBe(true);
    useStore.getState().setPaywallEnabled(false);
    expect(useStore.getState().paywallEnabled).toBe(false);
  });
});
