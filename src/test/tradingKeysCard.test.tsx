/**
 * #5176 (Teil 1: Anzeige) — "Trading keys" (Settings → System) shows Paper account 1–3 and Live,
 * each with the masked account number from the shell (plan docs/5176-*, epic #5166 §2.4). Paper
 * account 1 and Live come from `listAccounts` (#5174), Paper 2/3 from `listPaperSlots`; no engine
 * call. Paper 2/3 are read-only here until the rest sub-issue moves their key entry over.
 */
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const { isDesktop, getKeychainStatus } = vi.hoisted(() => ({
  isDesktop: vi.fn(),
  getKeychainStatus: vi.fn(),
}));
vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return {
    ...actual,
    isDesktop: () => isDesktop(),
    getKeychainStatus: () => getKeychainStatus(),
  };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchHealth: vi.fn().mockResolvedValue(null),
    fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
    getHitlPolicy: vi.fn().mockResolvedValue({ HITL_AUTONOMOUS_UNLIMITED: true }),
    fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  };
});

import type { PaperSlot } from "@/lib/desktopBridge";
import { useStore } from "@/console/store/useStore";
import { TradingKeysCard } from "@/console/desktop/TradingKeysCard";
import { Sidebar } from "@/console/desktop/Sidebar";

const NONE = { name: null, setupAccount: false, mainEngineLive: false } as const;
const SLOTS: PaperSlot[] = [
  { slot: "paper1", connected: true, accountMasked: "••••1111", engineStatus: "running", ...NONE, setupAccount: true },
  { slot: "paper2", connected: true, accountMasked: "••••2222", engineStatus: "running", ...NONE },
  { slot: "paper3", connected: false, accountMasked: null, engineStatus: null, ...NONE },
];
const ALL_CONNECTED = SLOTS.map((s) =>
  s.slot === "paper3" ? { ...s, connected: true, accountMasked: "••••3333", engineStatus: "running" } : s,
);
const ACCOUNTS = [
  { slot: "paper1", connected: true, accountMasked: "••••1111" },
  { slot: "live", connected: true, accountMasked: "••••9999" },
];

const ORDER = ["Paper account 1", "Paper account 2", "Paper account 3", "Live"];
const MASK = /^••••\S{1,4}$/;
const LIVE_KEYS = { ALPACA_LIVE_API_KEY: true, ALPACA_LIVE_SECRET_KEY: true };

function areaByDataAccount(container: HTMLElement, slot: "paper1" | "live"): HTMLElement {
  const el = container.querySelector<HTMLElement>(`[data-account="${slot}"]`);
  if (!el) throw new Error(`no account area ${slot}`);
  return el;
}

const classSet = (el: Element) => new Set(el.className.split(/\s+/).filter(Boolean));

let shell: Record<string, ReturnType<typeof vi.fn> | boolean>;
const fn = (k: string) => shell[k] as ReturnType<typeof vi.fn>;
const area = (name: string) => screen.getByRole("group", { name });
const number = (name: string) => area(name).querySelector("[data-account-number]")?.textContent;

async function renderCard() {
  render(<TradingKeysCard />);
  await screen.findByRole("group", { name: "Paper account 3" });
  await waitFor(() => expect(fn("listAccounts")).toHaveBeenCalled());
}

