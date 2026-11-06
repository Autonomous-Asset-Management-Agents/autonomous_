/**
 * #5166 (owner decision 05.11.2026) — "Enable strategies" on top of Settings → Trading → Strategies.
 *
 * The switch moved here from the "Paper accounts" card (STW-2f #5125). It still writes only the
 * boolean `strategyProfilesEnabled`, now after a confirmation; the app restart goes through
 * `relaunchApp` (#5142). Below it the strategy cards render only while this session's engine carries
 * the strategy routes; otherwise they stand greyed and inert, and nothing asks the engine.
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { HttpError } from "@/lib/api";

const listStrategies = vi.fn();
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listStrategies: () => listStrategies(),
}));

import { StrategiesGate } from "@/console/desktop/strategyWorkshop/StrategiesGate";

type Shell = Record<string, ReturnType<typeof vi.fn> | boolean>;
let shell: Shell;

function stage({ choice, fleetActive }: { choice?: unknown; fleetActive?: boolean | null }) {
  shell = {
    isDesktop: true,
    getSetupState: vi.fn().mockResolvedValue(choice === undefined ? {} : { strategyProfilesEnabled: choice }),
    saveSetupState: vi.fn().mockResolvedValue(undefined),
    relaunchApp: vi.fn().mockResolvedValue(undefined),
    ...(fleetActive === null || fleetActive === undefined
      ? {}
      : { getFleetSession: vi.fn().mockResolvedValue({ fleetActive }) }),
  };
  (window as unknown as { aaagents: unknown }).aaagents = shell;
}

function mount() {
  return render(
    <StrategiesGate>
      <div data-testid="cards">cards</div>
    </StrategiesGate>,
  );
}

const box = () => screen.getByRole("region", { name: "Strategies" });

async function openDialog(knopf: string | RegExp) {
  fireEvent.click(await within(box()).findByRole("button", { name: knopf }));
  return within(await screen.findByRole("alertdialog"));
}

describe("StrategiesGate (#5166)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listStrategies.mockResolvedValue({ strategien: [], max_strategien: 3 });
  });
  afterEach(() => {
    delete (window as unknown as { aaagents?: unknown }).aaagents;
  });

  describe("off", () => {
    beforeEach(() => stage({ choice: false, fleetActive: false }));

    it("Enable strategies is a .btn btn-primary; the cards stand greyed and inert; the engine is not asked", async () => {
      mount();
      const knopf = await within(box()).findByRole("button", { name: "Enable strategies" });
      expect(knopf.className).toBe("btn btn-primary");
      expect(screen.queryByTestId("cards")).toBeNull();
      const grau = screen.getByTestId("strategies-greyed");
      expect(grau).toHaveAttribute("inert");
      expect(grau).toHaveAttribute("aria-disabled", "true");
      expect(grau.className).toMatch(/\bopacity-40\b/);
      for (const titel of ["Live account strategy", "Paper accounts", "Strategies", "Simulate", "Compare account performance"]) {
        expect(within(grau).getByText(titel, { selector: ".eyebrow" })).toBeInTheDocument();
      }
      expect(within(grau).queryAllByRole("button")).toEqual([]);
      expect(screen.getByText("Enable strategies above to use these cards.")).toBeInTheDocument();
      expect(listStrategies).not.toHaveBeenCalled();
    });

    it("Enable strategies explains the consequences first; Cancel writes nothing", async () => {
      mount();
      const dialog = await openDialog("Enable strategies");
      expect(dialog.getByText("Enable strategies?")).toBeInTheDocument();
      for (const lead of ["Named strategies.", "Up to two more paper accounts", "Memory.", "App restart.", "Turning it off"]) {
        expect(dialog.getByText(lead)).toBeInTheDocument();
      }
      fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
      expect(shell.saveSetupState).not.toHaveBeenCalled();
      expect(shell.relaunchApp).not.toHaveBeenCalled();
    });

    it("Enable, restart later writes the boolean true once, restarts nothing, then asks for the restart", async () => {
      mount();
      const dialog = await openDialog("Enable strategies");
      (shell.getSetupState as ReturnType<typeof vi.fn>).mockResolvedValue({ strategyProfilesEnabled: true });
      fireEvent.click(dialog.getByRole("button", { name: "Enable, restart later" }));
      expect(await within(box()).findByText("Restart the app to apply this change.")).toBeInTheDocument();
      expect(shell.saveSetupState).toHaveBeenCalledTimes(1);
      expect(shell.saveSetupState).toHaveBeenCalledWith({ strategyProfilesEnabled: true });
      expect(shell.relaunchApp).not.toHaveBeenCalled();
      // The engine of this session still has no routes: the cards stay greyed until the restart.
      expect(screen.queryByTestId("cards")).toBeNull();
    });

    it("Enable & restart app writes first, then restarts the app", async () => {
      mount();
      const dialog = await openDialog("Enable strategies");
      fireEvent.click(dialog.getByRole("button", { name: "Enable & restart app" }));
      await waitFor(() => expect(shell.relaunchApp).toHaveBeenCalledTimes(1));
      const save = (shell.saveSetupState as ReturnType<typeof vi.fn>).mock.invocationCallOrder[0];
      const relaunch = (shell.relaunchApp as ReturnType<typeof vi.fn>).mock.invocationCallOrder[0];
      expect(save).toBeLessThan(relaunch);
    });

    it("a failing write shows error, restarts nothing and keeps Enable strategies", async () => {
      (shell.saveSetupState as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("ipc boom"));
      vi.spyOn(console, "warn").mockImplementation(() => {});
      mount();
      const dialog = await openDialog("Enable strategies");
      fireEvent.click(dialog.getByRole("button", { name: "Enable & restart app" }));
      await waitFor(() => expect(box().querySelector('[data-state="error"]')).not.toBeNull());
      expect(shell.relaunchApp).not.toHaveBeenCalled();
      expect(within(box()).getByRole("button", { name: "Enable strategies" })).toBeInTheDocument();
      expect(document.body.textContent).not.toMatch(/boom/);
    });
  });

  describe("on", () => {
    beforeEach(() => stage({ choice: true, fleetActive: true }));

    it("shows the state as a StatusDot and Disable… as a link; the cards render", async () => {
      mount();
      expect(await screen.findByTestId("cards")).toBeInTheDocument();
      expect(within(box()).getByText("Strategies enabled")).toBeInTheDocument();
      expect(within(box()).getByRole("button", { name: "Disable…" }).className).toBe("btn-link");
      expect(within(box()).queryByRole("button", { name: "Enable strategies" })).toBeNull();
      expect(screen.queryByTestId("strategies-greyed")).toBeNull();
    });

    it("Disable… asks first; Disable, restart later writes false and keeps the cards until the restart", async () => {
      mount();
      await screen.findByTestId("cards");
      const dialog = await openDialog("Disable…");
      expect(dialog.getByText("Disable strategies?")).toBeInTheDocument();
      (shell.getSetupState as ReturnType<typeof vi.fn>).mockResolvedValue({ strategyProfilesEnabled: false });
      fireEvent.click(dialog.getByRole("button", { name: "Disable, restart later" }));
      expect(await within(box()).findByText("Restart the app to apply this change.")).toBeInTheDocument();
      expect(shell.saveSetupState).toHaveBeenCalledWith({ strategyProfilesEnabled: false });
      expect(screen.getByTestId("cards")).toBeInTheDocument();
    });
  });

  it("How it works opens the explanation with Close only and writes nothing", async () => {
    stage({ choice: false, fleetActive: false });
    mount();
    const dialog = await openDialog("How it works");
    expect(dialog.getByText("Strategies")).toBeInTheDocument();
    expect(dialog.getAllByRole("button").map((b) => b.textContent)).toEqual(["Close"]);
    fireEvent.click(dialog.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(shell.saveSetupState).not.toHaveBeenCalled();
  });

  it("restart-required: Restart app asks first, then restarts the app", async () => {
    stage({ choice: true, fleetActive: false });
    mount();
    fireEvent.click(await within(box()).findByRole("button", { name: "Restart app" }));
    const dialog = within(await screen.findByRole("alertdialog"));
    expect(dialog.getByText("The app and all engines stop and start again. Continue?")).toBeInTheDocument();
    fireEvent.click(dialog.getByRole("button", { name: "Restart" }));
    await waitFor(() => expect(shell.relaunchApp).toHaveBeenCalledTimes(1));
  });

  it("choice and session agree → no restart notice", async () => {
    stage({ choice: true, fleetActive: true });
    mount();
    await screen.findByTestId("cards");
    expect(screen.queryByText("Restart the app to apply this change.")).toBeNull();
  });

  describe("without a fleet session (browser, older shell) the engine decides", () => {
    it("404 → off, greyed", async () => {
      stage({});
      listStrategies.mockRejectedValue(new HttpError(404, "Not Found", "/api/strategies"));
      mount();
      expect(await screen.findByTestId("strategies-greyed")).toBeInTheDocument();
      expect(screen.queryByTestId("cards")).toBeNull();
    });

    it("the routes answer → on, the cards render", async () => {
      stage({});
      mount();
      expect(await screen.findByTestId("cards")).toBeInTheDocument();
    });

    it("engine down → the cards render and show engine-offline themselves", async () => {
      stage({});
      listStrategies.mockRejectedValue(new TypeError("Failed to fetch"));
      mount();
      expect(await screen.findByTestId("cards")).toBeInTheDocument();
    });
  });

  it("the dialog has no coloured text, border or icon (design guide §7 modal standard)", async () => {
    stage({ choice: false, fleetActive: false });
    mount();
    await openDialog("Enable strategies");
    const html = screen.getByRole("alertdialog").innerHTML;
    expect(html).not.toMatch(/text-\[#|border-\[#|text-(red|green|amber|yellow)-|<svg/);
    for (const b of within(screen.getByRole("alertdialog")).getAllByRole("button")) {
      expect(b.classList.contains("btn")).toBe(true);
    }
  });
});
