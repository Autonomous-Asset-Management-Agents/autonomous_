import { useState, useEffect, useRef } from "react";
import { isDesktop, saveSetupState, getSetupState } from "@/lib/desktopBridge";
import { OnOffSegment } from "@/console/shared/SegmentPicker";
import { PortfolioShapeModal } from "@/console/desktop/PortfolioShapeModal";
import { AdvancedTradingSection } from "@/console/desktop/AdvancedTradingSection";
import {
  SettingsApplyModal,
  type SettingsApplyChange,
} from "@/console/desktop/SettingsApplyModal";
import {
  getTradingSettings,
  postTradingSettings,
  type PortfolioShape,
  type TradingSettingsApplyResult,
} from "@/lib/api";

/**
 * "Trading controls" card (Settings → Trading tab).
 *
 * Two INDIVIDUAL, neutral engine controls the user sets themselves — no profiles, no
 * recommendation, no risk characterisation. Each row states only WHAT the control
 * mechanically does (BaFin: execution-only, never advice). Values land 1:1 in
 * setup.json under the exact engine flag names (direct passthrough, #2863 Option A);
 * absent keys ⇒ the engine uses its shipped defaults.
 *
 *   Volatility sizing  -> VOL_TARGETING_SIZING_ENABLED  (default ON)
 *   Daily vol target        -> VOL_TARGET_DAILY_VOL          (default 0.015; shown only
 *                                                          while sizing is on)
 *   Maximum positions       -> FULL_UNIVERSE_MAX_POSITIONS   (default 10, range 10-20)
 *   Minimum holding period  -> SMART_EXIT_MIN_HOLD_DAYS      (default 20, range 5-20)
 *   "Default settings"   -> resets all of them to the shipped defaults.
 *
 * No control here is written on every change (#4447, owner decision 09.10.2026). Each
 * holds a pending value; applying it opens the WORM-audited confirm, which records,
 * persists and restarts (the values are read at engine boot, so a change is inert until
 * then). The two sizing controls are keys of the confirmed registry
 * (POST /api/trading-settings, bounds from its meta); the two PORTFOLIO-SHAPE controls
 * (#3132) use /api/portfolio-shape.
 *
 * The 10-20 ceiling is mechanical, not a preference: the slot is 1/N and must stay
 * >= MIN_POSITION_PERCENT (0.05), so N > 20 would leave the book unable to fill
 * (docs/6_runbooks/POSITION_BOOK_CAP_TUNING.md). Bounds are enforced server-side in
 * core/portfolio_shape.py — these sliders are convenience, not the safeguard.
 */

const DEFAULT_SIZING = true;
const DEFAULT_TARGET = "0.015";
// #4447: the bounds come from the confirmed registry (core/trading_settings.py, served as
// meta by GET /api/trading-settings) — one bound for UI, registry and engine clamp. These
// two values are only the fallback until the meta has loaded (the server clamps anyway):
// 0.5 × the lowest / 1.5 × the highest typical daily vol of the book; beyond them every
// name sits at the scaler clip(target / forecast_vol, 0.5, 1.5).
const FALLBACK_MIN_TARGET = 0.007;
const FALLBACK_MAX_TARGET = 0.075;
const VOL_KEY = "VOL_TARGET_DAILY_VOL";
const SIZING_KEY = "VOL_TARGETING_SIZING_ENABLED";
const VOL_ACK =
  "I confirm these trading-parameter changes. They apply to all future trading decisions and are recorded on a tamper-evident audit trail.";

// Mirrors core/portfolio_shape.py — the server clamps to the same values.
const DEFAULT_POSITIONS = 10;
const MIN_POSITIONS = 10;
const MAX_POSITIONS_UI = 20;
const DEFAULT_HOLD_DAYS = 20;
const MIN_HOLD_DAYS = 5;
const MAX_HOLD_DAYS = 20;

const clampInt = (v: number, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, Math.round(Number.isFinite(v) ? v : lo)));

const INPUT_CLASS =
  "mt-1 w-32 rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30";

/** "0,04" and "0.04" are the same number (#4447: the decimal comma was rejected). */
function normalizeTarget(raw: string): string {
  return raw.trim().replace(",", ".");
}

function targetIsValid(raw: string, lo: number, hi: number): boolean {
  const t = normalizeTarget(raw);
  const n = Number(t);
  return t !== "" && Number.isFinite(n) && n >= lo && n <= hi;
}

/** Display form: "0.0300" → "0.03" (the engine's canonical text carries four places). */
const shortNum = (v: string) => String(Number(normalizeTarget(v)));

