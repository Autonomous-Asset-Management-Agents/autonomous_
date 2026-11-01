import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { listStrategies, listStrategyAssignments, type NamedStrategy, type StrategyAssignment } from "@/lib/api";
import {
  connectPaperSlot,
  disconnectPaperSlot,
  listPaperSlots,
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

/**
 * "Paper accounts" (STW-2b #4934; UX-Konzept #4628, Ablauf 2, console cut).
 *
 * Three slots paper1–paper3 in fixed order — no sort, no highlighted slot (ADR-027 Regel 1).
 * The shell owns everything with an effect: it validates the keys, checks the account number
 * against the other slots and the live account BEFORE storing (A2-P3), stores, masks, starts and
 * stops the engines. Its side comes with STW-6b; until then `listPaperSlots()` is null and the
 * card does not render, like it does in the browser and with the flag off
 * (`GET /api/strategies` → 404).
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
 */

type Load = "loading" | "hidden" | "error" | "ready";

export function PaperAccountSlots({ onAssigned }: { onAssigned?: () => void } = {}) {
  const { t } = useTranslation();
  const [load, setLoad] = useState<Load>("loading");
  const [slots, setSlots] = useState<PaperSlot[]>([]);
  const [strategies, setStrategies] = useState<NamedStrategy[]>([]);
  const [assignments, setAssignments] = useState<StrategyAssignment[]>([]);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    // No bridge → hidden without asking the engine; then the flag decides (404 → hidden).
    Promise.resolve()
      .then(async () => {
        const list = await listPaperSlots();
        if (list === null) return null;
        const { strategien } = await listStrategies();
        const assigned = await Promise.resolve()
          .then(() => listStrategyAssignments())
          .catch(() => []);
        return { list, strategien, assigned };
      })
      .then(
        (read) => {
          if (!alive) return;
          if (read === null) {
            setLoad("hidden");
            return;
          }
          setSlots(read.list);
          setStrategies(read.strategien);
          setAssignments(read.assigned);
          setLoad("ready");
        },
        (e: unknown) => {
          if (!alive) return;
          const isNotFound =
            typeof e === "object" && e !== null && "status" in e && (e as { status: unknown }).status === 404;
          setLoad(isNotFound ? "hidden" : "error");
        },
      );
    return () => {
      alive = false;
    };
  }, [attempt]);

  if (load === "hidden") return null;

  const reload = () => setAttempt((n) => n + 1);
  const title = t("console.strategyWorkshop.T10");

  return (
    <section id="paper-accounts" aria-label={title} className="surface p-6">
      <div className="eyebrow mb-3">{title}</div>

      {load === "loading" && <WorkshopState kind="loading" />}

      {load === "error" && (
        <WorkshopState
          kind="error"
          action={t("console.strategyWorkshop.state.error.action")}
          onAction={() => {
            setLoad("loading");
            reload();
          }}
        />
      )}

      {load === "ready" && (
        <div className="flex flex-col gap-3">
          {slots.map((s, i) => (
            <Slot
              key={s.slot}
              slot={s}
              index={i + 1}
              strategies={strategies}
              assigned={assignments.find((a) => a.konto === s.slot)?.strategie ?? null}
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
  onAssigned,
  onChanged,
}: {
  slot: PaperSlot;
  index: number;
  strategies: NamedStrategy[];
  assigned: string | null;
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
        <span className="text-[13px] text-white/90">{name}</span>
        {slot.connected ? (
          <div className="flex items-center gap-3">
            <span className="num text-[13px] text-white/70">{slot.accountMasked ?? "—"}</span>
            {statusKey && status !== "stopped" && (
              <StatusDot tone={tone}>{t(statusKey, { defaultValue: "—" })}</StatusDot>
            )}
            <button type="button" className="btn" disabled={busy} onClick={() => setConfirmOpen(true)}>
              {t("console.strategyWorkshop.T15")}
            </button>
          </div>
        ) : (
          !formOpen && (
            <button type="button" className="btn" onClick={() => setFormOpen(true)}>
              {t("console.strategyWorkshop.T13")}
            </button>
          )
        )}
      </div>

      {slot.connected && status === "stopped" && notice !== "lowMemory" && (
        <div className="mt-3">
          <WorkshopState
            kind="engine-off"
            action={t("console.strategyWorkshop.state.engine-off.action")}
            onAction={() => void act(() => startPaperSlotEngine(slot.slot))}
          />
        </div>
      )}

      <PaperSlotStrategy slot={slot} strategies={strategies} assigned={assigned} onAssigned={onAssigned} />

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
