import { useEffect, useRef, type RefObject } from "react";
import { useStore } from "@/console/store/useStore";

/**
 * After a Paper↔Live switch clears — the ModeSwitchOverlay reveals the app again
 * (`modeSwitch` transitions from a set value back to `null`) — reset the given scroll
 * container to the top.
 *
 * Why this is needed: the console's `<main>` scroll container is SHARED across every page
 * and stays mounted underneath the full-screen switch overlay (App.tsx renders the overlay
 * above the router). So whatever scroll position the operator had before the switch — e.g.
 * scrolled down in Settings where the switch is triggered — carries straight over onto the
 * revealed page. The result the operator reported: after switching, the Overview opens
 * scrolled down and the account name/equity header is not immediately visible. Snapping the
 * container to the top on reveal makes the header the first thing they see, matching the
 * "fresh, clean account" the switch is FOR.
 *
 * Only the set→null (reveal) transition scrolls; a switch merely STARTING is covered by the
 * overlay and must not disturb the page underneath.
 */
export function useResetScrollOnSwitchReveal(ref: RefObject<HTMLElement | null>): void {
  const modeSwitch = useStore((s) => s.modeSwitch);
  const prev = useRef(modeSwitch);
  useEffect(() => {
    if (prev.current && !modeSwitch && ref.current) {
      ref.current.scrollTop = 0;
    }
    prev.current = modeSwitch;
  }, [modeSwitch, ref]);
}
