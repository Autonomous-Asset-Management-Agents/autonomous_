// #3071 — selectable performance baseline date (plan-approved).
// Settings → Trading row writes the engine-persisted baseline; the Overview KPI cards
// label honestly: "since inception" only when NO baseline is chosen, else "since <date>". Adapter carries the new response fields through.
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { adaptEquity } from "@/console/live/equity";
import { baselineHint } from "@/console/live/baselineLabel";
import { PerformanceBaselineCard } from "@/console/desktop/PerformanceBaselineCard";
import * as api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getApiBase: () => "http://localhost:8001",
  getPerformanceBaseline: vi.fn().mockResolvedValue({ baseline_date: "2026-08-10" }),
  setPerformanceBaseline: vi
    .fn()
    .mockImplementation(async (d: string | null) => ({ baseline_date: d })),
}));

describe("baselineHint (#3071) — honest labelling", () => {
  it("no baseline → 'since inception'", () => {
    expect(baselineHint(null)).toBe("since inception");
    expect(baselineHint(undefined)).toBe("since inception");
  });
  it("baseline set → 'since <date>' (English, no marker) — never 'since inception'", () => {
    expect(baselineHint("2026-08-10")).toBe("since Aug 10, 2026");
  });
});

describe("adaptEquity (#3071) — baseline fields survive the adapter", () => {
  it("carries baseline_date + inception_date through (null when absent)", () => {
    const v = adaptEquity({
      points: [
        { date: "2026-08-10", equity: 100 },
        { date: "2026-08-11", equity: 101 },
      ],
      spy_points: [],
      baseline_date: "2026-08-10",
      inception_date: "2026-07-24",
    });
    expect(v.baselineDate).toBe("2026-08-10");
    expect(v.inceptionDate).toBe("2026-07-24");
    const none = adaptEquity({ points: [], spy_points: [] });
    expect(none.baselineDate).toBeNull();
    expect(none.inceptionDate).toBeNull();
  });
});

describe("PerformanceBaselineCard (#3071) — Settings → Trading row", () => {
  it("loads and shows the persisted baseline", async () => {
    render(<PerformanceBaselineCard />);
    await waitFor(() => {
      expect(
        (screen.getByTestId("baseline-input") as HTMLInputElement).value,
      ).toBe("2026-08-10");
    });
  });

  it("saves a typed date via the engine API", async () => {
    render(<PerformanceBaselineCard />);
    const input = (await screen.findByTestId("baseline-input")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "2026-08-12" } });
    fireEvent.click(screen.getByTestId("baseline-save"));
    await waitFor(() => {
      expect(api.setPerformanceBaseline).toHaveBeenCalledWith("2026-08-12");
    });
  });

  it("clears back to true inception (null)", async () => {
    render(<PerformanceBaselineCard />);
    await screen.findByTestId("baseline-input");
    fireEvent.click(screen.getByTestId("baseline-clear"));
    await waitFor(() => {
      expect(api.setPerformanceBaseline).toHaveBeenCalledWith(null);
    });
  });
});
