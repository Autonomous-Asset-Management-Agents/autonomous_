import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  assignPaperStrategy,
  getTradingSettings,
  type NamedStrategy,
  type StrategyAssignResult,
} from "@/lib/api";
import {
  getPaperSlotSetup,
  hasPaperSlotSetup,
  restartPaperSlotEngine,
  savePaperSlotSetup,
  type PaperSlot,
} from "@/lib/desktopBridge";
import { SettingsApplyModal, type SettingsApplyChange } from "@/console/desktop/SettingsApplyModal";
import { PAPER_ASSIGN_ACK } from "@/console/desktop/strategyWorkshop/acknowledgments";
import { assignmentPreview } from "@/console/desktop/strategyWorkshop/assignmentPreview";

/**
 * "Strategy for this account" (T14, STW-2c #4953; UX-Konzept #4628, Ablauf 2, console cut).
 *
 * The strategy and the assignment stay with the engine the console talks to; the chain entry
 * carries `konto="paper<n>"`. The values reach the slot like they reach the live account, through
 * a setup.json, here the slot's own: confirm → record → save that file → restart that engine.
 *
 * Unlike the live card this writes `applied` in full, not `changes`: the server diff runs against
 * a `basis` and only the shell knows what stands in the file, so `changes` alone would leave the
 * values of an earlier strategy behind. `basis` holds the registry keys only, nothing else from
 * the file leaves the shell.
 *
 * The record works with a stopped engine; the next start reads the file. Without the shell side
 * (follow-up cut) `hasPaperSlotSetup()` is false and nothing renders.
 *
 * Neutral by construction (ADR-027 Regel 2/3): strategies in the order the engine returns them,
 * no preselection, no comparison figures.
 */

interface Review {
  basis: Record<string, string>;
  changes: SettingsApplyChange[];
}

export function PaperSlotStrategy({
  slot,
  strategies,
  assigned,
  onAssigned,
}: {
  slot: PaperSlot;
  strategies: NamedStrategy[];
  assigned: string | null;
  onAssigned: () => void;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [reviewError, setReviewError] = useState(false);
  const resultRef = useRef<StrategyAssignResult | null>(null);

  if (!slot.connected || !hasPaperSlotSetup() || strategies.length === 0) return null;

  const openReview = async () => {
    const strategy = strategies.find((s) => s.name === selected);
    if (!strategy) return;
    setReviewError(false);
    try {
      const [setup, ts] = await Promise.all([getPaperSlotSetup(slot.slot), getTradingSettings()]);
      if (setup === null) {
        setReviewError(true);
        return;
      }
      const basis = Object.fromEntries(ts.meta.map((m) => [m.key, setup[m.key] ?? m.default]));
      resultRef.current = null;
      setReview({ basis, changes: assignmentPreview(ts.meta, basis, strategy.werte) });
    } catch {
      setReviewError(true);
    }
  };

  const mustSucceed = (r: { ok: boolean }) => {
    if (!r.ok) throw new Error(t("console.strategyWorkshop.deviation.actionFailed"));
  };

  return (
    <div className="mt-3">
      {assigned && (
        <p className="text-[13px] text-white/70 mb-3">
          {t("console.strategyWorkshop.deviation.assigned")} {assigned}
        </p>
      )}
      <div className="flex items-end gap-3 flex-wrap">
        <label className="flex flex-col gap-1.5 text-[12px] text-white/70">
          {t("console.strategyWorkshop.T14")}
          <select
            aria-label={t("console.strategyWorkshop.T14")}
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            className="bg-transparent border border-white/12 rounded-lg px-3 py-2 text-[13px] text-white/90"
          >
            <option value="">—</option>
            {strategies.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn" disabled={!selected} onClick={() => void openReview()}>
          {t("console.strategyWorkshop.T40")}
        </button>
      </div>
      {reviewError && (
        <p className="surface-flat mt-3 px-4 py-3 text-[13px] text-white/70" role="status">
          {t("console.strategyWorkshop.state.error.text")}
        </p>
      )}

      <SettingsApplyModal
        open={review !== null}
        changes={review?.changes ?? []}
        title={t("console.strategyWorkshop.deviation.assignToPaperAccount")}
        emptyHint={t("console.strategyWorkshop.T42")}
        record={async (nonce) => {
          resultRef.current = await assignPaperStrategy(selected, slot.slot, review?.basis ?? {}, PAPER_ASSIGN_ACK, nonce);
        }}
        persist={async () => {
          mustSucceed(await savePaperSlotSetup(slot.slot, resultRef.current?.applied ?? {}));
        }}
        restart={async () => {
          mustSucceed(await restartPaperSlotEngine(slot.slot));
        }}
        onCancel={() => setReview(null)}
        onApplied={() => {
          setReview(null);
          setSelected("");
          resultRef.current = null;
          onAssigned();
        }}
      />
    </div>
  );
}