export function TradingControlsCard() {
  // Sizing controls: `saved*` is what setup.json holds, `sizing`/`target` the pending
  // value. They diverge until the audited confirm applies the change (#4447).
  const [sizing, setSizing] = useState<boolean>(DEFAULT_SIZING);
  const [target, setTarget] = useState<string>(DEFAULT_TARGET);
  const [savedSizing, setSavedSizing] = useState<boolean>(DEFAULT_SIZING);
  const [savedTarget, setSavedTarget] = useState<string>(DEFAULT_TARGET);
  const [bounds, setBounds] = useState({
    lo: FALLBACK_MIN_TARGET,
    hi: FALLBACK_MAX_TARGET,
  });
  const [volConfirming, setVolConfirming] = useState(false);
  const volResultRef = useRef<TradingSettingsApplyResult | null>(null);
  // Portfolio shape: `saved` is what the engine has, `pending` what the sliders show.
  // They diverge until the audited confirm applies the change.
  const [savedShape, setSavedShape] = useState({
    positions: DEFAULT_POSITIONS,
    hold_days: DEFAULT_HOLD_DAYS,
  });
  const [pendingShape, setPendingShape] = useState({
    positions: DEFAULT_POSITIONS,
    hold_days: DEFAULT_HOLD_DAYS,
  });
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    async function load() {
      if (!isDesktop()) return;
      try {
        const s = await getSetupState();
        if (s.VOL_TARGETING_SIZING_ENABLED != null) {
          const on = String(s.VOL_TARGETING_SIZING_ENABLED).toLowerCase() === "true";
          setSizing(on);
          setSavedSizing(on);
        }
        if (s.VOL_TARGET_DAILY_VOL != null) {
          const t = shortNum(String(s.VOL_TARGET_DAILY_VOL));
          setTarget(t);
          setSavedTarget(t);
        }
        const loaded = {
          positions:
            s.FULL_UNIVERSE_MAX_POSITIONS != null
              ? clampInt(Number(s.FULL_UNIVERSE_MAX_POSITIONS), MIN_POSITIONS, MAX_POSITIONS_UI)
              : DEFAULT_POSITIONS,
          hold_days:
            s.SMART_EXIT_MIN_HOLD_DAYS != null
              ? clampInt(Number(s.SMART_EXIT_MIN_HOLD_DAYS), MIN_HOLD_DAYS, MAX_HOLD_DAYS)
              : DEFAULT_HOLD_DAYS,
        };
        setSavedShape(loaded);
        setPendingShape(loaded);
      } catch (e) {
        console.error("Failed to load trading controls:", e);
      }
    }
    void load();
    // Bounds from the registry meta. Defensive like AdvancedTradingSection: a missing or
    // failing GET keeps the fallback bounds (the server clamps authoritatively).
    let alive = true;
    if (isDesktop()) {
      Promise.resolve()
        .then(() => getTradingSettings())
        .then((ts) => {
          const m = ts?.meta?.find((x) => x.key === VOL_KEY);
          if (alive && m && Number.isFinite(m.min) && Number.isFinite(m.max)) {
            setBounds({ lo: Number(m.min), hi: Number(m.max) });
          }
        })
        .catch(() => undefined);
    }
    return () => {
      alive = false;
    };
  }, []);

  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!isDesktop() && !(!!import.meta.env.DEV && !isTest)) return null;

  const valid = targetIsValid(target, bounds.lo, bounds.hi);

  // Only what actually differs from setup.json goes into the confirm (and onto the chain).
  const volDiff: Record<string, string> = {};
  const volChanges: SettingsApplyChange[] = [];
  if (sizing !== savedSizing) {
    volDiff[SIZING_KEY] = sizing ? "true" : "false";
    volChanges.push({
      key: SIZING_KEY,
      label: "Volatility sizing",
      from: savedSizing ? "On" : "Off",
      to: sizing ? "On" : "Off",
    });
  }
  if (sizing && valid && Number(normalizeTarget(target)) !== Number(savedTarget)) {
    volDiff[VOL_KEY] = normalizeTarget(target);
    volChanges.push({
      key: VOL_KEY,
      label: "Daily vol target",
      from: shortNum(savedTarget),
      to: shortNum(target),
    });
  }
  const volDirty = volChanges.length > 0 && (!sizing || valid);

  const onReset = () => {
    // Every control goes back to its DEFAULT as a pending value only — applying it still
    // needs the audited confirm (#4447: nothing is written without it).
    setSizing(DEFAULT_SIZING);
    setTarget(DEFAULT_TARGET);
    setPendingShape({ positions: DEFAULT_POSITIONS, hold_days: DEFAULT_HOLD_DAYS });
  };

  const shapeDirty =
    pendingShape.positions !== savedShape.positions ||
    pendingShape.hold_days !== savedShape.hold_days;

  const currentShape: PortfolioShape = {
    positions: savedShape.positions,
    hold_days: savedShape.hold_days,
  };
  const nextShape: PortfolioShape = {
    positions: pendingShape.positions,
    hold_days: pendingShape.hold_days,
  };

  return (
    <div className="surface p-5">
      <div className="eyebrow mb-2">Trading controls</div>
      <p className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">
        Settings you choose yourself. Each one describes only what it technically does.
      </p>

      {/* Volatility sizing */}
      <div className="flex items-start justify-between gap-4 py-3 border-t border-white/8">
        <div className="max-w-sm">
          <div className="text-[13px] font-semibold text-white/90">Volatility sizing</div>
          <div className="text-[11.5px] text-white/45 leading-relaxed mt-0.5">
            Scales position sizes inversely to each stock's volatility.
          </div>
        </div>
        {/* UXC-1 S8 (#3177): OnOffSegment, not the iOS Switch — both states visible. */}
        <OnOffSegment
          value={sizing}
          onChange={setSizing}
          ariaLabel="Volatility sizing"
        />
      </div>

      {/* Daily vol target — only relevant while sizing is on. Same row shape as the two
          portfolio-shape controls below (label + description left, value box right, slider
          under it) so every numeric cell lines up. Pending until the audited confirm
          applies it (#4447). */}
      {sizing && (
        <div className="py-3 border-t border-white/8">
          <div className="flex items-start justify-between gap-4">
            <div className="max-w-sm">
              <label
                htmlFor="vol-target-daily"
                className="text-[13px] font-semibold text-white/90"
              >
                Daily vol target
              </label>
              <div className="text-[11.5px] text-white/45 leading-relaxed mt-0.5">
                Target daily volatility. Higher = larger positions, lower = smaller positions.
              </div>
            </div>
            <input
              id="vol-target-daily"
              className={INPUT_CLASS + " w-20 text-center"}
              value={target}
              inputMode="decimal"
              onChange={(e) => setTarget(e.target.value)}
              aria-label="Daily vol target"
              aria-invalid={!valid}
            />
          </div>
          <div className="flex items-center gap-3 mt-3">
            <span className="num text-[10.5px] text-white/30">{bounds.lo}</span>
            <input
              type="range"
              className="flex-1 accent-[#00c27a]"
              min={bounds.lo}
              max={bounds.hi}
              step={0.001}
              value={valid ? Number(normalizeTarget(target)) : Number(DEFAULT_TARGET)}
              onChange={(e) => setTarget(e.target.value)}
              aria-label="Daily vol target slider"
            />
            <span className="num text-[10.5px] text-white/30">{bounds.hi}</span>
          </div>
          {!valid && (
            <div className="text-[11.5px] text-[#ffb4af] mt-1" role="alert">
              Please enter a number between {bounds.lo} and {bounds.hi}.
            </div>
          )}
        </div>
      )}
      {volDirty && (
        <div className="pb-3 flex items-center justify-between gap-4">
          <span className="text-[11.5px] text-white/45 leading-relaxed">
            Not applied yet. The engine restarts when you confirm.
          </span>
          <button
            type="button"
            onClick={() => setVolConfirming(true)}
            className="btn btn-primary"
            aria-label="Apply volatility settings"
          >
            Apply changes
          </button>
        </div>
      )}


      {/* Portfolio shape (#3132) — pending until the audited confirm applies it. */}
      <div className="py-3 border-t border-white/8">
        <div className="flex items-start justify-between gap-4">
          <div className="max-w-sm">
            <label htmlFor="max-positions" className="text-[13px] font-semibold text-white/90">
              Maximum positions
            </label>
            <div className="text-[11.5px] text-white/45 leading-relaxed mt-0.5">
              How many stocks the portfolio holds at the same time. Fewer positions means
              each holding is larger.
            </div>
          </div>
          <input
            id="max-positions"
            type="number"
            className={INPUT_CLASS + " w-20 text-center"}
            value={pendingShape.positions}
            min={MIN_POSITIONS}
            max={MAX_POSITIONS_UI}
            onChange={(e) =>
              setPendingShape((p) => ({
                ...p,
                positions: clampInt(Number(e.target.value), MIN_POSITIONS, MAX_POSITIONS_UI),
              }))
            }
            aria-label="Maximum positions"
          />
        </div>
        <div className="flex items-center gap-3 mt-3">
          <span className="num text-[10.5px] text-white/30">{MIN_POSITIONS}</span>
          <input
            type="range"
            className="flex-1 accent-[#00c27a]"
            min={MIN_POSITIONS}
            max={MAX_POSITIONS_UI}
            step={1}
            value={pendingShape.positions}
            onChange={(e) =>
              setPendingShape((p) => ({ ...p, positions: Number(e.target.value) }))
            }
            aria-label="Maximum positions slider"
          />
          <span className="num text-[10.5px] text-white/30">{MAX_POSITIONS_UI}</span>
        </div>
      </div>

      <div className="py-3 border-t border-white/8">
        <div className="flex items-start justify-between gap-4">
          <div className="max-w-sm">
            <label htmlFor="min-hold-days" className="text-[13px] font-semibold text-white/90">
              Minimum holding period
            </label>
            <div className="text-[11.5px] text-white/45 leading-relaxed mt-0.5">
              Trading days a position is kept before a better-scoring candidate may replace
              it.
            </div>
          </div>
          <input
            id="min-hold-days"
            type="number"
            className={INPUT_CLASS + " w-20 text-center"}
            value={pendingShape.hold_days}
            min={MIN_HOLD_DAYS}
            max={MAX_HOLD_DAYS}
            onChange={(e) =>
              setPendingShape((p) => ({
                ...p,
                hold_days: clampInt(Number(e.target.value), MIN_HOLD_DAYS, MAX_HOLD_DAYS),
              }))
            }
            aria-label="Minimum holding period"
          />
        </div>
        <div className="flex items-center gap-3 mt-3">
          <span className="num text-[10.5px] text-white/30">{MIN_HOLD_DAYS}</span>
          <input
            type="range"
            className="flex-1 accent-[#00c27a]"
            min={MIN_HOLD_DAYS}
            max={MAX_HOLD_DAYS}
            step={1}
            value={pendingShape.hold_days}
            onChange={(e) =>
              setPendingShape((p) => ({ ...p, hold_days: Number(e.target.value) }))
            }
            aria-label="Minimum holding period slider"
          />
          <span className="num text-[10.5px] text-white/30">{MAX_HOLD_DAYS}</span>
        </div>
        {shapeDirty && (
          <div className="mt-3 flex items-center justify-between gap-4">
            <span className="text-[11.5px] text-white/45 leading-relaxed">
              Not applied yet. The engine restarts when you confirm.
            </span>
            <button
              type="button"
              onClick={() => setConfirming(true)}
              className="btn btn-primary"
            >
              Apply changes
            </button>
          </div>
        )}
      </div>

      <div className="pt-4 border-t border-white/8 mt-1">
        <button type="button" onClick={onReset} className="btn">
          Default settings
        </button>
      </div>

      {/* UXC-1 S5 (#3158): collapsed expert area INSIDE this card (owner decision
          02.09.: no separate risk-limits card). Applies via the S3 WORM flow. */}
      <AdvancedTradingSection />

      <SettingsApplyModal
        open={volConfirming}
        changes={volChanges}
        record={async (nonce) => {
          volResultRef.current = await postTradingSettings(volDiff, VOL_ACK, nonce);
        }}
        persist={async () => {
          // What the ENGINE applied (it clamps), only the keys that actually changed.
          const toPersist = Object.fromEntries(
            (volResultRef.current?.changes ?? []).map((c) => [c.key, c.to]),
          );
          if (Object.keys(toPersist).length > 0) await saveSetupState(toPersist);
        }}
        onCancel={() => setVolConfirming(false)}
        onApplied={() => {
          const applied = volResultRef.current?.applied ?? volDiff;
          const nextSizing =
            applied[SIZING_KEY] !== undefined ? applied[SIZING_KEY] === "true" : sizing;
          const nextTarget =
            applied[VOL_KEY] !== undefined ? shortNum(applied[VOL_KEY]) : savedTarget;
          setSavedSizing(nextSizing);
          setSizing(nextSizing);
          setSavedTarget(nextTarget);
          if (applied[VOL_KEY] !== undefined) setTarget(nextTarget);
          volResultRef.current = null;
          setVolConfirming(false);
        }}
      />

      <PortfolioShapeModal
        open={confirming}
        current={currentShape}
        next={nextShape}
        onCancel={() => setConfirming(false)}
        onApplied={(applied) => {
          const next = {
            positions: Math.round(applied.positions),
            hold_days: Math.round(applied.hold_days),
          };
          setSavedShape(next);
          setPendingShape(next);
          setConfirming(false);
        }}
      />
    </div>
  );
}
