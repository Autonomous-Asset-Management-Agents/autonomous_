import type { ReactNode } from "react";

/**
 * Plain status standard (#60 follow-up) — the single source of truth for status indicators
 * across the console, derived from the Overview kill-switch "System Status: Active/Halted":
 * a small dot + white text. Semantics:
 *   - `on`      → green dot (#00c27a): active / running / open / enabled / paper-safe
 *   - `off`     → red dot   (#ff5a52): off / halted / closed / blocked / error
 *   - `neutral` → dim dot   (white/30): unknown / idle / pending / "—"
 * Status badges (state indicators) use this; data classifiers (BUY/SELL/HOLD, conviction %,
 * quality tags) and category labels (BETA/FREE, tier pills) stay as `.pill` chips.
 */
export function StatusDot({
  tone = "neutral",
  className = "",
  children,
}: {
  tone?: "on" | "off" | "neutral";
  className?: string;
  children: ReactNode;
}) {
  const dot = tone === "on" ? "bg-[#00c27a]" : tone === "off" ? "bg-[#ff5a52]" : "bg-white/30";
  return (
    <span className={`inline-flex items-center gap-2 text-[12px] font-medium text-white/70 ${className}`}>
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
      <span>{children}</span>
    </span>
  );
}
