import { useEffect, useState } from "react";
import { getPerformanceBaseline, setPerformanceBaseline } from "@/lib/api";
import { useStore } from "@/console/store/useStore";

/**
 * "Performance-Basis" row (Settings → Trading, #3071).
 *
 * A NEUTRAL setting (no profiles, no recommendation — BaFin execution-only line): the user
 * chooses the date every Overview performance figure (TWR, S&P comparison, max drawdown,
 * Sharpe window) is measured from. Empty = the account's true inception. Persisted ENGINE-side
 * (system_config) so console, reports and the audit trail agree; the Overview card hint then
 * reads "since <date>" — the chosen base is always visible next to the number.
 */

const INPUT_CLASS =
  "mt-1 w-40 rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30";

export function PerformanceBaselineCard() {
  const inceptionDate = useStore((s) => s.inceptionDate);
  const [value, setValue] = useState<string>("");
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getPerformanceBaseline()
      .then((r) => {
        if (!alive) return;
        setSaved(r.baseline_date ?? null);
        setValue(r.baseline_date ?? "");
      })
      .catch(() => {
        /* engine offline — the row stays editable; save will surface the error */
      });
    return () => {
      alive = false;
    };
  }, []);

  const todayIso = new Date().toISOString().slice(0, 10);

  const persist = async (next: string | null) => {
    setBusy(true);
    setError(null);
    try {
      const r = await setPerformanceBaseline(next);
      setSaved(r.baseline_date ?? null);
      setValue(r.baseline_date ?? "");
    } catch {
      setError("Saving failed — is the engine reachable?");
    } finally {
      setBusy(false);
    }
  };

  const dirty = (value || null) !== saved;

  return (
    <div className="surface rounded-2xl p-6">
      <div className="text-[15px] font-semibold text-white/90">Performance baseline</div>
      <p className="text-[12.5px] text-white/45 mt-1 leading-relaxed">
        Performance figures (return, S&amp;P comparison, max drawdown, Sharpe) are measured
        from this date onward. Empty = account inception{inceptionDate ? ` (${inceptionDate})` : ""}.
        The chosen baseline is always shown on the Overview cards as &quot;since &lt;date&gt;&quot;.
      </p>
      <div className="mt-3 flex items-end gap-2">
        <label className="block">
          <span className="text-[12px] text-white/55">Measure from</span>
          <input
            data-testid="baseline-input"
            type="date"
            className={INPUT_CLASS}
            value={value}
            min={inceptionDate ?? undefined}
            max={todayIso}
            onChange={(e) => setValue(e.target.value)}
          />
        </label>
        <button
          data-testid="baseline-save"
          disabled={busy || !dirty || !value}
          onClick={() => void persist(value)}
          className="rounded-lg border border-white/12 px-3 py-2 text-[13px] text-white/80 hover:text-white hover:border-white/30 disabled:opacity-40"
        >
          Apply
        </button>
        <button
          data-testid="baseline-clear"
          disabled={busy || saved == null}
          onClick={() => void persist(null)}
          className="rounded-lg border border-white/12 px-3 py-2 text-[13px] text-white/60 hover:text-white hover:border-white/30 disabled:opacity-40"
        >
          Reset
        </button>
      </div>
      {error && <div className="text-[12px] text-bear mt-2">{error}</div>}
    </div>
  );
}
