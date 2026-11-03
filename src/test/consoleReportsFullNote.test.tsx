import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { GroundedSynthesisCard } from "@/console/desktop/pages/GroundedSynthesisCard";
import type { SpecialistReport } from "@/console/types";

/**
 * REPORTS-UX Wave 1 (#2710) — the report body must render IN FULL, never
 * truncated, and the served-but-hidden quick-wins (⑤ TL;DR, ⑥ staleness,
 * ⑦ audit badge + rejected-claims) must appear. Palette: no amber on ESCALATED.
 */

// A single ~3000-char sentence (no . ! ?), so the ProseBlock renders it as ONE
// unit — a client-side clamp (or the old [:1000] server cap) would cut it.
const LONG = "BULLMARKER " + "A".repeat(3000);

function makeReport(over: Partial<SpecialistReport> = {}): SpecialistReport {
  return {
    symbol: "NVDA",
    recommendation: "BUY",
    sentimentScore: 7,
    confidence: 0.78,
    escalate: true,
    escalateReason: "flagged for human review",
    dataStale: true,
    companySummary: "NVIDIA designs accelerated-computing GPUs.",
    investmentThesis: LONG,
    bullCase: null,
    bearCase: null,
    newsSummary: null,
    headlines: [],
    reasons: [],
    edgeSignals: [],
    mlDirection: "up",
    mlConfidence: 0.4,
    mlReturns: { bear: -1, base: 1, bull: 2 },
    signalQuality: "ml_plus_llm",
    reportQuality: null,
    reportQualityLabel: null,
    reportMarkdown: null,
    reportAuditOk: true,
    reportAuditSummary: "61 claims · 0 flags",
    walkforwardIc: null,
    walkforwardSharpe: null,
    shortInterestPct: null,
    insiderTradesCount: 0,
    politicalTradesCount: null,
    materialEventsCount: 0,
    redditMentions: null,
    summary: "Topped-up long — staggered entry across the week.",
    pros: [],
    cons: [],
    synthesisCitations: [],
    synthesisDropped: [
      { kind: "bull", claim: "Record gross margin of 82%", reason: "no source match in 10-Q" },
    ],
    groundingOk: true,
    ...over,
  } as unknown as SpecialistReport;
}

describe("Reports — full untruncated note + quick-wins (#2710)", () => {
  it("renders the report body in full (no truncation / no clamp)", () => {
    render(<GroundedSynthesisCard r={makeReport()} />);
    const el = screen.getByText(/BULLMARKER/);
    // The whole ~3000-char sentence is present — not cut to 1000.
    expect(el.textContent!.length).toBeGreaterThan(2900);
  });

  it("shows the additive TL;DR summary on top", () => {
    render(<GroundedSynthesisCard r={makeReport()} />);
    expect(screen.getByText(/staggered entry across the week/)).toBeTruthy();
    expect(screen.getByText(/additive/i)).toBeTruthy();
  });

  it("surfaces the audit badge and the rejected-claims ledger (⑦)", () => {
    render(<GroundedSynthesisCard r={makeReport()} />);
    expect(screen.getByText(/61 claims · 0 flags/)).toBeTruthy();
    expect(screen.getByText(/Rejected claims \(1\)/)).toBeTruthy();
    expect(screen.getByText(/Record gross margin of 82%/)).toBeTruthy();
  });

  it("shows the staleness signal (⑥)", () => {
    render(<GroundedSynthesisCard r={makeReport()} />);
    expect(screen.getByText(/stale filings/)).toBeTruthy();
  });

  it("shows a visible Escalated tile with the plain-text reason, neutral (non-amber) — #2732", () => {
    const { container } = render(<GroundedSynthesisCard r={makeReport()} />);
    // the reason is now shown in full (not just a tooltip)
    expect(screen.getByText(/escalated for human review/i)).toBeTruthy();
    expect(screen.getByText("flagged for human review")).toBeTruthy();
    // neutral palette — no amber/yellow anywhere in the card
    expect(container.innerHTML).not.toMatch(/amber|text-yellow|bg-yellow/i);
  });
});
