import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentProfile } from "@/console/desktop/AgentProfile";
import type { AgentSetting } from "@/lib/api";

const base: AgentSetting = {
  name: "DrawdownGuardAgent", enabled: true, default_enabled: true, weight: 0, default_weight: 0,
  min_weight: 0, max_weight: 0, role: "veto_guard", role_title: "Drawdown Analyst",
  enable_flag: "DRAWDOWN_GUARD_AGENT_ENABLED", weight_key: "",
};

const profile = {
  type: "Deterministic", repeatable: "Yes: a formula on daily closes.", flow_role: "Veto on new buys (not in the mean)",
  responsibility: "Measures how far the price is below its recent high.", methods: "Drawdown over the last 30 trading days.",
  literature: [], data: "Daily bars.", default_state: "On, veto active", edition: "All editions",
  missing_data: "Abstains on invalid prices.", kpi: "Leave-one-out change in consensus Sharpe.", measured: ["Not measured."],
  evidence: ["ai_trading_bot/core/round_table/agenten/drawdown_guard.py:164-191"],
  settings: [{ key: "DRAWDOWN_GUARD_VETO_ENABLED", default: "True", ref: "ai_trading_bot/settings.py:1608", effect: "Off: never vetoes." }],
};

describe("AgentProfile (#4874)", () => {
  it("renders the served profile, collapsed, with settings, measurement and evidence", () => {
    render(<AgentProfile agent={{ ...base, profile }} />);
    const box = screen.getByTestId("agent-profile-DrawdownGuardAgent");
    expect(box.hasAttribute("open")).toBe(false);
    fireEvent.click(screen.getByText("Profile"));
    const text = box.textContent ?? "";
    expect(text).toContain("Deterministic");
    expect(text).toContain("Veto on new buys");
    expect(text).toContain("DRAWDOWN_GUARD_VETO_ENABLED = True: Off: never vetoes.");
    expect(text).toContain("Not measured.");
    expect(text).toContain("drawdown_guard.py:164-191");
    expect(text).toContain("DrawdownGuardAgent");
  });

  it("renders nothing for a row without a profile", () => {
    const { container } = render(<AgentProfile agent={{ ...base, name: "SkewSizeTilt", profile: {} }} />);
    expect(container.innerHTML).toBe("");
  });
});
