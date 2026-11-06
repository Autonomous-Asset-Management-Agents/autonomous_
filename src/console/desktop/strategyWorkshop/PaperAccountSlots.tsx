import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { listStrategies, listStrategyAssignments, type NamedStrategy, type StrategyAssignment } from "@/lib/api";
import {
  canRenamePaperSlot,
  connectPaperSlot,
  disconnectPaperSlot,
  getFleetSession,
  listPaperSlots,
  relaunchApp,
  renamePaperSlot,
  startPaperSlotEngine,
  type PaperSlot,
} from "@/lib/desktopBridge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ConsoleAlertDialogContent } from "@/console/shared/ConsoleAlertDialogContent";
import { StatusDot } from "@/console/shared/StatusDot";
import { PaperSlotStrategy } from "@/console/desktop/strategyWorkshop/PaperSlotStrategy";
import { WorkshopState } from "@/console/desktop/strategyWorkshop/WorkshopStates";
import { readChoice } from "@/console/desktop/strategyWorkshop/fleetChoice";
import { loadFailure } from "@/console/desktop/strategyWorkshop/errorText";
import { useBrokerAuthorization } from "@/console/desktop/useBrokerAuthorization";

/**
 * "Paper accounts" (STW-2b #4934; UX-Konzept #4628, Ablauf 2, console cut).
 *
 * Three slots paper1–paper3 in fixed order — no sort, no highlighted slot (ADR-027 Regel 1).
 * The shell owns everything with an effect: it validates the keys, checks the account number
 * against the other slots and the live account BEFORE storing (A2-P3), stores, masks, starts and
 * stops the engines. Without a shell (`listPaperSlots()` is null, the browser) the card does not
 * render.
 *
 * Operator choice (STW-2f #5125): the header carries the title and the sentence. "On" is `choice ?? runtime`, two separate questions:
 * - choice: what the operator set, ONLY a boolean `strategyProfilesEnabled` in setup.json (the
 *   string "true" is no choice, like in `engine-fleet.cjs::profilesFlagOn`);
 * - runtime: does the engine carry the strategy routes (`GET /api/strategies` answers, or 404).
 * `getEngineFleet()` is no source: the fleet instance lives for the whole session and says only
 * that one was built at start. Choice false → `off` without asking the engine; 404 without a
 * choice → `off`; 404 with choice true → the slots, without strategies until the app restarts.
 * #5166 moved the switch itself to "Enable strategies" on Trading → Strategies (StrategiesGate);
 * this card only reads the choice.
 * The strategies fail or no engine answers (#5166): the slots still show — they come from the
 * shell — without strategies, under an `engine-offline` (no HTTP status) or `error` notice with
 * Retry. Only an unreadable slot list is a card error.
 *
 * Keys live only in the form state of one slot. They go to `connectPaperSlot` once per submit;
 * nothing logs or renders them. They are cleared on success, on Cancel and on "Use other keys";
 * after a failure they stay in the form so the operator can correct them (#5142). The list is
 * re-read from the shell after every action, never edited optimistically. No push of the paper
 * engines' status exists (STW-6b), so it refreshes on mount and after each action.
 *
 * `low-memory` (A2-P5, STW-6c): the shell holds a start back and answers with a code; the card
 * knows neither the free memory nor the threshold. "Start anyway" overrides it for this one
 * start, Cancel returns to `engine-off`. An engine held back at app start shows `engine-off`.
 *
 * "Strategy for this account" (T14, STW-2c): `PaperSlotStrategy` per slot. An unreadable
 * assignment list only hides "Assigned: …", like on the live card.
 *
 * #5187 (epic #5169, one place for keys): this card renders per slot only `SlotStrategyRow` — name,
 * status, masked number and T14, no key field. Connect, Disconnect, Start and Rename are `Slot`,
 * exported and rendered by `TradingKeysCard` (System → Trading keys) for Paper account 2/3; Paper
 * account 1 gets `SlotName` in its head there. An empty slot here links to `#system`. The code stays
 * in this file so the diff carries only what changes (plan #5187, Option A).
 *
 * Layout (owner design of 02.11.2026, STW-2d #5117): header = name, then status dot before the
 * masked number; Disconnect is a `.btn-link` in the footer of `Slot` (#5187: its own, T14 is no
 * longer in `Slot`); state notices, `engine-off` included, close the block. A strategy assigned to
 * another *connected* slot is locked in the selection; disconnecting does not free the mirror entry
 * (plan #4953, Offen 4), so a disconnected slot locks nothing.
 *
 * STW-6d (#5142, owner decisions of 03.11.2026):
 * - Paper account 1 is the setup wizard's account on the main engine (`setupAccount`): no Connect,
 *   no Disconnect, no T14 (#5123 decides that), no engine-off. Without setup keys it points to the
 *   setup; with `mainEngineLive` it says that the main engine trades the live account. Its masked
 *   number comes from the shell (STW-6e #5148); "—" while the shell cannot determine it.
 * - `restart-required`: the fleet is built once at app start. When the operator's choice differs
 *   from `getFleetSession().fleetActive`, Connect, Disconnect and Start are locked and "Restart app"
 *   restarts the whole app after a confirmation. Nothing is rebuilt at runtime.
 * - `invalid_keys` and `network` have their own text.
 *
 * STW-6e (#5148, owner decision of 03.11.2026): each slot shows `name ?? "Paper account N"`. Rename
 * edits the name in place (name field, Save, Cancel); the shell validates and stores it. Offered on
 * the setup account and on connected slots, only when the shell has `renamePaperSlot`; locked in
 * `restart-required`. A refused name keeps the old one and shows `actionFailed`.
 */

