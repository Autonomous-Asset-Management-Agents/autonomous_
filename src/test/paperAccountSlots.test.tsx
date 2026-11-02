/** STW-2b (#4934) — "Paper accounts" (UX-Konzept #4628, Ablauf 2, console cut). */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { waehle } from "./listboxHelfer";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { HttpError } from "@/lib/api";
import type { PaperSlot } from "@/lib/desktopBridge";

const listStrategies = vi.fn(), listStrategyAssignments = vi.fn(), assignPaperStrategy = vi.fn(), getTradingSettings = vi.fn();
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listStrategies: () => listStrategies(),
  listStrategyAssignments: () => listStrategyAssignments(),
  assignPaperStrategy: (...a: unknown[]) => assignPaperStrategy(...a),
  getTradingSettings: () => getTradingSettings(),
}));

import { PaperAccountSlots } from "@/console/desktop/strategyWorkshop/PaperAccountSlots";

const EMPTY: PaperSlot[] = [
  { slot: "paper1", connected: false, accountMasked: null, engineStatus: null },
  { slot: "paper2", connected: false, accountMasked: null, engineStatus: null },
  { slot: "paper3", connected: false, accountMasked: null, engineStatus: null },
];
const withSlot = (i: number, s: Partial<PaperSlot>): PaperSlot[] => EMPTY.map((e, j) => (j === i ? { ...e, ...s } : e));

const KEY_ID = "PKTESTKEYID123456";
const SECRET = "s3cr3t-very-private-value";

let shell: {
  isDesktop: boolean;
  listPaperSlots: ReturnType<typeof vi.fn>;
  connectPaperSlot: ReturnType<typeof vi.fn>;
  disconnectPaperSlot: ReturnType<typeof vi.fn>;
  startPaperSlotEngine: ReturnType<typeof vi.fn>;
};

const slotBox = (n: number) => screen.getByRole("group", { name: `Paper account ${n}` });

async function openForm(n: number) {
  render(<PaperAccountSlots />);
  fireEvent.click(within(await screen.findByRole("group", { name: `Paper account ${n}` })).getByRole("button", { name: "Connect" }));
  const box = slotBox(n);
  fireEvent.change(within(box).getByLabelText("API key ID"), { target: { value: KEY_ID } });
  fireEvent.change(within(box).getByLabelText("Secret key"), { target: { value: SECRET } });
  return box;
}

