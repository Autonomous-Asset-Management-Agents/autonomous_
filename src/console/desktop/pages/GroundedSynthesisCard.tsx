import type { SpecialistReport, SynthesisCitation } from "@/console/types";
import type { ConsoleRoundTableDecision } from "@/console/live/roundTable";
import { RoundTableBar } from "@/console/shared/RoundTableView";
import { getCompanyName } from "@/console/lib/companyName";

/**
 * GroundedSynthesisCard — the rich ACN-style synthesis card (Direction A).
 *
 * Renders the grounded analyst prose sentence-by-sentence: every sentence the
 * backend gate (core/specialist/grounding.py) bound to a real headline carries
 * its "Tied to: …" chip; safe reasoning that passed the fabrication gates but
 * has no single supporting headline renders WITHOUT a chip (never invented —
 * the entity/number gates already dropped anything uncheckable).
 *
 * The model view (Modell-Sicht) connects the news read to OUR OWN models: TFT
 * direction/confidence + bear/base/bull scenario returns, the HAR-RV volatility
 * band, the Round-Table (senate) vote and the walk-forward footer. When the
 * model side agrees with a case, that case shows a "Bestätigt durch Modell"
 * source chip — models as sources, not just LLM news summaries. Without a
 * market-data connection the section states so honestly instead of fabricating.
 *
 * Presence is gated on `groundingOk != null` (set only when the card flag is ON).
 * Styling is the app's own design system (console.css: surface / pill / num /
 * text-bull / text-bear).
 */

function Arrow({ dir }: { dir: SpecialistReport["mlDirection"] }) {
  if (dir === "up") return <span className="text-bull num text-[12px]">ML ↑</span>;
  if (dir === "down") return <span className="text-bear num text-[12px]">ML ↓</span>;
  if (dir === "neutral") return <span className="num text-[12px] text-white/45">ML →</span>;
  return null;
}

/** Compact label for a model-signal source line (the raw English line is
 *  prompt material for the LLM — the chip shows the essence, the Modell-Sicht
 *  box carries the full context). */
