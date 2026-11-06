import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchHealth } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import {
  getFleetSession,
  isDesktop,
  listAccounts,
  listPaperSlots,
  type AccountRow,
  type PaperSlot,
} from "@/lib/desktopBridge";
import { BrokerKeysCard } from "@/console/desktop/BrokerKeysCard";
import { StatusDot } from "@/console/shared/StatusDot";
import { WorkshopState } from "@/console/desktop/strategyWorkshop/WorkshopStates";
import { readChoice } from "@/console/desktop/strategyWorkshop/fleetChoice";
import { Slot, SlotName } from "@/console/desktop/strategyWorkshop/PaperAccountSlots";
import { liveSwitchGate } from "@/console/desktop/switchStatus";

/**
 * "Trading keys" card (Settings → System tab, directly under the AI-model card; #2863).
 *
 * #5176 (epic #5166 §2.4, Teil 1: Anzeige): all four accounts in one place, in fixed order — Paper
 * account 1 (the setup account on the main engine), Paper account 2 and 3 (the fleet slots) and
 * Live — each with the shell's masked number. The numbers come from the shell only (`listAccounts`
 * #5174 for Paper 1 and Live, `listPaperSlots` for Paper 2/3), never from the engine, so they stay
 * visible while the engine is off. Keychain storage is unchanged: Paper 1 and Live keep their
 * <BrokerKeysCard> slots; the live slot stays locked for Junior (allow_live=false).
 *
 * #5187 (epic #5169: one place with key entry): Paper 2/3 are operated here — `Slot` from
 * PaperAccountSlots.tsx (Connect behind Alpaca's prompt, Start, Disconnect, Rename), re-read after
 * every action. Paper account 1 gets `SlotName` (Rename) in its head. `restart-required` (the
 * choice differs from this session's fleet) locks them like on Trading and links to the restart
 * there. Strategies off (`choice ?? runtime`, like "Paper accounts"): Paper 2/3 are dimmed and
 * inert, with a link to the switch.
 *
 * #5178 (Plan #5166 §2.4, "main engine without its own block"): the Paper/Live switch of the main
 * engine lives in the account areas — the former Trading-tab switch card is gone.
 *   • The active account carries a "Main engine" StatusDot and the honest state line (LSR R4).
 *   • Main engine on paper + live keys stored → the Live area carries the critical CAPS pill
 *     "ENABLE LIVE TRADING" (classes copied from the sidebar's reference control, Guide §7/§13).
 *   • Main engine on live → Paper account 1 carries the `.btn` "Switch main engine to paper".
 * Both only RAISE the one-shot store signals; the switch itself (WORM enable, restart, reconcile)
 * runs in the GLOBAL LiveConsentModal (#2465), exactly as from the sidebar. Locks: liveSwitchGate.
 */
// Plan #5176 Offen 1: Trading → Strategies, the page of "Enable strategies" and its restart (#5166 Teil 2).
const STRATEGIES_HREF = "#trading-strategies";

// React 18 knows no `inert` prop; an empty string sets the attribute.
const INERT = { inert: "" } as Record<string, string>;

function strategiesOff(choice: boolean | null, session: { fleetActive: boolean } | null): boolean {
  return choice === false || (choice === null && session?.fleetActive === false);
}

