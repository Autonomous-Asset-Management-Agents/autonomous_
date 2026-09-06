import { useState, useEffect } from "react";
import { isDesktop, saveSetupState, getSetupState } from "@/lib/desktopBridge";
import { OnOffSegment } from "@/console/shared/SegmentPicker";
import { PortfolioShapeModal } from "@/console/desktop/PortfolioShapeModal";
import { AdvancedTradingSection } from "@/console/desktop/AdvancedTradingSection";
import type { PortfolioShape } from "@/lib/api";

/**
 * "Trading controls" card (Settings → Trading tab).
 *
 * Two INDIVIDUAL, neutral engine controls the user sets themselves — no profiles, no
 * recommendation, no risk characterisation. Each row states only WHAT the control
 * mechanically does (BaFin: execution-only, never advice). Values are written 1:1 to
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
 * The two PORTFOLIO-SHAPE controls (#3132) behave differently from the two above: they
 * decide how capital is spread and how long it stays committed, so they are NOT written on
 * every drag. The sliders hold a pending value; "Apply changes" opens the WORM-audited
 * confirm, which records, persists and restarts (the values are read at engine boot, so a
 * change is inert until then).
 *
 * The 10-20 ceiling is mechanical, not a preference: the slot is 1/N and must stay
 * >= MIN_POSITION_PERCENT (0.05), so N > 20 would leave the book unable to fill
 * (docs/6_runbooks/POSITION_BOOK_CAP_TUNING.md). Bounds are enforced server-side in
 * core/portfolio_shape.py — these sliders are convenience, not the safeguard.
 */

const DEFAULT_SIZING = true;
const DEFAULT_TARGET = "0.015";
// Bounds tied to the vol-targeting scaler clamp: the engine scales a position by
// clip(target / forecast_vol, 0.5, 1.5) (risk_manager.vol_targeting_scaler). Outside
// (0.5·F, 1.5·F) a stock's size stops responding to the target. Measured daily
// forecast-vol across the live book (implied_vol.json, IV/√252) is ~0.014–0.050, so below
// ~0.5·min_F the whole book is pinned at 0.5× and above ~1.5·max_F it is pinned at 1.5× —
// moving the target past either edge changes nothing for any name. The slider spans exactly
// those two saturation edges (the absolute range where the control still bites anywhere).
const MIN_TARGET = 0.007; // 0.5 × lowest daily vol; below this every name is pinned at 0.5×
const MAX_TARGET = 0.075; // 1.5 × highest daily vol; above this every name is pinned at 1.5×

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

function targetIsValid(raw: string): boolean {
  const n = Number(raw);
  return raw.trim() !== "" && Number.isFinite(n) && n >= MIN_TARGET && n <= MAX_TARGET;
}

export function TradingControlsCard() {
  const [sizing, setSizing] = useState<boolean>(DEFAULT_SIZING);
  const [target, setTarget] = useState<string>(DEFAULT_TARGET);
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
        if (s.VOL_TARGETING_SIZING_ENABLED != null)
          setSizing(String(s.VOL_TARGETING_SIZING_ENABLED).toLowerCase() === "true");
        if (s.VOL_TARGET_DAILY_VOL != null) setTarget(String(s.VOL_TARGET_DAILY_VOL));
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
  }, []);

  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!isDesktop() && !(!!import.meta.env.DEV && !isTest)) return null;

  const valid = targetIsValid(target);

  const persistSizing = (next: boolean) => {
    setSizing(next);
    void saveSetupState({ VOL_TARGETING_SIZING_ENABLED: next ? "true" : "false" });
  };

  const onTargetChange = (raw: string) => {
    setTarget(raw);
    if (targetIsValid(raw)) void saveSetupState({ VOL_TARGET_DAILY_VOL: raw.trim() });
    // invalid input: keep the last persisted value, show the hint, write nothing.
  };

  const onReset = () => {
    setSizing(DEFAULT_SIZING);
    setTarget(DEFAULT_TARGET);
    // The two sizing controls persist straight away, as before. The portfolio-shape
    // sliders only go back to their DEFAULT as a pending value — applying it still needs
    // the audited confirm, because it changes how capital is distributed.
    setPendingShape({ positions: DEFAULT_POSITIONS, hold_days: DEFAULT_HOLD_DAYS });
    void saveSetupState({
      VOL_TARGETING_SIZING_ENABLED: DEFAULT_SIZING ? "true" : "false",
      VOL_TARGET_DAILY_VOL: DEFAULT_TARGET,
    });
  };

  const shapeDirty =
    pendingShape.positions !== savedShape.positions ||
    pendingShape.hold_days !== savedShape.hold_days;

  const currentShape: PortfolioShape = {
    positions: savedShape.positions,
    hold_days: savedShape.hold_days,
    vol_target: Number(target) || MIN_TARGET,
  };
  const nextShape: PortfolioShape = {
    positions: pendingShape.positions,
    hold_days: pendingShape.hold_days,
    vol_target: Number(target) || MIN_TARGET,
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
          onChange={persistSizing}
          ariaLabel="Volatility sizing"
        />
      </div>

      {/* Daily vol target — only relevant while sizing is on. Same row shape as the two
          portfolio-shape controls below (label + description left, value box right, slider
          under it) so every numeric cell lines up. Unlike those, it persists immediately —
          it is a sizing knob, not a book-shape change, so no pending/confirm step. */}
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
              onChange={(e) => onTargetChange(e.target.value)}
              aria-label="Daily vol target"
              aria-invalid={!valid}
            />
          </div>
          <div className="flex items-center gap-3 mt-3">
            <span className="num text-[10.5px] text-white/30">{MIN_TARGET}</span>
            <input
              type="range"
              className="flex-1 accent-[#00c27a]"
              min={MIN_TARGET}
              max={MAX_TARGET}
              step={0.001}
              value={valid ? Number(target) : Number(DEFAULT_TARGET)}
              onChange={(e) => onTargetChange(e.target.value)}
              aria-label="Daily vol target slider"
            />
            <span className="num text-[10.5px] text-white/30">{MAX_TARGET}</span>
          </div>
          {!valid && (
            <div className="text-[11.5px] text-[#ffb4af] mt-1" role="alert">
              Please enter a number between {MIN_TARGET} and {MAX_TARGET}.
            </div>
          )}
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
