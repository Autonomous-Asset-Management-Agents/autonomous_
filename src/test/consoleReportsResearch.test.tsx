import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { Reports } from "../console/desktop/pages/Reports";
import { useStore } from "../console/store/useStore";
import type { SpecialistReport } from "../console/types";

// T3 (#1452): the specialist card must render the carried-but-unrendered RPAR
// fields (company / bull / bear / news), each only when present. The page mounts
// two polls; stub both so the test renders from seeded store state.
vi.mock("../lib/api", () => ({
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchRoundTableDecisions: vi.fn().mockResolvedValue(null),
  fetchSpecialistReports: vi.fn().mockResolvedValue({ status: "ok", reports: [] }),
  // #2710: row-expand lazy fetch — null so the reader renders the seeded list report.
  fetchSpecialistReport: vi.fn().mockResolvedValue({ status: "ok", report: null }),
}));

const report = (over: Partial<SpecialistReport> = {}): SpecialistReport => ({
  symbol: "AAPL",
  recommendation: "BUY",
  sentimentScore: 7.2,
  confidence: 0.81,
  escalate: false,
  escalateReason: null,
  companySummary: null,
  investmentThesis: null,
  bullCase: null,
  bearCase: null,
  newsSummary: null,
  headlines: [],
  reasons: [],
  edgeSignals: [],
  mlDirection: "up",
  mlConfidence: 0.6,
  mlReturns: { base: 1.5, bull: 3.0, bear: -1.0 },
  signalQuality: "ml_plus_llm",
  walkforwardIc: 0.08,
  walkforwardSharpe: 0.9,
  shortInterestPct: null,
  insiderTradesCount: null,
  politicalTradesCount: null,
  materialEventsCount: null,
  redditMentions: null,
  summary: null,
  pros: [],
  cons: [],
  updatedAt: null,
  groundingOk: null,
  synthesisCitations: [],
  synthesisDropped: [],
  volBandLowPct: null,
  volBandBasePct: null,
  volBandHighPct: null,
  volBandSigmaPct: null,
  scenarioBasis: null,
  lstmXsecPercentile: null,
  lstmXsecRank: null,
  lstmXsecUniverse: null,
  lastClose: null,
  priceBandLow: null,
  priceBandHigh: null,
  chg20dPct: null,
  dist52wHighPct: null,
  rsi14: null,
  macdSignal: null,
  ...over,
});

describe("Reports — specialist research sections (T3 / RPAR)", () => {
  beforeEach(() => {
    useStore.setState({ roundTable: [], specialistReports: [], specialistStatus: "", specialistMessage: null });
  });

  it("renders the grounded card with the real volatility band when both news + vol band are present", () => {
    // Case (c) — unchanged path: news synthesis done AND the HAR-RV band computed
    // (live market data). The card renders and the real band value is shown.
    useStore.setState({
      specialistReports: [
        report({
          groundingOk: true,
          volBandSigmaPct: 5.2,
          companySummary: "Apple designs consumer devices and services.",
          investmentThesis: "Model-led thesis: the quantitative view favors patience.",
          bullCase: "Services margin expansion drives the upside.",
          bearCase: "Hardware cycle softness is the key risk.",
        }),
      ],
    });
    render(<Reports />);
    // Card-only reader (2026-07-23): the grounded synthesis card IS the report.
    expect(screen.getByText(/designs consumer devices/i)).toBeTruthy();
    expect(screen.getByText(/model-led thesis/i)).toBeTruthy();
    expect(screen.getByText(/services margin expansion/i)).toBeTruthy();
    expect(screen.getByText(/hardware cycle softness/i)).toBeTruthy();
    // The real HAR-RV band renders (value shown, not the market-closed note).
    expect(
      screen.getByText(/5\.2% volatility band \(HAR-RV, 20 trading days\)/i),
    ).toBeTruthy();
    expect(screen.queryByText(/n\/a \(market closed\)/i)).toBeNull();
  });

  it("renders the card market-independently when the vol band is null (market closed), with an honest n/a note", () => {
    // Case (a) — operator-approved (2026-07-24): reports are news/fundamentals-driven
    // and MUST be visible market-independently. The HAR-RV vol band is null off-hours,
    // so it is OPTIONAL, not card-blocking. Before the fix this card was HIDDEN.
    useStore.setState({
      specialistReports: [
        report({
          groundingOk: true,
          volBandSigmaPct: null,
          companySummary: "Apple designs consumer devices and services.",
          bullCase: "Services margin expansion drives the upside.",
        }),
      ],
    });
    render(<Reports />);
    // The card now renders (news synthesis is complete).
    expect(screen.getByText(/designs consumer devices/i)).toBeTruthy();
    expect(screen.getByText(/services margin expansion/i)).toBeTruthy();
    // The volatility area is honest about being unavailable off-hours.
    expect(screen.getByText(/n\/a \(market closed\)/i)).toBeTruthy();
    // The old completeness placeholder is gone for this report.
    expect(screen.queryByText(/research in progress/i)).toBeNull();
  });

  it("still hides the card when the news/grounding gate is not met (groundingOk null)", () => {
    // Case (b) — the relaxation drops ONLY the vol-band requirement; the news/grounding
    // gate still holds, so a genuinely-incomplete card (no synthesis yet) stays hidden,
    // even if a vol band happens to be present.
    useStore.setState({
      specialistReports: [
        report({
          groundingOk: null,
          volBandSigmaPct: 5.2,
          bullCase: "Only the bull case is present.",
        }),
      ],
    });
    render(<Reports />);
    expect(screen.getByText(/research in progress/i)).toBeTruthy();
    expect(screen.queryByText(/only the bull case is present/i)).toBeNull();
  });
});

/** The rendered value cell for a Data-coverage row label. */
function coverageValue(label: string): string | null | undefined {
  return screen.getByText(label).nextElementSibling?.textContent;
}

describe("Reports — Data-coverage dead-source honesty (#2587)", () => {
  beforeEach(() => {
    useStore.setState({ roundTable: [], specialistReports: [], specialistStatus: "", specialistMessage: null });
  });

  it("renders '—' (unavailable), not a fake 0, when a source count is null", () => {
    // Engine serializes null counts when the SOURCE failed (Quiver 404 / Reddit 403)
    // — "checked, nothing found" (0) and "could not check" (—) must look different.
    useStore.setState({
      specialistReports: [
        report({ groundingOk: true, politicalTradesCount: null, redditMentions: null }),
      ],
    });
    render(<Reports />);
    expect(coverageValue("Congress trades")).toBe("—");
    expect(coverageValue("Reddit mentions (24h)")).toBe("—");
  });

  it("renders a real checked-and-zero count as 0", () => {
    useStore.setState({
      specialistReports: [
        report({ groundingOk: true, politicalTradesCount: 0, redditMentions: 0 }),
      ],
    });
    render(<Reports />);
    expect(coverageValue("Congress trades")).toBe("0");
    expect(coverageValue("Reddit mentions (24h)")).toBe("0");
  });

  it("renders real counts unchanged", () => {
    useStore.setState({
      specialistReports: [
        report({ groundingOk: true, politicalTradesCount: 2, redditMentions: 7 }),
      ],
    });
    render(<Reports />);
    expect(coverageValue("Congress trades")).toBe("2");
    expect(coverageValue("Reddit mentions (24h)")).toBe("7");
  });
});
