import { useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Listbox } from "@/console/shared/Listbox";
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
 * The shell side is STW-2d (#5117, `paper-slots.cjs`); the engine route `assign-paper` is not
 * part of it yet, until then the record fails and nothing is saved or restarted.
 *
 * Unlike the live card this writes `applied` in full, not `changes`: the server diff runs against
 * a `basis` and only the shell knows what stands in the file, so `changes` alone would leave the
 * values of an earlier strategy behind. `basis` holds the registry keys only, nothing else from
 * the file leaves the shell.
 *
 * Owner design of 02.11.2026 (#5117): the preview runs on selection and stands in the slot as
 * "Changes on confirm"; the dialog shows no second diff. A counter discards the answer of a
 * superseded selection. A strategy on another connected slot (`takenElsewhere`) stays listed but
 * is not choosable. The footer (`disconnect` left, "Review changes" right) renders for every
 * connected slot, so Disconnect stays when the selection is not offered: in the browser, without
 * the shell side (`hasPaperSlotSetup()` false) or without strategies.
 *
 * The record works with a stopped engine; the next start reads the file.
 *
 * Neutral by construction (ADR-027 Regel 2/3): strategies in the order the engine returns them,
 * no preselection, no comparison figures.
 */

interface Preview {
  basis: Record<string, string>;
  changes: SettingsApplyChange[];
}

export function PaperSlotStrategy({
  slot,
  strategies,
  assigned,
  takenElsewhere,
  disconnect,
  onAssigned,
}: {
  slot: PaperSlot;
  strategies: NamedStrategy[];
  assigned: string | null;
  /** Strategies assigned to another connected paper slot: listed, not choosable. */
  takenElsewhere: string[];
  /** The slot's Disconnect control, placed left in the footer. */
  disconnect: ReactNode;
  onAssigned: () => void;
}) {
  const { t } = useTranslation();
  const previewLabelId = useId();
  const [selected, setSelected] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const selection = useRef(0);
  const resultRef = useRef<StrategyAssignResult | null>(null);

  if (!slot.connected) return null;
  const offered = hasPaperSlotSetup() && strategies.length > 0;

  const choose = async (name: string) => {
    const mine = ++selection.current;
    setSelected(name);
    setPreview(null);
    setPreviewError(false);
    const strategy = strategies.find((s) => s.name === name);
    if (!strategy) return;
    try {
      const [setup, ts] = await Promise.all([getPaperSlotSetup(slot.slot), getTradingSettings()]);
      if (mine !== selection.current) return;
      if (setup === null) {
        setPreviewError(true);
        return;
      }
      const basis = Object.fromEntries(ts.meta.map((m) => [m.key, setup[m.key] ?? m.default]));
      setPreview({ basis, changes: assignmentPreview(ts.meta, basis, strategy.werte) });
    } catch {
      if (mine === selection.current) setPreviewError(true);
    }
  };

  const mustSucceed = (r: { ok: boolean }) => {
    if (!r.ok) throw new Error(t("console.strategyWorkshop.deviation.actionFailed"));
  };

  const taken = new Set(takenElsewhere);

  return (
    <div className="mt-3">
      {offered && (
        <>
          {assigned && (
            <p className="text-[13px] text-white/70 mb-3">
              {t("console.strategyWorkshop.deviation.assigned")} {assigned}
            </p>
          )}
          <label className="flex flex-col gap-1.5 text-[12px] text-white/70">
            {t("console.strategyWorkshop.T14")}
            <Listbox
              ariaLabel={t("console.strategyWorkshop.T14")}
              value={selected}
              onChange={(v) => void choose(v)}
              placeholder={t("console.strategyWorkshop.deviation.selectStrategy")}
              className="min-w-48"
              options={strategies.map((s) => ({ value: s.name, label: s.name, disabled: taken.has(s.name) }))}
            />
          </label>

          {preview && preview.changes.length > 0 && (
            <div role="group" aria-labelledby={previewLabelId} className="surface-flat mt-3 px-4 py-3 flex flex-col gap-2.5">
              <div id={previewLabelId} className="sublabel">
                {t("console.strategyWorkshop.deviation.changesOnConfirm")}
              </div>
              {preview.changes.map((c) => (
                <div key={c.key} className="flex justify-between items-baseline gap-3.5 text-[13px]">
                  <span className="text-white/70">{c.label}</span>
                  <span className="num text-[13px]">
                    <span className="text-white/45">{c.from}</span>
                    <span className="text-white/45 mx-1.5">→</span>
                    <span className="text-white/92">{c.to}</span>
                  </span>
                </div>
              ))}
              <p className="text-[12px] text-white/45 leading-snug">
                {t("console.strategyWorkshop.deviation.onlyThisAccount")}
              </p>
            </div>
          )}

          {previewError && (
            <p className="surface-flat mt-3 px-4 py-3 text-[13px] text-white/70" role="status">
              {t("console.strategyWorkshop.state.error.text")}
            </p>
          )}
        </>
      )}

      <div className="mt-3 flex items-center justify-between gap-3">
        <div>{disconnect}</div>
        {offered && (
          <button
            type="button"
            className="btn btn-primary"
            disabled={!preview}
            onClick={() => {
              resultRef.current = null;
              setReviewOpen(true);
            }}
          >
            {t("console.strategyWorkshop.T40")}
          </button>
        )}
      </div>

      <SettingsApplyModal
        open={reviewOpen && preview !== null}
        changes={preview?.changes ?? []}
        title={t("console.strategyWorkshop.deviation.assignToPaperAccount")}
        emptyHint={t("console.strategyWorkshop.T42")}
        acknowledgment={PAPER_ASSIGN_ACK}
        record={async (nonce) => {
          resultRef.current = await assignPaperStrategy(selected, slot.slot, preview?.basis ?? {}, PAPER_ASSIGN_ACK, nonce);
        }}
        persist={async () => {
          mustSucceed(await savePaperSlotSetup(slot.slot, resultRef.current?.applied ?? {}));
        }}
        restart={async () => {
          mustSucceed(await restartPaperSlotEngine(slot.slot));
        }}
        onCancel={() => setReviewOpen(false)}
        onApplied={() => {
          selection.current += 1;
          setReviewOpen(false);
          setPreview(null);
          setSelected("");
          resultRef.current = null;
          onAssigned();
        }}
      />
    </div>
  );
}
