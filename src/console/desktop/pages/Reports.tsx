import { useStore } from "@/console/store/useStore";
import { useSpecialistPolling } from "@/console/live/useSpecialistPolling";
import { IconShield, IconLightbulb } from "@/console/shared/Icons";
import { StatusDot } from "@/console/shared/StatusDot";
import { fmtMlReturn } from "@/console/lib/format";
import { getCompanyName } from "@/console/lib/companyName";
import type { SpecialistReport } from "@/console/types";

/**
 * Console Reports page (G3c / G1b′, #1050). Two live sections:
 *  1. Round-Table decisions (from /round-table-decisions, G1b) — KEPT verbatim.
 *  2. Per-symbol specialist cards (from /specialist-reports, RPAR-#1284) — the
 *     rich research/ML cards, ported from the desktop bundle and ADAPTED to the
 *     canonical SpecialistReport view-model (camelCase, no senate/price/position
 *     data — that endpoint doesn't carry it; see console/types.ts scope note).
 *
 * The specialist registry stays DISABLED by default (enabling it wires the
 * specialist agent into the decision path — a human-gated P1 change, out of
 * scope for this frontend PR). So cards render only once an operator enables the
 * registry; until then the engine returns its "unavailable" contract and we show
 * an honest empty state from its own message — never faked data.
 */

// ─── Small helper components (adapted from the bundle Reports.tsx) ─────────────

function RecPill({ rec }: { rec: SpecialistReport["recommendation"] }) {
  if (!rec) return null;
  const cls =
    rec === "BUY" ? "pill-bull" :
    rec === "SELL" ? "pill-bear" :
    "pill-strong";
  return <span className={`pill ${cls} text-[11px] font-bold tracking-wide uppercase`}>{rec}</span>;
}

/** #1490 — deterministic, bundle-free report-quality badge (display-only). Renders nothing when the
 *  engine flag is OFF (label null), so the card is byte-identical to today by default. */
function QualityBadge({ label, score }: { label: string | null; score: number | null }) {
  if (!label) return null;
  const cls = label === "Strong" ? "pill-bull" : label === "Thin" ? "pill-warn" : "";
  return (
    <span
      className={`pill text-[10.5px] ${cls}`}
      title={score != null ? `Report quality ${score}/100 — deterministic completeness score` : undefined}
    >
      {label}
    </span>
  );
}

function MlBadge({ direction, confidence }: { direction: SpecialistReport["mlDirection"]; confidence: number | null }) {
  if (!direction || direction === "unavailable") return null;
  const arrow = direction === "up" ? "↑" : direction === "down" ? "↓" : "→";
  const cls = direction === "up" ? "text-bull" : direction === "down" ? "text-bear" : "text-white/60";
  // mlConfidence is forecast band-narrowness, NOT a directional confidence, so a
  // FLAT ~0% prediction scores HIGHEST — showing it would advertise the most
  // useless read as the most confident. Show the honest direction only; the real
  // quality (IC/Sharpe) lives in the walk-forward row. `confidence` intentionally
  // unused (kept in the signature for call-site symmetry).
  void confidence;
  const confStr = direction === "neutral" ? " · flat" : "";
  return (
    <span className={`pill text-[10.5px] num ${cls}`}>
      ML {arrow}{confStr}
    </span>
  );
}

/**
 * Derive a simple quality label from walk-forward IC and Sharpe.
 * "weak edge" when IC < 0.05 OR Sharpe < 0 (both must be present to judge).
 * Returns null when both values are null (no data).
 */
function wfQualityLabel(ic: number | null, sharpe: number | null): "weak edge" | "positive edge" | null {
  if (ic == null && sharpe == null) return null;
  const icWeak = ic != null && ic < 0.05;
  const sharpeWeak = sharpe != null && sharpe < 0;
  if (icWeak || sharpeWeak) return "weak edge";
  return "positive edge";
}

function WalkForwardRow({ ic, sharpe }: { ic: number | null; sharpe: number | null }) {
  const label = wfQualityLabel(ic, sharpe);

  if (ic == null && sharpe == null) {
    return (
      <p className="text-[12px] text-white/30">Model edge not yet validated for this symbol.</p>
    );
  }

  const icStr = ic != null ? ic.toFixed(3) : "—";
  const sharpeStr = sharpe != null ? sharpe.toFixed(2) : "—";
  const isWeak = label === "weak edge";

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-[12px] text-white/55 num">
        Walk-forward: IC <span className="text-white/80">{icStr}</span>
        {" · "}Sharpe <span className={sharpe != null && sharpe < 0 ? "text-bear" : "text-bull"}>{sharpeStr}</span>
      </span>
      {label && (
        <span className={`pill text-[10px] ${isWeak ? "pill-warn" : ""}`}>
          {label}
        </span>
      )}
    </div>
  );
}

