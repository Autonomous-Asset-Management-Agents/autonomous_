import { useState, useEffect } from "react";
import { isDesktop, saveSetupState, getSetupState } from "@/lib/desktopBridge";
import { Switch } from "@/components/ui/switch";

/**
 * "Handels-Kontrollen" card (Settings → Trading tab).
 *
 * Two INDIVIDUAL, neutral engine controls the user sets themselves — no profiles, no
 * recommendation, no risk characterisation. Each row states only WHAT the control
 * mechanically does (BaFin: execution-only, never advice). Values are written 1:1 to
 * setup.json under the exact engine flag names (direct passthrough, #2863 Option A);
 * absent keys ⇒ the engine uses its shipped defaults.
 *
 *   Volatilitäts-Sizing  -> VOL_TARGETING_SIZING_ENABLED  (default ON)
 *   Vol-Tagesziel        -> VOL_TARGET_DAILY_VOL          (default 0.015; shown only
 *                                                          while sizing is on)
 *   "Default Settings"   -> resets both to the shipped defaults.
 */

const DEFAULT_SIZING = true;
const DEFAULT_TARGET = "0.015";
const MIN_TARGET = 0.001; // sane bounds for a daily-vol target
const MAX_TARGET = 0.05;

const INPUT_CLASS =
  "mt-1 w-32 rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30";

function targetIsValid(raw: string): boolean {
  const n = Number(raw);
  return raw.trim() !== "" && Number.isFinite(n) && n >= MIN_TARGET && n <= MAX_TARGET;
}

export function TradingControlsCard() {
  const [sizing, setSizing] = useState<boolean>(DEFAULT_SIZING);
  const [target, setTarget] = useState<string>(DEFAULT_TARGET);

  useEffect(() => {
    async function load() {
      if (!isDesktop()) return;
      try {
        const s = await getSetupState();
        if (s.VOL_TARGETING_SIZING_ENABLED != null)
          setSizing(String(s.VOL_TARGETING_SIZING_ENABLED).toLowerCase() === "true");
        if (s.VOL_TARGET_DAILY_VOL != null) setTarget(String(s.VOL_TARGET_DAILY_VOL));
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
    void saveSetupState({
      VOL_TARGETING_SIZING_ENABLED: DEFAULT_SIZING ? "true" : "false",
      VOL_TARGET_DAILY_VOL: DEFAULT_TARGET,
    });
  };

  return (
    <div className="surface p-6">
      <div className="eyebrow mb-2">Handels-Kontrollen</div>
      <p className="text-[11px] text-white/30 mb-5 max-w-md leading-relaxed">
        Einstellungen, die du selbst wählst. Jede beschreibt nur, was sie technisch bewirkt.
      </p>

      {/* Volatilitäts-Sizing */}
      <div className="flex items-start justify-between gap-4 py-3 border-t border-white/8">
        <div className="max-w-sm">
          <div className="text-[13px] font-semibold text-white/90">Volatilitäts-Sizing</div>
          <div className="text-[11.5px] text-white/45 leading-relaxed mt-0.5">
            Skaliert Positionsgrößen invers zur Volatilität der jeweiligen Aktie.
          </div>
        </div>
        <Switch
          checked={sizing}
          onCheckedChange={persistSizing}
          aria-label="Volatilitäts-Sizing"
        />
      </div>

      {/* Vol-Tagesziel — only relevant while sizing is on */}
      {sizing && (
        <div className="py-3 border-t border-white/8">
          <label
            htmlFor="vol-target-daily"
            className="text-[13px] font-semibold text-white/90"
          >
            Vol-Tagesziel
          </label>
          <input
            id="vol-target-daily"
            className={INPUT_CLASS}
            value={target}
            inputMode="decimal"
            onChange={(e) => onTargetChange(e.target.value)}
            aria-label="Vol-Tagesziel"
            aria-invalid={!valid}
          />
          <div className="text-[11.5px] text-white/45 leading-relaxed mt-1">
            Zielwert der täglichen Volatilität. Höher = größere Positionen, niedriger =
            kleinere Positionen.
          </div>
          {!valid && (
            <div className="text-[11.5px] text-[#ff6b6b] mt-1" role="alert">
              Bitte eine Zahl zwischen {MIN_TARGET} und {MAX_TARGET} eingeben.
            </div>
          )}
        </div>
      )}

      <div className="pt-4 border-t border-white/8 mt-1">
        <button
          type="button"
          onClick={onReset}
          className="rounded-lg px-4 py-2 text-[12.5px] font-medium text-white/80 bg-white/5 hover:bg-white/10 border border-white/12 transition-colors"
        >
          Default Settings
        </button>
      </div>
    </div>
  );
}