describe("PaperAccountSlots (#4934)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listStrategies.mockResolvedValue({ strategien: [], max_strategien: 3 });
    listStrategyAssignments.mockResolvedValue([]);
    shell = {
      isDesktop: true,
      listPaperSlots: vi.fn().mockResolvedValue(EMPTY),
      connectPaperSlot: vi.fn().mockResolvedValue({ ok: true, accountMasked: "••••4321" }),
      disconnectPaperSlot: vi.fn().mockResolvedValue({ ok: true }),
      startPaperSlotEngine: vi.fn().mockResolvedValue({ ok: true }),
    };
    (window as unknown as { aaagents: unknown }).aaagents = shell;
  });
  afterEach(() => {
    delete (window as unknown as { aaagents?: unknown }).aaagents;
  });

  it("renders nothing on 404 from GET /api/strategies (flag off)", async () => {
    listStrategies.mockRejectedValue(new HttpError(404, "Not Found", "/api/strategies"));
    const { container } = render(<PaperAccountSlots />);
    await waitFor(() => expect(listStrategies).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing without a bridge (listPaperSlots → null)", async () => {
    delete (window as unknown as { aaagents?: unknown }).aaagents;
    const { container } = render(<PaperAccountSlots />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });

  it("A2-P1: loading, then three slots in fixed order, each with Connect", async () => {
    let resolve: (v: PaperSlot[]) => void = () => {};
    shell.listPaperSlots.mockReturnValueOnce(new Promise<PaperSlot[]>((r) => { resolve = r; }));
    render(<PaperAccountSlots />);
    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    resolve([EMPTY[2], EMPTY[0], EMPTY[1]]);
    const section = await screen.findByRole("region", { name: "Paper accounts" });
    expect(section).toHaveAttribute("id", "paper-accounts");
    const groups = await within(section).findAllByRole("group");
    expect(groups.map((g) => g.getAttribute("aria-label"))).toEqual(["Paper account 1", "Paper account 2", "Paper account 3"]);
    for (const g of groups) expect(within(g).getByRole("button", { name: "Connect" })).toBeInTheDocument();
  });

  it("A2-P2: Connect ok shows the masked number and the engine status, fields empty", async () => {
    shell.listPaperSlots
      .mockResolvedValueOnce(EMPTY)
      .mockResolvedValue(withSlot(1, { connected: true, accountMasked: "••••4321", engineStatus: "running" }));
    const box = await openForm(2);
    fireEvent.click(within(box).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(within(slotBox(2)).getByText("••••4321")).toBeInTheDocument());
    expect(shell.connectPaperSlot).toHaveBeenCalledTimes(1);
    expect(shell.connectPaperSlot).toHaveBeenCalledWith("paper2", KEY_ID, SECRET);
    expect(within(slotBox(2)).getByText("Running")).toBeInTheDocument();
    expect(within(slotBox(2)).getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
    expect(within(slotBox(2)).queryByLabelText("API key ID")).toBeNull();
  });

  it("A2-P3: duplicate_account shows duplicate-account; Use other keys clears the fields, form stays open", async () => {
    shell.connectPaperSlot.mockResolvedValue({ ok: false, code: "duplicate_account" });
    const box = await openForm(2);
    fireEvent.click(within(box).getByRole("button", { name: "Connect" }));
    const notice = await within(box).findByText(
      "This Alpaca account is already connected in another slot. Each slot needs its own account.",
    );
    expect(notice.closest("[data-state]")).toHaveAttribute("data-state", "duplicate-account");
    expect(within(box).queryByRole("button", { name: "Disconnect" })).toBeNull();
    fireEvent.change(within(box).getByLabelText("API key ID"), { target: { value: "other" } });
    fireEvent.click(within(box).getByRole("button", { name: "Use other keys" }));
    expect(within(box).getByLabelText("API key ID")).toHaveValue("");
    expect(within(box).getByLabelText("Secret key")).toHaveValue("");
    expect(within(box).queryByText(/already connected/)).toBeNull();
  });

  it.each(["invalid_keys", "network", "failed"])("%s shows error without the code text", async (code) => {
    shell.connectPaperSlot.mockResolvedValue({ ok: false, code });
    const box = await openForm(1);
    fireEvent.click(within(box).getByRole("button", { name: "Connect" }));
    const err = await waitFor(() => {
      const el = box.querySelector('[data-state="error"]');
      expect(el).not.toBeNull();
      return el!;
    });
    expect(err.textContent).not.toMatch(/invalid_keys|network|failed|_/);
    expect(within(box).queryByRole("button", { name: "Disconnect" })).toBeNull();
  });

  it("a throwing shell degrades to error, never crashes", async () => {
    shell.connectPaperSlot.mockRejectedValue(new Error("ipc boom"));
    const box = await openForm(1);
    fireEvent.click(within(box).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(box.querySelector('[data-state="error"]')).not.toBeNull());
    expect(box.textContent).not.toMatch(/boom/);
  });

  it("an unreadable list shows error with Retry", async () => {
    shell.listPaperSlots.mockRejectedValueOnce(new Error("x")).mockResolvedValue(EMPTY);
    render(<PaperAccountSlots />);
    expect(await screen.findByText("Could not load the data.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("group", { name: "Paper account 1" })).toBeInTheDocument();
  });

  it("A2-P4: a stopped engine shows engine-off; Start engine starts only this slot", async () => {
    shell.listPaperSlots.mockResolvedValue(withSlot(0, { connected: true, accountMasked: "••••1111", engineStatus: "stopped" }));
    render(<PaperAccountSlots />);
    const box = await screen.findByRole("group", { name: "Paper account 1" });
    const notice = await within(box).findByText("The engine for this account is not running.");
    expect(notice.closest("[data-state]")).toHaveAttribute("data-state", "engine-off");
    const before = shell.listPaperSlots.mock.calls.length;
    fireEvent.click(within(box).getByRole("button", { name: "Start engine" }));
    await waitFor(() => expect(shell.startPaperSlotEngine).toHaveBeenCalledTimes(1));
    expect(shell.startPaperSlotEngine).toHaveBeenCalledWith("paper1");
    await waitFor(() => expect(shell.listPaperSlots.mock.calls.length).toBeGreaterThan(before));
    expect(within(slotBox(2)).queryByText("The engine for this account is not running.")).toBeNull();
  });

  describe("A2-P5: low-memory (#4952)", () => {
    const STOPPED = withSlot(0, { connected: true, accountMasked: "••••1111", engineStatus: "stopped" });
    const MEMORY_TEXT = "This computer may not have enough memory for another engine.";
    const ENGINE_OFF_TEXT = "The engine for this account is not running.";

    async function holdBack() {
      shell.listPaperSlots.mockResolvedValue(STOPPED);
      shell.startPaperSlotEngine.mockResolvedValueOnce({ ok: false, code: "low_memory" });
      render(<PaperAccountSlots />);
      const box = await screen.findByRole("group", { name: "Paper account 1" });
      fireEvent.click(await within(box).findByRole("button", { name: "Start engine" }));
      await within(box).findByText(MEMORY_TEXT);
      return box;
    }

    it("low_memory shows low-memory with Start anyway and Cancel, without engine-off", async () => {
      const box = await holdBack();
      expect(within(box).getByText(MEMORY_TEXT).closest("[data-state]")).toHaveAttribute("data-state", "low-memory");
      expect(within(box).getByRole("button", { name: "Start anyway" })).toBeInTheDocument();
      expect(within(box).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
      expect(within(box).queryByText(ENGINE_OFF_TEXT)).toBeNull();
      expect(box.querySelector('[data-state="error"]')).toBeNull();
    });

    it("Start anyway passes force for this slot only and clears the notice", async () => {
      const box = await holdBack();
      fireEvent.click(within(box).getByRole("button", { name: "Start anyway" }));
      await waitFor(() => expect(shell.startPaperSlotEngine).toHaveBeenCalledTimes(2));
      expect(shell.startPaperSlotEngine).toHaveBeenLastCalledWith("paper1", { force: true });
      await waitFor(() => expect(within(box).queryByText(MEMORY_TEXT)).toBeNull());
    });

    it("Cancel calls nothing and shows engine-off again", async () => {
      const box = await holdBack();
      fireEvent.click(within(box).getByRole("button", { name: "Cancel" }));
      expect(await within(box).findByText(ENGINE_OFF_TEXT)).toBeInTheDocument();
      expect(within(box).queryByText(MEMORY_TEXT)).toBeNull();
      expect(shell.startPaperSlotEngine).toHaveBeenCalledTimes(1);
    });

    it("Connect with lowMemory shows the masked slot and low-memory", async () => {
      shell.listPaperSlots
        .mockResolvedValueOnce(EMPTY)
        .mockResolvedValue(withSlot(1, { connected: true, accountMasked: "••••4321", engineStatus: "stopped" }));
      shell.connectPaperSlot.mockResolvedValue({ ok: true, accountMasked: "••••4321", lowMemory: true });
      const box = await openForm(2);
      fireEvent.click(within(box).getByRole("button", { name: "Connect" }));
      await waitFor(() => expect(within(slotBox(2)).getByText("••••4321")).toBeInTheDocument());
      expect(within(slotBox(2)).getByText(MEMORY_TEXT).closest("[data-state]")).toHaveAttribute("data-state", "low-memory");
      expect(within(slotBox(2)).queryByText(ENGINE_OFF_TEXT)).toBeNull();
      expect(shell.startPaperSlotEngine).not.toHaveBeenCalled();
    });

    it("a refused start without a code stays actionFailed", async () => {
      shell.listPaperSlots.mockResolvedValue(STOPPED);
      shell.startPaperSlotEngine.mockResolvedValue({ ok: false });
      render(<PaperAccountSlots />);
      const box = await screen.findByRole("group", { name: "Paper account 1" });
      fireEvent.click(await within(box).findByRole("button", { name: "Start engine" }));
      await waitFor(() => expect(box.querySelector('[data-state="error"]')).not.toBeNull());
      expect(box.querySelector('[data-state="low-memory"]')).toBeNull();
    });

    it("a throwing shell on start degrades to actionFailed", async () => {
      shell.listPaperSlots.mockResolvedValue(STOPPED);
      shell.startPaperSlotEngine.mockRejectedValue(new Error("ipc boom"));
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      render(<PaperAccountSlots />);
      const box = await screen.findByRole("group", { name: "Paper account 1" });
      fireEvent.click(await within(box).findByRole("button", { name: "Start engine" }));
      await waitFor(() => expect(box.querySelector('[data-state="error"]')).not.toBeNull());
      warn.mockRestore();
    });

    it("lowMemory counts only as strict true", async () => {
      shell.listPaperSlots
        .mockResolvedValueOnce(EMPTY)
        .mockResolvedValue(withSlot(1, { connected: true, accountMasked: "••••4321", engineStatus: "running" }));
      shell.connectPaperSlot.mockResolvedValue({ ok: true, accountMasked: "••••4321", lowMemory: "true" });
      const box = await openForm(2);
      fireEvent.click(within(box).getByRole("button", { name: "Connect" }));
      await waitFor(() => expect(within(slotBox(2)).getByText("••••4321")).toBeInTheDocument());
      expect(within(slotBox(2)).queryByText(MEMORY_TEXT)).toBeNull();
    });
  });

  it("A2-P6: Disconnect asks first; Cancel calls nothing; confirm disconnects and empties the slot", async () => {
    shell.listPaperSlots
      .mockResolvedValueOnce(withSlot(0, { connected: true, accountMasked: "••••1111", engineStatus: "running" }))
      .mockResolvedValue(EMPTY);
    render(<PaperAccountSlots />);
    const box = await screen.findByRole("group", { name: "Paper account 1" });
    fireEvent.click(within(box).getByRole("button", { name: "Disconnect" }));
    let dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Are you sure you want to disconnect this paper account?")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(shell.disconnectPaperSlot).not.toHaveBeenCalled();

    fireEvent.click(within(slotBox(1)).getByRole("button", { name: "Disconnect" }));
    dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(shell.disconnectPaperSlot).toHaveBeenCalledWith("paper1"));
    await waitFor(() => expect(within(slotBox(1)).getByRole("button", { name: "Connect" })).toBeInTheDocument());
    expect(within(slotBox(1)).queryByText("••••1111")).toBeNull();
  });

  it("keys never leave the form: not in console output, not in the DOM after submit", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
    shell.connectPaperSlot.mockResolvedValue({ ok: false, code: "invalid_keys" });
    const box = await openForm(1);
    fireEvent.click(within(box).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(box.querySelector('[data-state="error"]')).not.toBeNull());
    expect(within(box).getByLabelText("API key ID")).toHaveValue("");
    expect(within(box).getByLabelText("Secret key")).toHaveValue("");
    const html = document.body.innerHTML;
    expect(html).not.toContain(KEY_ID);
    expect(html).not.toContain(SECRET);
    for (const s of spies) {
      const out = JSON.stringify(s.mock.calls);
      expect(out).not.toContain(KEY_ID);
      expect(out).not.toContain(SECRET);
      s.mockRestore();
    }
  });

  it("the secret field is a password field without autocomplete", async () => {
    const box = await openForm(3);
    const secret = within(box).getByLabelText("Secret key");
    expect(secret).toHaveAttribute("type", "password");
    expect(secret).toHaveAttribute("autocomplete", "off");
    expect(within(box).getByLabelText("API key ID")).toHaveAttribute("autocomplete", "off");
  });

  it("Cancel in the form leaves the slot empty and calls nothing", async () => {
    const box = await openForm(1);
    fireEvent.click(within(box).getByRole("button", { name: "Cancel" }));
    expect(within(box).queryByLabelText("API key ID")).toBeNull();
    expect(within(box).getByRole("button", { name: "Connect" })).toBeInTheDocument();
    expect(shell.connectPaperSlot).not.toHaveBeenCalled();
    fireEvent.click(within(box).getByRole("button", { name: "Connect" }));
    expect(within(box).getByLabelText("API key ID")).toHaveValue("");
  });

  it("entry: Settings → Trading, directly below the live account strategy", async () => {
    const { default: src } = await import("@/console/desktop/pages/Settings.tsx?raw");
    const assign = src.indexOf("<LiveStrategyAssignment />"), slots = src.indexOf("<PaperAccountSlots ");
    expect(assign).toBeGreaterThan(-1);
    expect(slots).toBeGreaterThan(assign);
    expect(src.slice(assign + "<LiveStrategyAssignment />".length, slots)).not.toMatch(/<[A-Z]\w+ /);
  });

  describe("Strategy for this account (T14, #4953)", () => {
    const LABEL = "Strategy for this account";
    const TWO = withSlot(0, { connected: true, accountMasked: "••••1111", engineStatus: "running" }).map((s) =>
      s.slot === "paper2" ? { ...s, connected: true, accountMasked: "••••2222", engineStatus: "running" } : s,
    );
    const zuweisung = (konto: string, strategie: string) => ({ konto, strategie, strategie_hash: "sha256:x", timestamp: "2026-10-30T10:00:00+00:00" });

    beforeEach(() => {
      Object.assign(shell, {
        getPaperSlotSetup: vi.fn().mockResolvedValue({}),
        savePaperSlotSetup: vi.fn().mockResolvedValue({ ok: true }),
        restartPaperSlotEngine: vi.fn().mockResolvedValue({ ok: true }),
      });
      shell.listPaperSlots.mockResolvedValue(TWO);
      listStrategies.mockResolvedValue({ strategien: [{ name: "A", werte: {} }, { name: "B", werte: {} }], max_strategien: 3 });
      getTradingSettings.mockResolvedValue({ settings: {}, meta: [] });
      assignPaperStrategy.mockResolvedValue({ success: true, strategie: "A", applied: {}, changes: [], detail: "" });
    });

    it("Assigned appears per slot, from the key of that slot", async () => {
      listStrategyAssignments.mockResolvedValue([zuweisung("live", "B"), zuweisung("paper2", "A")]);
      render(<PaperAccountSlots />);
      const second = await screen.findByRole("group", { name: "Paper account 2" });
      expect(within(second).getByText("Assigned: A")).toBeInTheDocument();
      expect(within(slotBox(1)).queryByText(/Assigned:/)).toBeNull();
      expect(within(slotBox(3)).queryByText(/Assigned:/)).toBeNull();
    });

    it("the selection stands in each connected slot, not in an empty one", async () => {
      render(<PaperAccountSlots />);
      await screen.findByRole("group", { name: "Paper account 1" });
      expect(within(slotBox(1)).getByLabelText(LABEL)).toBeInTheDocument();
      expect(within(slotBox(2)).getByLabelText(LABEL)).toBeInTheDocument();
      expect(within(slotBox(3)).queryByLabelText(LABEL)).toBeNull();
    });

    it("unreadable assignments leave the card usable", async () => {
      listStrategyAssignments.mockRejectedValue(new Error("503"));
      render(<PaperAccountSlots />);
      const first = await screen.findByRole("group", { name: "Paper account 1" });
      expect(within(first).getByLabelText(LABEL)).toBeInTheDocument();
      expect(within(first).queryByText(/Assigned:/)).toBeNull();
      expect(first.querySelector('[data-state="error"]')).toBeNull();
    });

    it("after the assignment the slot re-reads and shows it, onAssigned fires once", async () => {
      listStrategyAssignments.mockResolvedValueOnce([]).mockResolvedValue([zuweisung("paper1", "A")]);
      const onAssigned = vi.fn();
      render(<PaperAccountSlots onAssigned={onAssigned} />);
      const first = await screen.findByRole("group", { name: "Paper account 1" });
      waehle(within(first).getByLabelText(LABEL), "A");
      fireEvent.click(within(first).getByRole("button", { name: "Review changes" }));
      await screen.findByRole("dialog", { name: "Assign strategy to this paper account?" });
      fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
      expect(await within(slotBox(1)).findByText("Assigned: A")).toBeInTheDocument();
      expect(onAssigned).toHaveBeenCalledTimes(1);
      expect(within(slotBox(2)).queryByText(/Assigned:/)).toBeNull();
    });
  });
});
