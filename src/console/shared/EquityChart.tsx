import { useId, useMemo } from "react";

/**
 * Equity curve chart (G3b, #1050) — pure SVG, no chart library. Ported from the
 * desktop bundle. Optional benchmark overlay + percent-rebased mode. Renders an
 * empty frame (never a broken path) when there are fewer than 2 points.
 */
interface Props {
  data: { t: Date; eur: number }[];
  benchmark?: { t: Date; eur: number }[];
  height?: number;
  showGrid?: boolean;
  showAxes?: boolean;
  glowColor?: string;
  benchColor?: string;
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
  percent = false,
}: Props) {
  const hasData = data.length >= 2;

  const { path, areaPath, benchPath, min, max, xs, last, zeroY } = useMemo(() => {
    if (data.length < 2) {
      return { path: "", areaPath: "", benchPath: "", min: 0, max: 0, xs: [] as number[], last: 0, zeroY: null as number | null };
    }
    // Divisor guard for percent-rebase: a 0 first point would divide by zero.
    // Explicit `=== 0` check (not `|| 1`) so it can't be read as masking real data.
    const eqBase = data[0].eur === 0 ? 1 : data[0].eur;
    const vals = percent ? data.map((d) => ((d.eur - eqBase) / eqBase) * 100) : data.map((d) => d.eur);
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
    const ys = vals.map((v) => h - ((v - min) / range) * h);
    const path = xs.map((x, i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)},${ys[i].toFixed(2)}`).join(" ");
    const areaPath = path + ` L${w},${h} L0,${h} Z`;
    let benchPath = "";
    if (benchmark && benchmark.length >= 2) {
      const byx = benchVals.map((v, i) => ({
        x: (i / (benchVals.length - 1)) * w,
        y: h - ((v - min) / range) * h,
      }));
      benchPath = byx.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
    }
    const zeroY = percent && min < 0 && max > 0 ? h - ((0 - min) / range) * h : null;
    return { path, areaPath, benchPath, min, max, xs, last: ys[ys.length - 1], zeroY };
  }, [data, benchmark, percent]);

  // useId (not Math.random — impure during render); strip the `:` so it's a
  // valid CSS/url() id for the gradient reference.
  const id = useId().replace(/:/g, "");
  const fmtAxis = (v: number) => (percent ? `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` : `€${(v / 1000).toFixed(1)}k`);

  return (
    <div className="relative w-full" style={{ height }}>
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
          </>
        )}
      </svg>
      {showAxes && (
        <>
          <div className="absolute right-0 top-0 num text-[10px] text-white/30 px-1">{fmtAxis(max)}</div>
          <div className="absolute right-0 bottom-0 num text-[10px] text-white/30 px-1">{fmtAxis(min)}</div>
        </>
      )}
    </div>
  );
}
