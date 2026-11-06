/** STW-2c (#4953) — "Strategy for this account" per paper slot (UX-Konzept #4628, Ablauf 2, T14).
 *  STW-2d (#5117): the preview runs on selection and stands in the slot; the dialog carries no diff. */
import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { waehle, wert, optionen } from "./listboxHelfer";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useStore } from "@/console/store/useStore";
import type { PaperSlot } from "@/lib/desktopBridge";

const assignPaperStrategy = vi.fn(), getTradingSettings = vi.fn(), fetchHealth = vi.fn();
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  assignPaperStrategy: (...a: unknown[]) => assignPaperStrategy(...a),
  getTradingSettings: () => getTradingSettings(),
  fetchHealth: () => fetchHealth(),
}));

const restartEngine = vi.fn();
vi.mock("@/lib/desktopBridge", async (orig) => ({
  ...(await orig<typeof import("@/lib/desktopBridge")>()),
  restartEngine: (...a: unknown[]) => restartEngine(...a),
}));

import { PaperSlotStrategy } from "@/console/desktop/strategyWorkshop/PaperSlotStrategy";
import { PAPER_ASSIGN_ACK } from "@/console/desktop/strategyWorkshop/acknowledgments";

const SLOT: PaperSlot = { slot: "paper2", connected: true, accountMasked: "••••2222", engineStatus: "running" };
const STRATEGIES = [
  { name: "B", werte: { STOP_LOSS_PCT: "5.0" } },
  { name: "A", werte: { STOP_LOSS_PCT: "6.0" } },
];
const SETTINGS = {
  agents: [], implied_vol_forecast_enabled: true, guardrails: {},
  settings: { STOP_LOSS_PCT: "9.9", SIGNAL_BUY_THRESHOLD: "0.99" },
  meta: [
    { key: "STOP_LOSS_PCT", kind: "float", default: "7.0" },
    { key: "SIGNAL_BUY_THRESHOLD", kind: "float", default: "0.73" },
  ],
};
const APPLIED = { STOP_LOSS_PCT: "5.0", SIGNAL_BUY_THRESHOLD: "0.73" };
const LABEL = "Strategy for this account";
const PREVIEW = "Changes on confirm";
const DISCONNECT = "Disconnect";

let shell: {
  isDesktop: boolean;
  getPaperSlotSetup?: ReturnType<typeof vi.fn>;
  savePaperSlotSetup?: ReturnType<typeof vi.fn>;
  restartPaperSlotEngine?: ReturnType<typeof vi.fn>;
};

function mount(over: Partial<Parameters<typeof PaperSlotStrategy>[0]> = {}) {
  const onAssigned = vi.fn();
  render(
    <PaperSlotStrategy
      slot={SLOT}
      strategies={STRATEGIES}
      assigned={null}
      takenElsewhere={[]}
      disconnect={<button type="button" className="btn-link">{DISCONNECT}</button>}
      onAssigned={onAssigned}
      {...over}
    />,
  );
  return onAssigned;
}

const review = () => screen.getByRole("button", { name: "Review changes" }) as HTMLButtonElement;

async function choose(name = "B") {
  waehle(screen.getByLabelText(LABEL), name);
  await waitFor(() => expect(review().disabled).toBe(false));
}

async function openReview(name = "B") {
  await choose(name);
  fireEvent.click(review());
  return screen.findByRole("dialog", { name: "Assign strategy to this paper account?" });
}

