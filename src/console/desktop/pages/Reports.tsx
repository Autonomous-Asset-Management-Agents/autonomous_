import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "@/console/store/useStore";
import { useSpecialistPolling } from "@/console/live/useSpecialistPolling";
import { useRoundTablePolling } from "@/console/live/useRoundTablePolling";
import { adaptSpecialistReport, reportBadgeRecommendation } from "@/console/live/specialist";
import { fetchSpecialistReport } from "@/lib/api";
import { IconShield, IconLightbulb } from "@/console/shared/Icons";
import { MarkdownNote } from "@/console/shared/MarkdownNote";
import { StatusDot } from "@/console/shared/StatusDot";
import { getCompanyName } from "@/console/lib/companyName";
import { GroundedSynthesisCard } from "@/console/desktop/pages/GroundedSynthesisCard";
import type { SpecialistReport } from "@/console/types";

/**
 * Console Reports page — the RESEARCH READER (#1998, RPT-GUI-2, Georg 2026-07-15).
 *
 * One big auditable report per company, searchable by topic block — no summary
 * card. The company is chosen through a search combobox (not a scroll dropdown).
 * The report body is the deterministic, machine-audited RPT-GOLD note
 * (``report_markdown``, flag-gated); when the report generator is OFF the reader
 * degrades gracefully to the specialist's structured fields so the page is never
 * empty. Styling is the app's own design system (console.css: surface / pill /
 * num / eyebrow / text-bull).
 */

// ─── Small building blocks ────────────────────────────────────────────────────

/** Recommendation tag — same plain colored-mono style as the Decisions page
 *  (BUY green, SELL red, HOLD muted); no pill, border or glow. */
function RecPill({
  rec,
  size = "text-[12px]",
}: {
  rec: SpecialistReport["recommendation"];
  size?: string;
}) {
  if (!rec) return null;
  const cls = rec === "BUY" ? "text-bull" : rec === "SELL" ? "text-bear" : "text-white/55";
  return <span className={`num ${size} font-semibold ${cls}`}>{rec}</span>;
}

/** Mechanical audit badge — the "fully auditable" proof at a glance, rendered as
 *  the console's standard StatusDot (white text + dot; no border, no glow). */
function AuditBadge({
  ok,
  summary,
  size = "",
}: {
  ok: boolean | null;
  summary: string | null;
  size?: string;
}) {
  if (ok == null) return null;
  return (
    <span
      className="inline-flex items-center"
      title={summary ? `Mechanical audit — ${summary}` : "Every figure checked against source"}
    >
      <StatusDot tone={ok ? "on" : "off"} className={size}>
        <span className="num">
          {ok ? "Audited" : "Audit flags"}
          {summary ? ` · ${summary}` : ""}
        </span>
      </StatusDot>
    </span>
  );
}

// ─── Report → topic blocks ────────────────────────────────────────────────────

type Section = { id: string; title: string; body: string };

/** Split the report Markdown into a head (before the first ``##``) and topic
 *  sections (one per ``## `` heading), stripping the leading "N. " numbering. */
function splitReport(md: string): { head: string; sections: Section[] } {
  const lines = md.split(/\r?\n/);
  const head: string[] = [];
  const sections: Section[] = [];
  let cur: Section | null = null;
  let seen = false;
  for (const line of lines) {
    const h = /^##\s+(.*)$/.exec(line);
    if (h) {
      if (cur) sections.push(cur);
      seen = true;
      const title = h[1].trim().replace(/^\d+\.\s*/, "");
      cur = { id: "sec-" + title.toLowerCase().replace(/[^a-z0-9]+/g, "-"), title, body: "" };
    } else if (cur) {
      cur.body += line + "\n";
    } else if (!seen && !/^#\s/.test(line)) {
      head.push(line); // head paragraphs (drop the top-level "# title")
    }
  }
  if (cur) sections.push(cur);
  return { head: head.join("\n").trim(), sections };
}

/** Fallback content when the report generator is OFF: build reader sections from
 *  the specialist's structured fields so the page still reads as one report. */
