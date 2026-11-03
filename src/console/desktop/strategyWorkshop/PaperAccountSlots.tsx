import { useEffect, useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { listStrategies, listStrategyAssignments, type NamedStrategy, type StrategyAssignment } from "@/lib/api";
import {
  connectPaperSlot,
  disconnectPaperSlot,
  getSetupState,
  listPaperSlots,
  saveSetupState,
  startPaperSlotEngine,
  type PaperSlot,
} from "@/lib/desktopBridge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { StatusDot } from "@/console/shared/StatusDot";
import { PaperSlotStrategy } from "@/console/desktop/strategyWorkshop/PaperSlotStrategy";
import { WorkshopState } from "@/console/desktop/strategyWorkshop/WorkshopStates";
import { FleetSwitch } from "@/console/desktop/strategyWorkshop/FleetSwitch";

/**
 * "Paper accounts" (STW-2b #4934; UX-Konzept #4628, Ablauf 2, console cut).
 *
 * Three slots paper1–paper3 in fixed order — no sort, no highlighted slot (ADR-027 Regel 1).
 * The shell owns everything with an effect: it validates the keys, checks the account number
 * against the other slots and the live account BEFORE storing (A2-P3), stores, masks, starts and
 * stops the engines. Without a shell (`listPaperSlots()` is null, the browser) the card does not
 * render.
 *
 * Fleet switch (STW-2f #5125, owner design of 03.11.2026): the header carries the title, the
 * sentence and a switch. "On" is `choice ?? runtime`, two separate questions:
 * - choice: what the operator set, ONLY a boolean `strategyProfilesEnabled` in setup.json (the
 *   string "true" is no choice, like in `engine-fleet.cjs::profilesFlagOn`);
 * - runtime: does the engine carry the strategy routes (`GET /api/strategies` answers, or 404).
 * `getEngineFleet()` is no source: the fleet instance lives for the whole session and says only
 * that one was built at start. Choice false → `off` without asking the engine; 404 without a
 * choice → `off` (was: hidden); 404 with choice true → the slots, without strategies until the app
 * restarts. Switching writes the boolean and re-reads; it stops, disconnects and restarts nothing.
 *
 * Keys live only in the form state of one slot. They go to `connectPaperSlot` exactly once and
 * are cleared right after, on success and on failure; nothing logs or renders them. The list is
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
 * Layout (owner design of 02.11.2026, STW-2d #5117): header = name, then status dot before the
 * masked number; Disconnect is a `.btn-link` in the footer of `PaperSlotStrategy`; state notices,
 * `engine-off` included, close the block. A strategy assigned to another *connected* slot is
 * locked in the selection; disconnecting does not free the mirror entry (plan #4953, Offen 4),
 * so a disconnected slot locks nothing.
 */

type Load = "loading" | "hidden" | "off" | "error" | "ready";

const isNotFound = (e: unknown) =>
  typeof e === "object" && e !== null && "status" in e && (e as { status: unknown }).status === 404;

/** The operator's choice: only a boolean counts; anything else, or a failing read, is no choice. */
async function readChoice(): Promise<boolean | null> {
  try {
    const s = await getSetupState();
    return typeof s.strategyProfilesEnabled === "boolean" ? s.strategyProfilesEnabled : null;
  } catch (err) {
    console.warn("paperAccountSlots: getSetupState failed → no operator choice, runtime decides", err);
    return null;
  }
}

export function PaperAccountSlots({ onAssigned }: { onAssigned?: () => void } = {}) {
  const { t } = useTranslation();
  const [load, setLoad] = useState<Load>("loading");
  const [slots, setSlots] = useState<PaperSlot[]>([]);
  const [strategies, setStrategies] = useState<NamedStrategy[]>([]);
  const [assignments, setAssignments] = useState<StrategyAssignment[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [switching, setSwitching] = useState(false);
  const [writeFailed, setWriteFailed] = useState(false);
  const titleId = useId();
  const sentenceId = useId();

  useEffect(() => {
    let alive = true;
    // No bridge → hidden without asking anything. Then choice ?? runtime (see the docblock).
    Promise.resolve()
      .then(async () => {
        const list = await listPaperSlots();
        if (list === null) return "hidden" as const;
        const choice = await readChoice();
        if (choice === false) return "off" as const;
        let strategien: NamedStrategy[];
        try {
          ({ strategien } = await listStrategies());
        } catch (e) {
          if (!isNotFound(e)) throw e;
          // 404: the engine of this session runs without the strategy routes.
          if (choice === null) return "off" as const;
          return { list, strategien: [], assigned: [] };
        }
        const assigned = await Promise.resolve()
          .then(() => listStrategyAssignments())
          .catch(() => []);
        return { list, strategien, assigned };
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

  // Writes the boolean, then re-reads both sources. Stops, disconnects and restarts nothing.
  const toggle = async (next: boolean) => {
    setWriteFailed(false);
    setSwitching(true);
    try {
      await saveSetupState({ strategyProfilesEnabled: next });
      reload();
    } catch (err) {
      console.warn("paperAccountSlots: saveSetupState failed → switch unchanged", err);
      setWriteFailed(true);
    } finally {
      setSwitching(false);
    }
  };

  return (
    <section id="paper-accounts" aria-label={title} className="surface p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div id={titleId} className="eyebrow mb-1.5">
            {title}
          </div>
          <p id={sentenceId} className="text-[13px] text-white/70 leading-snug">
            {t("console.strategyWorkshop.fleet.sentence")}
          </p>
        </div>
        <FleetSwitch
          checked={load === "ready"}
          onChange={(next) => void toggle(next)}
          disabled={load === "loading" || switching}
          labelledBy={titleId}
          describedBy={sentenceId}
        />
      </div>

      {writeFailed && (
        <div className="mt-3">
          <WorkshopState kind="error" />
        </div>
      )}

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

      {load === "ready" && (
        <div className="mt-4 flex flex-col gap-3 border-t pt-4" style={{ borderColor: "rgba(255,255,255,0.06)" }}>
          {slots.map((s, i) => (
            <Slot
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
              onChanged={reload}
            />
          ))}
        </div>
      )}
    </section>
  );
}

type Notice = "duplicate" | "connectFailed" | "actionFailed" | "lowMemory" | null;

function Slot({
  slot,
  index,
  strategies,
  assigned,
  takenElsewhere,
  onAssigned,
  onChanged,
}: {
  slot: PaperSlot;
  index: number;
  strategies: NamedStrategy[];
  assigned: string | null;
  takenElsewhere: string[];
  onAssigned: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [formOpen, setFormOpen] = useState(false);
  const [keyId, setKeyId] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const name = `${t("console.strategyWorkshop.extra.paperAccount")} ${index}`;
  const clearKeys = () => {
    setKeyId("");
    setSecret("");
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const id = keyId, sec = secret;
    clearKeys();
    setNotice(null);
    setBusy(true);
    const r = await connectPaperSlot(slot.slot, id, sec);
    setBusy(false);
    if (r.ok) {
      setFormOpen(false);
      if (r.lowMemory) setNotice("lowMemory");
      onChanged();
      return;
    }
    setNotice("code" in r && r.code === "duplicate_account" ? "duplicate" : "connectFailed");
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
  const statusKey = status ? `console.strategyWorkshop.extra.engineStatus.${status}` : null;
  const tone = status === "running" ? "on" : status === "error" ? "off" : "neutral";

  return (
    <div role="group" aria-label={name} className="surface-flat px-4 py-3">
      <div className="flex items-center justify-between gap-4">
        <span className="text-[13px] font-semibold text-white/90">{name}</span>
        {slot.connected ? (
          <div className="flex items-center gap-3">
            {statusKey && status !== "stopped" && (
              <StatusDot tone={tone}>{t(statusKey, { defaultValue: "—" })}</StatusDot>
            )}
            <span className="num text-[13px] text-white/70">{slot.accountMasked ?? "—"}</span>
          </div>
        ) : (
          !formOpen && (
            <button type="button" className="btn" onClick={() => setFormOpen(true)}>
              {t("console.strategyWorkshop.T13")}
            </button>
          )
        )}
      </div>

      <PaperSlotStrategy
        slot={slot}
        strategies={strategies}
        assigned={assigned}
        takenElsewhere={takenElsewhere}
        disconnect={
          <button type="button" className="btn-link" disabled={busy} onClick={() => setConfirmOpen(true)}>
            {t("console.strategyWorkshop.T15")}
          </button>
        }
        onAssigned={onAssigned}
      />

      {!slot.connected && formOpen && (
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
            <button type="submit" className="btn" disabled={busy || !keyId || !secret}>
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
            secondaryAction={t("console.strategyWorkshop.extra.cancel")}
            onSecondaryAction={() => setNotice(null)}
          />
        </div>
      )}
      {(notice === "connectFailed" || notice === "actionFailed") && (
        <div className="mt-3">
          <WorkshopState
            kind="error"
            text={t(
              notice === "connectFailed"
                ? "console.strategyWorkshop.deviation.connectFailed"
                : "console.strategyWorkshop.deviation.actionFailed",
            )}
          />
        </div>
      )}
      {slot.connected && status === "stopped" && notice !== "lowMemory" && (
        <div className="mt-3">
          <WorkshopState
            kind="engine-off"
            action={t("console.strategyWorkshop.state.engine-off.action")}
            onAction={() => void act(() => startPaperSlotEngine(slot.slot))}
          />
        </div>
      )}

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent className="max-w-[520px] rounded-[20px] border-white/10 bg-[#1c1c1e] text-white">
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
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