/**
 * Signal quality pill with an explanatory caption.
 * Renders the raw signal_quality value, plus a plain-English caption below.
 */
const SIGNAL_QUALITY_CAPTIONS: Record<string, string> = {
  llm_only:  "LLM only — no model prediction this cycle.",
  converged: "LLM and model agree on direction.",
  partial:   "Partial overlap between LLM and model signal.",
  diverged:  "LLM and model disagree — treat with caution.",
  ml_plus_llm: "Both LLM and TFT model contributed.",
};

function SignalQualityTag({ quality }: { quality: string | null }) {
  if (!quality) return null;
  const caption = SIGNAL_QUALITY_CAPTIONS[quality] ?? quality;
  const isLlmOnly = quality === "llm_only";
  return (
    <div className="mt-2">
      <span className={`pill text-[10px] ${isLlmOnly ? "pill-warn" : ""}`}>
        {quality.replace(/_/g, " ")}
      </span>
      <p className="text-[11px] text-white/35 mt-1">{caption}</p>
    </div>
  );
}

function EscalateBadge({ reason }: { reason: string | null }) {
  return (
    <div className="flex items-start gap-2 text-[12px] text-amber-400/90 bg-amber-400/[0.04] border border-amber-400/20 rounded-xl px-4 py-3">
      <IconShield width={13} height={13} className="shrink-0 mt-0.5 text-amber-400" />
      <span>{reason ?? "Escalated — review required"}</span>
    </div>
  );
}

function SectionHeader({ label }: { label: string }) {
  return <div className="eyebrow mb-2">{label}</div>;
}

function SentimentBar({ score }: { score: number | null }) {
  if (score == null) return null;
  // score is 0-10; map to 0-100%
  const pct = score * 10;
  const color = score >= 6 ? "#00c27a" : score <= 4 ? "#ff453a" : "rgba(255,255,255,0.55)";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full overflow-hidden bg-white/[0.06]">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="num text-[11px] text-white/55 w-7 text-right">{score.toFixed(1)}</span>
    </div>
  );
}

function MlReturnsRow({ returns }: { returns: SpecialistReport["mlReturns"] }) {
  const { base, bull, bear } = returns;
  if (base == null && bull == null && bear == null) return null;
  // Magnitude-aware formatter (shared, unit-tested): a real-but-small projected
  // move never collapses to a dead-looking "0.0%" — see fmtMlReturn.
  const fmt = fmtMlReturn;
  const clr = (v: number | null) =>
    v == null ? "text-white/30" : v > 0 ? "text-bull" : v < 0 ? "text-bear" : "text-white/55";
  return (
    <div className="grid grid-cols-3 gap-3 text-center">
      <div>
        <div className="eyebrow mb-0.5">Bear</div>
        <div className={`num text-[13px] font-semibold ${clr(bear)}`}>{fmt(bear)}</div>
      </div>
      <div>
        <div className="eyebrow mb-0.5">Base</div>
        <div className={`num text-[14px] font-bold ${clr(base)}`}>{fmt(base)}</div>
      </div>
      <div>
        <div className="eyebrow mb-0.5">Bull</div>
        <div className={`num text-[13px] font-semibold ${clr(bull)}`}>{fmt(bull)}</div>
      </div>
    </div>
  );
}

/**
 * ML model contribution — the per-symbol TFT specialist's own read, surfaced in
 * the DEFAULT view. The quantitative counterpart to the LLM thesis in the
 * DecisionPanel: direction + the three scenario returns it projects, its
 * walk-forward track record (IC / Sharpe), and how it combined with the LLM
 * signal this cycle. Renders nothing when there is genuinely no model signal.
 */