function fallbackSections(r: SpecialistReport): Section[] {
  const out: Section[] = [];
  const add = (title: string, text?: string | null) => {
    if (text && text.trim()) out.push({ id: "sec-" + title.toLowerCase(), title, body: text.trim() });
  };
  add("Summary", r.summary || r.investmentThesis);
  add("Bull case", r.bullCase);
  add("Bear case", r.bearCase);
  add("Company", r.companySummary);
  add("News", r.newsSummary);
  if (r.pros.length || r.cons.length) {
    const pros = r.pros.map((p) => `- ✓ ${p.text}${p.value ? ` (${p.value})` : ""}`).join("\n");
    const cons = r.cons.map((c) => `- ✗ ${c.text}${c.value ? ` (${c.value})` : ""}`).join("\n");
    add("Signals", [pros, cons].filter(Boolean).join("\n"));
  }
  return out;
}

// SymbolCombo removed — REPORTS-UX Wave 1 (#2710): the full-width list IS the
// picker + filter (no search-only, one-company-at-a-time model).

// ─── The reader ───────────────────────────────────────────────────────────────

function ReportReader({ r }: { r: SpecialistReport }) {
  const [query, setQuery] = useState("");
  const roundTable = useStore((s) => s.roundTable);
  const decision = roundTable.find((d) => d.symbol === r.symbol);

  const { head, sections } = useMemo(() => {
    if (r.reportMarkdown && r.reportMarkdown.trim()) return splitReport(r.reportMarkdown);
    return { head: "", sections: fallbackSections(r) };
  }, [r]);

  const q = query.trim().toLowerCase();
  const shown = q ? sections.filter((s) => (s.title + " " + s.body).toLowerCase().includes(q)) : sections;
  const company = getCompanyName(r.symbol);
  const updated = r.updatedAt
    ? r.updatedAt.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }) +
      " · " + r.updatedAt.toLocaleDateString("en-US", { month: "short", day: "numeric" })
    : null;

  const jump = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

  // The rich grounded synthesis card (Direction A) renders once the grounded
  // NEWS synthesis is done (groundingOk non-null) — the reports are
  // news/fundamentals-driven and must be visible MARKET-INDEPENDENTLY (operator
  // 2026-07-24). The HAR-RV volatility band is a market-data read: it is null
  // off-hours (no live connection), so it is OPTIONAL enrichment, NOT a card
  // gate — a vol-band gate would hide every card while the market is closed even
  // though the backend still serves the full news synthesis. When the band is
  // present the card shows it; when null the card shows an honest
  // "n/a (market closed)" note (see GroundedSynthesisCard). The news/grounding
  // requirement still holds, so a genuinely-incomplete card (no synthesis yet)
  // stays behind the honest in-progress placeholder.
  const showCard = r.groundingOk != null;

  const note = (
    <div className="space-y-3.5">
      {/* ── report header ── */}
      <div className="surface px-6 py-5">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="text-[24px] font-semibold tracking-tight2 leading-none text-white/92">{r.symbol}</div>
            {company && <div className="mt-1.5 text-[12.5px] text-white/45">{company}</div>}
          </div>
          {/* Right-aligned verdict block: the recommendation anchors the corner,
              with the audit status + timestamp stacked beneath it (right-aligned). */}
          <div className="flex flex-col items-end gap-1.5">
            <RecPill rec={reportBadgeRecommendation(r)} size="text-[16px] leading-none" />
            <AuditBadge ok={r.reportAuditOk} summary={r.reportAuditSummary} size="!text-[11px]" />
            {updated && <span className="num text-[11px] text-white/35">{updated}</span>}
          </div>
        </div>
        {head && <div className="mt-4 border-t border-white/6 pt-4 text-[13.5px]"><MarkdownNote source={head} /></div>}
      </div>

      {/* ── in-report search ── */}
      <div className="relative">
        <svg className="absolute left-3 top-[11px] opacity-42" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="m21 21-4-4" /></svg>
        <input
          className="w-full rounded-xl border border-white/8 bg-white/[0.04] py-[9px] pl-[34px] pr-9 text-[13px] text-white/92 outline-none focus:border-white/25"
          placeholder="Search this report — e.g. Fundamentals, Risk, PEG, Volatility …"
          autoComplete="off"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && (
          <button className="absolute right-2 top-[6px] px-1.5 text-[16px] leading-none text-white/40 hover:text-white/90" onClick={() => setQuery("")} title="Clear">×</button>
        )}
      </div>

      {/* ── topic chips ── */}
      {!q && sections.length > 0 && (
        <div className="flex gap-2 overflow-x-auto pb-1 [scrollbar-width:none]">
          {sections.map((s) => (
            <button key={s.id} className="pill !text-[11.5px] hover:border-white/20 hover:text-white/85" onClick={() => jump(s.id)}>
              {s.title}
            </button>
          ))}
        </div>
      )}

      {/* ── report blocks ── */}
      {shown.length === 0 ? (
        <div className="surface-flat rounded-xl px-5 py-6 text-center text-[13px] text-white/40">No section matches your search.</div>
      ) : (
        shown.map((s, i) => (
          <section key={s.id} id={s.id} className="surface px-6 py-5 scroll-mt-4">
            <h2 className="mb-3 flex items-center gap-2.5 text-[15px] font-semibold tracking-tight2 text-white/92">
              <span className="num inline-flex h-[22px] w-[22px] flex-none items-center justify-center rounded-[7px] border border-white/[0.12] bg-white/[0.04] text-[11px] text-white/70">
                {i + 1}
              </span>
              {s.title}
            </h2>
            <MarkdownNote source={s.body} />
          </section>
        ))
      )}
    </div>
  );

  // The rich grounded synthesis card is the report. The deterministic FactSet note
  // is intentionally NOT shown: on the desktop its feeds (price/news/fundamentals)
  // are not wired, so it renders as an all-"n/a" shell — noise, not signal. Kept
  // computed (audit path) but not surfaced.
  void note;
  if (!showCard)
    return (
      <div className="surface-flat rounded-xl px-5 py-8 text-center text-[13px] text-white/45">
        Research in progress — the card appears once news analysis and model
        signals for this name are complete.
      </div>
    );

  return (
    <div className="space-y-3.5">
      <GroundedSynthesisCard r={r} decision={decision} />
    </div>
  );
}

