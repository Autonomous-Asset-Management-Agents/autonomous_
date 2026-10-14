import { useEffect, useMemo, useRef, useState } from "react";
import { isDesktop, saveSetupState } from "@/lib/desktopBridge";
import { OnOffSegment } from "@/console/shared/SegmentPicker";
import { getTradingSettings, postTradingSettings } from "@/lib/api";
import type { TradingSettingsApplyResult } from "@/lib/api";
import type { AgentSetting, TradingSettings } from "@/lib/api";
import { agentInfo } from "@/console/live/agentGlossary";
import { usePendingSettings } from "@/console/desktop/usePendingSettings";
import {
  SettingsApplyModal,
  type SettingsApplyChange,
} from "@/console/desktop/SettingsApplyModal";

/**
 * "Round table" card (Settings → Trading tab) — UXC-1 S4 (#3157, Epic #3151).
 *
 * Rev. 3 (owner decision 2026-09-02, supersedes plan A1/A2): the card mirrors the
 * decision pipeline in engine-true order as three stages —
 *
 *   1. Security selection  (mean voters: which names qualify as a buy)
 *   2. Risk overlay        (pre-trade veto + regime conditioning: is the buy acted on)
 *   3. Position sizing     (volatility sizing input: how much capital it receives)
 *
 * — and EVERY agent with an enable flag is toggleable, including the Drawdown Guard
 * pre-trade veto and the Regime overlay. Overlay weights are inert (#3084), so those
 * rows carry an On/Off toggle but no slider. Role designations (`role_title`) use industry
 * terminology and are served by the S2 GET.
 *
 * Everything is DYNAMIC from the S2 engine GET (#3155) — roster, stages (via role),
 * bounds, defaults and effective values; nothing hardcoded or mirrored (epic
 * guardrail 4). Mean-voter toggles (OnOffSegment, UXC-1 S8 #3177) bind to the S1 abstain flags (weight→0 would
 * NOT work, base_agent clamps to min_weight); sliders take per-agent min/max from
 * the GET. While IMPLIED_VOL_FORECAST_ENABLED is on, VIXAware arrives as role
 * "size_input" and sits in the sizing stage without a consensus share.
 *
 * NOTHING persists from this card. Every edit is a pending value; "Apply changes"
 * hands the pending map to the S3 WORM-audited apply flow (#3156: record → persist →
 * restart, values are read at engine boot). "Default settings" only resets the
 * pending values to the engine-shipped defaults from the GET.
 *
 * Copy rules (BaFin execution-only, #2866 pattern): factual mechanics only — no
 * profiles, no recommendations, no performance language. Percent appears only in
 * value cells, never in prose (guard test scopes the ban to description nodes).
 */

const APPLY_ACK =
  "I confirm these trading-parameter changes. They apply to all future trading decisions and are recorded on a tamper-evident audit trail.";

interface RoundTableCardProps {
  /** Test/extension seam: when given, Apply hands the pending diff here INSTEAD of
   *  running the built-in WORM flow (record → persist → restart, #3156). */
  onApply?: (pending: Record<string, string>) => void;
}

type StageId = "selection" | "overlay" | "sizing";

const STAGES: { id: StageId; title: string; question: string; sub: string }[] = [
  {
    id: "selection",
    title: "Security selection",
    question: "Which names do we buy?",
    sub: "Each agent scores the candidate and the weighted consensus decides. The share column shows each active agent's part of that consensus.",
  },
  {
    id: "overlay",
    title: "Risk overlay",
    question: "Do we buy at all?",
    sub: "A pre-trade veto can block the buy, and the regime overlay damps directional signals in risk-off markets.",
  },
  {
    id: "sizing",
    title: "Position sizing",
    question: "How much do we buy?",
    sub: "How much capital an approved buy receives.",
  },
];

const stageOf = (role: AgentSetting["role"]): StageId =>
  role === "mean_voter" ? "selection" : role === "size_input" ? "sizing" : "overlay";

const ROLE_BADGES: Record<AgentSetting["role"], string | null> = {
  mean_voter: null,
  veto_guard: "Pre-trade veto",
  conditioner: "Regime overlay",
  size_input: "Sizing input",
};

const enabledKey = (a: AgentSetting): string => a.enable_flag;
const weightKey = (a: AgentSetting): string => a.weight_key;

