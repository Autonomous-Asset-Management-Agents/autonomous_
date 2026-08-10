import { useState, useEffect } from "react";
import { fetchHealth, fetchEntitlementStatus } from "@/lib/api";
import { isDesktop, claimBeta } from "@/lib/desktopBridge";
import { useStore } from "@/console/store/useStore";
import { BrokerKeysCard } from "@/console/desktop/BrokerKeysCard";
import { StatusDot } from "@/console/shared/StatusDot";

/**
 * Trading account (#1425 · #60) — the "Trading Account" Settings card: the Paper⇄Live account tiles,
 * the honest state line, and both key slots (Paper/Live Trading Keys) which remain HOSTED here.
 *
 * #2465: the go-live consent + the whole switch logic (WORM enable, restart, reconcile) moved to the
 * GLOBAL LiveConsentModal (mounted in DesktopApp) so the confirm opens OVER the Overview without a
 * Settings detour. This card only RAISES the one-shot store signals (requestLiveConsent /
 * requestSwitchToPaper) and REFLECTS the reconcile display state the modal publishes to the store.
 * Junior (allow_live=false) is paper-only → no tiles, the live-keys slot is locked, CTA is Upgrade.
 */
export function LiveTradingSwitchCard() {
  const paper = useStore((s) => s.paperTrading);
  const setPaperTrading = useStore((s) => s.setPaperTrading);
  const allowLive = useStore((s) => s.allowLive);
  const setEntitlement = useStore((s) => s.setEntitlement);
  const engineServing = useStore((s) => s.engineServing);
  const modeSwitch = useStore((s) => s.modeSwitch);
  const switchReconciling = useStore((s) => s.switchReconciling);
  const switchConfirmFailed = useStore((s) => s.switchConfirmFailed);
  const switchError = useStore((s) => s.switchError);
  const setRequestLiveConsent = useStore((s) => s.setRequestLiveConsent);
  const setRequestSwitchToPaper = useStore((s) => s.setRequestSwitchToPaper);
  const [upgrading, setUpgrading] = useState(false);
  const [upgradeNote, setUpgradeNote] = useState<string | null>(null);

  // Seed the shared store on mount (harmless if the always-on health poll also does it).
  useEffect(() => {
    let alive = true;
    fetchHealth().then((h) => {
      if (alive && h && h.status !== "starting") setPaperTrading(h.paper_trading ?? true);
    });
    return () => {
      alive = false;
    };
  }, [setPaperTrading]);

  // Desktop-only: the cloud edition manages its account out-of-band.
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!isDesktop() && !(!!import.meta.env.DEV && !isTest)) return null;

  // #1915: Junior (allow_live=false) — live trading is a Senior feature. No tiles; the live-keys slot
  // is locked and the CTA is Upgrade (the engine 403s live-arming for BASIC anyway).
  if (allowLive === false) {
    const onUpgrade = async () => {
      if (upgrading) return;
      setUpgrading(true);
      setUpgradeNote(null);
      try {
        const res = await claimBeta();
        if (res.status === "claimed") {
          const s = await fetchEntitlementStatus();
          if (s)
            setEntitlement(s.tier, s.can_upgrade, s.simulation_enabled ?? false, s.allow_live ?? false);
        } else {
          setUpgradeNote(
            res.error === "desktop-only"
              ? "Available in the desktop app."
              : "Upgrade failed — please try again.",
          );
        }
      } catch {
        setUpgradeNote("Upgrade failed — please try again.");
      } finally {
        setUpgrading(false);
      }
    };
    return (
      <div className="surface p-6">
        <div className="flex items-center justify-between mb-2">
          <div className="eyebrow">Trading account</div>
          <StatusDot tone={paper === false ? "off" : "on"}>{paper === false ? "Live" : "Paper"}</StatusDot>
        </div>
        <p className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">
          <strong className="text-white/70">Live trading is a Senior feature.</strong> Junior runs
          paper-only — upgrade to trade real money on your Alpaca live account.
        </p>

        <BrokerKeysCard mode="paper" embedded />
        <BrokerKeysCard mode="live" embedded locked />

        <div className="mt-5 flex flex-col items-end">
          {upgradeNote && (
            <div className="text-[12px] text-[#ff8a80] bg-[#ff453a]/10 border border-[#ff453a]/25 rounded-lg px-3 py-2 mb-3">
              {upgradeNote}
            </div>
          )}
          <button
            className="w-[178px] rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide uppercase text-white bg-[#00c27a] hover:bg-[#00d687] border-none transition-all transform active:scale-[0.97] disabled:opacity-60"
            onClick={() => void onUpgrade()}
            disabled={upgrading}
          >
            {upgrading ? "UPGRADING…" : "UPGRADE TO SENIOR"}
          </button>
        </div>
      </div>
    );
  }

  const isLive = paper === false;
  // A switch is in progress while the mode-switch overlay is up or the modal's reconcile is running.
  const switching = switchReconciling || modeSwitch != null;

  // #2465: the tiles only RAISE the one-shot signals. → Live opens the global WORM-audited consent
  // (over the Overview, no Settings detour); → Paper switches back (handled in LiveConsentModal).
  const select = (live: boolean) => {
    if (switching || paper == null || live === isLive) return;
    if (live) setRequestLiveConsent(true);
    else setRequestSwitchToPaper(true);
  };

  const accountTile = (live: boolean, title: string, body: string) => {
    const active = isLive === live;
    const ring = "border-white/20 bg-white/[0.06]";
    return (
      <button
        aria-label={live ? "Live" : "Paper"}
        onClick={() => select(live)}
        disabled={switching || paper == null || active}
        className={`p-4 rounded-xl border text-left transition-all ${
          active ? ring : "border-white/10 hover:border-white/20 bg-white/[0.03]"
        }`}
      >
        <div className="flex items-center gap-2 mb-1.5">
          <span className={`text-[13px] font-semibold ${active ? "text-white" : "text-white/70"}`}>
            {title}
          </span>
          {active && (
            <StatusDot tone={live ? "off" : "on"} className="ml-auto !text-[11px]">
              active
            </StatusDot>
          )}
        </div>
        <div className="text-[11px] text-white/30 leading-relaxed">{body}</div>
      </button>
    );
  };

  return (
    <div className={`surface p-6 ${isLive ? "border border-[#ff453a]/40" : ""}`}>
      <div className="eyebrow mb-2">Trading account</div>
      <p className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">
        Which Alpaca account the engine trades. Switching to live is a deliberate, audited step —
        recorded on the tamper-evident WORM chain.
      </p>

      <div className="grid grid-cols-2 gap-3 mb-4">
        {accountTile(
          false,
          "Paper",
          "Simulated trading with virtual money — no real capital at risk. Practice and validate strategies safely.",
        )}
        {accountTile(
          true,
          "Live",
          "Executes real orders with real money on your Alpaca live account. You are responsible for every order (EU AI Act Art. 14).",
        )}
      </div>

      {/* One honest state line — what the engine ACTUALLY trades right now. LSR R4 (#2251): while a
          just-restarted engine is still booting, show a transient confirm instead of a stale/wrong
          label; if /health never becomes authoritative within the cap, say so. Reconcile state is
          published to the store by the global LiveConsentModal (#2465). */}
      <div className="mb-4 px-4 py-3 rounded-lg bg-white/[0.03] border border-white/8">
        {switchReconciling ? (
          <StatusDot tone="neutral">Confirming the account with the engine…</StatusDot>
        ) : switchConfirmFailed && !engineServing ? (
          <StatusDot tone="neutral">
            Account unconfirmed — the engine isn&apos;t responding. Check the engine status.
          </StatusDot>
        ) : (
          <StatusDot tone={isLive ? "off" : "neutral"}>
            {isLive
              ? "Live — orders execute with real money on your Alpaca live account."
              : "Paper — simulated trading, no real capital at risk."}
          </StatusDot>
        )}
      </div>

      {switchError && (
        <div className="text-[12px] text-[#ff8a80] bg-[#ff453a]/10 border border-[#ff453a]/25 rounded-lg px-3 py-2 mb-3">
          {switchError}
        </div>
      )}

      <BrokerKeysCard mode="paper" embedded />
      <BrokerKeysCard mode="live" embedded />
    </div>
  );
}
