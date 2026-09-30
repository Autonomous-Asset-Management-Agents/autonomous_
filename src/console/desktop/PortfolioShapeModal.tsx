import { useRef } from "react";
import { savePortfolioShape, type PortfolioShape, type PortfolioShapeResult } from "@/lib/api";
import { saveSetupState } from "@/lib/desktopBridge";
import {
  SettingsApplyModal,
  type SettingsApplyChange,
} from "@/console/desktop/SettingsApplyModal";

/**
 * PortfolioShapeModal (#3132) — since UXC-1 S3 (#3156) a THIN wrapper around the
 * shared SettingsApplyModal: same record → persist → restart order, same live-mode
 * re-consent + goLive branch (A4 fix — the old direct `restartEngine(nonce)` call
 * silently booted PAPER after a live-mode shape apply). The #3132 endpoint stays
 * the record path; setup.json persists what the engine ACTUALLY applied (it clamps
 * server-side), under the exact engine flag names (#2863 passthrough).
 */

const ACK =
  "I confirm this change to how my capital is distributed. It applies to all future " +
  "trading decisions and is recorded on a tamper-evident audit trail.";

const LABELS: Record<keyof PortfolioShape, string> = {
  positions: "Maximum positions",
  hold_days: "Minimum holding period",
  vol_target: "Daily vol target",
};

function fmt(field: keyof PortfolioShape, v: number): string {
  if (field === "positions") return String(Math.round(v));
  if (field === "hold_days") return `${v.toFixed(0)} days`;
  return v.toFixed(3);
}

export interface PortfolioShapeModalProps {
  open: boolean;
  current: PortfolioShape;
  next: PortfolioShape;
  onCancel: () => void;
  onApplied: (applied: PortfolioShape) => void;
}

export function PortfolioShapeModal({
  open,
  current,
  next,
  onCancel,
  onApplied,
}: PortfolioShapeModalProps) {
  const resRef = useRef<PortfolioShapeResult | null>(null);

  const changes: SettingsApplyChange[] = (
    Object.keys(LABELS) as (keyof PortfolioShape)[]
  )
    .filter((f) => fmt(f, current[f]) !== fmt(f, next[f]))
    .map((f) => ({
      key: f,
      label: LABELS[f],
      from: fmt(f, current[f]),
      to: fmt(f, next[f]),
    }));

  return (
    <SettingsApplyModal
      open={open}
      changes={changes}
      title="Change portfolio structure?"
      description={
        "This setting determines how many stocks your capital is spread across and how " +
        "long a position is held at minimum. It applies to all future trading decisions. " +
        "The engine restarts so the change takes effect."
      }
      record={async (nonce) => {
        resRef.current = await savePortfolioShape(next, ACK, nonce);
      }}
      persist={async () => {
        const applied = resRef.current?.applied ?? next;
        await saveSetupState({
          FULL_UNIVERSE_MAX_POSITIONS: String(Math.round(applied.positions)),
          SMART_EXIT_MIN_HOLD_DAYS: applied.hold_days.toFixed(1),
          VOL_TARGET_DAILY_VOL: applied.vol_target.toFixed(4),
        });
      }}
      onCancel={onCancel}
      onApplied={() => onApplied(resRef.current?.applied ?? next)}
    />
  );
}
