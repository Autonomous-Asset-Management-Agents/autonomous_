import { useState } from "react";
import { claimBeta } from "@/lib/desktopBridge";
import { useStore } from "@/console/store/useStore";
import { StatusDot } from "@/console/shared/StatusDot";

/**
 * Upgrade / editions modal (GTM-1) — the tier ladder opened from the sidebar. It is TIER-AWARE:
 *   • Junior (BASIC) sees it as an upgrade: Senior is the primary green CTA (claim the free-beta
 *     unlock in-app via claimBeta, ADR-GTM-1b).
 *   • Senior (PRO) sees it as "plans & editions": the Senior card is marked "your plan" (no CTA),
 *     so the actionable choices are Institutional (contact sales) and Developer (OSS/GitHub).
 * Only Senior is claimable in-app; the other two are contact/link CTAs. Design: the Overview
 * kill-switch confirm modal (fixed overlay + surface card + pill buttons + StatusDot).
 */

const GITHUB_URL = "https://github.com/Autonomous-Asset-Management-Agents/autonomous_";
const SALES_MAILTO =
  "mailto:sales@aaagents.de?subject=AAAgents%20Institutional%20enquiry";

function Feature({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2 text-[12px] text-white/60 leading-snug">
      <span className="text-[#00c27a] mt-0.5 shrink-0 leading-none">✓</span>
      <span>{children}</span>
    </li>
  );
}

export function UpgradeModal({ onClose, refetch }: { onClose: () => void; refetch: () => Promise<void> }) {
  const tier = useStore((s) => s.tier);
  const isSenior = tier === "PRO"; // PRO is the console's "Senior" token.
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const claimSenior = async () => {
    if (busy) return;
    setBusy(true);
    setNote(null);
    try {
      const res = await claimBeta();
      if (res.status === "claimed") {
        await refetch(); // Senior now → entitlement flips → close.
        onClose();
      } else if (res.status === "cap-reached") {
        setNote("Beta full — Senior is now €0.99/month.");
      } else {
        setNote(res.error === "desktop-only" ? "Available in the desktop app." : "Upgrade failed — please try again.");
      }
    } catch {
      setNote("Upgrade failed — please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="surface w-full max-w-3xl p-6 rounded-xl border border-white/10 shadow-2xl max-h-[88vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-4 mb-1">
          <div>
            <h3 className="text-lg font-bold text-white">{isSenior ? "Plans & editions" : "Upgrade your plan"}</h3>
            <div className="mt-1.5">
              <StatusDot tone="on" className="!text-[11px]">You&apos;re on {isSenior ? "Senior" : "Junior"}</StatusDot>
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-white/40 hover:text-white/80 text-[15px] leading-none px-1 shrink-0"
          >
            ✕
          </button>
        </div>

        <p className="text-[12px] text-white/40 mb-5 max-w-xl leading-relaxed">
          {isSenior ? (
            <>
              You&apos;re on <strong className="text-white/70">Senior</strong>. Explore the other editions below —
              or reach out about Institutional.
            </>
          ) : (
            <>
              Choose how you want to run AAAgents. Senior is <strong className="text-white/70">free during the beta</strong>{" "}
              and unlocks locally — no cloud login, no payment.
            </>
          )}
        </p>

        {/* Tier cards */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {/* Senior — current plan for Senior, else the primary claimable upgrade */}
          <div className="flex flex-col rounded-xl border border-white/20 bg-white/[0.05] p-4">
            <div className="flex items-center justify-between">
              <div className="eyebrow">Senior</div>
              <StatusDot tone="on" className="!text-[10px]">{isSenior ? "your plan" : "recommended"}</StatusDot>
            </div>
            <div className="mt-1 mb-3">
              {isSenior ? (
                <span className="text-[20px] font-bold text-white/92">Active</span>
              ) : (
                <>
                  <span className="text-[20px] font-bold text-white/92">Free</span>
                  <span className="text-[11px] text-white/40"> · in beta</span>
                </>
              )}
            </div>
            <ul className="space-y-1.5 mb-4 flex-1">
              <Feature>Live trading with <strong className="text-white/70">real money</strong> (Alpaca live)</Feature>
              <Feature>One-click Paper ⇄ Live switch</Feature>
              <Feature>Full Round Table — the complete agent panel</Feature>
              <Feature>Unlimited backtesting</Feature>
              <Feature>Automated daily reports to your <strong className="text-white/70">private channels</strong> (Slack, Telegram, email)</Feature>
            </ul>
            {isSenior ? (
              <div className="w-full rounded-lg px-5 py-2 text-center text-[12px] font-semibold text-white/45 border border-white/10">
                ✓ Your current plan
              </div>
            ) : (
              <>
                {note && <div className="text-[11px] text-white/55 mb-2">{note}</div>}
                <button
                  onClick={() => void claimSenior()}
                  disabled={busy}
                  className="w-full rounded-lg px-5 py-2 text-[12px] font-bold tracking-wide text-white bg-[#00c27a] hover:bg-[#00d687] border border-transparent transition-all transform active:scale-[0.98] disabled:opacity-60"
                >
                  {busy ? "CLAIMING…" : "CLAIM SENIOR"}
                </button>
              </>
            )}
          </div>

          {/* Developer — open source */}
          <div className="flex flex-col rounded-xl border border-white/10 bg-white/[0.03] p-4">
            <div className="eyebrow">Developer</div>
            <div className="mt-1 mb-3">
              <span className="text-[20px] font-bold text-white/92">Open source</span>
            </div>
            <ul className="space-y-1.5 mb-4 flex-1">
              <Feature>Full source code</Feature>
              <Feature>Self-host (BORA container stack)</Feature>
              <Feature>Bring your own broker keys</Feature>
            </ul>
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className="btn w-full rounded-lg px-5 py-2 text-center text-[12px] font-semibold"
            >
              View on GitHub
            </a>
          </div>

          {/* Institutional — contact sales */}
          <div className="flex flex-col rounded-xl border border-white/10 bg-white/[0.03] p-4">
            <div className="eyebrow">Institutional</div>
            <div className="mt-1 mb-3">
              <span className="text-[20px] font-bold text-white/92">Talk to us</span>
            </div>
            <ul className="space-y-1.5 mb-4 flex-1">
              <Feature>Compliance-ready — WORM · RTS 6 · MiFID II</Feature>
              <Feature>Custom cloud (BYOC) + multi-seat</Feature>
              <Feature>24/7 support + dedicated manager</Feature>
            </ul>
            <a
              href={SALES_MAILTO}
              className="btn w-full rounded-lg px-5 py-2 text-center text-[12px] font-semibold"
            >
              Contact sales
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
