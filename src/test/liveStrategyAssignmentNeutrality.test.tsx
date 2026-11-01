/**
 * STW-4 (#4630) — neutrality guard for the "Live account strategy" card, after the
 * pattern of advancedTradingNeutrality.test.tsx with the word list of the UX concept
 * (docs/4628-ux-konzept-strategie-werkstatt/ux_konzept.md, "Neutrale Wortliste").
 * Checked is what the user reads: the rendered card AND the open modal.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { useStore } from "@/console/store/useStore";

vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return { ...actual, isDesktop: () => true, restartEngine: vi.fn(), saveSetupState: vi.fn() };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listStrategies: vi.fn().mockResolvedValue({
      strategien: [
        { name: "A", werte: { STOP_LOSS_PCT: "6.0" } },
        { name: "B", werte: { STOP_LOSS_PCT: "5.0" } },
      ],
      max_strategien: 3,
    }),
    getHitlPolicy: vi.fn().mockResolvedValue({ HITL_ENABLED: true }),
    getTradingSettings: vi.fn().mockResolvedValue({
      agents: [],
      implied_vol_forecast_enabled: true,
      guardrails: {},
      settings: { STOP_LOSS_PCT: "7.0" },
      meta: [{ key: "STOP_LOSS_PCT", kind: "float", default: "7.0" }],
    }),
    assignLiveStrategy: vi.fn(),
  };
});

import { LiveStrategyAssignment } from "@/console/desktop/strategyWorkshop/LiveStrategyAssignment";

const BANNED = [
  /\bbest\b/i,
  /recommend/i,
  /empfohlen/i,
  /winner/i,
  /\btop\b/i,
  /top-ranked/i,
  /\brank/i,
  /outperform/i,
  /\bsafe(r|st)?\b/i,
  /risky/i,
  /guarantee/i,
  /aggressive/i,
  /conservative/i,
  /profile/i,
  /sharpe/i,
];

describe("LiveStrategyAssignment neutrality (STW-4 #4630)", () => {
  it("N1: no ranking or evaluative wording in the card or the open modal", async () => {
    act(() => useStore.setState({ paperTrading: false }));
    const { container } = render(<LiveStrategyAssignment />);
    await screen.findByText("Live account strategy");
    fireEvent.change(screen.getByLabelText("Strategy for the live account"), {
      target: { value: "B" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
    await screen.findByRole("dialog");

    const text = `${container.textContent ?? ""} ${document.body.textContent ?? ""}`;
    for (const re of BANNED) expect(text, String(re)).not.toMatch(re);
    // No numbered order or position marker in front of a strategy name.
    expect(text).not.toMatch(/#\s*1|\b1st\b|No\.\s*1/i);
  });

  it("N2: reachable only from Settings — no comparison view or other page imports it, no link inside", async () => {
    const sources = import.meta.glob("../console/**/*.tsx", {
      query: "?raw",
      import: "default",
      eager: true,
    }) as Record<string, string>;
    const importers = Object.entries(sources)
      .filter(([file, src]) => !file.endsWith("LiveStrategyAssignment.tsx") && /LiveStrategyAssignment/.test(src))
      .map(([file]) => file);
    expect(importers).toEqual(["../console/desktop/pages/Settings.tsx"]);

    const own = sources["../console/desktop/strategyWorkshop/LiveStrategyAssignment.tsx"];
    expect(own).not.toMatch(/<a\s|href=|navigate\(/);
  });
});
