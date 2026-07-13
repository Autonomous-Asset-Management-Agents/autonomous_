// SIM-1 T1 (#1484): the Console Simulation page + its nav registration.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const runSimulation = vi.fn(() => Promise.resolve({ status: "success" }));
const getSimulationResult = vi.fn(() => Promise.resolve({ status: "idle" }));
vi.mock("@/lib/api", () => ({
  runSimulation: (...a: unknown[]) => runSimulation(...(a as [])),
  getSimulationResult: (...a: unknown[]) => getSimulationResult(...(a as [])),
}));

import { Simulation } from "@/console/desktop/pages/Simulation";
import { navItems, validPages } from "@/console/desktop/nav";

describe("SIM-1 T1: Console Simulation page", () => {
  beforeEach(() => {
    runSimulation.mockClear();
    getSimulationResult.mockClear();
  });

  it("renders the date-range + capital + universe inputs and a run button", () => {
    render(<Simulation />);
    expect(screen.getByLabelText(/start date/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/end date/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/capital/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/universe/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run/i })).toBeInTheDocument();
  });

  it("shows the mandatory simulated / not-advice / past-performance / AI framing", () => {
    render(<Simulation />);
    const txt = document.body.textContent || "";
    expect(txt).toMatch(/simulated/i);
    expect(txt).toMatch(/not investment advice/i);
    expect(txt).toMatch(/past performance/i);
    expect(txt).toMatch(/\bAI\b/);
  });

  it("starts a backtest over the chosen range + capital + universe", async () => {
    render(<Simulation />);
    fireEvent.click(screen.getByRole("button", { name: /run/i }));
    await waitFor(() => expect(runSimulation).toHaveBeenCalledTimes(1));
    expect(runSimulation.mock.calls[0][0]).toMatchObject({
      start_date: expect.any(String),
      end_date: expect.any(String),
      initial_capital: expect.any(Number),
      symbol_sample_mode: expect.any(String),
    });
  });
});

describe("SIM: the Simulation page is entitlement-gated in the Console nav", () => {
  const dir = path.dirname(fileURLToPath(import.meta.url));
  const read = (p: string) => readFileSync(path.join(dir, "..", p), "utf8");

  it("is HIDDEN from the sidebar + routes by default (simulation disabled)", () => {
    expect(navItems(false).some((i) => i.id === "simulation")).toBe(false);
    expect(validPages(false).has("simulation")).toBe(false);
  });

  it("is EXPOSED only when the entitlement enables simulation", () => {
    expect(navItems(true).some((i) => i.id === "simulation")).toBe(true);
    expect(validPages(true).has("simulation")).toBe(true);
  });

  it("keeps every OTHER nav item + route regardless of the flag", () => {
    for (const flag of [false, true]) {
      const ids = navItems(flag).map((i) => i.id);
      expect(ids).toEqual(
        expect.arrayContaining([
          "overview",
          "decisions",
          "positions",
          "reports",
          "audit",
          "settings",
        ]),
      );
      expect(validPages(flag).has("overview")).toBe(true);
      expect(validPages(flag).has("chat")).toBe(true);
    }
  });

  it("retains the ConsolePage type + label for simulation (component kept, just unlinked)", () => {
    expect(read("console/store/useStore.ts")).toMatch(/"simulation"/);
    expect(read("console/desktop/consoleCommands.ts")).toMatch(/simulation:\s*"Simulation"/);
  });
});