export function TradingKeysCard() {
  const { t } = useTranslation();
  const allowLive = useStore((s) => s.allowLive);
  const paper = useStore((s) => s.paperTrading);
  const setPaperTrading = useStore((s) => s.setPaperTrading);
  const systemHalted = useStore((s) => s.systemHalted);
  const engineServing = useStore((s) => s.engineServing);
  const modeSwitch = useStore((s) => s.modeSwitch);
  const switchReconciling = useStore((s) => s.switchReconciling);
  const switchConfirmFailed = useStore((s) => s.switchConfirmFailed);
  const switchError = useStore((s) => s.switchError);
  const setRequestLiveConsent = useStore((s) => s.setRequestLiveConsent);
  const setRequestSwitchToPaper = useStore((s) => s.setRequestSwitchToPaper);

  const [accounts, setAccounts] = useState<AccountRow[] | null>(null);
  const [slots, setSlots] = useState<PaperSlot[] | "error" | null>(null);
  const [off, setOff] = useState(false);
  const [restart, setRestart] = useState(false);
  const [renameFailed, setRenameFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const reload = () => setAttempt((n) => n + 1);

  // Seed the shared store on mount (harmless if the always-on health poll also does it) — moved
  // with the switch from the former Trading-tab card, so the areas never guess the main engine's account.
  useEffect(() => {
    let alive = true;
    fetchHealth().then((h) => {
      if (alive && h && h.status !== "starting") setPaperTrading(h.paper_trading ?? true);
    });
    return () => {
      alive = false;
    };
  }, [setPaperTrading]);

  useEffect(() => {
    let alive = true;
    const accountsRead = Promise.resolve()
      .then(() => listAccounts())
      .catch((err) => {
        console.warn("TradingKeysCard: listAccounts failed → numbers of Paper 1 and Live show —", err);
        return null;
      });
    const slotsRead = Promise.resolve()
      .then(() => listPaperSlots())
      .catch((err) => {
        console.warn("TradingKeysCard: listPaperSlots failed → Paper 2/3 show an error", err);
        return "error" as const;
      });
    void Promise.all([accountsRead, slotsRead, readChoice(), getFleetSession()]).then(
      ([acc, list, choice, session]) => {
        if (!alive) return;
        setAccounts(acc);
        setSlots(list);
        setOff(strategiesOff(choice, session));
        // #5142, like "Paper accounts": a choice that differs from this session's fleet.
        setRestart(session !== null && choice !== null && choice !== session.fleetActive);
      },
    );
    return () => {
      alive = false;
    };
  }, [attempt]);

  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!isDesktop() && !(!!import.meta.env.DEV && !isTest)) return null;

  const masked = (slot: AccountRow["slot"]) => accounts?.find((a) => a.slot === slot)?.accountMasked ?? null;
  const list = Array.isArray(slots) ? slots : [];
  const paperRow = list.find((s) => s.slot === "paper1");
  const paperName = paperRow?.name ?? `${t("console.strategyWorkshop.extra.paperAccount")} 1`;
  const fleetSlots = list.filter((s) => s.slot !== "paper1");
  const liveName = t("console.strategyWorkshop.extra.liveAccount");

  const gate = liveSwitchGate({ paperTrading: paper, switchReconciling, modeSwitch, systemHalted, allowLive });
  const isLive = paper === false;

  // The handlers are the correctness boundary (a keyboard activation can't bypass them); the
  // buttons' `disabled` is the honesty boundary — both read the same gate as the sidebar.
  const enableLive = () => {
    if (!gate.canEnableLive) return;
    setRequestLiveConsent(true);
  };
  const switchToPaper = () => {
    if (!gate.canSwitchToPaper) return;
    setRequestSwitchToPaper(true);
  };

  // One honest state line — what the engine ACTUALLY trades right now. LSR R4 (#2251): while a
  // just-restarted engine is still booting, show a transient confirm instead of a stale/wrong label;
  // if /health never becomes authoritative within the cap, say so. The reconcile state is published
  // to the store by the global LiveConsentModal (#2465).
  const stateLine = (
    <>
      <div className="mb-3 px-4 py-3 rounded-lg bg-white/[0.03] border border-white/8">
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
        <div className="text-[12px] text-[#ffb4af] bg-[#ff5a52]/10 border border-[#ff5a52]/25 rounded-lg px-3 py-2 mb-3">
          {switchError}
        </div>
      )}
    </>
  );

  const paperSlot = () => (
    <>
      {!isLive && stateLine}
      {isLive && (
        <div className="mb-4">
          <button className="btn" onClick={switchToPaper} disabled={!gate.canSwitchToPaper}>
            Switch main engine to paper
          </button>
        </div>
      )}
    </>
  );

  const liveSlot = (keysPresent: boolean) => (
    <>
      {isLive && stateLine}
      {/* Critical CAPS pill (Guide §7/§13): classes copied 1:1 from the sidebar's "Live Trading"
          reference control; the 178 px frame gives it the sidebar's width. Junior never sees it. */}
      {!isLive && keysPresent && allowLive !== false && (
        <div className="w-[178px] mb-4">
          <button
            onClick={enableLive}
            disabled={!gate.canEnableLive}
            className="w-full rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide uppercase text-white bg-[#00c27a] hover:bg-[#00d687] transition-all transform active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60"
          >
            ENABLE LIVE TRADING
          </button>
        </div>
      )}
    </>
  );

  // Paper account 1 runs on the main engine, so its state is the main engine's. When the main
  // engine trades the live account (#5142), the paper account is not running.
  const paperStatus = paperRow?.mainEngineLive ? (
    <StatusDot>{t("console.strategyWorkshop.extra.engineStatus.stopped")}</StatusDot>
  ) : (
    <span role="status">
      <StatusDot tone={engineServing ? "on" : "neutral"}>
        {engineServing ? t("console.strategyWorkshop.extra.engineStatus.running") : t("console.tradingKeys.engineOffline")}
      </StatusDot>
    </span>
  );

  return (
    <div className="surface p-6">
      <div className="eyebrow mb-2">Trading keys</div>
      <p className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">{t("console.tradingKeys.subtitle")}</p>
      <BrokerKeysCard
        mode="paper"
        embedded
        label={paperName}
        heading={
          paperRow ? (
            <SlotName
              slot={paperRow}
              name={paperName}
              locked={restart}
              nameClass="eyebrow"
              onChanged={reload}
              onFailed={setRenameFailed}
            />
          ) : (
            <div className="eyebrow">{paperName}</div>
          )
        }
        status={paperStatus}
        accountMasked={masked("paper1")}
        mainEngine={paper === true}
        engineSlot={paperSlot}
      />
      {renameFailed && (
        <div className="mt-3">
          <WorkshopState kind="error" text={t("console.strategyWorkshop.deviation.actionFailed")} />
        </div>
      )}
      {slots === "error" && (
        <div className="mt-4">
          <WorkshopState kind="error" action={t("console.strategyWorkshop.state.error.action")} onAction={reload} />
        </div>
      )}
      {fleetSlots.length > 0 && restart && (
        <div className="border-t border-white/5 pt-4 mt-4 flex flex-col gap-2">
          <WorkshopState kind="empty" text={t("console.strategyWorkshop.deviation.restartRequired")} />
          <a href={STRATEGIES_HREF} className="btn-link self-start">
            {t("console.tradingKeys.restartOnStrategies")}
          </a>
        </div>
      )}
      {fleetSlots.length > 0 && off && (
        <div className="border-t border-white/5 pt-4 mt-4">
          <a href={STRATEGIES_HREF} className="btn-link">
            {t("console.tradingKeys.enableStrategies")}
          </a>
        </div>
      )}
      {fleetSlots.length > 0 && (
        <div {...(off ? { ...INERT, "aria-disabled": "true", className: "opacity-40" } : {})}>
          {fleetSlots.map((s, i) => (
            <Slot
              key={s.slot}
              slot={s}
              index={i + 2}
              locked={restart}
              onChanged={reload}
              className="border-t border-white/5 pt-4 mt-4"
              nameClass="eyebrow"
            />
          ))}
        </div>
      )}
      <BrokerKeysCard
        mode="live"
        embedded
        label={liveName}
        heading={<div className="eyebrow">{liveName}</div>}
        locked={allowLive === false}
        accountMasked={masked("live")}
        mainEngine={isLive}
        engineSlot={liveSlot}
      />
    </div>
  );
}
