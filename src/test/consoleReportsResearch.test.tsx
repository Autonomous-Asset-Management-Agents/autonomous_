import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { Reports } from "../console/desktop/pages/Reports";
import { useStore } from "../console/store/useStore";
import type { SpecialistReport } from "../console/types";

// T3 (#1452): the specialist card must render the carried-but-unrendered RPAR
// fields (company / bull / bear / news), each only when present. The page mounts
// two polls; stub both so the test renders from seeded store state.
vi.mock("../lib/api", () => ({
  fetchRoundTableDecisions: vi.fn().mockResolvedValue(null),
  fetchSpecialistReports: vi.fn().mockResolvedValue({ status: "ok", reports: [] }),
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
  ...over,
});

describe("Reports — specialist research sections (T3 / RPAR)", () => {
  beforeEach(() => {
    useStore.setState({ roundTable: [], specialistReports: [], specialistStatus: "", specialistMessage: null });
  });

  it("renders Company / Bull case / Bear case / News when the report carries them", () => {
    useStore.setState({
      specialistReports: [
        report({
          companySummary: "Apple designs consumer devices and services.",
          bullCase: "Services margin expansion drives the upside.",
          bearCase: "Hardware cycle softness is the key risk.",
          newsSummary: "Vision Pro reviews are mixed.",
        }),
      ],
    });
    render(<Reports />);
    expect(screen.getByText("Company")).toBeTruthy();
    expect(screen.getByText(/designs consumer devices/i)).toBeTruthy();
    expect(screen.getByText("Bull case")).toBeTruthy();
    expect(screen.getByText(/services margin expansion/i)).toBeTruthy();
    expect(screen.getByText("Bear case")).toBeTruthy();
    expect(screen.getByText(/hardware cycle softness/i)).toBeTruthy();
    expect(screen.getByText("News")).toBeTruthy();
  });

  it("omits a section whose field is null (no empty placeholder)", () => {
    useStore.setState({ specialistReports: [report({ bullCase: "Only the bull case is present." })] });
    render(<Reports />);
    expect(screen.getByText("Bull case")).toBeTruthy();
    expect(screen.queryByText("Bear case")).toBeNull();
    expect(screen.queryByText("Company")).toBeNull();
    expect(screen.queryByText("News")).toBeNull();
  });
});
