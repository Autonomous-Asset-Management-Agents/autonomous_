import { useId, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { nearestIndex } from "@/console/live/chartHover";
import { twrSeries } from "./twr";

/**
 * Equity curve chart (G3b, #1050) — pure SVG, no chart library. Ported from the
 * desktop bundle. Optional benchmark overlay + percent-rebased mode. Renders an
 * empty frame (never a broken path) when there are fewer than 2 points.
 *
 * PR-A: a native pointer crosshair + tooltip reads the REAL value at any point
 * on the line (own equity + benchmark + %Δ), like a DAX/S&P chart. Pure helper
 * `nearestIndex` maps the pointer fraction to the nearest evenly-spaced point.
 */
interface Props {
  /** #2845: `cashflow` is the external deposit/withdrawal that landed at this point (already
   *  contained in `eur`) — it drives the deposit-neutral %-mode and the cashflow markers. */
  data: { t: Date; eur: number; cashflow?: number }[];
  benchmark?: { t: Date; eur: number }[];
  height?: number;
  showGrid?: boolean;
  showAxes?: boolean;
  glowColor?: string;
  benchColor?: string;
  /** Currency symbol for the value axis (compact k/M labels). Console defaults to €; the demo passes $. */
  currency?: string;
  /** Plot each series as % change from its own first visible point (both start at 0%, directly comparable). */
  percent?: boolean;
}

export function EquityChart({
  data,
  benchmark,
  height = 220,
  showGrid = true,
  showAxes = true,
  glowColor = "rgba(255,255,255,0.92)",
  benchColor = "rgba(255,255,255,0.22)",
  currency = "€",
  percent = false,
}: Props) {
  const hasData = data.length >= 2;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const { path, areaPath, benchPath, min, max, xs, ys, benchXY, vals, benchVals, last, zeroY, markers } =
    useMemo(() => {
      if (data.length < 2) {
        return {
          path: "",
          areaPath: "",
          benchPath: "",
          min: 0,
          max: 0,
          xs: [] as number[],
          ys: [] as number[],
          benchXY: [] as { x: number; y: number }[],
          vals: [] as number[],
          benchVals: [] as number[],
          last: 0,
          zeroY: null as number | null,
          markers: [] as { i: number; cf: number; xPct: number; yPct: number }[],
        };
      }
      // #2845: %-mode plots the cumulative TIME-WEIGHTED return, not raw rebased equity — a
      // deposit is external capital, not growth, and must not paint as a step in the line.
      // Money mode keeps the raw equity (the honest book value); deposits get MARKERS instead.
      const vals = percent ? twrSeries(data) : data.map((d) => d.eur);
      const bBase = benchmark && benchmark.length && benchmark[0].eur !== 0 ? benchmark[0].eur : 1;
      const benchVals = percent
        ? (benchmark?.map((d) => ((d.eur - bBase) / bBase) * 100) ?? [])
        : (benchmark?.map((d) => d.eur) ?? []);

      const all = vals.concat(benchVals);
      const lo = Math.min(...all);
      const hi = Math.max(...all);
      const pad = (hi - lo) * 0.06 || Math.max(Math.abs(hi), 1) * 0.06;
      const min = lo - pad;
      const max = hi + pad;
      const range = max - min || 1;
      const w = 1000;
      const h = 100;
      const xs = data.map((_, i) => (i / (data.length - 1)) * w);
      const yOf = (v: number) => h - ((v - min) / range) * h;
      const ys = vals.map(yOf);
      // Money mode: at a cash-flow point the line makes a VERTICAL step — the market first moves to
      // (equity − cashflow), then jumps by the external flow — so a deposit reads as an injection,
      // not a diagonal "gain". %-mode plots the deposit-neutral TWR, which needs no step.
      let path = "";
      for (let i = 0; i < data.length; i++) {
        const cf = percent ? 0 : (data[i].cashflow ?? 0);
        // rc3 R3: only draw the vertical step when the flow is CONSISTENT with the actual
        // equity step — a flow larger than the step (over-stacked/mid-settlement data)
        // would paint the pre-step vertex far below the curve (the 350$ cliff on 28.08.).
        const actualStep = i > 0 ? data[i].eur - data[i - 1].eur : 0;
        const cfConsistent = cf !== 0 && i > 0 && Math.abs(cf) <= Math.abs(actualStep) * 1.5 && (cf > 0) === (actualStep > 0);
        if (cfConsistent) {
          const yPre = yOf(data[i].eur - cf);
          path += ` L${xs[i].toFixed(2)},${yPre.toFixed(2)} L${xs[i].toFixed(2)},${ys[i].toFixed(2)}`;
        } else {
          path += `${i === 0 ? "M" : " L"}${xs[i].toFixed(2)},${ys[i].toFixed(2)}`;
        }
      }
      const areaPath = path + ` L${w},${h} L0,${h} Z`;
      // Cash-flow markers (deposit/withdrawal) are rendered as HTML overlays (a round dot + the
      // signed amount) — an SVG <circle> distorts to an "egg" under preserveAspectRatio="none".
      // The dot sits at the TOP of the money-mode step (deposit → post-deposit point; withdrawal →
      // pre-withdrawal point); in %-mode it sits on the deposit-neutral TWR line.
      const markers = data
        .map((d, i) => {
          const cf = d.cashflow ?? 0;
          // #3068: sub-$1 flows (fee dust / rounding) are not capital EVENTS — a marker
          // labelled "−$0" is pure clutter. The flow still counts in the math above.
          if (!cf || Math.abs(cf) < 1) return null;
          const topVal = percent ? vals[i] : Math.max(d.eur, d.eur - cf);
          return { i, cf, xPct: (xs[i] / w) * 100, yPct: (yOf(topVal) / h) * 100 };
        })
        .filter(
          (m): m is { i: number; cf: number; xPct: number; yPct: number } => m !== null,
        );
      let benchXY: { x: number; y: number }[] = [];
      let benchPath = "";
      if (benchmark && benchmark.length >= 2) {
        benchXY = benchVals.map((v, i) => ({
          x: (i / (benchVals.length - 1)) * w,
          y: h - ((v - min) / range) * h,
        }));
        benchPath = benchXY.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
      }
      const zeroY = percent && min < 0 && max > 0 ? h - ((0 - min) / range) * h : null;
      return { path, areaPath, benchPath, min, max, xs, ys, benchXY, vals, benchVals, last: ys[ys.length - 1], zeroY, markers };
    }, [data, benchmark, percent]);

  // useId (not Math.random — impure during render); strip the `:` so it's a
  // valid CSS/url() id for the gradient reference.
  const id = useId().replace(/:/g, "");
  const fmtAxis = (v: number) => (percent ? `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` : `${currency}${(v / 1000).toFixed(1)}k`);

  // --- PR-A hover crosshair -------------------------------------------------
  const hi = hoverIdx != null && hasData ? Math.min(Math.max(hoverIdx, 0), data.length - 1) : null;
  // Same pointer fraction → nearest benchmark index (benchmark may differ in length).
  const benchHi =
    hi != null && benchXY.length >= 2
      ? Math.min(Math.round((hi / (data.length - 1)) * (benchXY.length - 1)), benchXY.length - 1)
      : null;
  // Intraday-like when the whole curve spans < ~2 days → show time, else date.
  const intradayLike = hasData && data[data.length - 1].t.getTime() - data[0].t.getTime() < 2 * 86_400_000;

  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const el = containerRef.current;
    if (!el || !hasData) return;
    const rect = el.getBoundingClientRect();
    const frac = rect.width ? (e.clientX - rect.left) / rect.width : 0;
    setHoverIdx(nearestIndex(frac, data.length));
  };
  const onLeave = () => setHoverIdx(null);

  const fmtMoney = (v: number) => `${currency}${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  const fmtSeries = (i: number) =>
    percent ? `${vals[i] >= 0 ? "+" : ""}${vals[i].toFixed(2)}%` : fmtMoney(data[i].eur);
  const fmtBench = (bi: number) =>
    percent ? `${benchVals[bi] >= 0 ? "+" : ""}${benchVals[bi].toFixed(2)}%` : fmtMoney(benchmark![bi].eur);
  // #2845: the range-delta label is the TWR up to the hovered point — the raw endpoint
  // difference counted a deposit 1:1 as "return" (the +53 %-weekly bug). Computed once for
  // both modes from the SAME points the line plots.
  const twrVals = useMemo(() => twrSeries(data), [data]);
  const deltaPct = hi != null && twrVals.length > hi ? twrVals[hi] : null;
  const tooltipLeft = hi != null ? Math.min(92, Math.max(8, (xs[hi] / 1000) * 100)) : 0;

  return (
    <div
      ref={containerRef}
      className="relative w-full"
      style={{ height, touchAction: "none" }}
      onPointerMove={onMove}
      onPointerDown={onMove}
      onPointerLeave={onLeave}
    >
      <svg viewBox="0 0 1000 100" preserveAspectRatio="none" className="absolute inset-0 w-full h-full">
        <defs>
          <linearGradient id={`grad-${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={glowColor} stopOpacity="0.16" />
            <stop offset="100%" stopColor={glowColor} stopOpacity="0" />
          </linearGradient>
        </defs>
        {showGrid &&
          [20, 40, 60, 80].map((y) => (
            <line key={y} x1={0} x2={1000} y1={y} y2={y} stroke="rgba(255,255,255,0.04)" strokeWidth="0.5" />
          ))}
        {hasData && (
          <>
            {zeroY !== null && (
              <line x1={0} x2={1000} y1={zeroY} y2={zeroY} stroke="rgba(255,255,255,0.18)" strokeWidth="0.6" strokeDasharray="3 3" />
            )}
            <path d={areaPath} fill={`url(#grad-${id})`} />
            {benchPath && (
              <path d={benchPath} fill="none" stroke={benchColor} strokeWidth="1.25" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
            )}
            <path d={path} fill="none" stroke={glowColor} strokeWidth="1.4" vectorEffect="non-scaling-stroke" />
            <circle cx={xs[xs.length - 1]} cy={last} r="2.2" fill={glowColor} />
            {hi != null && (
              <>
                <line x1={xs[hi]} x2={xs[hi]} y1={0} y2={100} stroke="rgba(255,255,255,0.35)" strokeWidth="0.6" vectorEffect="non-scaling-stroke" />
                {benchHi != null && <circle cx={benchXY[benchHi].x} cy={benchXY[benchHi].y} r="2" fill={benchColor} />}
                <circle cx={xs[hi]} cy={ys[hi]} r="2.8" fill={glowColor} />
              </>
            )}
          </>
        )}
      </svg>
      {/* Cash-flow markers: round dot (HTML, so it never distorts to an "egg" like an SVG circle in
          the non-uniform viewBox) + the signed amount, in the app's bear red. Label flips above/below
          so a marker near the top edge isn't clipped. */}
      {hasData &&
        markers.map((m) => {
          const above = m.yPct > 22;
          return (
            <div
              key={`cf-${m.i}`}
              className="pointer-events-none absolute z-[6]"
              style={{ left: `${m.xPct}%`, top: `${m.yPct}%`, transform: "translate(-50%, -50%)" }}
            >
              <div
                data-testid="cashflow-marker"
                style={{ width: 8, height: 8, borderRadius: "50%", background: "#ff453a" }}
              />
              <div
                className="num"
                style={{
                  position: "absolute",
                  left: "50%",
                  transform: "translateX(-50%)",
                  whiteSpace: "nowrap",
                  fontSize: 10,
                  fontWeight: 600,
                  color: "rgba(255,255,255,0.92)",
                  ...(above ? { bottom: "10px" } : { top: "10px" }),
                }}
              >
                {m.cf > 0 ? "+" : "−"}
                {currency}
                {Math.round(Math.abs(m.cf)).toLocaleString("en-US")}
              </div>
            </div>
          );
        })}
      {!hasData && (
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-[11px] text-white/30">Collecting equity history…</span>
        </div>
      )}
      {showAxes && (
        <>
          <div className="absolute right-0 top-0 num text-[10px] text-white/30 px-1">{fmtAxis(max)}</div>
          <div className="absolute right-0 bottom-0 num text-[10px] text-white/30 px-1">{fmtAxis(min)}</div>
        </>
      )}
      {hi != null && (
        <div
          className="pointer-events-none absolute z-10 top-1 -translate-x-1/2 rounded-md border border-white/10 bg-black/80 px-2 py-1 text-[10px] leading-tight backdrop-blur-sm"
          style={{ left: `${tooltipLeft}%` }}
        >
          <div className="num text-white/92">{fmtSeries(hi)}</div>
          {benchHi != null && benchmark && (
            <div className="num text-white/45">S&amp;P {fmtBench(benchHi)}</div>
          )}
          {deltaPct != null && !percent && (
            <div className={`num ${deltaPct >= 0 ? "text-emerald-400/80" : "text-red-400/80"}`}>
              {deltaPct >= 0 ? "+" : ""}
              {deltaPct.toFixed(2)}%
            </div>
          )}
          <div className="text-white/35">
            {data[hi].t.toLocaleString("en-US", intradayLike
              ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
              : { year: "numeric", month: "short", day: "numeric" })}
          </div>
        </div>
      )}
    </div>
  );
}