function MlContributionBlock({ r }: { r: SpecialistReport }) {
  const hasDirection = r.mlDirection != null && r.mlDirection !== "unavailable";
  const hasReturns = r.mlReturns.base != null || r.mlReturns.bull != null || r.mlReturns.bear != null;
  const hasWf = r.walkforwardIc != null || r.walkforwardSharpe != null;
  if (!hasDirection && !hasReturns && !hasWf) return null;

  return (
    <div className="surface p-6 space-y-4 relative overflow-hidden">
      <div className="flex items-center justify-between relative">
        <SectionHeader label="ML model · TFT specialist" />
        {hasDirection
          ? <MlBadge direction={r.mlDirection} confidence={r.mlConfidence} />
          : <span className="text-[11px] text-white/30 italic">no model signal</span>}
      </div>

      {/* Scenario returns the model projects: Bear / Base / Bull */}
      {hasReturns
        ? <MlReturnsRow returns={r.mlReturns} />
        : <p className="text-[12px] text-white/30 relative">Scenario returns unavailable for this symbol.</p>}

      {/* Walk-forward track record (IC / Sharpe) + how it fused with the LLM */}
      <div className="border-t border-white/5 pt-3 space-y-1 relative">
        <WalkForwardRow ic={r.walkforwardIc} sharpe={r.walkforwardSharpe} />
        <SignalQualityTag quality={r.signalQuality} />
      </div>
    </div>
  );
}

function SignalPills({ r }: { r: SpecialistReport }) {
  const pills: { label: string; cls: string }[] = [];

  if (r.shortInterestPct != null && r.shortInterestPct > 0) {
    const cls = r.shortInterestPct > 5 ? "pill-bear" : r.shortInterestPct > 2.5 ? "pill-warn" : "";
    pills.push({ label: `Short ${r.shortInterestPct.toFixed(1)}%`, cls });
  }
  if (r.insiderTradesCount != null && r.insiderTradesCount > 0) {
    pills.push({ label: `${r.insiderTradesCount} insider`, cls: "pill-bull" });
  }
  if (r.politicalTradesCount != null && r.politicalTradesCount > 0) {
    pills.push({ label: `${r.politicalTradesCount} political`, cls: "pill-warn" });
  }
  if (r.materialEventsCount != null && r.materialEventsCount > 0) {
    pills.push({ label: `${r.materialEventsCount} material event${r.materialEventsCount > 1 ? "s" : ""}`, cls: "pill-strong" });
  }
  if (r.redditMentions != null && r.redditMentions > 50) {
    pills.push({ label: `${r.redditMentions} Reddit`, cls: "" });
  }
  if (r.signalQuality === "llm_only") {
    pills.push({ label: "LLM only", cls: "pill-warn" });
  }

  if (pills.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {pills.map((p) => (
        <span key={p.label} className={`pill text-[10.5px] ${p.cls}`}>{p.label}</span>
      ))}
    </div>
  );
}

/**
 * Compact signals strip — sentiment + key signal pills in the default card view.
 * (The senate tally lives in the separate Round-Table section, not here — this
 * endpoint carries no senate data.)
 */
function SignalsStrip({ r }: { r: SpecialistReport }) {
  const hasSentiment = r.sentimentScore != null;
  const hasPills =
    (r.shortInterestPct != null && r.shortInterestPct > 0) ||
    (r.insiderTradesCount != null && r.insiderTradesCount > 0) ||
    (r.politicalTradesCount != null && r.politicalTradesCount > 0) ||
    (r.materialEventsCount != null && r.materialEventsCount > 0) ||
    (r.redditMentions != null && r.redditMentions > 50) ||
    r.signalQuality === "llm_only";

  // Nothing to show — render nothing rather than an empty card.
  if (!hasSentiment && !hasPills) return null;

  return (
    <div className="surface p-6 space-y-5">
      <SectionHeader label="Signals" />

      {/* Sentiment */}
      {hasSentiment && (
        <div className="flex items-center gap-4">
          <span className="text-[11px] text-white/45 w-20 shrink-0">Sentiment</span>
          <div className="flex-1">
            <SentimentBar score={r.sentimentScore} />
          </div>
          <span className={`num text-[11px] font-semibold shrink-0 ${r.sentimentScore! >= 6 ? "text-bull" : r.sentimentScore! <= 4 ? "text-bear" : "text-white/60"}`}>
            {r.sentimentScore!.toFixed(1)} / 10
          </span>
        </div>
      )}

      {/* Key signal pills — the insider / short / material cluster, shown once */}
      {hasPills && (
        <div className="flex items-start gap-4">
          <span className="text-[11px] text-white/45 w-20 shrink-0 mt-1">Key signals</span>
          <div className="flex-1"><SignalPills r={r} /></div>
        </div>
      )}
    </div>
  );
}

