import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Decisions } from "../console/desktop/pages/Decisions";
import { useStore } from "../console/store/useStore";
import type { ConsoleRoundTableDecision } from "../console/live/roundTable";

// Decisions mounts the round-table poll; stub the fetch so the page renders from
// seeded store state deterministically.
vi.mock("../lib/api", () => ({ fetchRoundTableDecisions: vi.fn().mockResolvedValue(null) }));

const dec = (over: Partial<ConsoleRoundTableDecision> = {}): ConsoleRoundTableDecision => ({
  symbol: "AAPL", action: "BUY", passed: true, conviction: 0.5, sector: "",
  votesFor: 5, votesAbstain: 2, votesAgainst: 1, vetoReason: "", ts: "14:52", senators: [], ...over,
});

describe("console Decisions page", () => {
  beforeEach(() => {
    useStore.setState({ roundTable: [] });
  });

  it("shows an honest empty state with no decisions", () => {
    render(<Decisions />);
    expect(screen.getByText(/no decisions yet/i)).toBeTruthy();
  });

  it("lists decisions collapsed, with an honest 'Autonom' source marker", () => {
    useStore.setState({ roundTable: [dec({ symbol: "NVDA", action: "BUY" }), dec({ symbol: "TSLA", action: "SELL" })] });
    render(<Decisions />);
    expect(screen.getByText("NVDA")).toBeTruthy();
    expect(screen.getByText("TSLA")).toBeTruthy();
    expect(screen.getByText("2 decisions")).toBeTruthy();
    expect(screen.getAllByText(/autonom/i).length).toBe(2);
    expect(screen.queryByText(/hitl/i)).toBeNull();
  });

  it("filters the list by action", () => {
    useStore.setState({ roundTable: [dec({ symbol: "NVDA", action: "BUY" }), dec({ symbol: "TSLA", action: "SELL" })] });
    render(<Decisions />);
    fireEvent.click(screen.getByRole("button", { name: "SELL" }));
    expect(screen.getByText("TSLA")).toBeTruthy();
    expect(screen.queryByText("NVDA")).toBeNull();
  });

  it("the agent name is a link that reveals its role, tasks and criteria", () => {
    useStore.setState({
      roundTable: [dec({ symbol: "MSFT", senators: [
        { name: "DrawdownGuardAgent", vote: "BULL", conviction: 0.92, reasoning: "DrawdownGuard: H=377 L=370 → score=0.92", hardVeto: false },
      ] })],
    });
    render(<Decisions />);
    fireEvent.click(screen.getByText("MSFT")); // expand the decision card
    expect(screen.getByText(/score=0\.92/)).toBeTruthy(); // the agent's raw read is shown
    expect(screen.queryByText(/risk sentinel/i)).toBeNull(); // profile hidden until the link is clicked

    fireEvent.click(screen.getByRole("button", { name: "Drawdown Guard" })); // the agent-name link
    expect(screen.getByText(/^role$/i)).toBeTruthy();
    expect(screen.getByText(/risk sentinel/i)).toBeTruthy(); // role
    expect(screen.getByText(/from the recent high/i)).toBeTruthy(); // tasks
    expect(screen.getByText(/shallow drawdown/i)).toBeTruthy(); // criteria
  });

  it("shows the HITL marker only when a decision carries source=hitl (future-ready)", () => {
    useStore.setState({ roundTable: [dec({ symbol: "MSFT", action: "BUY", source: "hitl" })] });
    render(<Decisions />);
    expect(screen.getByText(/hitl/i)).toBeTruthy();
  });

  // RQ-1 (#1516): the final execution-gate outcome (Iron Dome / risk / kill-switch)
  // — makes "approved verdict != actually traded" visible next to the action.
  it("shows a green Executed badge when the order reached the broker", () => {
    useStore.setState({
      roundTable: [dec({ symbol: "NVDA", action: "BUY", executionOutcome: "executed" })],
    });
    render(<Decisions />);
    expect(screen.getByText("Executed")).toBeTruthy();
  });

  it("shows a Blocked badge when the Iron Dome (ComplianceGuardian) blocked the order", () => {
    useStore.setState({
      roundTable: [dec({ symbol: "NVDA", action: "BUY", executionOutcome: "blocked:order_value" })],
    });
    render(<Decisions />);
    expect(screen.getByText(/blocked · order-value/i)).toBeTruthy();
  });

  it("renders no execution badge for a decision without an outcome (e.g. HOLD)", () => {
    useStore.setState({ roundTable: [dec({ symbol: "AAPL", action: "HOLD" })] });
    render(<Decisions />);
    expect(screen.queryByText(/executed|blocked|halted|pending/i)).toBeNull();
  });

  // RQ-1: the verdict/action is a WEIGHTED vote — not the raw head-count in the vote
  // bar — so a high-weight agent can carry BUY/SELL despite a HOLD majority. Explain it
  // under the heading so the action never reads as inconsistent with the shown votes.
  it("shows the weighted-consensus explainer under the heading (always, even when empty)", () => {
    const { container } = render(<Decisions />);
    expect(screen.getByText(/how a verdict is reached/i)).toBeTruthy();
    expect(container.textContent).toMatch(/risk-weighted vote of the agents/i);
  });
});