describe("TradingKeysCard (#5176 Teil 1: Anzeige)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    isDesktop.mockReturnValue(true);
    getKeychainStatus.mockResolvedValue({ ALPACA_API_KEY: true, ALPACA_LIVE_API_KEY: true });
    useStore.setState({ allowLive: true, engineServing: true });
    shell = {
      isDesktop: true,
      listAccounts: vi.fn().mockResolvedValue(ACCOUNTS),
      listPaperSlots: vi.fn().mockResolvedValue(SLOTS),
      getKeychainStatus: vi.fn().mockResolvedValue({ ALPACA_API_KEY: true, ALPACA_LIVE_API_KEY: true }),
      getSetupState: vi.fn().mockResolvedValue({}),
      getFleetSession: vi.fn().mockResolvedValue({ fleetActive: true }),
      connectPaperSlot: vi.fn().mockResolvedValue({ ok: true, accountMasked: "••••3333" }),
    };
    (window as unknown as { aaagents: unknown }).aaagents = shell;
  });
  afterEach(() => {
    delete (window as unknown as { aaagents?: unknown }).aaagents;
    vi.restoreAllMocks();
  });

  it("Kennzahl: four areas in fixed order, 4 of 4 with a masked number", async () => {
    fn("listPaperSlots").mockResolvedValue(ALL_CONNECTED);
    await renderCard();
    const groups = screen.getAllByRole("group").filter((g) => ORDER.includes(g.getAttribute("aria-label") ?? ""));
    expect(groups.map((g) => g.getAttribute("aria-label"))).toEqual(ORDER);
    await waitFor(() => expect(number("Live")).toBe("••••9999"));
    expect(ORDER.map(number)).toEqual(["••••1111", "••••2222", "••••3333", "••••9999"]);
    expect(ORDER.map(number).every((n) => MASK.test(n ?? ""))).toBe(true);
  });

  it("the subtitle is English (Guide §11)", async () => {
    await renderCard();
    expect(screen.queryByText(/Zugangsdaten|Schlüsselbund/)).toBeNull();
    expect(screen.getByText(/system keychain/i)).toBeInTheDocument();
  });

  it("never a full number in the DOM: an unmasked shell answer shows —", async () => {
    fn("listAccounts").mockResolvedValue([
      { slot: "paper1", connected: true, accountMasked: "PA9SETUP8888" },
      { slot: "live", connected: true, accountMasked: "••••9999" },
    ]);
    await renderCard();
    await waitFor(() => expect(number("Live")).toBe("••••9999"));
    expect(number("Paper account 1")).toBe("—");
    expect(document.body.innerHTML).not.toContain("PA9SETUP8888");
  });

  it("Live keys stored before the update: — and the hint to re-enter them", async () => {
    fn("listAccounts").mockResolvedValue([ACCOUNTS[0], { slot: "live", connected: true, accountMasked: null }]);
    await renderCard();
    const live = area("Live");
    expect(await within(live).findByText("Re-enter the live keys to show the account number.")).toBeInTheDocument();
    expect(number("Live")).toBe("—");
  });

  it("engine off: accounts, numbers and stored keys still show; Paper account 1 says Engine offline", async () => {
    useStore.setState({ engineServing: false });
    await renderCard();
    const p1 = area("Paper account 1");
    expect(within(p1).getByRole("status")).toHaveTextContent("Engine offline");
    await waitFor(() => expect(number("Paper account 1")).toBe("••••1111"));
    expect(number("Live")).toBe("••••9999");
    expect(number("Paper account 2")).toBe("••••2222");
    await waitFor(() => expect(within(p1).getByText("Keys stored")).toBeInTheDocument());
  });

  it("strategies off: Paper 2/3 visible but dimmed, inert, aria-disabled, with the link to enable them", async () => {
    fn("getSetupState").mockResolvedValue({ strategyProfilesEnabled: false });
    fn("getFleetSession").mockResolvedValue({ fleetActive: false });
    await renderCard();
    await waitFor(() => expect(screen.getByRole("link", { name: "Enable strategies on Trading → Strategies" })).toBeInTheDocument());
    for (const name of ["Paper account 2", "Paper account 3"]) {
      const off = area(name).closest("[aria-disabled]")!;
      expect(off).toHaveAttribute("aria-disabled", "true");
      expect(off).toHaveAttribute("inert");
      expect(off.className).toMatch(/opacity-40/);
    }
    const link = screen.getByRole("link", { name: "Enable strategies on Trading → Strategies" });
    expect(link).toHaveAttribute("href", "#trading-strategies");
    expect(link).toHaveClass("btn-link");
    expect(area("Paper account 1").closest("[aria-disabled]")).toBeNull();
  });

  it("no choice, but no fleet in this session: strategies count as off", async () => {
    fn("getFleetSession").mockResolvedValue({ fleetActive: false });
    await renderCard();
    await waitFor(() => expect(area("Paper account 2").closest("[aria-disabled]")).not.toBeNull());
  });

  it("strategies on: no dimming, no enable link", async () => {
    await renderCard();
    await waitFor(() => expect(fn("getFleetSession")).toHaveBeenCalled());
    expect(area("Paper account 2").closest("[aria-disabled]")).toBeNull();
    expect(screen.queryByRole("link", { name: /enable strategies/i })).toBeNull();
  });

  it("#5187: Paper 3 not connected: Connect opens the key form here, no link to Trading", async () => {
    await renderCard();
    const p3 = area("Paper account 3");
    expect(p3.querySelector("input")).toBeNull();
    expect(screen.queryByRole("link", { name: /Connect on Trading/ })).toBeNull();
    fireEvent.click(within(p3).getByRole("button", { name: "Connect" }));
    expect(within(p3).getByLabelText("API key ID")).toHaveAttribute("autocomplete", "off");
    expect(within(p3).getByLabelText("Secret key")).toHaveAttribute("type", "password");
    expect(area("Paper account 2").querySelector("input")).toBeNull();
    expect(within(area("Paper account 2")).getByText("Running")).toBeInTheDocument();
    expect(within(area("Paper account 2")).getByRole("button", { name: "Disconnect" })).toHaveClass("btn-link");
  });

  it("#5187: Paper account 1 is renamed in its area, the shell gets paper1", async () => {
    const renamePaperSlot = vi.fn().mockResolvedValue({ ok: true });
    shell.renamePaperSlot = renamePaperSlot;
    await renderCard();
    fireEvent.click(within(area("Paper account 1")).getByRole("button", { name: "Rename" }));
    fireEvent.change(within(area("Paper account 1")).getByLabelText("Account name"), { target: { value: "Main paper" } });
    fn("listPaperSlots").mockResolvedValue(SLOTS.map((s) => (s.slot === "paper1" ? { ...s, name: "Main paper" } : s)));
    fireEvent.click(within(area("Paper account 1")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(renamePaperSlot).toHaveBeenCalledWith("paper1", "Main paper"));
    expect(await screen.findByRole("group", { name: "Main paper" })).toBeInTheDocument();
  });

  it("#5187: restart-required locks Rename and Disconnect of Paper 2/3 and links to the restart", async () => {
    shell.renamePaperSlot = vi.fn().mockResolvedValue({ ok: true });
    fn("getSetupState").mockResolvedValue({ strategyProfilesEnabled: true });
    fn("getFleetSession").mockResolvedValue({ fleetActive: false });
    await renderCard();
    const link = await screen.findByRole("link", { name: "Restart on Trading → Strategies" });
    expect(link).toHaveAttribute("href", "#trading-strategies");
    const p2 = area("Paper account 2");
    expect(within(p2).getByRole("button", { name: "Rename" })).toBeDisabled();
    expect(within(p2).getByRole("button", { name: "Disconnect" })).toBeDisabled();
    expect(within(area("Paper account 3")).getByRole("button", { name: "Connect" })).toBeDisabled();
  });

  it("the operator's slot name is shown instead of Paper account N", async () => {
    fn("listPaperSlots").mockResolvedValue(SLOTS.map((s) => (s.slot === "paper2" ? { ...s, name: "Momentum" } : s)));
    render(<TradingKeysCard />);
    expect(await screen.findByRole("group", { name: "Momentum" })).toBeInTheDocument();
  });

  it("listAccounts rejects: numbers of Paper 1 and Live show —, with a console.warn", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    fn("listAccounts").mockRejectedValue(new Error("ipc down"));
    await renderCard();
    await waitFor(() => expect(warn).toHaveBeenCalledWith(expect.stringMatching(/listAccounts/), expect.anything()));
    expect(number("Paper account 1")).toBe("—");
    expect(number("Live")).toBe("—");
    expect(number("Paper account 2")).toBe("••••2222");
  });

  it("listPaperSlots rejects: an error state with Retry, which reads again", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    fn("listPaperSlots").mockRejectedValueOnce(new Error("ipc down")).mockResolvedValue(SLOTS);
    render(<TradingKeysCard />);
    const retry = await screen.findByRole("button", { name: "Retry" });
    expect(warn).toHaveBeenCalledWith(expect.stringMatching(/listPaperSlots/), expect.anything());
    expect(screen.queryByRole("group", { name: "Paper account 2" })).toBeNull();
    fireEvent.click(retry);
    expect(await screen.findByRole("group", { name: "Paper account 2" })).toBeInTheDocument();
  });

  it("Junior: the live slot shows a neutral Senior .pill, no emoji", async () => {
    useStore.setState({ allowLive: false });
    await renderCard();
    expect(within(area("Live")).getByText("Senior")).toHaveClass("pill");
    expect(document.body.textContent).not.toContain("🔒");
  });

  it("reads nothing from the engine: no connect, no engine call", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await renderCard();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(fn("connectPaperSlot")).not.toHaveBeenCalled();
  });
});


