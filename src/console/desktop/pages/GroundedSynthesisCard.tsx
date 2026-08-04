import type { SpecialistReport, SynthesisCitation } from "@/console/types";
import type { ConsoleRoundTableDecision } from "@/console/live/roundTable";
import { RoundTableSenators, RoundTableBar } from "@/console/shared/RoundTableView";

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

function RecPill({ rec }: { rec: SpecialistReport["recommendation"] }) {
  if (!rec) return null;
  const cls = rec === "BUY" ? "text-bull" : rec === "SELL" ? "text-bear" : "text-white/55";
  return <span className={`num text-[16px] font-semibold leading-none ${cls}`}>{rec}</span>;
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
  tone,
  prose,
  citations,
  modelConfirms,
  r,
}: {
  title: string;
  tone: "bull" | "bear" | "neutral";
  prose: string | null;
  citations: SynthesisCitation[];
  /** Render the "Bestätigt durch Modell" source chip under this block. */
  modelConfirms?: boolean;
  r: SpecialistReport;
}) {
  const dot =
    tone === "bull" ? "text-bull" : tone === "bear" ? "text-bear" : "text-white/45";
  // Grounded prose is the source of truth; the citation list annotates it. A
  // sentence with a matching citation claim renders its "Tied to" chip; safe
  // reasoning without a binding renders plain (already fabrication-gated).
  const sentences =
    prose && !/^No supported (?:bull|bear|case|thesis)/i.test(prose) ? splitSentences(prose) : [];
  const empty = sentences.length === 0;
  return (
    <div className="space-y-2">
      <div className={`eyebrow !text-[13.5px] ${dot}`}>{title}</div>
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
        <div className="flex items-center gap-3 flex-wrap">
          <RecPill rec={r.recommendation} />
          {confPct != null && (
            <span className="num text-[12px] text-white/55">{confPct}% conf</span>
          )}
          <Arrow dir={r.mlDirection} />
          {r.escalate && (
            <span
              className="pill !text-[10.5px] !border-amber-400/40 !text-amber-300"
              title={r.escalateReason ?? "Escalated for human review"}
            >
              ESCALATED
            </span>
          )}
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

      {/* ── company header: what they do, main market, rough size ── */}
      {r.companySummary && (
        <div className="space-y-1.5">
          <div className="eyebrow !text-[13.5px] text-white/50">About the company</div>
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
        <div className="eyebrow !text-[13.5px] text-white/50">Model view</div>
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
        <div className="eyebrow !text-[13.5px] text-white/50">Data coverage</div>
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

      {/* ── senate verdict (Round-Table) — only when a decision exists ── */}
      {decision && decision.senators.length > 0 && (
        <div className="border-t border-white/6 pt-4 space-y-2.5">
          <div className="eyebrow !text-[13.5px] text-white/50">Round-Table verdict</div>
          <RoundTableBar
            votesFor={decision.votesFor}
            votesAbstain={decision.votesAbstain}
            votesAgainst={decision.votesAgainst}
          />
          <RoundTableSenators senators={decision.senators} />
        </div>
      )}

      {/* ── walk-forward footer (honest weak-edge label; omitted when gate-dead) ── */}
      {hasWalkForward && (
        <div className="border-t border-white/6 pt-3 num text-[11px] text-white/40 flex gap-4">
          {r.walkforwardIc != null && <span>IC {r.walkforwardIc.toFixed(3)}</span>}
          {r.walkforwardSharpe != null && <span>Sharpe {r.walkforwardSharpe.toFixed(2)}</span>}
          <span className="text-white/30">weak edge — not for sizing</span>
        </div>
      )}
    </div>
  );
}
