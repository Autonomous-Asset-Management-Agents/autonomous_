import type { EngineStatus } from "@/lib/desktopBridge";

/**
 * Desktop engine status → UI effect mapping (LSR R8, #2261).
 *
 * Extracted from App.tsx's inline `handleStatus` switch so the benign-restart
 * behaviour is a PURE, unit-testable function (no React, no side effects). App.tsx
 * applies the returned descriptor; this module owns the policy.
 *
 * The load-bearing rule: a deliberate, EXPECTED restart (disable/Paper →
 * native-engine-manager.restart() = stop → waitPortFree → start) reports
 * status "restarting" — a benign transient. It must show a neutral loading toast
 * and MUST NOT navigate to /offline or fire a toast.error. A genuine "error"
 * (boot crash, exit≠0, crash while running) still navigates to /offline.
 */

/** Shared sonner toast id so each engine-status toast replaces the previous one. */
export const ENGINE_STATUS_TOAST_ID = "engine-status";

export type EngineToastKind = "loading" | "success" | "error" | "warning";

export interface EngineToastSpec {
  kind: EngineToastKind;
  message: string;
  /** sonner duration in ms; Infinity keeps a sticky loading toast up. */
  duration: number;
}

export interface EngineStatusEffect {
  /** New liveness flag, or undefined to leave it unchanged (e.g. "starting"). */
  engineReady?: boolean;
  /** Health-poll lifecycle action for this status. */
  polling: "start" | "stop" | "none";
  /** Toast to show under ENGINE_STATUS_TOAST_ID, or null for none. */
  toast: EngineToastSpec | null;
  /**
   * Whether this status is a GENUINE failure that should send the operator to the
   * /offline recovery screen. A benign, expected "restarting" is `false` — an
   * expected restart must never read as a Backend-Offline error (LSR R8 #2261).
   */
  navigateOffline: boolean;
}

export function engineStatusEffect(
  status: EngineStatus,
  detail?: string | null,
): EngineStatusEffect {
  switch (status) {
    case "starting":
      return {
        polling: "none",
        toast: null,
        navigateOffline: false,
      };

    case "restarting":
      // LSR R8 (#2261): a deliberate restart is an EXPECTED transient. Neutral
      // loading toast; NEVER a toast.error and NEVER a jump to /offline.
      return {
        engineReady: false,
        polling: "stop",
        toast: null,
        navigateOffline: false,
      };

    case "running":
      return {
        engineReady: true,
        polling: "start",
        toast: null,
        navigateOffline: false,
      };

    case "error":
      // Genuine failure — surface it honestly (red toast + /offline). Unchanged.
      return {
        engineReady: false,
        polling: "stop",
        toast: { kind: "error", message: detail || "Engine error", duration: 5000 },
        navigateOffline: true,
      };

    case "stopped":
      return {
        engineReady: false,
        polling: "stop",
        toast: null,
        navigateOffline: false,
      };

    case "stopping":
    case "unavailable":
    default:
      // No user-facing effect (mirrors the original switch's missing cases).
      return { polling: "none", toast: null, navigateOffline: false };
  }
}