describe("TradingKeysCard — Paper/Live switch of the main engine (#5178)", () => {
  beforeEach(() => {
    isDesktop.mockReset().mockReturnValue(true);
    getKeychainStatus.mockReset().mockResolvedValue(LIVE_KEYS);
    useStore.setState({
      allowLive: true,
      paperTrading: true,
      systemHalted: false,
      modeSwitch: null,
      switchReconciling: false,
      switchConfirmFailed: false,
      switchError: null,
      engineServing: true,
      requestLiveConsent: false,
      requestSwitchToPaper: false,
      tier: "PRO",
      canUpgrade: false,
    });
    shell = {
      isDesktop: true,
      listAccounts: vi.fn().mockResolvedValue(ACCOUNTS),
      listPaperSlots: vi.fn().mockResolvedValue(SLOTS),
      getKeychainStatus: vi.fn().mockResolvedValue(LIVE_KEYS),
      getSetupState: vi.fn().mockResolvedValue({}),
      getFleetSession: vi.fn().mockResolvedValue({ fleetActive: true }),
      connectPaperSlot: vi.fn().mockResolvedValue({ ok: true, accountMasked: "••••3333" }),
    };
    (window as unknown as { aaagents: unknown }).aaagents = shell;
  });
  afterEach(() => {
    delete (window as unknown as { aaagents?: unknown }).aaagents;
  });

  it("the Live pill carries exactly the classes of the sidebar's 'Live Trading' reference control", async () => {
    isDesktop.mockReturnValue(false);
    const { unmount } = render(<Sidebar />);
    const reference = classSet(screen.getByRole("button", { name: /start live trading/i }));
    unmount();

    isDesktop.mockReturnValue(true);
    const { container } = render(<TradingKeysCard />);
    const pill = await within(areaByDataAccount(container, "live")).findByRole("button", {
      name: "ENABLE LIVE TRADING",
    });
    expect(classSet(pill)).toEqual(reference);
  });

  it("main engine on paper + live keys stored: the pill raises the global consent signal", async () => {
    const { container } = render(<TradingKeysCard />);
    const pill = await within(areaByDataAccount(container, "live")).findByRole("button", {
      name: "ENABLE LIVE TRADING",
    });
    expect(pill).not.toBeDisabled();
    fireEvent.click(pill);
    expect(useStore.getState().requestLiveConsent).toBe(true);
    expect(useStore.getState().requestSwitchToPaper).toBe(false);
    expect(screen.queryByRole("button", { name: /switch main engine to paper/i })).toBeNull();
  });

  it("no live keys stored: no live pill (the area shows the key form instead)", async () => {
    getKeychainStatus.mockResolvedValue({});
    const { container } = render(<TradingKeysCard />);
    await waitFor(() => expect(getKeychainStatus).toHaveBeenCalled());
    await waitFor(() => expect(within(areaByDataAccount(container, "live")).getByLabelText("alpaca-live-key-id")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "ENABLE LIVE TRADING" })).toBeNull();
  });

  it("main engine on live: Paper account 1 offers the .btn way back, raising requestSwitchToPaper", async () => {
    useStore.setState({ paperTrading: false });
    const { container } = render(<TradingKeysCard />);
    const back = await within(areaByDataAccount(container, "paper1")).findByRole("button", {
      name: /switch main engine to paper/i,
    });
    expect(back.classList.contains("btn")).toBe(true);
    expect(back.className).not.toContain("rounded-full");
    fireEvent.click(back);
    expect(useStore.getState().requestSwitchToPaper).toBe(true);
    expect(useStore.getState().requestLiveConsent).toBe(false);
    expect(screen.queryByRole("button", { name: "ENABLE LIVE TRADING" })).toBeNull();
  });

  it("the active account carries the 'Main engine' StatusDot (state, not a .pill)", async () => {
    const { container, unmount } = render(<TradingKeysCard />);
    const dot = await within(areaByDataAccount(container, "paper1")).findByText("Main engine");
    expect(dot.closest(".pill")).toBeNull();
    expect(within(areaByDataAccount(container, "live")).queryByText("Main engine")).toBeNull();
    unmount();

    useStore.setState({ paperTrading: false });
    const second = render(<TradingKeysCard />);
    expect(await within(areaByDataAccount(second.container, "live")).findByText("Main engine")).toBeTruthy();
    expect(within(areaByDataAccount(second.container, "paper1")).queryByText("Main engine")).toBeNull();
  });

  it("unknown account state: the pill is not triggerable and nobody is marked main engine", async () => {
    useStore.setState({ paperTrading: null });
    const { container } = render(<TradingKeysCard />);
    const pill = await within(areaByDataAccount(container, "live")).findByRole("button", { name: "ENABLE LIVE TRADING" });
    expect(pill).toBeDisabled();
    fireEvent.click(pill);
    expect(useStore.getState().requestLiveConsent).toBe(false);
    expect(screen.queryByText("Main engine")).toBeNull();
  });

  it("a switch in flight (overlay up or reconcile running): neither direction is triggerable", async () => {
    useStore.setState({ modeSwitch: { to: "live", startedAt: 1 } });
    const { container, unmount } = render(<TradingKeysCard />);
    const pill = await within(areaByDataAccount(container, "live")).findByRole("button", { name: "ENABLE LIVE TRADING" });
    expect(pill).toBeDisabled();
    fireEvent.click(pill);
    expect(useStore.getState().requestLiveConsent).toBe(false);
    unmount();

    useStore.setState({ modeSwitch: null, switchReconciling: true, paperTrading: false });
    const second = render(<TradingKeysCard />);
    const back = await within(areaByDataAccount(second.container, "paper1")).findByRole("button", {
      name: /switch main engine to paper/i,
    });
    expect(back).toBeDisabled();
    fireEvent.click(back);
    expect(useStore.getState().requestSwitchToPaper).toBe(false);
  });

  it("kill switch active (system halted): the live pill is not triggerable", async () => {
    useStore.setState({ systemHalted: true });
    const { container } = render(<TradingKeysCard />);
    const pill = await within(areaByDataAccount(container, "live")).findByRole("button", { name: "ENABLE LIVE TRADING" });
    expect(pill).toBeDisabled();
    fireEvent.click(pill);
    expect(useStore.getState().requestLiveConsent).toBe(false);
  });

  it("Junior (allow_live=false): no live action at all, the live area stays locked", async () => {
    useStore.setState({ allowLive: false });
    const { container } = render(<TradingKeysCard />);
    await waitFor(() => expect(within(areaByDataAccount(container, "live")).getByText(/unlocks with senior/i)).toBeTruthy());
    expect(screen.queryByRole("button", { name: "ENABLE LIVE TRADING" })).toBeNull();
    expect(within(areaByDataAccount(container, "live")).queryByLabelText("alpaca-live-key-id")).toBeNull();
  });

  it("LSR R4: while the reconcile runs, the active account shows the transient confirm", async () => {
    useStore.setState({ switchReconciling: true });
    const { container } = render(<TradingKeysCard />);
    expect(
      await within(areaByDataAccount(container, "paper1")).findByText(/confirming the account with the engine/i),
    ).toBeTruthy();
    expect(screen.queryByText(/simulated trading, no real capital at risk/i)).toBeNull();
  });

  it("LSR R4: a reconcile give-up with no serving engine says 'Account unconfirmed'", async () => {
    useStore.setState({ switchConfirmFailed: true, engineServing: false });
    render(<TradingKeysCard />);
    expect(await screen.findByText(/account unconfirmed/i)).toBeTruthy();
  });

  it("LSR R4: once the engine serves again the honest state line returns (no stuck 'unconfirmed')", async () => {
    useStore.setState({ switchConfirmFailed: true, engineServing: true, paperTrading: false });
    const { container } = render(<TradingKeysCard />);
    expect(
      await within(areaByDataAccount(container, "live")).findByText(/orders execute with real money/i),
    ).toBeTruthy();
    expect(screen.queryByText(/account unconfirmed/i)).toBeNull();
  });

  it("the last switch error (published by the global modal) is shown in the card", async () => {
    useStore.setState({ switchError: "Couldn't switch back to paper — the engine is offline." });
    render(<TradingKeysCard />);
    expect(await screen.findByText(/couldn't switch back to paper/i)).toBeTruthy();
  });
});
