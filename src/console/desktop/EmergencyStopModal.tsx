import { useState } from "react";
import { IconShield } from "@/console/shared/Icons";
import { useStore } from "@/console/store/useStore";
import { panicSell } from "@/lib/api";

type StopMode = "halt" | "sell";

/** One selectable stop-mode card (radio semantics). Idle cards keep a faint border (#3177 grammar). */
function OptionCard({
  title,
  note,
  active,
  disabled,
  onSelect,
}: {
  title: string;
  note: string;
  active: boolean;
  disabled: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onSelect}
      className={`w-full text-left rounded-xl px-4 py-3 border transition-all flex items-start gap-3 disabled:opacity-60 disabled:cursor-not-allowed ${
        active ? "border-white/20 bg-white/[0.06]" : "border-white/10 bg-white/[0.03] hover:bg-white/[0.05]"
      }`}
    >
      <span
        className={`mt-0.5 w-4 h-4 rounded-full flex items-center justify-center shrink-0 ${
          active ? "bg-white/12 border border-white/25 text-white" : "border border-white/30"
        }`}
      >
        {active && <span className="text-[10px] leading-none font-bold">✓</span>}
      </span>
      <span>
        <span className="block text-[13.5px] font-semibold text-white">{title}</span>
        <span className="block text-[11.5px] text-white/45">{note}</span>
      </span>
    </button>
  );
}

/**
 * EmergencyStopModal — the shared "how do you want to stop?" confirm for the Sidebar kill
 * control (always present on the left). Extracted to a single component so the emergency flow
 * can't drift into divergent inline copies. (#2863: the former Settings › Trading SafetyControlCard
 * copy was removed — the Kill-Switch lives only in the sidebar now.)
 *
 * Select-then-confirm (design A3): pick a mode, then press one Confirm button. Two modes:
 *   1. "Stop live trading"          → `onHalt` (the existing kill switch: halt + cancel
 *      open orders + force-paper + WORM disable; OPEN POSITIONS ARE KEPT).
 *   2. "Stop live trading and sell all" → POST /panic-sell, which additionally MARKET-SELLS
 *      every open position. The outcome is surfaced app-wide via the sidebar status line.
 *
 * The safer "halt only" mode is preselected; both are recorded on the WORM audit trail (RTS 6 Art. 5).
 */
export function EmergencyStopModal({
  open,
  onClose,
  onHalt,
}: {
  open: boolean;
  onClose: () => void;
  onHalt: () => void;
}) {
  const [selling, setSelling] = useState(false);
  const [mode, setMode] = useState<StopMode>("halt");
  const setSidebarStatusMsg = useStore((s) => s.setSidebarStatusMsg);

  if (!open) return null;

  // Close + reset the choice to the safer "halt only" — so a later re-open never shows a stale
  // "sell all" preselection (done on close, not in an effect).
  const dismiss = () => {
    setMode("halt");
    onClose();
  };

  const handleSellAll = async () => {
    setSelling(true);
    try {
      const res = await panicSell();
      setSidebarStatusMsg(
        res?.status === "success"
          ? res.message || "Emergency stop: all positions sold."
          : (res as { message?: string })?.message || "Emergency sell-all failed — check the engine.",
      );
    } catch (e: unknown) {
      setSidebarStatusMsg(e instanceof Error ? e.message : "Emergency sell-all failed — check the engine.");
    } finally {
      setSelling(false);
      dismiss();
      setTimeout(() => setSidebarStatusMsg(null), 6000);
    }
  };

  const confirm = () => {
    if (mode === "sell") {
      void handleSellAll();
    } else {
      dismiss();
      onHalt();
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4">
      <div className="surface max-w-md w-full p-6 space-y-4 relative">
        <div className="flex items-center gap-3">
          <IconShield width={24} height={24} className="text-white/75" />
          <h3 className="text-lg font-bold text-white">Emergency Stop</h3>
        </div>
        <p className="text-[13px] text-white/60 leading-relaxed">
          Choose how to stop live trading, then confirm. Both immediately halt the strategy and block any new orders.
        </p>

        <div className="space-y-2">
          <OptionCard
            title="Stop live trading"
            note="Open positions stay in the account."
            active={mode === "halt"}
            disabled={selling}
            onSelect={() => setMode("halt")}
          />
          <OptionCard
            title="Stop live trading and sell all"
            note="Market-sells every open position now — irreversible."
            active={mode === "sell"}
            disabled={selling}
            onSelect={() => setMode("sell")}
          />
        </div>

        <button
          type="button"
          disabled={selling}
          onClick={confirm}
          className="w-full rounded-full px-6 py-2.5 text-[13px] font-bold uppercase tracking-wide text-white transition-all transform active:scale-[0.99] disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2 bg-[#ff5a52] hover:bg-[#ff6c65]"
        >
          {selling && (
            <svg className="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
            </svg>
          )}
          {selling ? "Selling all positions…" : mode === "sell" ? "Confirm — stop and sell all" : "Confirm emergency stop"}
        </button>

        <p className="surface-flat px-4 py-3 text-[11.5px] text-white/45">
          <strong className="text-white/65">Audit Notice:</strong> permanently recorded on the WORM compliance audit trail.
        </p>
        <div className="flex justify-end pt-1">
          <button
            type="button"
            disabled={selling}
            className="btn"
            onClick={dismiss}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