// ─── Wave 2 (#2732) row atoms: board call + escalation bolt ─────────────────────

/** The board's decision for the row — the Round-Table `signal_action`, the
 *  authoritative call (not the specialist recommendation). "—" when no decision
 *  exists for the symbol yet. */
function BoardCallPill({ action }: { action?: string }) {
  const a = action === "BUY" || action === "SELL" || action === "HOLD" ? action : null;
  if (!a) return <span className="num text-[12.5px] text-white/30" title="No board decision this cycle">—</span>;
  const cls = a === "BUY" ? "text-bull" : a === "SELL" ? "text-bear" : "text-white/55";
  return <span className={`num text-[12.5px] font-semibold ${cls}`}>{a}</span>;
}

/** Escalation marker — a white/light-grey lightning bolt (replaces the ambiguous
 *  grey dot). RPT-GOLD Wave 2 (#2732). */
function EscalationBolt({ reason }: { reason: string | null }) {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="currentColor"
      className="flex-none text-white/85"
      role="img"
      aria-label="Escalated — flagged for human review"
    >
      <title>{reason ? `Flagged for human review — ${reason}` : "Flagged for human review"}</title>
      <path d="M13 2 L3 14 L12 14 L11 22 L21 10 L12 10 Z" />
    </svg>
  );
}

// ─── Digest-list row signals (escalation bolt · ⑦ audit dot · ⑥ staleness) ───────
//  Palette: red/green + neutrals ONLY (no amber) — REPORTS-UX Wave 1 (#2710).
function RowSignals({ r }: { r: SpecialistReport }) {
  return (
    <span className="flex items-center justify-start gap-2">
      {r.escalate && <EscalationBolt reason={r.escalateReason} />}
      {r.reportAuditOk != null && (
        <span
          className="inline-flex"
          title={r.reportAuditOk ? "Audited — no flags" : "Audit flags"}
        >
          <span className={`inline-block h-[7px] w-[7px] flex-none rounded-full ${r.reportAuditOk ? "bg-[#00c27a]" : "bg-[#ff453a]"}`} />
        </span>
      )}
      {r.dataStale && (
        <span className="pill !text-[10px] !px-2 !py-0.5" title="A source's filings are stale">stale</span>
      )}
    </span>
  );
}

