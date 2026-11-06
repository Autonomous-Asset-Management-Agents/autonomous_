/**
 * STW-2b (#4934) — "Paper accounts" (UX-Konzept #4628, Ablauf 2, console cut).
 * #5187: the key entry, Rename, Disconnect and Start of Paper account 2/3 render under System →
 * Trading keys (`renderKeys`); Trading → Strategies keeps the restart dialog and T14.
 * #5166: the switch moved to "Enable strategies" (StrategiesGate, strategiesSwitch.test.tsx).
 */
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
  fetchHealth: vi.fn().mockResolvedValue(null),
}));

import { PaperAccountSlots } from "@/console/desktop/strategyWorkshop/PaperAccountSlots";
import { TradingKeysCard } from "@/console/desktop/TradingKeysCard";

const NONE = { name: null, setupAccount: false, mainEngineLive: false } as const;
const EMPTY: PaperSlot[] = [
  { slot: "paper1", connected: false, accountMasked: null, engineStatus: null, ...NONE },
  { slot: "paper2", connected: false, accountMasked: null, engineStatus: null, ...NONE },
  { slot: "paper3", connected: false, accountMasked: null, engineStatus: null, ...NONE },
];
const withSlot = (i: number, s: Partial<PaperSlot>): PaperSlot[] => EMPTY.map((e, j) => (j === i ? { ...e, ...s } : e));

const KEY_ID = "PKTESTKEYID123456";
const SENTENCE =
  "Run up to three paper accounts side by side. Each one gets its own engine and its own data folder on this computer.";
const SECRET = "s3cr3t-very-private-value";

let shell: {
  isDesktop: boolean;
  listPaperSlots: ReturnType<typeof vi.fn>;
  recordBrokerAuthorization: ReturnType<typeof vi.fn>;
  connectPaperSlot: ReturnType<typeof vi.fn>;
  disconnectPaperSlot: ReturnType<typeof vi.fn>;
  startPaperSlotEngine: ReturnType<typeof vi.fn>;
  listAccounts: ReturnType<typeof vi.fn>;
  getKeychainStatus: ReturnType<typeof vi.fn>;
};

const slotBox = (n: number) => screen.getByRole("group", { name: `Paper account ${n}` });
// #5187: the operation of Paper account 2/3 stands under System → Trading keys.
const renderKeys = () => render(<TradingKeysCard />);

async function openForm(n: number) {
  renderKeys();
  fireEvent.click(within(await screen.findByRole("group", { name: `Paper account ${n}` })).getByRole("button", { name: "Connect" }));
  const box = slotBox(n);
  fireEvent.change(within(box).getByLabelText("API key ID"), { target: { value: KEY_ID } });
  fireEvent.change(within(box).getByLabelText("Secret key"), { target: { value: SECRET } });
  return box;
}

// #5155: Connect first shows Alpaca's authorization prompt; `answer` is clicked there.
async function connect(box: HTMLElement, answer: "ALLOW" | "DENY" = "ALLOW") {
  fireEvent.click(within(box).getByRole("button", { name: "Connect" }));
  fireEvent.click(await screen.findByRole("button", { name: answer }));
}

