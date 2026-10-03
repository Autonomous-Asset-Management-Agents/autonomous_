import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { useResetScrollOnSwitchReveal } from "../console/desktop/useResetScrollOnSwitchReveal";
import { useStore } from "../console/store/useStore";
import type { RefObject } from "react";

/**
 * Operator finding: after a Paper↔Live switch clears (ModeSwitchOverlay reveals the app
 * again), the revealed Overview was scrolled DOWN — the account name/equity header was not
 * immediately visible. The shared <main> scroll container persists under the overlay, so its
 * old scroll position carried over. On reveal, the scroll container must reset to the top.
 */
describe("useResetScrollOnSwitchReveal", () => {
  beforeEach(() => {
    useStore.setState({ modeSwitch: null });
  });
  afterEach(() => {
    useStore.setState({ modeSwitch: null });
  });

  function fakeRef(scrollTop: number): RefObject<HTMLElement> {
    // A plain object (not a jsdom node) so scrollTop assignment is observable — jsdom's
    // real scrollTop is a no-op getter that always reports 0.
    return { current: { scrollTop } as unknown as HTMLElement };
  }

  it("resets the container to the top when a switch clears (reveal)", () => {
    const ref = fakeRef(420);
    useStore.setState({ modeSwitch: { to: "paper" } });
    renderHook(() => useResetScrollOnSwitchReveal(ref));
    // While the switch is in progress the overlay owns the screen — do not touch scroll yet.
    expect(ref.current!.scrollTop).toBe(420);
    // Switch clears → the app is revealed → scroll to top so the header is visible.
    act(() => {
      useStore.setState({ modeSwitch: null });
    });
    expect(ref.current!.scrollTop).toBe(0);
  });

  it("does not reset on unrelated re-renders while no switch is active", () => {
    const ref = fakeRef(300);
    renderHook(() => useResetScrollOnSwitchReveal(ref));
    act(() => {
      // A store change that is NOT a switch-clear transition must leave scroll alone.
      useStore.setState({ engineServing: true });
    });
    expect(ref.current!.scrollTop).toBe(300);
  });

  it("tolerates a null ref (container not mounted yet)", () => {
    const ref: RefObject<HTMLElement> = { current: null };
    useStore.setState({ modeSwitch: { to: "live" } });
    renderHook(() => useResetScrollOnSwitchReveal(ref));
    expect(() =>
      act(() => {
        useStore.setState({ modeSwitch: null });
      }),
    ).not.toThrow();
  });
});