// ─── Page: full-width digest list + inline FULL-report expand (⑪, #2710) ─────────

export function Reports() {
  useSpecialistPolling();
  // Also poll the Round-Table so the grounded synthesis card can show the senate
  // verdict for the expanded symbol (shared store; no-op when no decisions exist).
  useRoundTablePolling();
  const specialistReports = useStore((s) => s.specialistReports);
  const specialistStatus = useStore((s) => s.specialistStatus);
  const specialistMessage = useStore((s) => s.specialistMessage);
  // Wave 2 (#2732): the Round-Table decision is the authoritative call shown per
  // row (signal_action), joined by symbol from the shared store.
  const roundTable = useStore((s) => s.roundTable);
  // Wave 2 Strand B (#2732): a symbol deep-link from another page (SymbolLink).
  const reportSymbol = useStore((s) => s.reportSymbol);
  const setReportSymbol = useStore((s) => s.setReportSymbol);
  const [query, setQuery] = useState("");
  // `open` is the user's explicit choice; until they touch a row the FIRST report
  // shows expanded (the reader is the point of the page). `touched` lets an explicit
  // collapse (open=null) win over that default.
  const [open, setOpen] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  // INC-2 (#2710): the FULL, untruncated report per symbol — fetched lazily for
  // whichever row is open (GET /specialist-report/{symbol}) so the reader never
  // truncates. The bulk /specialist-reports list stays light (body capped server-side).
  const [fullReports, setFullReports] = useState<Record<string, SpecialistReport>>({});
  const fetched = useRef<Set<string>>(new Set());

  // Any specialist with content is listable (report_markdown when the report
  // generator is ON; otherwise its structured fields drive the fallback reader).
  const readable = specialistReports.filter(
    (r) =>
      (r.reportMarkdown && r.reportMarkdown.trim()) ||
      !!(r.summary || r.investmentThesis || r.bullCase || r.bearCase || r.companySummary || r.newsSummary) ||
      r.pros.length > 0 || r.cons.length > 0 ||
      r.groundingOk != null, // the grounded synthesis card is content on its own
  );

  const q = query.trim().toLowerCase();
  const filtered = q
    ? readable.filter((r) => (r.symbol + " " + (getCompanyName(r.symbol) || "")).toLowerCase().includes(q))
    : readable;

  const effectiveOpen = touched ? open : readable[0]?.symbol ?? null;

  useEffect(() => {
    const sym = effectiveOpen;
    if (!sym || fetched.current.has(sym)) return;
    fetched.current.add(sym);
    void fetchSpecialistReport(sym).then((resp) => {
      const adapted = adaptSpecialistReport(resp.report);
      if (adapted) setFullReports((m) => ({ ...m, [sym]: adapted }));
    });
  }, [effectiveOpen]);

  // Strand B (#2732): consume a symbol deep-link — open that report expanded, then
  // clear the target so a later manual collapse (open=null) still wins. Syncing
  // local UI state to an external one-shot store signal is a legitimate effect.
  /* eslint-disable react-hooks/set-state-in-effect -- consume external deep-link signal */
  useEffect(() => {
    if (!reportSymbol) return;
    setOpen(reportSymbol);
    setTouched(true);
    setReportSymbol(null);
  }, [reportSymbol, setReportSymbol]);
  /* eslint-enable react-hooks/set-state-in-effect */

  function toggle(sym: string) {
    const currentlyOpen = effectiveOpen;
    setTouched(true);
    setOpen(currentlyOpen === sym ? null : sym);
  }

  const emptyNote =
    specialistMessage ??
    (specialistStatus === "unavailable"
      ? "The specialist registry is not running on this deployment."
      : "No specialist reports yet — the registry produces them as it evaluates the universe.");

  return (
    <div className="px-8 py-7 space-y-5 max-w-[1040px]">
      <div className="eyebrow">Specialist Research</div>
      <h1 className="text-[26px] font-bold tracking-tight2 text-white/92">Reports</h1>
      <p className="-mt-1 text-[13px] text-white/45">
        Every company&rsquo;s machine-audited research note — one row per company; open a row to read the full report inline.
      </p>

      {/* How-to-read note — a report is ONE assistant's view, not the final trade
          decision (the collective vote decides). Kept per Georg — important. */}
      <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
        <IconLightbulb width={16} height={16} className="text-white/70 mt-0.5 shrink-0" />
        <div className="text-[13px] text-white/70 leading-relaxed">
          <span className="font-semibold text-white/90">How to read these reports.</span>{" "}
          The call on each row is the <span className="font-semibold text-white/90">Round-Table decision</span> — the board&rsquo;s collective vote, and the only call the engine acts on. Inside a report the specialist is just one of many AI assistants whose weighted vote feeds that decision, which is why a single assistant&rsquo;s view can differ from the result.
        </div>
      </div>

      {readable.length > 0 && (
        <div className="relative">
          <svg className="absolute left-3 top-[11px] opacity-42" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="m21 21-4-4" /></svg>
          <input
            className="w-full rounded-xl border border-white/8 bg-white/[0.04] py-[9px] pl-[34px] pr-3 text-[13px] text-white/92 outline-none focus:border-white/25"
            placeholder="Filter company or ticker — NVDA, Apple …"
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      )}

      {readable.length === 0 ? (
        <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
          <IconShield width={16} height={16} className="text-white/30 mt-0.5" />
          <div className="text-[12.5px] text-white/45 leading-relaxed">{emptyNote}</div>
        </div>
      ) : (
        <div className="surface overflow-hidden">
          {/* Wave 2 (#2732): labelled column header — the call shown is the
              Round-Table decision (signal_action), not the specialist rec. */}
          {filtered.length > 0 && (
            <div className="num grid grid-cols-[16px_86px_1fr_auto_112px_58px] items-center gap-3 border-b border-white/10 px-5 py-2.5 text-[9.5px] uppercase tracking-wider text-white/35">
              <span />
              <span>Symbol</span>
              <span>Company</span>
              <span>Round Table</span>
              <span>Audit · Flag</span>
              <span className="text-right">Last run</span>
            </div>
          )}
          {filtered.length === 0 && (
            <div className="px-5 py-8 text-center text-[13px] text-white/40">No report matches &ldquo;{query}&rdquo;.</div>
          )}
          {filtered.map((r) => {
            const isOpen = effectiveOpen === r.symbol;
            const company = getCompanyName(r.symbol);
            const decision = roundTable.find((d) => d.symbol === r.symbol);
            const when = r.updatedAt
              ? r.updatedAt.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })
              : "";
            return (
              <div key={r.symbol} className="border-t border-white/6 first:border-t-0">
                <button
                  type="button"
                  aria-expanded={isOpen}
                  onClick={() => toggle(r.symbol)}
                  className={`grid w-full grid-cols-[16px_86px_1fr_auto_112px_58px] items-center gap-3 px-5 py-3 text-left ${isOpen ? "bg-white/[0.05]" : "hover:bg-white/[0.03]"}`}
                >
                  <span className={`num text-[13px] leading-none transition-transform ${isOpen ? "rotate-90 text-[#00c27a]" : "text-white/35"}`}>&rsaquo;</span>
                  <span className="num text-[13px] font-semibold text-white/92">{r.symbol}</span>
                  <span className="truncate text-[12.5px] text-white/55">{company}</span>
                  <BoardCallPill action={decision?.action} />
                  <RowSignals r={r} />
                  <span className="num text-right text-[11px] text-white/35">{when}</span>
                </button>
                {isOpen && (
                  <div className="border-t border-white/8 bg-black/20 px-5 py-4">
                    <ReportReader key={r.symbol} r={fullReports[r.symbol] ?? r} />
                  </div>
                )}
              </div>
            );
          })}
          <div className="num border-t border-white/6 px-5 py-2.5 text-[10.5px] text-white/30">
            {readable.length} reports · the full report is fetched per symbol on expand
          </div>
        </div>
      )}
    </div>
  );
}