describe("PaperAccountSlots (#4934)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listStrategies.mockResolvedValue({ strategien: [], max_strategien: 3 });
    listStrategyAssignments.mockResolvedValue([]);
    shell = {
      isDesktop: true,
      listPaperSlots: vi.fn().mockResolvedValue(EMPTY),
      recordBrokerAuthorization: vi.fn().mockResolvedValue({ ok: true }),
      connectPaperSlot: vi.fn().mockResolvedValue({ ok: true, accountMasked: "••••4321" }),
      disconnectPaperSlot: vi.fn().mockResolvedValue({ ok: true }),
      startPaperSlotEngine: vi.fn().mockResolvedValue({ ok: true }),
      listAccounts: vi.fn().mockResolvedValue([]),
      getKeychainStatus: vi.fn().mockResolvedValue({}),
    };
    (window as unknown as { aaagents: unknown }).aaagents = shell;
  });
  afterEach(() => {
    delete (window as unknown as { aaagents?: unknown }).aaagents;
  });

  describe("#5155 Alpaca authorization prompt", () => {
    it("Connect shows the prompt first — connectPaperSlot is not called while it is open", async () => {
      const box = await openForm(2);
      fireEvent.click(within(box).getByRole("button", { name: "Connect" }));
      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      expect(shell.recordBrokerAuthorization).not.toHaveBeenCalled();
      expect(shell.connectPaperSlot).not.toHaveBeenCalled();
    });

    it("paper2 ALLOW: the answer (slot paper2, masked key) is written before connectPaperSlot", async () => {
      const box = await openForm(2);
      await connect(box, "ALLOW");
      await waitFor(() => expect(shell.connectPaperSlot).toHaveBeenCalledWith("paper2", KEY_ID, SECRET));
      expect(shell.recordBrokerAuthorization).toHaveBeenCalledWith(
        expect.objectContaining({ answer: "allow", slot: "paper2", keyMasked: "••••3456" }),
      );
      expect(JSON.stringify(shell.recordBrokerAuthorization.mock.calls[0][0])).not.toContain(KEY_ID);
      expect(JSON.stringify(shell.recordBrokerAuthorization.mock.calls[0][0])).not.toContain(SECRET);
      expect(shell.recordBrokerAuthorization.mock.invocationCallOrder[0]).toBeLessThan(
        shell.connectPaperSlot.mock.invocationCallOrder[0],
      );
    });

    it("paper3 ALLOW: the answer carries slot paper3", async () => {
      const box = await openForm(3);
      await connect(box, "ALLOW");
      await waitFor(() => expect(shell.connectPaperSlot).toHaveBeenCalledWith("paper3", KEY_ID, SECRET));
      expect(shell.recordBrokerAuthorization).toHaveBeenCalledWith(expect.objectContaining({ answer: "allow", slot: "paper3" }));
    });

    it("DENY: written as deny, no connectPaperSlot, the keys stay in the form", async () => {
      const box = await openForm(2);
      await connect(box, "DENY");
      await waitFor(() =>
        expect(shell.recordBrokerAuthorization).toHaveBeenCalledWith(expect.objectContaining({ answer: "deny", slot: "paper2" })),
      );
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(shell.connectPaperSlot).not.toHaveBeenCalled();
      expect(within(box).getByLabelText("API key ID")).toHaveValue(KEY_ID);
      expect(within(box).getByLabelText("Secret key")).toHaveValue(SECRET);
      expect(box.querySelector('[data-state="error"]')).toBeNull();
    });

    it("a failed answer write is fail-closed: nothing connected, an error says nothing was saved", async () => {
      shell.recordBrokerAuthorization.mockResolvedValue({ ok: false });
      const box = await openForm(2);
      await connect(box, "ALLOW");
      expect(await within(box).findByText(/nothing was saved/i)).toBeInTheDocument();
      expect(shell.connectPaperSlot).not.toHaveBeenCalled();
      expect(within(box).getByLabelText("API key ID")).toHaveValue(KEY_ID);
    });
  });

  it("flag off (404, no choice) → title and sentence only; the switch moved to Strategies (#5166)", async () => {
    listStrategies.mockRejectedValue(new HttpError(404, "Not Found", "/api/strategies"));
    render(<PaperAccountSlots />);
    const section = await screen.findByRole("region", { name: "Paper accounts" });
    await waitFor(() => expect(within(section).queryByText("Loading…")).toBeNull());
    expect(screen.queryByRole("switch")).toBeNull();
    expect(within(section).getByText(SENTENCE)).toBeInTheDocument();
    expect(within(section).queryByRole("group")).toBeNull();
    expect(section.querySelector(".border-t")).toBeNull();
    expect(within(section).queryByLabelText("API key ID")).toBeNull();
  });

  it("renders nothing without a bridge (listPaperSlots → null)", async () => {
    delete (window as unknown as { aaagents?: unknown }).aaagents;
    const { container } = render(<PaperAccountSlots />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });

  it("#5187: Trading → Strategies has no field for account keys", async () => {
    shell.listPaperSlots.mockResolvedValue(withSlot(1, { connected: true, accountMasked: "••••2222", engineStatus: "running" }));
    render(<PaperAccountSlots />);
    const section = await screen.findByRole("region", { name: "Paper accounts" });
    await within(section).findByRole("group", { name: "Paper account 3" });
    expect(section.querySelector("input")).toBeNull();
    expect(within(section).queryByRole("button", { name: "Connect" })).toBeNull();
    expect(within(section).queryByRole("button", { name: "Disconnect" })).toBeNull();
    const link = within(slotBox(3)).getByRole("link", { name: "Connect under System → Trading keys" });
    expect(link).toHaveAttribute("href", "#system");
    expect(link).toHaveClass("btn-link");
    expect(within(slotBox(3)).getByText("Not connected")).toBeInTheDocument();
    expect(within(slotBox(2)).getByText("••••2222")).toBeInTheDocument();
  });

  it("A2-P1: loading, then three slots in fixed order, empty ones link to Trading keys", async () => {
    let resolve: (v: PaperSlot[]) => void = () => {};
    shell.listPaperSlots.mockReturnValueOnce(new Promise<PaperSlot[]>((r) => { resolve = r; }));
    render(<PaperAccountSlots />);
    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    resolve([EMPTY[2], EMPTY[0], EMPTY[1]]);
    const section = await screen.findByRole("region", { name: "Paper accounts" });
    expect(section).toHaveAttribute("id", "paper-accounts");
    const groups = await within(section).findAllByRole("group");
    expect(groups.map((g) => g.getAttribute("aria-label"))).toEqual(["Paper account 1", "Paper account 2", "Paper account 3"]);
    for (const g of groups) expect(within(g).getByRole("link", { name: "Connect under System → Trading keys" })).toBeInTheDocument();
  });

  it("A2-P2: Connect ok shows the masked number and the engine status, fields empty", async () => {
    shell.listPaperSlots
      .mockResolvedValueOnce(EMPTY)
      .mockResolvedValue(withSlot(1, { connected: true, accountMasked: "••••4321", engineStatus: "running" }));
    const box = await openForm(2);
    await connect(box);
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
    await connect(box);
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
    const box = await openForm(3);
    await connect(box);
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
    const box = await openForm(3);
    await connect(box);
    await waitFor(() => expect(box.querySelector('[data-state="error"]')).not.toBeNull());
    expect(box.textContent).not.toMatch(/boom/);
  });

  it("#5166: engine down (strategies fail without HTTP status) → the slots stay, engine-offline, no card error", async () => {
    listStrategies.mockRejectedValue(new TypeError("Failed to fetch"));
    shell.listPaperSlots.mockResolvedValue(withSlot(1, { connected: true, accountMasked: "••••2222", engineStatus: "running" }));
    render(<PaperAccountSlots />);
    const box = await screen.findByRole("group", { name: "Paper account 2" });
    expect(within(box).getByText("••••2222")).toBeInTheDocument();
    expect(screen.getAllByRole("group")).toHaveLength(3);
    const hinweis = screen.getByText("Engine offline. Start the engine in Settings → System, then retry.").closest('[role="status"]');
    expect(hinweis).toHaveAttribute("data-state", "engine-offline");
    expect(screen.queryByText("Could not load the data.")).toBeNull();
  });

  it("#5166: a failing strategies read (HTTP 500) keeps the slots too, with error and Retry", async () => {
    listStrategies.mockRejectedValueOnce(new HttpError(500, "Server Error", "/api/strategies"));
    render(<PaperAccountSlots />);
    expect(await screen.findAllByRole("group")).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(document.querySelector('[data-state="error"]')).toBeNull());
    expect(screen.getAllByRole("group")).toHaveLength(3);
  });

  it("an unreadable list shows error with Retry", async () => {
    shell.listPaperSlots.mockRejectedValueOnce(new Error("x")).mockResolvedValue(EMPTY);
    render(<PaperAccountSlots />);
    expect(await screen.findByText("Could not load the data.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("group", { name: "Paper account 1" })).toBeInTheDocument();
  });

  it("A2-P4: a stopped engine shows engine-off; Start engine starts only this slot", async () => {
    shell.listPaperSlots.mockResolvedValue(withSlot(1, { connected: true, accountMasked: "••••1111", engineStatus: "stopped" }));
    renderKeys();
    const box = await screen.findByRole("group", { name: "Paper account 2" });
    const notice = await within(box).findByText("The engine for this account is not running.");
    expect(notice.closest("[data-state]")).toHaveAttribute("data-state", "engine-off");
    const before = shell.listPaperSlots.mock.calls.length;
    fireEvent.click(within(box).getByRole("button", { name: "Start engine" }));
    await waitFor(() => expect(shell.startPaperSlotEngine).toHaveBeenCalledTimes(1));
    expect(shell.startPaperSlotEngine).toHaveBeenCalledWith("paper2");
    await waitFor(() => expect(shell.listPaperSlots.mock.calls.length).toBeGreaterThan(before));
    expect(within(slotBox(3)).queryByText("The engine for this account is not running.")).toBeNull();
  });

  describe("A2-P5: low-memory (#4952)", () => {
    const STOPPED = withSlot(1, { connected: true, accountMasked: "••••1111", engineStatus: "stopped" });
    const MEMORY_TEXT = "This computer may not have enough memory for another engine.";
    const ENGINE_OFF_TEXT = "The engine for this account is not running.";

    async function holdBack() {
      shell.listPaperSlots.mockResolvedValue(STOPPED);
      shell.startPaperSlotEngine.mockResolvedValueOnce({ ok: false, code: "low_memory" });
      renderKeys();
      const box = await screen.findByRole("group", { name: "Paper account 2" });
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
      expect(shell.startPaperSlotEngine).toHaveBeenLastCalledWith("paper2", { force: true });
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
      await connect(box);
      await waitFor(() => expect(within(slotBox(2)).getByText("••••4321")).toBeInTheDocument());
      expect(within(slotBox(2)).getByText(MEMORY_TEXT).closest("[data-state]")).toHaveAttribute("data-state", "low-memory");
      expect(within(slotBox(2)).queryByText(ENGINE_OFF_TEXT)).toBeNull();
      expect(shell.startPaperSlotEngine).not.toHaveBeenCalled();
    });

    it("a refused start without a code stays actionFailed", async () => {
      shell.listPaperSlots.mockResolvedValue(STOPPED);
      shell.startPaperSlotEngine.mockResolvedValue({ ok: false });
      renderKeys();
      const box = await screen.findByRole("group", { name: "Paper account 2" });
      fireEvent.click(await within(box).findByRole("button", { name: "Start engine" }));
      await waitFor(() => expect(box.querySelector('[data-state="error"]')).not.toBeNull());
      expect(box.querySelector('[data-state="low-memory"]')).toBeNull();
    });

    it("a throwing shell on start degrades to actionFailed", async () => {
      shell.listPaperSlots.mockResolvedValue(STOPPED);
      shell.startPaperSlotEngine.mockRejectedValue(new Error("ipc boom"));
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      renderKeys();
      const box = await screen.findByRole("group", { name: "Paper account 2" });
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
      await connect(box);
      await waitFor(() => expect(within(slotBox(2)).getByText("••••4321")).toBeInTheDocument());
      expect(within(slotBox(2)).queryByText(MEMORY_TEXT)).toBeNull();
    });
  });

  it("A2-P6: Disconnect asks first; Cancel calls nothing; confirm disconnects and empties the slot", async () => {
    shell.listPaperSlots
      .mockResolvedValueOnce(withSlot(1, { connected: true, accountMasked: "••••1111", engineStatus: "running" }))
      .mockResolvedValue(EMPTY);
    renderKeys();
    const box = await screen.findByRole("group", { name: "Paper account 2" });
    fireEvent.click(within(box).getByRole("button", { name: "Disconnect" }));
    let dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Are you sure you want to disconnect this paper account?")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(shell.disconnectPaperSlot).not.toHaveBeenCalled();

    fireEvent.click(within(slotBox(2)).getByRole("button", { name: "Disconnect" }));
    dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(shell.disconnectPaperSlot).toHaveBeenCalledWith("paper2"));
    await waitFor(() => expect(within(slotBox(2)).getByRole("button", { name: "Connect" })).toBeInTheDocument());
    expect(within(slotBox(2)).queryByText("••••1111")).toBeNull();
  });

  it("#5172: the Disconnect dialog lies in an .aaa-console scope", async () => {
    shell.listPaperSlots.mockResolvedValue(withSlot(1, { connected: true, accountMasked: "••••2222", engineStatus: "running" }));
    renderKeys();
    fireEvent.click(within(await screen.findByRole("group", { name: "Paper account 2" })).getByRole("button", { name: "Disconnect" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.closest(".aaa-console")).not.toBeNull();
    for (const b of within(dialog).getAllByRole("button")) {
      expect(b.closest(".aaa-console")).not.toBeNull();
      expect(b.classList.contains("btn")).toBe(true);
    }
  });

  it("keys never leave the form: not in console output, not in a notice, cleared after success", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
    shell.connectPaperSlot.mockResolvedValueOnce({ ok: false, code: "invalid_keys" });
    const box = await openForm(3);
    await connect(box);
    await waitFor(() => expect(box.querySelector('[data-state="error"]')).not.toBeNull());
    // #5142: after a failure the keys stay in the form so the operator can correct them.
    expect(within(box).getByLabelText("API key ID")).toHaveValue(KEY_ID);
    expect(within(box).getByLabelText("Secret key")).toHaveValue(SECRET);
    const notice = box.querySelector('[data-state="error"]')!.textContent ?? "";
    expect(notice).not.toContain(KEY_ID);
    expect(notice).not.toContain(SECRET);
    shell.listPaperSlots.mockResolvedValue(withSlot(2, { connected: true, accountMasked: "••••4321", engineStatus: "running" }));
    await connect(box);
    await waitFor(() => expect(within(slotBox(3)).getByText("••••4321")).toBeInTheDocument());
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
    const box = await openForm(3);
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
      // #5117: the preview runs on selection; Review opens once it stands.
      await waitFor(() =>
        expect((within(first).getByRole("button", { name: "Review changes" }) as HTMLButtonElement).disabled).toBe(false),
      );
      fireEvent.click(within(first).getByRole("button", { name: "Review changes" }));
      await screen.findByRole("dialog", { name: "Assign strategy to this paper account?" });
      fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
      expect(await within(slotBox(1)).findByText("Assigned: A")).toBeInTheDocument();
      expect(onAssigned).toHaveBeenCalledTimes(1);
      expect(within(slotBox(2)).queryByText(/Assigned:/)).toBeNull();
    });

    it("a strategy on another connected slot is locked; a disconnected slot locks nothing (#5117)", async () => {
      listStrategyAssignments.mockResolvedValue([zuweisung("paper2", "A"), zuweisung("paper3", "B"), zuweisung("live", "B")]);
      render(<PaperAccountSlots />);
      const first = await screen.findByRole("group", { name: "Paper account 1" });
      fireEvent.click(within(first).getByLabelText(LABEL));
      expect(within(first).getByRole("option", { name: "A" }).getAttribute("aria-disabled")).toBe("true");
      expect(within(first).getByRole("option", { name: "B" }).getAttribute("aria-disabled")).not.toBe("true");
      // Its own assignment does not lock it for paper2.
      fireEvent.click(within(slotBox(2)).getByLabelText(LABEL));
      expect(within(slotBox(2)).getByRole("option", { name: "A" }).getAttribute("aria-disabled")).not.toBe("true");
    });
  });

  describe("slot layout (owner design 02.11.2026, #5117)", () => {
    const follows = (a: Node, b: Node) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

    it("Disconnect is a .btn-link (Trading keys, #5187)", async () => {
      shell.listPaperSlots.mockResolvedValue(withSlot(1, { connected: true, accountMasked: "••••1111", engineStatus: "running" }));
      renderKeys();
      const box = await screen.findByRole("group", { name: "Paper account 2" });
      expect(within(box).getByRole("button", { name: "Disconnect" }).className).toBe("btn-link");
    });

    it("header: name weight 600, the status dot before the masked number", async () => {
      shell.listPaperSlots.mockResolvedValue(withSlot(0, { connected: true, accountMasked: "••••1111", engineStatus: "running" }));
      render(<PaperAccountSlots />);
      const box = await screen.findByRole("group", { name: "Paper account 1" });
      expect(within(box).getByText("Paper account 1").className).toMatch(/font-semibold/);
      expect(follows(within(box).getByText("Running"), within(box).getByText("••••1111"))).toBe(true);
    });

    it("engine-off stands at the end of the block, after Disconnect; no strategy selection under Trading keys (#5187)", async () => {
      shell.listPaperSlots.mockResolvedValue(withSlot(1, { connected: true, accountMasked: "••••1111", engineStatus: "stopped" }));
      renderKeys();
      const box = await screen.findByRole("group", { name: "Paper account 2" });
      const notice = await within(box).findByText("The engine for this account is not running.");
      expect(within(box).queryByLabelText("Strategy for this account")).toBeNull();
      expect(follows(within(box).getByRole("button", { name: "Disconnect" }), notice)).toBe(true);
      expect(box.lastElementChild!.contains(notice)).toBe(true);
    });
  });

  // STW-2f (#5125): the operator's choice gates the slots. #5166 moved the switch itself to the
  // Strategies card on Trading → Strategies (strategiesSwitch.test.tsx); this card only reads it.
  describe("operator choice (STW-2f #5125, switch on Strategies since #5166)", () => {
    const withSetup = (state: Record<string, unknown> | (() => Promise<unknown>)) => {
      const getSetupState = typeof state === "function" ? vi.fn(state) : vi.fn().mockResolvedValue(state);
      Object.assign(shell, { getSetupState });
      return { getSetupState };
    };

    it("unchanged stage: getSetupState missing → three slots, no switch", async () => {
      render(<PaperAccountSlots />);
      expect(await screen.findAllByRole("group")).toHaveLength(3);
      expect(screen.queryByRole("switch")).toBeNull();
    });

    it("unchanged stage: getSetupState {} → three slots, sentence above", async () => {
      withSetup({});
      render(<PaperAccountSlots />);
      expect(await screen.findAllByRole("group")).toHaveLength(3);
      expect(screen.getByText(SENTENCE)).toBeInTheDocument();
    });

    it("off by choice: strategyProfilesEnabled false → no slots, the engine is not asked", async () => {
      withSetup({ strategyProfilesEnabled: false });
      render(<PaperAccountSlots />);
      const section = await screen.findByRole("region", { name: "Paper accounts" });
      await waitFor(() => expect(within(section).queryByText("Loading…")).toBeNull());
      expect(screen.queryByRole("group")).toBeNull();
      expect(listStrategies).not.toHaveBeenCalled();
    });

    it("a string does not count: 'false' with strategies → slots (no choice)", async () => {
      withSetup({ strategyProfilesEnabled: "false" });
      render(<PaperAccountSlots />);
      expect(await screen.findAllByRole("group")).toHaveLength(3);
    });

    it("getSetupState throws → no choice, the runtime decides, warned", async () => {
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      withSetup(() => Promise.reject(new Error("ipc down")));
      render(<PaperAccountSlots />);
      expect(await screen.findAllByRole("group")).toHaveLength(3);
      expect(warn).toHaveBeenCalled();
      warn.mockRestore();
    });
  });

  describe("STW-6d: setup account, restart, error texts (#5142)", () => {
    const SETUP_ROW = (s: Partial<PaperSlot> = {}): PaperSlot[] =>
      withSlot(0, { connected: true, accountMasked: null, engineStatus: "running", setupAccount: true, ...s });
    const withSetupT14 = () => {
      Object.assign(shell, {
        getPaperSlotSetup: vi.fn().mockResolvedValue({}),
        savePaperSlotSetup: vi.fn().mockResolvedValue({ ok: true }),
        restartPaperSlotEngine: vi.fn().mockResolvedValue({ ok: true }),
      });
      listStrategies.mockResolvedValue({ strategien: [{ name: "A", werte: {} }], max_strategien: 3 });
    };
    const session = (fleetActive: boolean, choice: boolean) => {
      Object.assign(shell, {
        getFleetSession: vi.fn().mockResolvedValue({ fleetActive }),
        relaunchApp: vi.fn().mockResolvedValue(undefined),
        getSetupState: vi.fn().mockResolvedValue({ strategyProfilesEnabled: choice }),
        saveSetupState: vi.fn().mockResolvedValue({ ok: true }),
      });
      return shell as unknown as { relaunchApp: ReturnType<typeof vi.fn> };
    };
    const RESTART_TEXT = "Restart the app to apply the paper account setting.";

    it("paper account 1 is the setup account: status of the main engine, masked number (#5148), neither Connect nor Disconnect nor T14", async () => {
      withSetupT14();
      shell.listPaperSlots.mockResolvedValue(SETUP_ROW({ accountMasked: "••••8888" }));
      render(<PaperAccountSlots />);
      const box = await screen.findByRole("group", { name: "Paper account 1" });
      expect(within(box).getByText("••••8888")).toBeInTheDocument();
      expect(within(box).getByText("Running")).toBeInTheDocument();
      expect(within(box).queryByRole("button", { name: "Connect" })).toBeNull();
      expect(within(box).queryByRole("button", { name: "Disconnect" })).toBeNull();
      expect(within(box).queryByLabelText("Strategy for this account")).toBeNull();
    });

    it("without setup keys paper account 1 points to the setup and offers no form", async () => {
      shell.listPaperSlots.mockResolvedValue(SETUP_ROW({ connected: false, accountMasked: null, engineStatus: null }));
      render(<PaperAccountSlots />);
      const box = await screen.findByRole("group", { name: "Paper account 1" });
      expect(within(box).getByText("Add your paper keys in the setup first.")).toBeInTheDocument();
      expect(within(box).queryByRole("button", { name: "Connect" })).toBeNull();
      expect(within(box).queryByLabelText("API key ID")).toBeNull();
    });

    it("main engine live: paper account 1 says so and offers no action", async () => {
      shell.listPaperSlots.mockResolvedValue(SETUP_ROW({ engineStatus: null, mainEngineLive: true }));
      render(<PaperAccountSlots />);
      const box = await screen.findByRole("group", { name: "Paper account 1" });
      expect(
        within(box).getByText("The main engine trades the live account. This paper account is not running."),
      ).toBeInTheDocument();
      expect(within(box).getByText("—")).toBeInTheDocument();
      expect(within(box).queryByRole("button")).toBeNull();
    });

    it("restart-required under Trading keys: Connect locked, the notice links to the restart on Trading (#5187)", async () => {
      session(false, true);
      renderKeys();
      expect(await screen.findByText(RESTART_TEXT)).toBeInTheDocument();
      for (const n of [2, 3]) {
        expect((within(slotBox(n)).getByRole("button", { name: "Connect" }) as HTMLButtonElement).disabled).toBe(true);
      }
      const link = screen.getByRole("link", { name: "Restart on Trading → Strategies" });
      expect(link).toHaveAttribute("href", "#trading-strategies");
      expect(link).toHaveClass("btn-link");
      expect(screen.queryByRole("button", { name: "Restart app" })).toBeNull();
    });

    it("restart-required: switched on, fleet not active → notice and Restart app with confirmation", async () => {
      listStrategies.mockRejectedValue(new HttpError(404, "Not Found", "/api/strategies"));
      const s = session(false, true);
      render(<PaperAccountSlots />);
      expect(await screen.findByText(RESTART_TEXT)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Restart app" }));
      let dialog = await screen.findByRole("alertdialog");
      expect(within(dialog).getByText("The app and all engines stop and start again. Continue?")).toBeInTheDocument();
      fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
      expect(s.relaunchApp).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Restart app" }));
      dialog = await screen.findByRole("alertdialog");
      fireEvent.click(within(dialog).getByRole("button", { name: "Restart" }));
      await waitFor(() => expect(s.relaunchApp).toHaveBeenCalledTimes(1));
    });

    it("#5172: the Restart app dialog lies in an .aaa-console scope", async () => {
      listStrategies.mockRejectedValue(new HttpError(404, "Not Found", "/api/strategies"));
      session(false, true);
      render(<PaperAccountSlots />);
      await screen.findByText(RESTART_TEXT);
      fireEvent.click(screen.getByRole("button", { name: "Restart app" }));
      const dialog = await screen.findByRole("alertdialog");
      expect(dialog.closest(".aaa-console")).not.toBeNull();
      for (const b of within(dialog).getAllByRole("button")) {
        expect(b.closest(".aaa-console")).not.toBeNull();
        expect(b.classList.contains("btn")).toBe(true);
      }
    });

    it("restart-required also locks Disconnect and Start engine of a connected slot", async () => {
      listStrategies.mockRejectedValue(new HttpError(404, "Not Found", "/api/strategies"));
      session(false, true);
      shell.listPaperSlots.mockResolvedValue(withSlot(1, { connected: true, accountMasked: "••••2222", engineStatus: "stopped" }));
      renderKeys();
      await screen.findByText(RESTART_TEXT);
      const box = slotBox(2);
      expect((within(box).getByRole("button", { name: "Disconnect" }) as HTMLButtonElement).disabled).toBe(true);
      expect((within(box).getByRole("button", { name: "Start engine" }) as HTMLButtonElement).disabled).toBe(true);
    });

    it("switched off while the fleet runs: no slots and the restart notice", async () => {
      session(true, false);
      render(<PaperAccountSlots />);
      expect(await screen.findByText(RESTART_TEXT)).toBeInTheDocument();
      expect(screen.queryByRole("group")).toBeNull();
    });

    it("choice and session agree → no restart notice", async () => {
      session(true, true);
      renderKeys();
      await screen.findByRole("group", { name: "Paper account 2" });
      expect(screen.queryByText(RESTART_TEXT)).toBeNull();
      expect((within(slotBox(2)).getByRole("button", { name: "Connect" }) as HTMLButtonElement).disabled).toBe(false);
    });

    it.each([
      ["invalid_keys", "These keys were not accepted by Alpaca's paper endpoint."],
      ["network", "Alpaca could not be reached. Check the connection and try again."],
      ["failed", "Could not connect this account."],
    ])("%s has its own text, the keys stay in the form", async (code, text) => {
      shell.connectPaperSlot.mockResolvedValue({ ok: false, code });
      const box = await openForm(2);
      await connect(box);
      expect(await within(box).findByText(text)).toBeInTheDocument();
      expect(within(box).getByLabelText("API key ID")).toHaveValue(KEY_ID);
      expect(within(box).getByLabelText("Secret key")).toHaveValue(SECRET);
    });
  });

  describe("STW-6e: account number and name per paper account (#5148)", () => {
    const P2 = { connected: true, accountMasked: "••••1234", engineStatus: "running" };
    const withRename = (impl = vi.fn().mockResolvedValue({ ok: true })) => {
      Object.assign(shell, { renamePaperSlot: impl });
      return impl;
    };

    it("a stored name stands next to the masked number", async () => {
      shell.listPaperSlots.mockResolvedValue(withSlot(1, { ...P2, name: "Momentum Test" }));
      render(<PaperAccountSlots />);
      const box = await screen.findByRole("group", { name: "Momentum Test" });
      expect(within(box).getByText("Momentum Test")).toBeInTheDocument();
      expect(within(box).getByText("••••1234")).toBeInTheDocument();
      expect(within(box).queryByText("Paper account 2")).toBeNull();
    });

    it("Rename → name field, Save sends the name for this slot, the slot re-reads and shows it", async () => {
      const rename = withRename();
      shell.listPaperSlots.mockResolvedValue(withSlot(1, P2));
      renderKeys();
      const box = await screen.findByRole("group", { name: "Paper account 2" });
      fireEvent.click(within(box).getByRole("button", { name: "Rename" }));
      const field = within(box).getByLabelText("Account name");
      expect(field).toHaveValue("Paper account 2");
      fireEvent.change(field, { target: { value: "Momentum Test" } });
      shell.listPaperSlots.mockResolvedValue(withSlot(1, { ...P2, name: "Momentum Test" }));
      fireEvent.click(within(box).getByRole("button", { name: "Save" }));
      await waitFor(() => expect(rename).toHaveBeenCalledWith("paper2", "Momentum Test"));
      const renamed = await screen.findByRole("group", { name: "Momentum Test" });
      expect(within(renamed).getByText("••••1234")).toBeInTheDocument();
      expect(within(renamed).queryByLabelText("Account name")).toBeNull();
    });

    it("a refused name: the card keeps the old name and says the action did not complete", async () => {
      withRename(vi.fn().mockResolvedValue({ ok: false }));
      shell.listPaperSlots.mockResolvedValue(withSlot(1, { ...P2, name: "Breakout" }));
      renderKeys();
      const box = await screen.findByRole("group", { name: "Breakout" });
      fireEvent.click(within(box).getByRole("button", { name: "Rename" }));
      fireEvent.change(within(box).getByLabelText("Account name"), { target: { value: "x".repeat(41) } });
      fireEvent.click(within(box).getByRole("button", { name: "Save" }));
      expect(await within(box).findByText("The action did not complete.")).toBeInTheDocument();
      expect(within(box).getByText("Breakout")).toBeInTheDocument();
    });

    it("Cancel calls nothing and keeps the name", async () => {
      const rename = withRename();
      shell.listPaperSlots.mockResolvedValue(withSlot(1, P2));
      renderKeys();
      const box = await screen.findByRole("group", { name: "Paper account 2" });
      fireEvent.click(within(box).getByRole("button", { name: "Rename" }));
      fireEvent.change(within(box).getByLabelText("Account name"), { target: { value: "Neu" } });
      fireEvent.click(within(box).getByRole("button", { name: "Cancel" }));
      expect(within(box).getByText("Paper account 2")).toBeInTheDocument();
      expect(rename).not.toHaveBeenCalled();
    });

    it("Rename on the setup account and on connected slots; not on an empty slot", async () => {
      withRename();
      shell.listPaperSlots.mockResolvedValue(
        withSlot(0, { connected: true, accountMasked: "••••8888", engineStatus: "running", setupAccount: true }).map((s, i) =>
          i === 1 ? { ...s, ...P2 } : s,
        ),
      );
      renderKeys();
      await screen.findByRole("group", { name: "Paper account 1" });
      expect(within(slotBox(1)).getByRole("button", { name: "Rename" })).toBeInTheDocument();
      expect(within(slotBox(2)).getByRole("button", { name: "Rename" })).toBeInTheDocument();
      expect(within(slotBox(3)).queryByRole("button", { name: "Rename" })).toBeNull();
    });

    it("without renamePaperSlot in the shell there is no Rename", async () => {
      shell.listPaperSlots.mockResolvedValue(withSlot(1, P2));
      renderKeys();
      const box = await screen.findByRole("group", { name: "Paper account 2" });
      expect(within(box).queryByRole("button", { name: "Rename" })).toBeNull();
    });

    it("restart-required locks Rename", async () => {
      withRename();
      listStrategies.mockRejectedValue(new HttpError(404, "Not Found", "/api/strategies"));
      Object.assign(shell, {
        getFleetSession: vi.fn().mockResolvedValue({ fleetActive: false }),
        getSetupState: vi.fn().mockResolvedValue({ strategyProfilesEnabled: true }),
      });
      shell.listPaperSlots.mockResolvedValue(withSlot(1, P2));
      renderKeys();
      await screen.findByText("Restart the app to apply the paper account setting.");
      expect((within(slotBox(2)).getByRole("button", { name: "Rename" }) as HTMLButtonElement).disabled).toBe(true);
    });
  });
});