const confirm = () => fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("PaperSlotStrategy (#4953, #5117)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    act(() => useStore.setState({ paperTrading: true }));
    getTradingSettings.mockResolvedValue(SETTINGS);
    assignPaperStrategy.mockResolvedValue({ success: true, strategie: "B", applied: APPLIED, changes: [{ key: "STOP_LOSS_PCT", from: "6.5", to: "5.0" }], detail: "" });
    fetchHealth.mockResolvedValue({ status: "healthy", paper_trading: false });
    restartEngine.mockResolvedValue(undefined);
    shell = {
      isDesktop: true,
      getPaperSlotSetup: vi.fn().mockResolvedValue({ STOP_LOSS_PCT: "6.5", USER_NAME: "not a setting" }),
      savePaperSlotSetup: vi.fn().mockResolvedValue({ ok: true }),
      restartPaperSlotEngine: vi.fn().mockResolvedValue({ ok: true }),
    };
    (window as unknown as { aaagents: unknown }).aaagents = shell;
  });
  afterEach(() => {
    delete (window as unknown as { aaagents?: unknown }).aaagents;
  });

  describe("visibility", () => {
    it.each(["getPaperSlotSetup", "savePaperSlotSetup", "restartPaperSlotEngine"] as const)("no selection while the shell lacks %s, Disconnect stays", (member) => {
      delete shell[member];
      mount();
      expect(screen.queryByLabelText(LABEL)).toBeNull();
      expect(screen.queryByRole("button", { name: "Review changes" })).toBeNull();
      expect(screen.getByRole("button", { name: DISCONNECT })).toBeInTheDocument();
    });

    it("no selection in the browser, Disconnect stays", () => {
      delete (window as unknown as { aaagents?: unknown }).aaagents;
      mount();
      expect(screen.queryByLabelText(LABEL)).toBeNull();
      expect(screen.getByRole("button", { name: DISCONNECT })).toBeInTheDocument();
    });

    it("no selection without strategies, Disconnect stays", () => {
      mount({ strategies: [] });
      expect(screen.queryByLabelText(LABEL)).toBeNull();
      expect(screen.getByRole("button", { name: DISCONNECT })).toBeInTheDocument();
    });

    it("nothing on a slot that is not connected", () => {
      mount({ slot: { ...SLOT, connected: false } });
      expect(screen.queryByLabelText(LABEL)).toBeNull();
      expect(screen.queryByRole("button", { name: DISCONNECT })).toBeNull();
    });

    it("visible for a connected slot whose engine is stopped", () => {
      mount({ slot: { ...SLOT, engineStatus: "stopped" } });
      expect(screen.getByLabelText(LABEL)).toBeInTheDocument();
    });
  });

  it("no preselection: a placeholder instead of an empty option, strategies in the order of the engine", () => {
    mount({ assigned: "A" });
    const feld = screen.getByLabelText(LABEL);
    expect(wert(feld)).toBe("");
    expect(feld.textContent).toContain("Select a strategy");
    expect(optionen(feld)).toEqual(["B", "A"]);
    expect(review().disabled).toBe(true);
    expect(review().className).toBe("btn btn-primary");
  });

  it("takenElsewhere: listed, aria-disabled, not choosable", () => {
    mount({ takenElsewhere: ["A"] });
    const feld = screen.getByLabelText(LABEL);
    fireEvent.click(feld);
    const a = screen.getByRole("option", { name: "A" });
    expect(a.getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("option", { name: "B" }).getAttribute("aria-disabled")).not.toBe("true");
    fireEvent.click(a);
    expect(wert(feld)).toBe("");
  });

  it("shows Assigned only when something is assigned", () => {
    mount({ assigned: "A" });
    expect(screen.getByText("Assigned: A")).toBeInTheDocument();
  });

  it("the preview appears on selection, before any dialog; from comes from the slot's setup.json", async () => {
    mount();
    await choose("B");
    const preview = screen.getByRole("group", { name: PREVIEW });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(shell.getPaperSlotSetup).toHaveBeenCalledWith("paper2");
    expect(preview.textContent).toContain("6.5");
    expect(preview.textContent).toContain("5.0");
    expect(preview.textContent).not.toContain("9.9");
    expect(preview.textContent).not.toContain("0.99");
    // SIGNAL_BUY_THRESHOLD: not in setup.json, not in the strategy → default on both sides, no row.
    expect(preview.textContent).not.toContain("0.73");
    expect(within(preview).getByText("Only this account's setup.json is written. The live account is not touched.")).toBeInTheDocument();
  });

  it("the dialog shows no diff, the preview stays the one place", async () => {
    mount();
    const dialog = await openReview();
    expect(dialog.textContent).not.toContain("6.5");
    expect(dialog.textContent).not.toContain("→");
  });

  it("the dialog shows PAPER_ASSIGN_ACK as text, the bytes record sends (#5125)", async () => {
    mount();
    const dialog = await openReview();
    expect(within(dialog).getByText(PAPER_ASSIGN_ACK).textContent).toBe(PAPER_ASSIGN_ACK);
    expect(within(dialog).queryByRole("checkbox")).toBeNull();
  });

  it("a superseded answer is discarded", async () => {
    const slow = deferred<Record<string, string>>();
    shell.getPaperSlotSetup!.mockReturnValueOnce(slow.promise).mockResolvedValue({ STOP_LOSS_PCT: "6.5" });
    mount();
    waehle(screen.getByLabelText(LABEL), "B");
    await choose("A");
    expect(screen.getByRole("group", { name: PREVIEW }).textContent).toContain("6.0");
    await act(async () => {
      slow.resolve({ STOP_LOSS_PCT: "1.1" });
      await slow.promise;
    });
    const preview = screen.getByRole("group", { name: PREVIEW });
    expect(preview.textContent).toContain("6.0");
    expect(preview.textContent).not.toContain("1.1");
  });

  it("preview reads setup.json like the engine boot does (setupEnv): numbers as text, blank counts as unset", async () => {
    shell.getPaperSlotSetup!.mockResolvedValue({ STOP_LOSS_PCT: 6.5, SIGNAL_BUY_THRESHOLD: "  " });
    mount();
    await openReview();
    confirm();
    await waitFor(() => expect(assignPaperStrategy).toHaveBeenCalledTimes(1));
    expect(assignPaperStrategy.mock.calls[0][2]).toEqual({ STOP_LOSS_PCT: "6.5", SIGNAL_BUY_THRESHOLD: "0.73" });
  });

  it("no value change: no preview, the dialog shows T42", async () => {
    shell.getPaperSlotSetup!.mockResolvedValue({ STOP_LOSS_PCT: "5.0" });
    mount();
    await choose("B");
    expect(screen.queryByRole("group", { name: PREVIEW })).toBeNull();
    fireEvent.click(review());
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("No setting values change. The assignment is still recorded in the audit chain.")).toBeInTheDocument();
  });

  it.each([
    ["null from the bridge", () => shell.getPaperSlotSetup!.mockResolvedValue(null)],
    ["a failing bridge", () => shell.getPaperSlotSetup!.mockRejectedValue(new Error("ipc boom"))],
    ["a failing engine", () => getTradingSettings.mockRejectedValue(new Error("http boom"))],
  ])("%s shows the error text without a code, Review stays locked", async (_name, arrange) => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    arrange();
    mount();
    waehle(screen.getByLabelText(LABEL), "B");
    expect(await screen.findByText("Could not load the data.")).toBeInTheDocument();
    expect(review().disabled).toBe(true);
    expect(screen.queryByRole("group", { name: PREVIEW })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.textContent).not.toMatch(/boom/);
    warn.mockRestore();
  });

  it("record sends konto, the full basis of the slot and PAPER_ASSIGN_ACK", async () => {
    mount();
    await openReview();
    confirm();
    await waitFor(() => expect(assignPaperStrategy).toHaveBeenCalledTimes(1));
    const [name, konto, basis, ack, nonce] = assignPaperStrategy.mock.calls[0];
    expect(name).toBe("B");
    expect(konto).toBe("paper2");
    expect(basis).toEqual({ STOP_LOSS_PCT: "6.5", SIGNAL_BUY_THRESHOLD: "0.73" });
    expect(ack).toBe(PAPER_ASSIGN_ACK);
    expect(typeof nonce).toBe("string");
  });

  it("persist writes applied in full, not the changes, for this slot only; then restarts this slot", async () => {
    const onAssigned = mount();
    await openReview();
    confirm();
    await waitFor(() => expect(onAssigned).toHaveBeenCalledTimes(1));
    expect(shell.savePaperSlotSetup).toHaveBeenCalledTimes(1);
    expect(shell.savePaperSlotSetup).toHaveBeenCalledWith("paper2", APPLIED);
    expect(shell.restartPaperSlotEngine).toHaveBeenCalledTimes(1);
    expect(shell.restartPaperSlotEngine).toHaveBeenCalledWith("paper2");
    expect(restartEngine).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByRole("group", { name: PREVIEW })).toBeNull();
  });

  it("the live profile running live changes nothing: no checkbox, no live restart, no goLive", async () => {
    act(() => useStore.setState({ paperTrading: false }));
    const onAssigned = mount();
    await openReview();
    expect(screen.queryByRole("checkbox")).toBeNull();
    confirm();
    await waitFor(() => expect(onAssigned).toHaveBeenCalled());
    expect(restartEngine).not.toHaveBeenCalled();
    expect(fetchHealth).not.toHaveBeenCalled();
  });

  it("a failed record saves nothing and restarts nothing", async () => {
    assignPaperStrategy.mockRejectedValue(new Error("chain not writable"));
    const onAssigned = mount();
    await openReview();
    confirm();
    await screen.findByRole("alert");
    expect(shell.savePaperSlotSetup).not.toHaveBeenCalled();
    expect(shell.restartPaperSlotEngine).not.toHaveBeenCalled();
    expect(onAssigned).not.toHaveBeenCalled();
  });

  it("ok false on save shows the error text, no restart", async () => {
    shell.savePaperSlotSetup!.mockResolvedValue({ ok: false });
    const onAssigned = mount();
    await openReview();
    confirm();
    expect((await screen.findByRole("alert")).textContent).toBe("The action did not complete.");
    expect(shell.restartPaperSlotEngine).not.toHaveBeenCalled();
    expect(onAssigned).not.toHaveBeenCalled();
  });

  it("ok false on restart shows the error text", async () => {
    shell.restartPaperSlotEngine!.mockResolvedValue({ ok: false });
    const onAssigned = mount();
    await openReview();
    confirm();
    expect((await screen.findByRole("alert")).textContent).toBe("The action did not complete.");
    expect(onAssigned).not.toHaveBeenCalled();
  });
});