type Load = "loading" | "hidden" | "off" | "error" | "ready";

export function PaperAccountSlots({ onAssigned }: { onAssigned?: () => void } = {}) {
  const { t } = useTranslation();
  const [load, setLoad] = useState<Load>("loading");
  const [slots, setSlots] = useState<PaperSlot[]>([]);
  const [strategies, setStrategies] = useState<NamedStrategy[]>([]);
  const [assignments, setAssignments] = useState<StrategyAssignment[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [strategiesFailure, setStrategiesFailure] = useState<"engine-offline" | "error" | null>(null);
  const [restartRequired, setRestartRequired] = useState(false);
  const [restartOpen, setRestartOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    // No bridge → hidden without asking anything. Then choice ?? runtime (see the docblock).
    Promise.resolve()
      .then(async () => {
        const list = await listPaperSlots();
        if (list === null) return "hidden" as const;
        const choice = await readChoice();
        // #5142: a choice that differs from the fleet of this session needs an app restart.
        const session = await getFleetSession();
        const restart = session !== null && choice !== null && choice !== session.fleetActive;
        if (alive) setRestartRequired(restart);
        if (choice === false) return "off" as const;
        let strategien: NamedStrategy[];
        try {
          ({ strategien } = await listStrategies());
        } catch (e) {
          const failure = loadFailure(e);
          // #5166: the engine failed or did not answer — the slots come from the shell and still show.
          if (failure !== "hidden") return { list, strategien: [], assigned: [], failure };
          // 404: the engine of this session runs without the strategy routes.
          if (choice === null) return "off" as const;
          return { list, strategien: [], assigned: [], failure: null };
        }
        const assigned = await Promise.resolve()
          .then(() => listStrategyAssignments())
          .catch(() => []);
        return { list, strategien, assigned, failure: null };
      })
      .then(
        (read) => {
          if (!alive) return;
          if (read === "hidden" || read === "off") {
            setLoad(read);
            return;
          }
          setSlots(read.list);
          setStrategies(read.strategien);
          setAssignments(read.assigned);
          setStrategiesFailure(read.failure);
          setLoad("ready");
        },
        () => {
          if (alive) setLoad("error");
        },
      );
    return () => {
      alive = false;
    };
  }, [attempt]);

  if (load === "hidden") return null;

  const reload = () => setAttempt((n) => n + 1);
  const title = t("console.strategyWorkshop.T10");
  const assignedTo = (slot: string) => assignments.find((a) => a.konto === slot)?.strategie ?? null;

  return (
    <section id="paper-accounts" aria-label={title} className="surface p-6">
      <div className="eyebrow mb-1.5">{title}</div>
      <p className="text-[13px] text-white/70 leading-snug">{t("console.strategyWorkshop.fleet.sentence")}</p>

      {restartRequired && (load === "ready" || load === "off") && (
        <div className="mt-3">
          <WorkshopState
            kind="empty"
            text={t("console.strategyWorkshop.deviation.restartRequired")}
            action={t("console.strategyWorkshop.deviation.restartApp")}
            onAction={() => setRestartOpen(true)}
          />
        </div>
      )}
      <AlertDialog open={restartOpen} onOpenChange={setRestartOpen}>
        <ConsoleAlertDialogContent className="max-w-[520px] rounded-[20px] border-white/10 bg-[#1c1c1e] text-white">
          <AlertDialogHeader>
            <AlertDialogTitle>{t("console.strategyWorkshop.deviation.restartConfirm")}</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="btn">{t("console.strategyWorkshop.extra.cancel")}</AlertDialogCancel>
            <AlertDialogAction className="btn" onClick={() => void relaunchApp()}>
              {t("console.strategyWorkshop.deviation.restart")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </ConsoleAlertDialogContent>
      </AlertDialog>

      {load === "loading" && (
        <div className="mt-3">
          <WorkshopState kind="loading" />
        </div>
      )}

      {load === "error" && (
        <div className="mt-3">
          <WorkshopState
            kind="error"
            action={t("console.strategyWorkshop.state.error.action")}
            onAction={() => {
              setLoad("loading");
              reload();
            }}
          />
        </div>
      )}

      {load === "ready" && strategiesFailure && (
        <div className="mt-3">
          <WorkshopState
            kind={strategiesFailure}
            action={t("console.strategyWorkshop.state.error.action")}
            onAction={reload}
          />
        </div>
      )}

      {load === "ready" && (
        <div className="mt-4 flex flex-col gap-3 border-t pt-4" style={{ borderColor: "rgba(255,255,255,0.06)" }}>
          {slots.map((s, i) => (
            <SlotStrategyRow
              key={s.slot}
              slot={s}
              index={i + 1}
              strategies={strategies}
              assigned={assignedTo(s.slot)}
              takenElsewhere={slots
                .filter((o) => o.connected && o.slot !== s.slot)
                .map((o) => assignedTo(o.slot))
                .filter((name): name is string => name !== null)}
              onAssigned={() => {
                reload();
                onAssigned?.();
              }}
            />
          ))}
        </div>
      )}
    </section>
  );
}

type Notice =
  | "duplicate"
  | "connectFailed"
  | "authorizationFailed"
  | "invalidKeys"
  | "network"
  | "actionFailed"
  | "lowMemory"
  | null;

const CONNECT_NOTICE: Record<string, Notice> = {
  duplicate_account: "duplicate",
  invalid_keys: "invalidKeys",
  network: "network",
};
const ERROR_TEXT: Partial<Record<NonNullable<Notice>, string>> = {
  connectFailed: "console.strategyWorkshop.deviation.connectFailed",
  authorizationFailed: "console.strategyWorkshop.deviation.authorizationFailed",
  invalidKeys: "console.strategyWorkshop.deviation.invalidKeys",
  network: "console.strategyWorkshop.deviation.network",
  actionFailed: "console.strategyWorkshop.deviation.actionFailed",
};

const NAME_CLASS = "text-[13px] font-semibold text-white/90";

/** Status dot (not for a stopped engine, which shows `engine-off`) before the masked number. */
function SlotNumber({ slot }: { slot: PaperSlot }) {
  const { t } = useTranslation();
  const status = slot.engineStatus;
  const tone = status === "running" ? "on" : status === "error" ? "off" : "neutral";
  return (
    <div className="flex items-center gap-3">
      {status && status !== "stopped" && (
        <StatusDot tone={tone}>{t(`console.strategyWorkshop.extra.engineStatus.${status}`, { defaultValue: "—" })}</StatusDot>
      )}
      <span data-account-number className="num text-[13px] text-white/70">
        {slot.accountMasked ?? "—"}
      </span>
    </div>
  );
}

/**
 * #5187: one slot on Trading → Strategies. Name, status, masked number and T14; no key field. An
 * empty slot links to System → Trading keys, where `Slot` connects it.
 */
function SlotStrategyRow({
  slot,
  index,
  strategies,
  assigned,
  takenElsewhere,
  onAssigned,
}: {
  slot: PaperSlot;
  index: number;
  strategies: NamedStrategy[];
  assigned: string | null;
  takenElsewhere: string[];
  onAssigned: () => void;
}) {
  const { t } = useTranslation();
  const name = slot.name ?? `${t("console.strategyWorkshop.extra.paperAccount")} ${index}`;
  const setup = slot.setupAccount;
  return (
    <div role="group" aria-label={name} className="surface-flat px-4 py-3">
      <div className="flex items-center justify-between gap-4">
        <span className={NAME_CLASS}>{name}</span>
        {slot.connected ? (
          <SlotNumber slot={slot} />
        ) : (
          !setup && (
            <div className="flex items-center gap-3">
              <StatusDot>{t("console.tradingKeys.notConnected")}</StatusDot>
              <a href="#system" className="btn-link">
                {t("console.strategyWorkshop.extra.connectUnderTradingKeys")}
              </a>
            </div>
          )
        )}
      </div>
      {/* #5142: the setup account has no T14 here (#5123). */}
      {!setup && (
        <PaperSlotStrategy
          slot={slot}
          strategies={strategies}
          assigned={assigned}
          takenElsewhere={takenElsewhere}
          disconnect={null}
          onAssigned={onAssigned}
        />
      )}
      {setup && !slot.connected && (
        <div className="mt-3">
          <WorkshopState kind="empty" text={t("console.strategyWorkshop.deviation.setupKeysMissing")} />
        </div>
      )}
      {setup && slot.connected && slot.mainEngineLive && (
        <div className="mt-3">
          <WorkshopState kind="empty" text={t("console.strategyWorkshop.deviation.mainEngineLive")} />
        </div>
      )}
    </div>
  );
}

/**
 * #5187: name and Rename of one slot (STW-6e #5148), taken out of `Slot`. `Slot` uses it, and
 * Trading keys puts it into the head of Paper account 1. `onFailed(true)` after a refused name,
 * `onFailed(false)` when a new attempt starts.
 */
export function SlotName({
  slot,
  name,
  locked,
  nameClass = NAME_CLASS,
  onChanged,
  onFailed,
}: {
  slot: PaperSlot;
  name: string;
  locked: boolean;
  nameClass?: string;
  onChanged: () => void;
  onFailed: (failed: boolean) => void;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const renamable = canRenamePaperSlot() && (slot.setupAccount || slot.connected);

  const saveName = async (e: FormEvent) => {
    e.preventDefault();
    onFailed(false);
    setBusy(true);
    const r = await renamePaperSlot(slot.slot, draft);
    setBusy(false);
    setEditing(false);
    if (!r.ok) {
      onFailed(true);
      return;
    }
    onChanged();
  };

  if (editing) {
    return (
      <form className="flex items-center gap-2" onSubmit={(e) => void saveName(e)}>
        <input
          className="input"
          aria-label={t("console.strategyWorkshop.deviation.accountName")}
          value={draft}
          autoComplete="off"
          onChange={(e) => setDraft(e.target.value)}
        />
        <button type="submit" className="btn" disabled={busy || locked || draft.trim() === ""}>
          {t("console.strategyWorkshop.deviation.save")}
        </button>
        <button type="button" className="btn" onClick={() => setEditing(false)}>
          {t("console.strategyWorkshop.extra.cancel")}
        </button>
      </form>
    );
  }
  return (
    <div className="flex items-center gap-3">
      <span className={nameClass}>{name}</span>
      {renamable && (
        <button
          type="button"
          className="btn-link"
          disabled={busy || locked}
          onClick={() => {
            setDraft(name);
            setEditing(true);
          }}
        >
          {t("console.strategyWorkshop.deviation.rename")}
        </button>
      )}
    </div>
  );
}

/**
 * One slot's operation: key form behind Alpaca's prompt, Start, Disconnect, Rename. #5187: rendered
 * by `TradingKeysCard` for Paper account 2/3; T14 is no longer part of it (`SlotStrategyRow`).
 */
export function Slot({
  slot,
  index,
  locked,
  onChanged,
  className = "surface-flat px-4 py-3",
  nameClass,
}: {
  slot: PaperSlot;
  index: number;
  /** #5142 restart-required: Connect, Disconnect, Start and Rename (#5148) are locked. */
  locked: boolean;
  onChanged: () => void;
  className?: string;
  nameClass?: string;
}) {
  const { t } = useTranslation();
  const [formOpen, setFormOpen] = useState(false);
  const [keyId, setKeyId] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const { authorize, dialog: authorizationDialog } = useBrokerAuthorization();

  const name = slot.name ?? `${t("console.strategyWorkshop.extra.paperAccount")} ${index}`;
  const setup = slot.setupAccount;

  const clearKeys = () => {
    setKeyId("");
    setSecret("");
  };

  // Keys are cleared only on success: after a failure the operator corrects them in place.
  // #5155: Alpaca's authorization prompt comes first; connectPaperSlot runs only after the ALLOW
  // answer is written (fail-closed). DENY keeps the keys in the form, like a failure.
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setNotice(null);
    setBusy(true);
    const answer = await authorize(slot.slot, keyId);
    if (answer !== "allow") {
      setBusy(false);
      if (answer === "error") setNotice("authorizationFailed");
      return;
    }
    const r = await connectPaperSlot(slot.slot, keyId, secret);
    setBusy(false);
    if (r.ok) {
      clearKeys();
      setFormOpen(false);
      if (r.lowMemory) setNotice("lowMemory");
      onChanged();
      return;
    }
    setNotice(("code" in r && CONNECT_NOTICE[r.code]) || "connectFailed");
  };

  const act = async (fn: () => Promise<{ ok: boolean; code?: string }>) => {
    setNotice(null);
    setBusy(true);
    const r = await fn();
    setBusy(false);
    if (!r.ok) setNotice(r.code === "low_memory" ? "lowMemory" : "actionFailed");
    onChanged();
  };

  const status = slot.engineStatus;

  return (
    <div role="group" aria-label={name} className={className}>
      <div className="flex items-center justify-between gap-4">
        <SlotName
          slot={slot}
          name={name}
          locked={busy || locked}
          nameClass={nameClass}
          onChanged={onChanged}
          onFailed={(failed) => setNotice(failed ? "actionFailed" : null)}
        />
        {slot.connected ? (
          <SlotNumber slot={slot} />
        ) : (
          !setup &&
          !formOpen && (
            <button type="button" className="btn" disabled={locked} onClick={() => setFormOpen(true)}>
              {t("console.strategyWorkshop.T13")}
            </button>
          )
        )}
      </div>

      {/* #5142: the setup account has no Disconnect. #5187: Disconnect in the slot's own footer. */}
      {!setup && slot.connected && (
        <div className="mt-3">
          <button type="button" className="btn-link" disabled={busy || locked} onClick={() => setConfirmOpen(true)}>
            {t("console.strategyWorkshop.T15")}
          </button>
        </div>
      )}

      {setup && !slot.connected && (
        <div className="mt-3">
          <WorkshopState kind="empty" text={t("console.strategyWorkshop.deviation.setupKeysMissing")} />
        </div>
      )}
      {setup && slot.connected && slot.mainEngineLive && (
        <div className="mt-3">
          <WorkshopState kind="empty" text={t("console.strategyWorkshop.deviation.mainEngineLive")} />
        </div>
      )}

      {!setup && !slot.connected && formOpen && (
        <form className="mt-3 flex flex-col gap-3" onSubmit={(e) => void submit(e)}>
          <label className="flex flex-col gap-1 text-[13px] text-white/70">
            {t("console.strategyWorkshop.T11")}
            <input
              className="input"
              value={keyId}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => setKeyId(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-[13px] text-white/70">
            {t("console.strategyWorkshop.T12")}
            <input
              className="input"
              type="password"
              value={secret}
              autoComplete="off"
              onChange={(e) => setSecret(e.target.value)}
            />
          </label>
          <div className="flex gap-2">
            <button type="submit" className="btn" disabled={busy || locked || !keyId || !secret}>
              {t("console.strategyWorkshop.T13")}
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => {
                clearKeys();
                setNotice(null);
                setFormOpen(false);
              }}
            >
              {t("console.strategyWorkshop.extra.cancel")}
            </button>
          </div>
        </form>
      )}
      {authorizationDialog}

      {notice === "duplicate" && (
        <div className="mt-3">
          <WorkshopState
            kind="duplicate-account"
            action={t("console.strategyWorkshop.state.duplicate-account.action")}
            onAction={() => {
              clearKeys();
              setNotice(null);
            }}
          />
        </div>
      )}
      {notice === "lowMemory" && (
        <div className="mt-3">
          <WorkshopState
            kind="low-memory"
            action={t("console.strategyWorkshop.state.low-memory.action")}
            onAction={() => void act(() => startPaperSlotEngine(slot.slot, { force: true }))}
            actionDisabled={locked}
            secondaryAction={t("console.strategyWorkshop.extra.cancel")}
            onSecondaryAction={() => setNotice(null)}
          />
        </div>
      )}
      {notice && ERROR_TEXT[notice] && (
        <div className="mt-3">
          <WorkshopState kind="error" text={t(ERROR_TEXT[notice]!)} />
        </div>
      )}
      {!setup && slot.connected && status === "stopped" && notice !== "lowMemory" && (
        <div className="mt-3">
          <WorkshopState
            kind="engine-off"
            action={t("console.strategyWorkshop.state.engine-off.action")}
            onAction={() => void act(() => startPaperSlotEngine(slot.slot))}
            actionDisabled={locked}
          />
        </div>
      )}

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <ConsoleAlertDialogContent className="max-w-[520px] rounded-[20px] border-white/10 bg-[#1c1c1e] text-white">
          <AlertDialogHeader>
            <AlertDialogTitle>{t("console.strategyWorkshop.deviation.disconnectConfirmation")}</AlertDialogTitle>
            <p className="text-[13px] text-white/70">{name}</p>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="btn">{t("console.strategyWorkshop.extra.cancel")}</AlertDialogCancel>
            <AlertDialogAction className="btn" onClick={() => void act(() => disconnectPaperSlot(slot.slot))}>
              {t("console.strategyWorkshop.T15")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </ConsoleAlertDialogContent>
      </AlertDialog>
    </div>
  );
}