/**
 * Decision panel — the at-a-glance "should I act?" block. A plain-language
 * summary plus two clearly separated lists: reasons to buy (pros) and reasons
 * for caution (cons). Bullets are deterministic, built from real signals with
 * actual numbers (engine: _build_pros_cons).
 *
 * The thesis line falls back: summary → investmentThesis when summary is empty.
 */
function DecisionPanel({ r }: { r: SpecialistReport }) {
  const hasPros = r.pros.length > 0;
  const hasCons = r.cons.length > 0;
  const thesis = r.summary || r.investmentThesis || "";
  if (!thesis && !hasPros && !hasCons) return null;
  return (
    <div className="surface p-6 space-y-4">
      {thesis && (
        <p className="text-[14.5px] leading-relaxed text-white/90">{thesis}</p>
      )}
      {(hasPros || hasCons) && (
        <div className="grid grid-cols-2 gap-4">
          <div className="surface-flat p-4 border-t-2 border-t-bull/40">
            <SectionHeader label="Reasons to buy" />
            {hasPros ? (
              <ul className="space-y-2">
                {r.pros.map((p, i) => (
                  <li key={i} className="flex items-start gap-2 text-[13px] leading-snug text-white/85">
                    <span className="text-bull mt-0.5 leading-none shrink-0">✓</span>
                    <span>{p.text}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[12.5px] italic text-white/30">No clear positives this cycle.</p>
            )}
          </div>
          <div className="surface-flat p-4 border-t-2 border-t-bear/40">
            <SectionHeader label="Reasons for caution" />
            {hasCons ? (
              <ul className="space-y-2">
                {r.cons.map((c, i) => (
                  <li key={i} className="flex items-start gap-2 text-[13px] leading-snug text-white/85">
                    <span className="text-bear mt-0.5 leading-none shrink-0">✕</span>
                    <span>{c.text}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[12.5px] italic text-white/30">No specific risks flagged this cycle.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Specialist card ──────────────────────────────────────────────────────────

/**
 * A single specialist report card. Header (symbol + rec + confidence + ML badge
 * + ESCALATED pill), optional escalation banner, then — once the symbol has any
 * synthesized content — the Signals strip, the ML contribution block and the
 * Decision panel. A report with no content yet shows an honest "warming up"
 * state instead of an empty card (mirrors the bundle's hasAnyContent check).
 */
function SpecialistCard({ r }: { r: SpecialistReport }) {
  // A report with no synthesized content at all is a placeholder — the specialist
  // hasn't completed its first research+model cycle yet (common during warm-up on
  // a local machine, where each symbol runs on-device). Show an explicit
  // "still warming up" state instead of a blank card.
  const hasAnyContent =
    r.recommendation != null ||
    r.sentimentScore != null ||
    !!(r.summary || r.investmentThesis || r.bullCase || r.bearCase || r.companySummary || r.newsSummary) ||
    (r.mlDirection != null && r.mlDirection !== "unavailable") ||
    r.pros.length > 0 || r.cons.length > 0 ||
    r.headlines.length > 0;

  const updatedStr = r.updatedAt
    ? r.updatedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) + " · " +
      r.updatedAt.toLocaleDateString([], { month: "short", day: "numeric" })
    : null;

  return (
    <div className="space-y-4">
      {/* ── Header ─────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[28px] font-bold tracking-tight3 leading-none text-white/92">{r.symbol}</h2>
          {getCompanyName(r.symbol) && (
            <div className="text-[13px] text-white/45 mt-1.5">{getCompanyName(r.symbol)}</div>
          )}
          <div className="flex items-center gap-2 mt-3 flex-wrap">
            <RecPill rec={r.recommendation} />
            <QualityBadge label={r.reportQualityLabel} score={r.reportQuality} />
            {r.confidence != null && (
              <span className="pill text-[10.5px] num text-white/60">
                {Math.round(r.confidence * 100)}% conf.
              </span>
            )}
            {r.mlDirection && r.mlDirection !== "unavailable" && (
              <MlBadge direction={r.mlDirection} confidence={r.mlConfidence} />
            )}
            {r.escalate && <StatusDot tone="off" className="!text-[10.5px]">ESCALATED</StatusDot>}
          </div>
        </div>
        {updatedStr && (
          <div className="text-[11px] text-white/30 num shrink-0 mt-1">{updatedStr}</div>
        )}
      </div>

      {/* ── Escalation banner ──────────────────────────────────── */}
      {r.escalate && r.escalateReason && <EscalateBadge reason={r.escalateReason} />}

      {/* ── Warming-up state (placeholder report, no content yet) ── */}
      {!hasAnyContent ? (
        <div className="surface p-8 text-center space-y-3">
          <div className="text-[14.5px] text-white/75">Specialist is still gathering data for {r.symbol}.</div>
          <p className="text-[12.5px] text-white/40 leading-relaxed max-w-[440px] mx-auto">
            Each specialist runs its full research and model cycle on-device. During
            warm-up that can take a few minutes per symbol on a local machine — this
            card fills in automatically once the first cycle completes.
          </p>
          {updatedStr && <div className="text-[10px] text-white/20 num">Last checked {updatedStr}</div>}
        </div>
      ) : (
        <>
          <SignalsStrip r={r} />
          <MlContributionBlock r={r} />
          <DecisionPanel r={r} />
          <ResearchSections r={r} />
        </>
      )}
    </div>
  );
}

// ─── Reports Page ─────────────────────────────────────────────────────────────

/**
 * Research sections (T3, #1452) — the carried-but-unrendered RPAR fields:
 * company summary, bull case, bear case, news. Each renders ONLY when present —
 * no empty placeholder, no fabricated text.
 */
function ResearchSections({ r }: { r: SpecialistReport }) {
  const items: { label: string; text: string | null; cls: string }[] = [
    { label: "Company", text: r.companySummary, cls: "text-white/35" },
    { label: "Bull case", text: r.bullCase, cls: "text-bull/80" },
    { label: "Bear case", text: r.bearCase, cls: "text-bear/80" },
    { label: "News", text: r.newsSummary, cls: "text-white/35" },
  ];
  const present = items.filter((i) => i.text && i.text.trim());
  if (present.length === 0) return null;
  return (
    <div className="surface p-6 space-y-4">
      {present.map(({ label, text, cls }) => (
        <div key={label}>
          <div className={`mb-1.5 text-[10px] uppercase tracking-[0.12em] font-semibold ${cls}`}>{label}</div>
          <p className="text-[13.5px] leading-relaxed text-white/85">{text}</p>
        </div>
      ))}
    </div>
  );
}

export function Reports() {
  useSpecialistPolling();
  const specialistReports = useStore((s) => s.specialistReports);
  const specialistStatus = useStore((s) => s.specialistStatus);
  const specialistMessage = useStore((s) => s.specialistMessage);

  // The engine sends an explicit message only when the registry is off / not
  // running ("unavailable"); otherwise the neutral pre-data state applies.
  const specialistEmptyNote =
    specialistMessage ??
    (specialistStatus === "unavailable"
      ? "Specialist registry is not running on this deployment."
      : "No specialist reports yet — the registry produces them as it evaluates the universe.");

  return (
    <div className="px-8 py-7 space-y-6 max-w-[1100px]">
      {/* ── Specialist reports (G1b′) ─────────────────────────────────── */}
      <div>
        <div className="eyebrow mb-2">Specialist Research</div>
        <h1 className="text-[26px] font-bold tracking-tight2 text-white/92">
          {specialistReports.length} {specialistReports.length === 1 ? "Research Report" : "Research Reports"}
        </h1>
      </div>

      {/* Consolidated explanation box (designed matching Decisions page) */}
      <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
        <IconLightbulb width={16} height={16} className="text-white/70 mt-1 shrink-0" />
        <div className="text-[13.5px] text-white/70 leading-relaxed space-y-2">
          <p>
            <span className="font-semibold text-white/90">How to read these reports.</span>{" "}
            Each card shows the analysis and recommendation of a single specialized AI assistant. The final trading decisions, however, are made by a collective vote of all nine AI assistants. This is why a trade can differ from what you see on a single report.
          </p>
          <p className="text-[13px] text-white/45">
            <span className="text-white/60">Tip:</span> Click on any stock card to see its full analysis, including purchase trends and market news.
          </p>
        </div>
      </div>

      {specialistReports.length === 0 ? (
        <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
          <IconShield width={16} height={16} className="text-white/30 mt-0.5" />
          <div className="text-[12px] text-white/45 leading-relaxed">{specialistEmptyNote}</div>
        </div>
      ) : (
        <div className="space-y-8">
          {specialistReports.map((r) => (
            <SpecialistCard key={r.symbol} r={r} />
          ))}
        </div>
      )}
    </div>
  );
}
