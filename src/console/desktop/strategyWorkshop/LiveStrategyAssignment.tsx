import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Listbox } from "@/console/shared/Listbox";
import {
  assignLiveStrategy,
  getHitlPolicy,
  getTradingSettings,
  listStrategies,
  listStrategyAssignments,
  type NamedStrategy,
  type StrategyAssignResult,
} from "@/lib/api";
import { saveSetupState } from "@/lib/desktopBridge";
import {
  SettingsApplyModal,
  type SettingsApplyChange,
} from "@/console/desktop/SettingsApplyModal";
import { ASSIGN_ACK } from "@/console/desktop/strategyWorkshop/acknowledgments";
import { assignmentPreview } from "@/console/desktop/strategyWorkshop/assignmentPreview";
import { WorkshopState } from "@/console/desktop/strategyWorkshop/WorkshopStates";
import { loadFailure, type LoadFailure } from "@/console/desktop/strategyWorkshop/errorText";

/**
 * "Live account strategy" (STW-4 #4630; UX-Konzept #4628, Ablauf 7).
 *
 * Assigns one of the named strategies (#4629) to the live account through the shared
 * SettingsApplyModal: confirm → record (`POST /api/strategies/{name}/assign-live`, a
 * `strategy_assignment` WORM entry) → persist → restart. The preview diff is computed
 * here from the strategy values over the delivery state; what gets persisted are the
 * `changes` of the server response — the server diff (after cross-clamping) is the
 * authoritative one.
 *
 * Neutral by construction (ADR-027 Regel 2/3): strategies in the order the engine
 * returns them, no preselection, no comparison figures, no link to or from a
 * comparison view. Flag off (`GET /api/strategies` → 404): the card does not render. No engine
 * answering (#5166): `engine-offline` with Retry instead of `error`.
 *
 * "Assigned: …" comes from `GET /api/strategy-assignments` (#4891), so it survives a
 * reload. It names the last sealed assignment and preselects nothing.
 *
 * Texts come from the catalogue namespace `console.strategyWorkshop` (#4892); the
 * acknowledgment sent to the engine comes from `acknowledgments.ts` and is never translated.
 */

type Load = "loading" | LoadFailure | "ready";

export function LiveStrategyAssignment() {
  const { t } = useTranslation();
  const [load, setLoad] = useState<Load>("loading");
  const [strategies, setStrategies] = useState<NamedStrategy[]>([]);
  const [hitlOn, setHitlOn] = useState(false);
  const [selected, setSelected] = useState("");
  const [assigned, setAssigned] = useState<string | null>(null);
  const [preview, setPreview] = useState<SettingsApplyChange[] | null>(null);
  const [reviewError, setReviewError] = useState(false);
  const resultRef = useRef<StrategyAssignResult | null>(null);

  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    // Promise.resolve(...) so a synchronous throw degrades to the error state instead of
    // crashing the hosting Settings page (HitlPolicyCard pattern).
    Promise.resolve()
      .then(() => listStrategies())
      .then(
        async (list) => {
          // Fail-closed: an unreadable policy counts as HITL off.
          const policy = await getHitlPolicy().catch(() => null);
          // Fail-soft (#4891): an unreadable assignment list only hides "Assigned: …".
          const assignments = await Promise.resolve()
            .then(() => listStrategyAssignments())
            .catch(() => []);
          if (!alive) return;
          setStrategies(list.strategien);
          setHitlOn(policy?.HITL_ENABLED === true);
          const live = assignments.find((a) => a.konto === "live");
          if (live) setAssigned(live.strategie);
          setLoad("ready");
        },
        (e: unknown) => {
          if (alive) setLoad(loadFailure(e));
        },
      );
    return () => {
      alive = false;
    };
  }, [attempt]);

  if (load === "hidden") return null;

  const openReview = async () => {
    const strategy = strategies.find((s) => s.name === selected);
    if (!strategy) return;
    setReviewError(false);
    try {
      const ts = await getTradingSettings();
      resultRef.current = null;
      setPreview(assignmentPreview(ts.meta, ts.settings, strategy.werte));
    } catch {
      setReviewError(true);
    }
  };

  return (
    <div className="surface p-6">
      <div className="eyebrow mb-1">{t("console.strategyWorkshop.T38")}</div>

      {load === "loading" && <WorkshopState kind="loading" />}

      {(load === "error" || load === "engine-offline") && (
        <WorkshopState
          kind={load}
          action={t("console.strategyWorkshop.state.error.action")}
          onAction={() => {
            setLoad("loading");
            setAttempt((n) => n + 1);
          }}
        />
      )}

      {load === "ready" && (
        <>
          {assigned && (
            <p className="text-[13px] text-white/70 mb-3">
              {t("console.strategyWorkshop.deviation.assigned")} {assigned}
            </p>
          )}
          <div className="flex items-end gap-3 flex-wrap">
            <label className="flex flex-col gap-1.5 text-[12px] text-white/70">
              {t("console.strategyWorkshop.T39")}
              <Listbox
                ariaLabel={t("console.strategyWorkshop.T39")}
                value={selected}
                onChange={setSelected}
                className="min-w-48"
                options={[
                  { value: "", label: "—" },
                  ...strategies.map((s) => ({ value: s.name, label: s.name })),
                ]}
              />
            </label>
            <button
              type="button"
              className="btn"
              disabled={!selected || !hitlOn}
              onClick={() => void openReview()}
            >
              {t("console.strategyWorkshop.T40")}
            </button>
          </div>
          {!hitlOn && (
            <p className="surface-flat mt-3 px-4 py-3 text-[13px] text-white/70" role="status">
              {t("console.strategyWorkshop.T43")}
            </p>
          )}
          {reviewError && (
            <p className="surface-flat mt-3 px-4 py-3 text-[13px] text-white/70" role="status">
              {t("console.strategyWorkshop.state.error.text")}
            </p>
          )}
        </>
      )}

      <SettingsApplyModal
        open={preview !== null}
        changes={preview ?? []}
        title={t("console.strategyWorkshop.T41")}
        emptyHint={t("console.strategyWorkshop.T42")}
        record={async (nonce, sizingAck) => {
          resultRef.current = await assignLiveStrategy(selected, sizingAck ?? ASSIGN_ACK, nonce);
        }}
        persist={async () => {
          const toPersist = Object.fromEntries(
            (resultRef.current?.changes ?? []).map((c) => [c.key, c.to]),
          );
          if (Object.keys(toPersist).length > 0) await saveSetupState(toPersist);
        }}
        onCancel={() => setPreview(null)}
        onApplied={() => {
          setAssigned(resultRef.current?.strategie ?? selected);
          setPreview(null);
          resultRef.current = null;
        }}
      />
    </div>
  );
}