/** Engine baseline as a string map (setup.json convention). */
const toMap = (
  agents: readonly AgentSetting[],
  pick: (a: AgentSetting) => { enabled: boolean; weight: number },
): Record<string, string> => {
  const out: Record<string, string> = {};
  for (const a of agents) {
    const v = pick(a);
    if (a.enable_flag) out[enabledKey(a)] = v.enabled ? "true" : "false";
    if (a.weight_key) out[weightKey(a)] = String(v.weight);
  }
  return out;
};

// #3187: applying a settings change restarts the engine, which is unreachable for a few seconds.
// The card polls the roster back through that window rather than stranding on "Engine not available".
// ~20 × 1.5 s ≈ 30 s cap, comfortably covering a ~14 s engine restart.
const RECONNECT_ATTEMPTS = 20;
const RECONNECT_DELAY_MS = 1500;

export function RoundTableCard({ onApply }: RoundTableCardProps) {
  const [settings, setSettings] = useState<TradingSettings | null>(null);
  const [failed, setFailed] = useState(false);
  // #3187: the engine is briefly unreachable during the settings-restart the card itself triggers.
  // Show a reconnecting state and keep the last-known roster visible instead of "not available".
  const [reconnecting, setReconnecting] = useState(false);
  // C (owner decision): engine-inactive agents are collapsed behind a counter.
  const [showInactive, setShowInactive] = useState(false);
  // #3156: the built-in WORM apply flow — pending diff staged for the shared modal.
  const [applyChanges, setApplyChanges] = useState<SettingsApplyChange[] | null>(null);
  const applyDiffRef = useRef<Record<string, string>>({});
  const applyResultRef = useRef<TradingSettingsApplyResult | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  // #3187: load the agent roster. `retriesLeft > 0` (the post-restart refresh) tolerates a transient
  // outage — it flags `reconnecting`, keeps `failed` false so the last-known rows stay visible, and
  // polls until the engine returns, then swaps in the fresh values. Exhausted → the honest "not
  // available". The initial mount uses a single attempt (retriesLeft 0): a first-load failure IS a
  // genuinely-down engine, not a restart-in-progress.
  const loadRoster = (retriesLeft = 0) => {
    getTradingSettings()
      .then((s) => {
        if (!mountedRef.current) return;
        setSettings(s);
        setFailed(false);
        setReconnecting(false);
      })
      .catch(() => {
        if (!mountedRef.current) return;
        if (retriesLeft > 0) {
          setReconnecting(true);
          retryRef.current = setTimeout(
            () => loadRoster(retriesLeft - 1),
            RECONNECT_DELAY_MS,
          );
        } else {
          setReconnecting(false);
          setFailed(true);
        }
      });
  };

  useEffect(() => {
    mountedRef.current = true;
    if (isDesktop()) loadRoster(0);
    return () => {
      mountedRef.current = false;
      if (retryRef.current) clearTimeout(retryRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Engine-current values (apply payload base) and shipped defaults ("Changed" base).
  const current = useMemo(
    () =>
      settings
        ? toMap(settings.agents, (a) => ({ enabled: a.enabled, weight: a.weight }))
        : null,
    [settings],
  );
  const defaults = useMemo(
    () =>
      settings
        ? toMap(settings.agents, (a) => ({
            enabled: a.default_enabled,
            weight: a.default_weight,
          }))
        : null,
    [settings],
  );

  const pending = usePendingSettings(current);

  if (!isDesktop()) return null;

  const rowEnabled = (a: AgentSetting): boolean =>
    a.enable_flag ? pending.values?.[enabledKey(a)] !== "false" : a.enabled;
  const rowWeight = (a: AgentSetting): number => {
    const raw = a.weight_key ? pending.values?.[weightKey(a)] : undefined;
    const n = raw !== undefined ? Number(raw) : a.weight;
    return Number.isFinite(n) ? n : a.weight;
  };
  const rowDirty = (a: AgentSetting): boolean => {
    if (!pending.values || !defaults) return false;
    return (
      (a.enable_flag !== "" &&
        pending.values[enabledKey(a)] !== defaults[enabledKey(a)]) ||
      (a.weight_key !== "" && pending.values[weightKey(a)] !== defaults[weightKey(a)])
    );
  };

  // Consensus share: ENGINE-reported weights over the active mean voters — never the
  // pending slider values (epic guardrail 4). Toggling a row previews the membership
  // change; a dragged weight only counts after the audited apply + restart.
  const activeMeanVoters = (settings?.agents ?? []).filter(
    (a) => a.role === "mean_voter" && rowEnabled(a),
  );
  const activeSum = activeMeanVoters.reduce((sum, a) => sum + a.weight, 0);
  const share = (a: AgentSetting): string => {
    if (a.role !== "mean_voter" || !rowEnabled(a) || activeSum <= 0) return "—";
    return `${((a.weight / activeSum) * 100).toFixed(1)}%`;
  };

  // Inactive by ENGINE state (stable while the card is open): disabled flag, or a
  // mean voter shipping at weight 0 (dormant/muted). Overlay rows are never
  // "inactive" by weight — their weights are inert (#3084).
  const isInactive = (a: AgentSetting): boolean =>
    (a.enable_flag !== "" && !a.enabled) ||
    (a.role === "mean_voter" && a.weight === 0);

  // Human-readable staged changes for the visible Pending-changes list.
  const pendingRows = (): { key: string; label: string; from: string; to: string }[] => {
    if (!current || !settings) return [];
    const changes = pending.diffAgainst(current);
    return Object.entries(changes).map(([key, to]) => {
      const byFlag = settings.agents.find((a) => a.enable_flag === key);
      const byWeight = settings.agents.find((a) => a.weight_key === key);
      const agent = byFlag ?? byWeight;
      const label = agent ? agentInfo(agent.name).label : key;
      const fmt = (v: string) => (byFlag ? (v === "true" ? "On" : "Off") : v);
      return {
        key,
        label: byFlag ? `${label} enabled` : `${label} weight`,
        from: fmt(current[key]),
        to: fmt(to),
      };
    });
  };

  const dirtyMap = defaults ? pending.diffAgainst(defaults) : {};
  const anyDirty = Object.keys(dirtyMap).length > 0;

  const applyPending = () => {
    if (!current) return;
    const changes = pending.diffAgainst(current);
    if (Object.keys(changes).length === 0) return;
    if (onApply) {
      onApply(changes);
      return;
    }
    // Built-in flow (#3156): open the shared WORM confirm with the old→new rows.
    applyDiffRef.current = changes;
    setApplyChanges(
      Object.entries(changes).map(([key, to]) => ({
        key,
        label: pendingRows().find((r) => r.key === key)?.label ?? key,
        from: current[key],
        to,
      })),
    );
  };

  const refresh = () => {
    setApplyChanges(null);
    applyResultRef.current = null;
    setFailed(false);
    // #3187: a restart was just triggered — poll through the engine-restart outage until it returns.
    loadRoster(RECONNECT_ATTEMPTS);
  };

  const resetToDefaults = () => {
    if (defaults) pending.replaceAll(defaults);
  };

  const renderRow = (a: AgentSetting) => {
    const info = agentInfo(a.name);
    const badge = ROLE_BADGES[a.role];
    const enabled = rowEnabled(a);
    const hasSlider = a.weight_key !== "" && a.max_weight > a.min_weight;
    return (
      <div
        key={a.name}
        data-testid={`agent-row-${a.name}`}
        className="py-3 border-t border-white/8"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="max-w-sm">
            <div className="text-[13px] font-semibold text-white/90">
              {info.label}
              {badge && (
                <span className="ml-2 rounded-full bg-white/8 px-2 py-0.5 text-[10px] font-medium text-white/50 align-middle">
                  {badge}
                </span>
              )}
              {rowDirty(a) && (
                <span className="ml-2 rounded-full bg-white/8 px-2 py-0.5 text-[10px] font-medium text-white/60 align-middle">
                  Changed
                </span>
              )}
            </div>
            <div className="sublabel mt-0.5">
              {a.role_title}
            </div>
            <div
              data-copy="description"
              className="text-[11.5px] text-white/45 leading-relaxed mt-0.5"
            >
              {info.role}
            </div>
          </div>
          <div className="flex items-center gap-4">
            {a.role === "mean_voter" && (
              <span className="num text-[12px] text-white/60 w-14 text-right">
                {share(a)}
              </span>
            )}
            {a.enable_flag !== "" && (
              <OnOffSegment
                ariaLabel={`${a.name} enabled`}
                value={enabled}
                onChange={(next) =>
                  pending.set(enabledKey(a), next ? "true" : "false")
                }
              />
            )}
          </div>
        </div>
        {hasSlider && enabled && (
          <div className="flex items-center gap-3 mt-3">
            <span className="num text-[10.5px] text-white/30">{a.min_weight}</span>
            <input
              type="range"
              className="flex-1 accent-[#00c27a]"
              min={a.min_weight}
              max={a.max_weight}
              step={0.05}
              value={rowWeight(a)}
              onChange={(e) => pending.set(weightKey(a), e.target.value)}
              aria-label={`${a.name} weight`}
            />
            <span className="num text-[10.5px] text-white/30">{a.max_weight}</span>
            <span className="num text-[12px] text-white/70 w-10 text-right">
              {rowWeight(a)}
            </span>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="surface p-5">
      <div className="eyebrow mb-2">Round table</div>
      <p data-copy="description" className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">
        How each buy decision is made, stage by stage. Each row states only what the
        agent technically does. Changes stay pending until you apply and confirm them;
        the engine then records the change and restarts with the new values.
      </p>

      {reconnecting && (
        <div
          role="status"
          className="text-[12px] text-white/55 py-3 border-t border-white/8"
        >
          Engine is restarting — reconnecting…
        </div>
      )}
      {failed && !reconnecting && (
        <div className="text-[12px] text-white/45 py-3 border-t border-white/8">
          Engine not available — the current settings cannot be shown.
        </div>
      )}

      {/* Stage panels: each pipeline step is its own visually separated panel
          (inset ground + neutral number chip), joined by a thin neutral connector
          line so the top-to-bottom flow reads as ONE process. UXC-1 S8 (#3177):
          neutral chrome throughout — no accent-colored surfaces or controls
          outside critical/arming flows. */}
      {!failed &&
        settings &&
        STAGES.map((stage, i) => {
          const agents = settings.agents.filter((a) => stageOf(a.role) === stage.id);
          if (agents.length === 0) return null;
          return (
            <div key={stage.id}>
              {i > 0 && <div aria-hidden className="ml-5 h-3.5 w-px bg-white/10" />}
              <div
                data-testid={`stage-${stage.id}`}
                className="surface-flat px-4 pt-3.5 pb-1.5"
              >
                <div className="flex items-baseline gap-2.5">
                  <span className="sublabel">
                    {stage.title}
                  </span>
                  <span className="ml-auto text-[11px] text-white/40">{stage.question}</span>
                </div>
                <p
                  data-copy="description"
                  className="text-[11px] text-white/30 mt-1.5 mb-1 max-w-md leading-relaxed"
                >
                  {stage.sub}
                </p>
                {(showInactive ? agents : agents.filter((a) => !isInactive(a))).map(renderRow)}
                {agents.some(isInactive) && (
                  <button
                    type="button"
                    onClick={() => setShowInactive((v) => !v)}
                    className="btn-link block w-full text-left border-t border-white/6 py-2.5"
                  >
                    {showInactive
                      ? "Hide inactive agents"
                      : `Show inactive agents (${agents.filter(isInactive).length})`}
                  </button>
                )}
              </div>
            </div>
          );
        })}

      {!failed && settings && pendingRows().length > 0 && (
        <div
          data-testid="pending-changes"
          className="surface-flat mt-5 px-4 py-3"
        >
          <div className="eyebrow mb-1">Pending changes</div>
          <p
            data-copy="description"
            className="text-[11px] text-white/30 mb-2 max-w-md leading-relaxed"
          >
            Nothing takes effect yet. Applying records each change and restarts the
            engine with the new values.
          </p>
          {pendingRows().map((r) => (
            <div
              key={r.key}
              className="flex items-baseline justify-between gap-4 border-t border-white/6 py-1.5"
            >
              <span className="text-[12px] text-white/70">{r.label}</span>
              <span className="num text-[12px] text-white/60">
                {r.from} {"→"} {r.to}
              </span>
            </div>
          ))}
        </div>
      )}

      <SettingsApplyModal
        open={applyChanges !== null}
        changes={applyChanges ?? []}
        record={async (nonce) => {
          applyResultRef.current = await postTradingSettings(
            applyDiffRef.current,
            APPLY_ACK,
            nonce,
          );
        }}
        persist={async () => {
          const res = applyResultRef.current;
          const toPersist = Object.fromEntries(
            (res?.changes ?? []).map((c) => [c.key, c.to]),
          );
          if (Object.keys(toPersist).length > 0) await saveSetupState(toPersist);
        }}
        onCancel={() => setApplyChanges(null)}
        onApplied={refresh}
      />

      {!failed && settings && (
        <div className="flex items-center justify-between gap-4 pt-4 border-t border-white/8 mt-5">
          {/* UXC-1 S8 (#3177): non-critical actions use the .btn family — uniform across cards. */}
          <button type="button" onClick={resetToDefaults} className="btn">
            Default settings
          </button>
          <button
            type="button"
            onClick={applyPending}
            disabled={!anyDirty && Object.keys(current ? pending.diffAgainst(current) : {}).length === 0}
            className="btn btn-primary"
          >
            Apply changes
          </button>
        </div>
      )}
    </div>
  );
}