function modelChipLabel(line: string): string {
  const range = line.match(/\$(\d+(?:\.\d+)?) and \$(\d+(?:\.\d+)?)/);
  if (range) return `vol model: 20-day range $${range[1]}–$${range[2]}`;
  const rank = line.match(/#(\d+) of (\d+)/);
  if (rank) return `LSTM rank #${rank[1]} of ${rank[2]}`;
  const pct = line.match(/(\d+(?:\.\d+)?)% either way/);
  if (pct) return `vol model: ±${pct[1]}% (20 days)`;
  return line.replace(/^\S+\s+model signal:\s*/i, "").slice(0, 60);
}

/** Split display prose into sentences (mirrors the backend's sentence units). */
function splitSentences(text: string): string[] {
  return text
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** The model-confirmation source chip ("models as sources"). */
function ModelConfirmChip({ r }: { r: SpecialistReport }) {
  const confPct = r.mlConfidence != null ? Math.round(r.mlConfidence * 100) : null;
  const base = r.mlReturns.base;
  return (
    <div className="text-[11px] text-white/40 leading-snug">
      <span className="text-white/30">Confirmed by model: </span>
      <span className="italic">
        TFT {r.mlDirection === "up" ? "↑" : "↓"}
        {base != null && ` · base scenario ${base > 0 ? "+" : ""}${base.toFixed(1)}%`}
        {confPct != null && ` · confidence ${confPct}%`}
      </span>
    </div>
  );
}

/** One prose block (Synthesis / Bull / Bear): the grounded sentences, each with
 *  its "Tied to" chip when a headline binding exists. Empty → honest empty line. */
function ProseBlock({
  title,
  prose,
  citations,
  modelConfirms,
  r,
}: {
  title: string;
  /** Kept for the callers' semantics; UXC-1 S8 (#3177) removed the coloured header. */
  tone: "bull" | "bear" | "neutral";
  prose: string | null;
  citations: SynthesisCitation[];
  /** Render the "Bestätigt durch Modell" source chip under this block. */
  modelConfirms?: boolean;
  r: SpecialistReport;
}) {
  // UXC-1 S8 (#3177): headers are plain .eyebrow — direction reads from the
  // block's content, never from a coloured label.
  // Grounded prose is the source of truth; the citation list annotates it. A
  // sentence with a matching citation claim renders its "Tied to" chip; safe
  // reasoning without a binding renders plain (already fabrication-gated).
  const sentences =
    prose && !/^No supported (?:bull|bear|case|thesis)/i.test(prose) ? splitSentences(prose) : [];
  const empty = sentences.length === 0;
  return (
    <div className="space-y-2">
      <div className="eyebrow">{title}</div>
      {empty ? (
        <div className="text-[12.5px] italic text-white/35">
          No supported case from this news cycle yet — updated with the next research round.
        </div>
      ) : (
        <div className="space-y-2.5">
          {(() => {
            // Each distinct source gets its chip ONCE per block — a second
            // sentence citing the same source repeats no chip (live feedback
            // 2026-07-23: the identical model chip twice in a row "looks dumb").
            const seen = new Set<string>();
            return sentences.map((s, i) => {
              // Exact claim match, with containment fallback for sentences that
              // the abbreviation-blind splitter cut differently than the backend.
              const cites = citations.filter(
                (c) => c.claim === s || c.claim.includes(s) || s.includes(c.claim)
              );
              const fresh = cites.filter((c) => {
                if (seen.has(c.headlineText)) return false;
                seen.add(c.headlineText);
                return true;
              });
              return (
                <div key={i} className="space-y-1">
                  <div className="text-[13.5px] leading-relaxed text-white/85">{s}</div>
                  {fresh.map((c, j) =>
                    /\bmodel signal:/i.test(c.headlineText) ? (
                      // The system's OWN quantitative view as the source —
                      // compact label; full context lives in the Model view box.
                      <div key={j} className="text-[11px] leading-snug">
                        <span className="text-white/30">Tied to: </span>
                        <span className="num text-white/60">
                          Own model · {modelChipLabel(c.headlineText)}
                        </span>
                      </div>
                    ) : (
                      <div key={j} className="text-[11px] text-white/40 leading-snug">
                        <span className="text-white/30">Tied to: </span>
                        <span className="italic">
                          [H{c.headlineIndex}] &ldquo;{c.headlineText}&rdquo;
                        </span>
                      </div>
                    )
                  )}
                </div>
              );
            });
          })()}
          {modelConfirms && <ModelConfirmChip r={r} />}
        </div>
      )}
    </div>
  );
}

export function GroundedSynthesisCard({
  r,
  decision,
}: {
  r: SpecialistReport;
  /** The matching Round-Table decision for this symbol, or undefined. */
  decision?: ConsoleRoundTableDecision;
}) {
  const allCites = r.synthesisCitations ?? [];
  const thesisCites = allCites.filter((c) => c.kind === "thesis");
  const bullCites = allCites.filter((c) => c.kind === "bull");
  const bearCites = allCites.filter((c) => c.kind === "bear");

  const confPct = r.confidence != null ? Math.round(r.confidence * 100) : null;
  const sourced = allCites.length;

  const mlAvailable = r.mlDirection === "up" || r.mlDirection === "down" || r.mlDirection === "neutral";
  const mlConfPct = r.mlConfidence != null ? Math.round(r.mlConfidence * 100) : null;
  const hasScenario =
    r.mlReturns.bear != null || r.mlReturns.base != null || r.mlReturns.bull != null;
  const hasWalkForward = r.walkforwardIc != null || r.walkforwardSharpe != null;

  const fmtPct = (v: number | null) =>
    v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;

  return (
    <div className="surface px-6 py-5 space-y-5">
      {/* ── header ── */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        {/* Company name + ticker top-left — the report's subject, NOT a call.
            The authoritative call is the Round-Table verdict below (#2732). */}
        <div className="flex items-baseline gap-2.5 flex-wrap">
          <span className="text-[20px] font-semibold tracking-tight2 leading-none text-white/92">
            {getCompanyName(r.symbol) || r.symbol}
          </span>
          {getCompanyName(r.symbol) && <span className="num text-[13px] text-white/40">{r.symbol}</span>}
          <Arrow dir={r.mlDirection} />
        </div>
        {/* Grounding badge + freshness — proof-of-honesty at a glance. */}
        <div className="flex flex-col items-end gap-0.5">
          <div
            className="num text-[11px] text-white/45"
            title="Every sourced claim is mechanically bound to a real headline of this symbol; fabricated entities and numbers never render."
          >
            <span className={r.groundingOk ? "text-bull" : "text-white/55"}>
              {r.groundingOk ? "✓ " : ""}
              {sourced} sourced
            </span>
          </div>
          {/* ⑦ (#2710): the mechanical audit verdict, served but not shown before. */}
          {r.reportAuditOk != null && (
            <span
              className="num text-[11px]"
              title={r.reportAuditSummary ?? "Every displayed figure is checked against its source"}
            >
              <span className={r.reportAuditOk ? "text-bull" : "text-bear"}>
                {r.reportAuditOk ? "✓ Audited" : "⚑ Audit flags"}
                {r.reportAuditSummary ? ` · ${r.reportAuditSummary}` : ""}
              </span>
            </span>
          )}
          {/* ⑥ (#2710): honest freshness — a source's filings are beyond the cutoff. */}
          {r.dataStale && (
            <span className="num text-[10.5px] text-white/50" title="A source's filings are stale">
              stale filings
            </span>
          )}
          {r.updatedAt && (
            <span
              className="num text-[10.5px] text-white/35"
              title="Reports refresh continuously (focus names every ~2 hours)."
            >
              as of{" "}
              {r.updatedAt.toLocaleTimeString("en-GB", {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
          )}
        </div>
      </div>

      {/* ── Escalated (#2732): the plain-text reason, VISIBLE (not just a tooltip)
             — a white/light-grey lightning bolt + the reason + an honest note that
             it is an attention marker, not a call. ── */}
      {r.escalate && (
        <div className="surface-flat rounded-xl px-4 py-3 flex items-start gap-3">
          <svg
            width="15"
            height="15"
            viewBox="0 0 24 24"
            fill="currentColor"
            className="text-white/85 mt-0.5 flex-none"
            role="img"
            aria-label="Escalated — flagged for human review"
          >
            <path d="M13 2 L3 14 L12 14 L11 22 L21 10 L12 10 Z" />
          </svg>
          <div>
            <div className="num text-[10px] uppercase tracking-wide text-white/40">Escalated for human review</div>
            {r.escalateReason && <div className="mt-0.5 text-[13px] text-white/85">{r.escalateReason}</div>}
            <div className="mt-1 text-[11.5px] leading-snug text-white/40">
              An unusual signal tripped the specialist&rsquo;s escalation rule — an attention marker only. It is not a
              buy/sell action and does not change the Round-Table decision.
            </div>
          </div>
        </div>
      )}

      {/* ── TL;DR (⑤, #2710): an ADDITIVE one-liner on top — never a replacement
             for the full note below (the `summary` field is served, but was only
             consumed by the dead fallback reader before). ── */}
      {r.summary && r.summary.trim() && (
        <div className="border-t border-white/6 pt-4">
          <div className="text-[10px] uppercase tracking-wide text-white/35">
            Summary <span className="tracking-normal normal-case text-white/25">· additive — the full note follows</span>
          </div>
          <div className="mt-1 text-[14.5px] font-semibold leading-snug text-white/90">{r.summary}</div>
        </div>
      )}

      {/* ── company header: what they do, main market, rough size ── */}
      {r.companySummary && (
        <div className="space-y-1.5">
          <div className="eyebrow">About the company</div>
          <div className="text-[13px] leading-relaxed text-white/70">
            {r.companySummary}
          </div>
        </div>
      )}

      {/* ── synthesis (thesis) ── */}
      <ProseBlock
        title="Synthesis"
        tone="neutral"
        prose={r.investmentThesis}
        citations={thesisCites}
        r={r}
      />

      {/* ── bull / bear (model-confirmed side carries the model source chip) ── */}
      <div className="grid gap-5 sm:grid-cols-2">
        <ProseBlock
          title="Bull case"
          tone="bull"
          prose={r.bullCase}
          citations={bullCites}
          modelConfirms={r.mlDirection === "up"}
          r={r}
        />
        <ProseBlock
          title="Bear case"
          tone="bear"
          prose={r.bearCase}
          citations={bearCites}
          modelConfirms={r.mlDirection === "down"}
          r={r}
        />
      </div>

      {/* ── model view: OUR models next to the news read — never fabricated ── */}
      <div className="surface-flat rounded-xl px-4 py-3 space-y-2">
        <div className="eyebrow">Model view</div>
        {mlAvailable ? (
          <div className="space-y-2">
            <div className="flex items-center gap-3 flex-wrap">
              <Arrow dir={r.mlDirection} />
              {mlConfPct != null && (
                <span className="num text-[12px] text-white/55">
                  confidence {mlConfPct}%
                </span>
              )}
            </div>
            {hasScenario && (
              <div className="grid grid-cols-3 gap-2 text-center">
                <div>
                  <div className="text-[10.5px] uppercase tracking-wide text-white/35">Bear</div>
                  <div className="num text-[13px] text-bear">{fmtPct(r.mlReturns.bear)}</div>
                </div>
                <div>
                  <div className="text-[10.5px] uppercase tracking-wide text-white/35">Base</div>
                  <div className="num text-[13px] text-white/80">{fmtPct(r.mlReturns.base)}</div>
                </div>
                <div>
                  <div className="text-[10.5px] uppercase tracking-wide text-white/35">Bull</div>
                  <div className="num text-[13px] text-bull">{fmtPct(r.mlReturns.bull)}</div>
                </div>
              </div>
            )}
          </div>
        ) : r.priceBandLow == null && r.lstmXsecRank == null ? (
          // Only when there is NO model content at all — with a price range or
          // rank on display this line would just be noise above real numbers.
          <div className="text-[11.5px] text-white/40 leading-snug">
            No directional model signal for this name — only what survives walk-forward validation is shown.
          </div>
        ) : null}
        {/* LSTM cross-section standing — the same rank the Round Table votes on. */}
        {r.lstmXsecRank != null && r.lstmXsecUniverse != null && (
          <div className="border-t border-white/6 pt-2 flex items-baseline gap-3 flex-wrap">
            <span className="num text-[13px] text-white/85">
              LSTM rank #{r.lstmXsecRank} of {r.lstmXsecUniverse}
            </span>
            {r.lstmXsecPercentile != null && (
              <span className="num text-[11px] text-white/45">
                scores above {r.lstmXsecPercentile.toFixed(0)}% of the universe
              </span>
            )}
          </div>
        )}
        {/* TA reads (only when the TA stage produced them this cycle) */}
        {(r.rsi14 != null || r.macdSignal) && (
          <div className="border-t border-white/6 pt-2 num text-[11.5px] text-white/55 flex gap-4">
            {r.rsi14 != null && <span>RSI-14 {r.rsi14.toFixed(1)}</span>}
            {r.macdSignal && <span>MACD {r.macdSignal}</span>}
          </div>
        )}
        {/* OWN-MODEL price range: HAR-RV sigma projected on the last close. */}
        {r.priceBandLow != null && r.priceBandHigh != null && (
          <div className="border-t border-white/6 pt-2 space-y-1">
            <div className="num text-[15px] text-white/90">
              ${r.priceBandLow.toFixed(2)} – ${r.priceBandHigh.toFixed(2)}
            </div>
            <div className="text-[11px] text-white/45 leading-snug">
              our vol model's expected 20-day price range
              {r.lastClose != null && <> · last close ${r.lastClose.toFixed(2)}</>}
              {r.volBandSigmaPct != null && <> · ±{r.volBandSigmaPct.toFixed(1)}%</>}
            </div>
          </div>
        )}
        {/* Hard price stats from the same bars (deterministic, LLM-free). */}
        {(r.chg20dPct != null || r.dist52wHighPct != null) && (
          <div className="border-t border-white/6 pt-2 num text-[11.5px] text-white/55 flex gap-4 flex-wrap">
            {r.chg20dPct != null && (
              <span className={r.chg20dPct >= 0 ? "text-bull" : "text-bear"}>
                past 20 days {r.chg20dPct >= 0 ? "+" : ""}
                {r.chg20dPct.toFixed(1)}%
              </span>
            )}
            {r.dist52wHighPct != null && (
              <span>
                {r.dist52wHighPct.toFixed(1)}% vs 52-week high
              </span>
            )}
          </div>
        )}
        {r.priceBandLow == null && (
          <div className="border-t border-white/6 pt-2 space-y-1">
            {r.volBandSigmaPct != null ? (
              <>
                <div className="num text-[12px] text-white/70">
                  ±{r.volBandSigmaPct.toFixed(1)}% volatility band (HAR-RV, 20 trading days)
                </div>
                <div className="text-[11px] text-white/40 leading-snug">
                  A spread, not a direction call — it sizes risk, it does not pick a side.
                </div>
              </>
            ) : (
              // The HAR-RV band needs a live market-data connection; off-hours it
              // is null. Report visibility is news/fundamentals-driven, so the
              // band is stated as unavailable honestly instead of hidden or left
              // looking broken (operator 2026-07-24).
              <>
                <div className="text-[12px] text-white/45">
                  Volatility — n/a (market closed)
                </div>
                <div className="text-[11px] text-white/40 leading-snug">
                  The HAR-RV volatility band needs a live market-data connection — it returns once the market reopens.
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* ── data footprint: EVERY source category the bot checks, always shown —
             a 0 still proves the system looked (Georg 2026-07-23) ── */}
      <div className="surface-flat rounded-xl px-4 py-3 space-y-2">
        <div className="eyebrow">Data coverage</div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-2">
          {(
            [
              ["News headlines", `${r.headlines.length}`],
              ["Insider filings (Form 4)", `${r.insiderTradesCount ?? 0}`],
              // #2587: null = the SOURCE failed this cycle (engine serializes null,
              // e.g. Quiver 404 / Reddit 403) — "—" (could not check), never a
              // fabricated 0 ("checked, nothing found").
              ["Congress trades", r.politicalTradesCount != null ? `${r.politicalTradesCount}` : "—"],
              ["8-K filings", `${r.materialEventsCount ?? 0}`],
              ["Reddit mentions (24h)", r.redditMentions != null ? `${r.redditMentions}` : "—"],
              [
                "Short interest",
                r.shortInterestPct != null ? `${r.shortInterestPct.toFixed(1)}%` : "—",
              ],
              [
                "Price history",
                r.volBandSigmaPct != null ? "5 years (daily)" : "—",
              ],
              [
                "LSTM ranking",
                r.lstmXsecRank != null && r.lstmXsecUniverse != null
                  ? `#${r.lstmXsecRank}/${r.lstmXsecUniverse}`
                  : "—",
              ],
              [
                "Sentiment score",
                r.sentimentScore != null ? `${r.sentimentScore.toFixed(1)}/10` : "—",
              ],
            ] as [string, string][]
          ).map(([label, value]) => (
            <div key={label}>
              <div className="text-[10px] uppercase tracking-wide text-white/35">
                {label}
              </div>
              <div className="num text-[12.5px] text-white/75">{value}</div>
            </div>
          ))}
        </div>
        <div className="text-[10.5px] text-white/30 leading-snug">
          Every source the system checked for this name this cycle — a 0 means checked, nothing
          found; — means the source could not be checked this cycle.
        </div>
      </div>

      {/* ── Round-Table verdict — the AUTHORITATIVE decision + full transparency on
             how the consensus is computed from the individual agent votes (#2732).
             consensus = Σ(score×weight) / Σ(weight) over weight>0 voters
             (core/round_table/consensus.py:265-271). ── */}
      {decision && decision.senators.length > 0 && (() => {
        const active = decision.senators.filter((s) => s.weight > 0);
        const wSum = active.reduce((a, s) => a + s.conviction * s.weight, 0);
        const wTot = active.reduce((a, s) => a + s.weight, 0);
        // Prefer the transparent Σ(score×weight)/Σweight; fall back to the served
        // consensus_score (rebased from the signed conviction) if no weighted voter.
        const consensusPct = wTot > 0 ? Math.round((wSum / wTot) * 100) : Math.round(((decision.conviction + 1) / 2) * 100);
        const abstainers = decision.senators.length - active.length;
        // The console adapter carries votes as BULL/BEAR/ABSTAIN; the board speaks
        // BUY/HOLD/SELL — the same vocabulary as the decision.
        const voteWord = (v: string) => (v === "BULL" ? "BUY" : v === "BEAR" ? "SELL" : "HOLD");
        const voteCls = (v: string) => (v === "BULL" ? "text-bull" : v === "BEAR" ? "text-bear" : "text-white/55");
        const actCls = decision.action === "BUY" ? "text-bull" : decision.action === "SELL" ? "text-bear" : "text-white/55";
        const grid = "grid grid-cols-[minmax(104px,1fr)_40px_42px_44px_52px_minmax(0,1.2fr)] gap-2.5";
        return (
          <div className="border-t border-white/6 pt-4 space-y-3">
            <div className="eyebrow">Round-Table verdict</div>

            {/* Consensus · Confidence Vote — the result of the weighted vote */}
            <div className="flex items-baseline gap-2.5 flex-wrap">
              <span className={`num text-[22px] font-bold leading-none ${actCls}`}>{consensusPct}%</span>
              <span className="text-[12px] text-white/55">
                board consensus — the result of the weighted vote (
                <span className={`num font-semibold ${actCls}`}>{decision.action}</span>)
              </span>
            </div>
            <RoundTableBar
              votesFor={decision.votesFor}
              votesAbstain={decision.votesAbstain}
              votesAgainst={decision.votesAgainst}
            />
            <div className="num text-[11px] text-white/55">
              <span className="text-bull">{decision.votesFor} buy</span> ·{" "}
              <span className="text-white/45">{decision.votesAbstain} hold</span> ·{" "}
              <span className="text-bear">{decision.votesAgainst} sell</span>
              {abstainers > 0 && <> · {abstainers} abstained (weight 0 → excluded from the average)</>}
            </div>
            {confPct != null && (
              <div className="text-[11.5px] text-white/40">
                Specialist note confidence: {confPct}% — data quality of this report, not the vote result.
              </div>
            )}

            {/* per-agent votes + the value each contributes to the vote */}
            <div className="mt-1">
              <div className={`${grid} border-b border-white/10 pb-1.5 num text-[9px] uppercase tracking-wide text-white/35`}>
                <span>Agent</span>
                <span>Vote</span>
                <span className="text-right">Score</span>
                <span className="text-right">Weight</span>
                <span className="text-right">Contrib.</span>
                <span>Rationale</span>
              </div>
              {decision.senators.map((s, i) => (
                <div key={i} className={`${grid} items-start border-t border-white/6 py-1 ${s.weight === 0 ? "opacity-55" : ""}`}>
                  <span className="text-[12px] text-white/70">{s.name}</span>
                  <span className={`num text-[11.5px] font-semibold ${voteCls(s.vote)}`}>{voteWord(s.vote)}</span>
                  <span className="num text-right text-[11.5px] text-white/45">{s.conviction.toFixed(2)}</span>
                  <span className="num text-right text-[11.5px] text-white/45">{s.weight.toFixed(1)}</span>
                  <span className="num text-right text-[11.5px] text-white/60" title="score × weight">
                    {s.weight > 0 ? (s.conviction * s.weight).toFixed(2) : "—"}
                  </span>
                  <span className="text-[11.5px] leading-snug text-white/45">{s.reasoning}</span>
                </div>
              ))}
            </div>

            {/* the computation transparency row — how the consensus is derived */}
            <div className="border-t border-white/10 pt-2 num text-[11.5px] leading-relaxed text-white/55">
              consensus = Σ(score × weight) <span className="text-white/85">{wSum.toFixed(2)}</span> ÷ Σ weight{" "}
              <span className="text-white/85">{wTot.toFixed(2)}</span> = <span className="text-white/85">{consensusPct}%</span>{" "}
              · thresholds: BUY ≥ 65% · SELL ≤ 35% · else HOLD
            </div>
          </div>
        );
      })()}

      {/* ── walk-forward footer (honest weak-edge label; omitted when gate-dead) ── */}
      {hasWalkForward && (
        <div className="border-t border-white/6 pt-3 num text-[11px] text-white/40 flex gap-4">
          {r.walkforwardIc != null && <span>IC {r.walkforwardIc.toFixed(3)}</span>}
          {r.walkforwardSharpe != null && <span>Sharpe {r.walkforwardSharpe.toFixed(2)}</span>}
          <span className="text-white/30">weak edge — not for sizing</span>
        </div>
      )}

      {/* ── rejected claims (⑦, #2710): the NEGATIVE audit evidence — what the
             mechanical auditor refused to let the note say, and why. `synthesisDropped`
             is served + adapted but was rendered nowhere before. ── */}
      {r.synthesisDropped && r.synthesisDropped.length > 0 && (
        <details className="border-t border-white/6 pt-3">
          <summary className="num cursor-pointer text-[12px] text-white/55">
            Rejected claims ({r.synthesisDropped.length}) — mechanically checked
          </summary>
          <ul className="mt-2 list-disc space-y-1.5 pl-5">
            {r.synthesisDropped.map((d, i) => (
              <li key={i} className="text-[12px] leading-relaxed text-white/45">
                <span className="text-white/70">&ldquo;{d.claim}&rdquo;</span> — {d.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
