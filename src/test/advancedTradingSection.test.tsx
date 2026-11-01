import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";

/**
 * UXC-1 S5 (#3158) — "Advanced" section: collapsed-by-default expert area inside the
 * Trading-controls card (owner decision 02.09.: no separate risk-limits card).
 * Groups/fields render data-driven from advancedTradingFields.ts; values come from
 * the S2 GET (engine-effective), edits stay pending until the S3 WORM apply.
 */

vi.mock("../lib/api", () => ({
  getTradingSettings: vi.fn(),
}));

import { getTradingSettings } from "../lib/api";
import { AdvancedTradingSection } from "../console/desktop/AdvancedTradingSection";
import { ADVANCED_GROUPS } from "../console/desktop/advancedTradingFields";
import { payload, setBridge } from "./advancedTradingFixtures";

const mockGet = getTradingSettings as unknown as ReturnType<typeof vi.fn>;

describe("Advanced trading section (UXC-1 S5)", () => {
  beforeEach(() => {
    setBridge();
    mockGet.mockResolvedValue(payload());
  });
  afterEach(() => {
    (window as unknown as { aaagents?: unknown }).aaagents = undefined;
    vi.clearAllMocks();
  });

  it("S1: collapsed by default — expert fields only after an explicit expand", async () => {
    render(<AdvancedTradingSection />);
    const toggle = await waitFor(() => screen.getByRole("button", { name: /advanced/i }));
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByLabelText("Buy threshold")).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    await waitFor(() => screen.getByLabelText("Buy threshold"));
  });

  it("S1b: the collapsed header advertises the parameter count and a content hint (discoverability)", async () => {
    render(<AdvancedTradingSection />);
    const toggle = await waitFor(() => screen.getByRole("button", { name: /advanced/i }));
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // A parameter-count badge + a hint of what's inside — no longer a bare "Show".
    expect(toggle.textContent).toMatch(/\d+ parameters/);
    expect(toggle.textContent?.toLowerCase()).toContain("sizing");
  });

  it("S2 #5003: expanded, the six groups render in the order of the decision flow", async () => {
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    for (const g of ADVANCED_GROUPS) {
      expect(await waitFor(() => screen.getByText(g.title))).toBeTruthy();
    }
    expect(ADVANCED_GROUPS).toHaveLength(6);
    const shown = Array.from(document.querySelectorAll(".surface-flat > .sublabel")).map(
      (e) => e.textContent,
    );
    expect(shown).toEqual([
      "Candidates",
      "Decision",
      "Position sizing",
      "Portfolio limits",
      "Buy pacing",
      "Exits",
    ]);
  });

  it("S7 #5003: consensus retention shows under Decision, the displacement switch under Exits", async () => {
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    await waitFor(() => screen.getByText("Exits"));
    const box = (title: string) => screen.getByText(title).parentElement as HTMLElement;
    expect(within(box("Exits")).getByText("Displacement exits")).toBeTruthy();
    // the consensus-retention slider is always shown (it also governs rotation/overflow)
    expect(within(box("Decision")).getByLabelText("Consensus retention")).toBeTruthy();
  });

  it("S8 #5003: the fixed limits stay with the stops, ahead of trailing", async () => {
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    const guard = await waitFor(() => screen.getByTestId("guardrail-hard-stop"));
    const panic = document.querySelector('[data-key="PANIC_PROTECTION_ENABLED"]') as Element;
    const trailing = document.querySelector('[data-key="EXIT_TRAIL_PROFILE"]') as Element;
    expect(panic.compareDocumentPosition(guard) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(guard.compareDocumentPosition(trailing) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("guardrail-loss-time-tiers")).toBeTruthy();
  });

  describe("sizing references (#5046)", () => {
    const expand = async () => {
      const view = render(<AdvancedTradingSection />);
      fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
      await waitFor(() => screen.getByTestId("sizing-references"));
      return view;
    };
    const open = async (engine: Record<string, string | undefined>, positions?: number) => {
      mockGet.mockResolvedValue(payload(engine, positions));
      await expand();
      return screen.getByTestId("sizing-references");
    };
    const row = (id: string) => screen.getByTestId(`sizing-ref-${id}`);
    const ALL_ON = {
      VOL_TARGETING_SIZING_ENABLED: "true",
      VOL_TARGET_DAILY_VOL: "0.0150",
      VIX_RISK_AGENT_ENABLED: "true",
      VIX_SIZE_INFLUENCE: "0.75",
      SKEW_SIZE_TILT_ENABLED: "true",
      SKEW_SIZE_TILT_CAP: "0.10",
    };

    it("R1: each control on another card shows its value in its unit, under the sizing group", async () => {
      const block = await open(ALL_ON, 12);
      expect(screen.getByText("Position sizing").parentElement?.contains(block)).toBe(true);
      expect(row("positions").textContent).toContain("12 positions");
      expect(row("vol-target").textContent).toContain("1.5% per day");
      expect(row("vix").textContent).toContain("75%");
      expect(row("skew").textContent).toContain("±10%");
    });

    it("R1b: the percent is the decimal shift, not a float product", async () => {
      await open({ ...ALL_ON, VOL_TARGET_DAILY_VOL: "0.0700", SKEW_SIZE_TILT_CAP: "0.07" });
      expect(row("vol-target").textContent).toContain("7% per day");
      expect(row("skew").textContent).toContain("±7%");
    });

    it("R2: a control that is off says so", async () => {
      await open({
        ...ALL_ON,
        VOL_TARGETING_SIZING_ENABLED: "false",
        VIX_RISK_AGENT_ENABLED: "false",
        SKEW_SIZE_TILT_ENABLED: "false",
      });
      for (const id of ["vol-target", "vix", "skew"]) expect(row(id).textContent).toContain("Off");
    });

    it("R2b: with the IV forecast off, the VIX strength is not called a size input", async () => {
      mockGet.mockResolvedValue({ ...payload(ALL_ON), implied_vol_forecast_enabled: false });
      await expand();
      expect(row("vix").textContent).toContain("Not a sizing input");
      expect(row("vix").textContent).not.toContain("75%");
    });

    it("R2c: with volatility sizing off the scaler never reads the VIX influence", async () => {
      await open({ ...ALL_ON, VOL_TARGETING_SIZING_ENABLED: "false" });
      expect(row("vix").textContent).toContain("Off");
      expect(row("vix").textContent).not.toContain("75%");
    });

    it("R3: a value the engine did not send shows a dash, never a guess", async () => {
      mockGet.mockResolvedValue({
        ...payload({
          ...ALL_ON,
          VOL_TARGET_DAILY_VOL: undefined,
          VIX_SIZE_INFLUENCE: "",
          SKEW_SIZE_TILT_CAP: "n/a",
        }),
        effective_positions: undefined,
      });
      await expand();
      for (const id of ["vol-target", "positions", "vix", "skew"]) {
        expect(row(id).textContent).toContain("—");
      }
    });

    it("R3b: a switch the engine did not send shows a dash, not Off", async () => {
      await open({
        ...ALL_ON,
        VOL_TARGETING_SIZING_ENABLED: undefined,
        VIX_RISK_AGENT_ENABLED: undefined,
        SKEW_SIZE_TILT_ENABLED: undefined,
      });
      for (const id of ["vol-target", "vix", "skew"]) {
        expect(row(id).textContent).toContain("—");
        expect(row(id).textContent).not.toContain("Off");
      }
    });

    it("R4: it is a reference, not a second place to write", async () => {
      const block = await open({});
      expect(block.querySelector("input, select, textarea")).toBeNull();
      const ids = Array.from(block.querySelectorAll<HTMLElement>("[data-testid]")).map(
        (r) => r.dataset.testid,
      );
      expect(ids).toEqual([
        "sizing-ref-vol-target",
        "sizing-ref-positions",
        "sizing-ref-vix",
        "sizing-ref-skew",
      ]);
    });

    it("R5: the Agents link goes to the Agents page, the same-card link scrolls to the control", async () => {
      await open({});
      expect(row("vix").querySelector("a")?.getAttribute("href")).toBe("#trading-agents");
      expect(row("skew").querySelector("a")?.getAttribute("href")).toBe("#trading-agents");
      const jump = (id: string, rowId: string) => {
        const target = document.createElement("div");
        target.id = id;
        target.scrollIntoView = vi.fn();
        document.body.appendChild(target);
        fireEvent.click(row(rowId).querySelector("button") as HTMLElement);
        expect(target.scrollIntoView).toHaveBeenCalled();
        target.remove();
      };
      jump("max-positions", "positions");
      jump("volatility-sizing", "vol-target");
    });

    it("R6: the reference texts carry no advice and no internal term", async () => {
      const block = await open({});
      expect(block.textContent).not.toMatch(/recommend|\bbest\b|\bbook\b|ADR-/i);
    });

    it("R7: a new refresh token shows the engine's new value and keeps an open change", async () => {
      mockGet.mockResolvedValue(payload(ALL_ON));
      const view = await expand();
      fireEvent.change(screen.getByLabelText("Buy threshold"), { target: { value: "0.7" } });
      mockGet.mockResolvedValue(payload({ ...ALL_ON, VOL_TARGET_DAILY_VOL: "0.0300" }, 15));

      view.rerender(<AdvancedTradingSection refreshToken={1} />);

      await waitFor(() => expect(row("vol-target").textContent).toContain("3% per day"));
      expect(row("positions").textContent).toContain("15 positions");
      expect((screen.getByLabelText("Buy threshold") as HTMLInputElement).value).toBe("0.7");
      expect(screen.getByTestId("pending-changes-advanced").textContent).toContain("Buy threshold");
    });

    it("R7b: without a new token nothing is read twice", async () => {
      await open(ALL_ON);
      expect(mockGet).toHaveBeenCalledTimes(1);
    });

    it("R8: a failed reload shows dashes in every row, never the old value", async () => {
      mockGet.mockResolvedValue(payload(ALL_ON, 12));
      const view = await expand();
      mockGet.mockRejectedValue(new Error("engine down"));

      view.rerender(<AdvancedTradingSection refreshToken={1} />);

      await waitFor(() => expect(row("positions").textContent).toContain("—"));
      for (const id of ["vol-target", "vix", "skew"]) {
        expect(row(id).textContent).toContain("—");
        expect(row(id).textContent).not.toContain("%");
      }
    });

    it("R8b: the engine answers late after its restart, and the block asks again", async () => {
      mockGet.mockResolvedValue(payload(ALL_ON));
      const view = await expand();
      vi.useFakeTimers();
      try {
        mockGet.mockRejectedValueOnce(new Error("engine still booting"));
        mockGet.mockResolvedValue(payload({ ...ALL_ON, VOL_TARGET_DAILY_VOL: "0.0300" }));

        view.rerender(<AdvancedTradingSection refreshToken={1} />);
        await act(() => vi.advanceTimersByTimeAsync(0));
        expect(row("vol-target").textContent).toContain("—");

        await act(() => vi.advanceTimersByTimeAsync(3000));
        expect(row("vol-target").textContent).toContain("3% per day");
      } finally {
        vi.useRealTimers();
      }
    });
  });

  it("S3: no non-scope field is editable; the hard stop shows as a read-only guardrail", async () => {
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    await waitFor(() => screen.getByLabelText("Buy threshold"));

    // #3220/#3221: COMPLIANCE_MAX_DAILY_TRADES ist seit dem Owner-Entscheid 05.09.
    // ein einstellbares Betriebslimit innerhalb des ratifizierten Ceilings (50) -
    // keine Leitplanke mehr, also hier bewusst nicht mehr gelistet.
    for (const banned of [
      "TRAILING_STOP_PCT",
      "HARD_STOP_LOSS_PCT",
    ]) {
      expect(document.querySelector(`[data-key="${banned}"] input`)).toBeNull();
      expect(document.querySelector(`input[aria-label="${banned}"]`)).toBeNull();
    }
    const guard = screen.getByTestId("guardrail-hard-stop");
    expect(guard.textContent).toContain("8.0");
    expect(guard.querySelector("input")).toBeNull();
  });

  it("S4: values shown are the ENGINE-effective ones from the GET, not UI state", async () => {
    mockGet.mockResolvedValue(payload({ STOP_LOSS_PCT: "5.5" }));
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    const stop = (await waitFor(() => screen.getByLabelText("Broker stop distance"))) as HTMLInputElement;
    expect(stop.value).toBe("5.5");
    // engine deviation vs shipped default carries a Custom badge
    await waitFor(() => screen.getByText("Custom"));
    expect(screen.queryByText("Changed")).toBeNull();
  });

  it("S6: dependent rotation fields are HIDDEN while rotation exits are off (no inert knobs)", async () => {
    mockGet.mockResolvedValue(payload({ ROTATION_EXIT_ENABLED: "false" }));
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    // the group and its parent toggle still render
    await waitFor(() => screen.getByText("Rotation exits"));
    // the three dependents are gone entirely (not merely disabled)
    expect(screen.queryByText("Max exits per cycle")).toBeNull();
    expect(screen.queryByText("Max exits per session")).toBeNull();
    expect(screen.queryByText("Ranking data max age")).toBeNull();
    expect(screen.queryByLabelText("Max exits per cycle")).toBeNull();
  });

  it("S6b: the dependent rotation fields reappear when rotation exits are on", async () => {
    mockGet.mockResolvedValue(payload({ ROTATION_EXIT_ENABLED: "true" }));
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    await waitFor(() => screen.getByLabelText("Max exits per cycle"));
    expect(screen.getByLabelText("Max exits per session")).toBeTruthy();
    expect(screen.getByLabelText("Ranking data max age")).toBeTruthy();
  });

  it("S6c: toggling rotation off live hides the dependents at once (pending-aware)", async () => {
    mockGet.mockResolvedValue(payload({ ROTATION_EXIT_ENABLED: "true" }));
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    await waitFor(() => screen.getByLabelText("Max exits per cycle"));
    // flip the parent toggle to Off (segmented control → click the "Off" segment)
    const rotationToggle = screen.getByRole("group", { name: "Rotation exits" });
    fireEvent.click(within(rotationToggle).getByRole("button", { name: "Off" }));
    await waitFor(() => expect(screen.queryByLabelText("Max exits per cycle")).toBeNull());
  });

  it("S8: without a sizing preview the block stays neutral and the fields stay usable (#5005)", async () => {
    // The api mock above has no getSizingPreview: the call fails like an unreachable engine.
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    await waitFor(() => screen.getByText("No model calculation available."));
    const slider = screen.getByLabelText("Buy threshold") as HTMLInputElement;
    expect(slider.disabled).toBe(false);
    const next = String(Number(slider.value) + Number(slider.step || 0.01));
    fireEvent.change(slider, { target: { value: next } });
    await waitFor(() => screen.getByTestId("pending-changes-advanced"));
  });
});

describe("Sizing method in Advanced (#4987)", () => {
  const onApply = vi.fn();
  const open = async (engine: Record<string, string> = {}) => {
    mockGet.mockResolvedValue(payload(engine));
    render(<AdvancedTradingSection onApply={onApply} />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    return (await waitFor(() => screen.getByLabelText("Sizing method"))) as HTMLSelectElement;
  };
  const slider = (label: string) => screen.getByLabelText(label) as HTMLInputElement;
  const hintOf = (key: string) =>
    document.querySelector(`[data-key="${key}"] [data-copy="inert-hint"]`)?.textContent;

  beforeEach(() => setBridge());
  afterEach(() => {
    (window as unknown as { aaagents?: unknown }).aaagents = undefined;
    vi.clearAllMocks();
  });

  it("M1: the active method comes from the engine", async () => {
    const select = await open({ CLEAN_WEIGHT_SIZING: "b" });
    expect(select.value).toBe("b");
    expect(select.selectedOptions[0].textContent).toBe("Equal weight with risk tilts");
  });

  it("M2: in b, minimum and cap are locked and name the method they work in", async () => {
    await open({ CLEAN_WEIGHT_SIZING: "b" });
    expect(slider("Minimum position size").disabled).toBe(true);
    expect(slider("Sizing cap").disabled).toBe(true);
    expect(slider("Coverage sizing strength").disabled).toBe(false);
    expect(slider("Maximum position size").disabled).toBe(false);
    expect(hintOf("MIN_POSITION_PERCENT")).toContain("Takes effect in Conviction");
    expect(hintOf("MAX_POSITION_PERCENT_SIZING")).toContain("Takes effect in Conviction");
    expect(hintOf("COVERAGE_SIZING_STRENGTH")).toBeUndefined();
  });

  it("M3: in a, coverage is locked as well", async () => {
    await open({ CLEAN_WEIGHT_SIZING: "a" });
    expect(slider("Coverage sizing strength").disabled).toBe(true);
    expect(hintOf("COVERAGE_SIZING_STRENGTH")).toContain(
      "Takes effect in Conviction or Equal weight with risk tilts",
    );
  });

  it("M4: a locked row stays visible, with its value", async () => {
    await open({ CLEAN_WEIGHT_SIZING: "b", MIN_POSITION_PERCENT: "0.04" });
    expect(slider("Minimum position size").value).toBe("0.04");
  });

  it("M5: switching the pending method locks at once", async () => {
    const select = await open({ CLEAN_WEIGHT_SIZING: "b" });
    expect(slider("Coverage sizing strength").disabled).toBe(false);
    fireEvent.change(select, { target: { value: "a" } });
    expect(slider("Coverage sizing strength").disabled).toBe(true);
  });

  it("M6: Conviction can be chosen", async () => {
    const select = await open({ CLEAN_WEIGHT_SIZING: "b" });
    const options = within(select).getAllByRole("option") as HTMLOptionElement[];
    expect(options.map((o) => o.value)).toEqual(["off", "a", "b"]);
    expect(options.map((o) => o.disabled)).toEqual([false, false, false]);
  });

  // fireEvent.change reaches a disabled option too; a browser does not offer it.
  const chooseConviction = (select: HTMLSelectElement) => {
    const option = within(select).getByRole("option", { name: "Conviction" }) as HTMLOptionElement;
    expect(option.disabled).toBe(false);
    fireEvent.change(select, { target: { value: option.value } });
  };

  it("M6b: choosing Conviction applies exactly the method", async () => {
    chooseConviction(await open({ CLEAN_WEIGHT_SIZING: "b" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply changes" }));
    expect(onApply).toHaveBeenCalledWith({ CLEAN_WEIGHT_SIZING: "off" });
  });

  it("M6c: choosing Conviction frees minimum and sizing cap at once", async () => {
    chooseConviction(await open({ CLEAN_WEIGHT_SIZING: "b" }));
    expect(slider("Minimum position size").disabled).toBe(false);
    expect(slider("Sizing cap").disabled).toBe(false);
  });

  it("M6d: without a choice the method stays out of the apply diff", async () => {
    await open({ CLEAN_WEIGHT_SIZING: "b" });
    fireEvent.change(slider("Maximum position size"), { target: { value: "0.28" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply changes" }));
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(onApply.mock.calls[0][0]).not.toHaveProperty("CLEAN_WEIGHT_SIZING");
  });

  it("M7: an engine already in off is shown as Conviction, with nothing locked", async () => {
    const select = await open({ CLEAN_WEIGHT_SIZING: "off" });
    expect(select.value).toBe("off");
    expect(select.selectedOptions[0].textContent).toBe("Conviction");
    expect(slider("Minimum position size").disabled).toBe(false);
    expect(slider("Sizing cap").disabled).toBe(false);
  });

  it("M8: the apply diff carries the chosen method and only that", async () => {
    const select = await open({ CLEAN_WEIGHT_SIZING: "b" });
    fireEvent.change(select, { target: { value: "a" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply changes" }));
    expect(onApply).toHaveBeenCalledWith({ CLEAN_WEIGHT_SIZING: "a" });
  });

  it("M9: the cap message does not tell the user to raise a locked cap", async () => {
    await open({ CLEAN_WEIGHT_SIZING: "b" });
    fireEvent.change(slider("Maximum position size"), { target: { value: "0.35" } });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("sizing cap");
    expect(alert.textContent).not.toMatch(/raise/i);
  });
});

describe("Values in their unit (#4999)", () => {
  const onApply = vi.fn();
  const open = async (engine: Record<string, string> = {}) => {
    mockGet.mockResolvedValue(payload(engine));
    render(<AdvancedTradingSection onApply={onApply} />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    await waitFor(() => screen.getByLabelText("Sizing method"));
  };
  const shown = (key: string) =>
    document.querySelector(`[data-key="${key}"] [data-copy="value"]`)?.textContent;
  const entry = (label: string) => screen.getByLabelText(`${label} value`) as HTMLInputElement;
  const type = (label: string, text: string) => {
    fireEvent.change(entry(label), { target: { value: text } });
    fireEvent.blur(entry(label));
  };
  const apply = () => fireEvent.click(screen.getByRole("button", { name: "Apply changes" }));

  beforeEach(() => setBridge());
  afterEach(() => {
    (window as unknown as { aaagents?: unknown }).aaagents = undefined;
    vi.clearAllMocks();
  });

  it("U1: 0.05 reads 5% and 10000 reads $10,000", async () => {
    await open({ CLEAN_WEIGHT_SIZING: "off" });
    expect(shown("MIN_POSITION_PERCENT")).toBe("5%");
    expect(shown("COMPLIANCE_MAX_ORDER_VALUE")).toBe("$10,000");
    expect(shown("GLOBAL_BUY_COOLDOWN_MINUTES")).toBe("30 min");
    expect(shown("REENTRY_LOCKOUT_DAYS")).toBe("1 trading day");
    expect(shown("REGIME_RISKOFF_PERCENTILE")).toBe("75th percentile");
  });

  it("U2: a field without a unit keeps its plain number", async () => {
    await open();
    expect(shown("SIGNAL_BUY_THRESHOLD")).toBe("0.73");
    expect(shown("ROTATION_MAX_EXITS_PER_CYCLE")).toBe("1");
  });

  it("U3: loss tiers and broker stop both read as a distance with a minus; the stored sign stays", async () => {
    await open();
    expect(shown("LOSS_CUT_PCT")).toBe("-6%");
    expect(shown("STOP_LOSS_PCT")).toBe("-7%");
    expect((screen.getByLabelText("Broker stop distance") as HTMLInputElement).value).toBe("7");
  });

  it("U4: 4 entered for the minimum in off is stored as 0.04", async () => {
    await open({ CLEAN_WEIGHT_SIZING: "off" });
    type("Minimum position size", "4");
    apply();
    expect(onApply).toHaveBeenCalledWith({ MIN_POSITION_PERCENT: "0.04" });
  });

  it("U5: the entry takes a decimal comma and keeps the stored sign of the broker stop", async () => {
    await open();
    type("Broker stop distance", "-5,5");
    apply();
    expect(onApply).toHaveBeenCalledWith({ STOP_LOSS_PCT: "5.5" });
  });

  it("U6: the entry rounds to the registry's decimals from meta", async () => {
    mockGet.mockResolvedValue({
      ...payload(),
      meta: [{ key: "MAX_POSITION_PERCENT", kind: "float", default: "0.25", min: 0.05, max: 0.4, decimals: 2 }],
    });
    render(<AdvancedTradingSection onApply={onApply} />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    await waitFor(() => screen.getByLabelText("Sizing method"));
    type("Maximum position size", "20,4");
    apply();
    expect(onApply).toHaveBeenCalledWith({ MAX_POSITION_PERCENT: "0.2" });
  });

  it("U7: nothing is taken over while typing, only on leaving or Enter", async () => {
    await open();
    fireEvent.change(entry("Maximum position size"), { target: { value: "2" } });
    expect(shown("MAX_POSITION_PERCENT")).toBe("25%");
    fireEvent.change(entry("Maximum position size"), { target: { value: "20" } });
    fireEvent.keyDown(entry("Maximum position size"), { key: "Enter" });
    expect(shown("MAX_POSITION_PERCENT")).toBe("20%");
  });

  it("U8: a value outside the bounds is refused with the bounds in their unit and stays visible", async () => {
    await open();
    type("Maximum position size", "45");
    expect(entry("Maximum position size").value).toBe("45");
    expect(entry("Maximum position size").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByRole("alert").textContent).toContain("5%");
    expect(screen.getByRole("alert").textContent).toContain("40%");
    expect(shown("MAX_POSITION_PERCENT")).toBe("25%");
    apply();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("U9: text that is no number is refused the same way", async () => {
    await open();
    type("Maximum position size", "abc");
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(shown("MAX_POSITION_PERCENT")).toBe("25%");
  });

  it("U10: the slider replaces a refused entry", async () => {
    await open();
    type("Maximum position size", "45");
    fireEvent.change(screen.getByLabelText("Maximum position size"), { target: { value: "0.2" } });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(entry("Maximum position size").value).toBe("20");
  });

  it("U11: Default settings replaces an entry in progress", async () => {
    await open({ MAX_POSITION_PERCENT: "0.2" });
    type("Maximum position size", "45");
    fireEvent.click(screen.getByRole("button", { name: "Default settings" }));
    await waitFor(() => expect(entry("Maximum position size").value).toBe("25"));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("U12: a locked row locks its entry too", async () => {
    await open({ CLEAN_WEIGHT_SIZING: "b" });
    expect(entry("Minimum position size").disabled).toBe(true);
    expect(entry("Maximum position size").disabled).toBe(false);
  });

  it("U13: pending changes read in the unit, the diff stays the stored value", async () => {
    await open();
    type("Maximum position size", "20");
    const rows = screen.getByTestId("pending-changes-advanced");
    expect(rows.textContent).toContain("25%");
    expect(rows.textContent).toContain("20%");
    expect(rows.textContent).not.toContain("0.2");
  });
});

describe("Default and errors at the field (#5001)", () => {
  const onApply = vi.fn();
  const open = async (engine: Record<string, string> = {}, positions = 10) => {
    mockGet.mockResolvedValue({ ...payload(engine), effective_positions: positions });
    render(<AdvancedTradingSection onApply={onApply} />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    await waitFor(() => screen.getByLabelText("Sizing method"));
  };
  const row = (key: string) => document.querySelector(`[data-key="${key}"]`) as HTMLElement;
  const defaultOf = (key: string) => row(key).querySelector('[data-copy="default"]')?.textContent;
  const reset = (key: string) => within(row(key)).getByRole("button", { name: /^reset/i }) as HTMLButtonElement;
  const alerts = (key: string) => within(row(key)).queryAllByRole("alert").map((a) => a.textContent);
  const apply = () => screen.getByRole("button", { name: "Apply changes" }) as HTMLButtonElement;
  const setSlider = (label: string, value: string) =>
    fireEvent.change(screen.getByLabelText(label), { target: { value } });

  beforeEach(() => setBridge());
  afterEach(() => {
    (window as unknown as { aaagents?: unknown }).aaagents = undefined;
    vi.clearAllMocks();
  });

  it("D1: a field on its default shows neither mark, default nor reset", async () => {
    await open();
    const stop = within(row("STOP_LOSS_PCT"));
    expect(stop.queryByText("Custom")).toBeNull();
    expect(defaultOf("STOP_LOSS_PCT")).toBeUndefined();
    expect(stop.queryByRole("button", { name: /^reset/i })).toBeNull();
  });

  it("D2: a deviating field shows its default in the unit", async () => {
    await open({ MAX_POSITION_PERCENT: "0.2", COMPLIANCE_MAX_ORDER_VALUE: "20000", STOP_LOSS_PCT: "5.5" });
    expect(within(row("MAX_POSITION_PERCENT")).getByText("Custom")).toBeTruthy();
    expect(defaultOf("MAX_POSITION_PERCENT")).toBe("Default: 25%");
    expect(defaultOf("COMPLIANCE_MAX_ORDER_VALUE")).toBe("Default: $10,000");
    expect(defaultOf("STOP_LOSS_PCT")).toBe("Default: -7%");
  });

  it("D3: toggles and selects show the default as they read in the row", async () => {
    await open({ DISPLACEMENT_ENABLED: "false", CLEAN_WEIGHT_SIZING: "a" });
    expect(defaultOf("DISPLACEMENT_ENABLED")).toBe("Default: On");
    expect(defaultOf("CLEAN_WEIGHT_SIZING")).toBe("Default: Equal weight with risk tilts");
  });

  it("D4: reset stages the default as a pending value, nothing is applied", async () => {
    await open({ MAX_POSITION_PERCENT: "0.2" });
    fireEvent.click(reset("MAX_POSITION_PERCENT"));
    expect(onApply).not.toHaveBeenCalled();
    expect(within(row("MAX_POSITION_PERCENT")).queryByText("Custom")).toBeNull();
    expect((screen.getByLabelText("Maximum position size") as HTMLInputElement).value).toBe("0.25");
    fireEvent.click(apply());
    expect(onApply).toHaveBeenCalledWith({ MAX_POSITION_PERCENT: "0.25" });
  });

  it("D5: reset touches only its own field", async () => {
    await open({ MAX_POSITION_PERCENT: "0.2", STOP_LOSS_PCT: "5.5" });
    fireEvent.click(reset("MAX_POSITION_PERCENT"));
    expect(within(row("STOP_LOSS_PCT")).getByText("Custom")).toBeTruthy();
  });

  it("D6: a locked row cannot be reset", async () => {
    await open({ CLEAN_WEIGHT_SIZING: "b", MIN_POSITION_PERCENT: "0.04" });
    expect(reset("MIN_POSITION_PERCENT").disabled).toBe(true);
    expect(reset("MIN_POSITION_PERCENT").getAttribute("aria-label")).toContain("Minimum position size");
    expect(defaultOf("MIN_POSITION_PERCENT")).toBe("Default: 5%");
  });

  it("D7: reset replaces a refused entry", async () => {
    await open({ MAX_POSITION_PERCENT: "0.2" });
    const entry = () => screen.getByLabelText("Maximum position size value") as HTMLInputElement;
    fireEvent.change(entry(), { target: { value: "45" } });
    fireEvent.blur(entry());
    fireEvent.click(reset("MAX_POSITION_PERCENT"));
    expect(entry().value).toBe("25");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("E1: a minimum above the 1/N slot is reported at that field, not below all groups", async () => {
    await open({ CLEAN_WEIGHT_SIZING: "off" }, 20);
    setSlider("Minimum position size", "0.08");
    expect(alerts("MIN_POSITION_PERCENT").join(" ")).toContain("1/20");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByLabelText("Minimum position size value").getAttribute("aria-invalid")).toBe("true");
    expect(apply().disabled).toBe(true);
  });

  it("E2: the minimum keeps both messages when it breaks both of its rules", async () => {
    await open({ CLEAN_WEIGHT_SIZING: "off", MAX_POSITION_PERCENT: "0.06", MIN_POSITION_PERCENT: "0.08" }, 20);
    expect(alerts("MIN_POSITION_PERCENT")).toHaveLength(2);
    expect(alerts("MIN_POSITION_PERCENT").join(" ")).toContain("below the maximum");
    expect(alerts("MIN_POSITION_PERCENT").join(" ")).toContain("1/20");
  });

  it("E3: every cross-field rule lands on the field that can resolve it", async () => {
    // Sell above buy cannot be reached with the sliders' bands, only from the engine.
    await open({ SIGNAL_SELL_THRESHOLD: "0.60", SIGNAL_BUY_THRESHOLD: "0.55" }, 20);
    setSlider("Maximum position size", "0.35");
    setSlider("Round-table candidates", "15");
    expect(alerts("MAX_POSITION_PERCENT").join(" ")).toContain("sizing cap");
    expect(alerts("ROUND_TABLE_TOP_K_EVAL").join(" ")).toContain("(20)");
    expect(alerts("SIGNAL_SELL_THRESHOLD").join(" ")).toContain("buy threshold");
    expect(screen.getAllByRole("alert")).toHaveLength(3);
  });

  it("E4: resolving the error clears the field and frees Apply", async () => {
    await open({ CLEAN_WEIGHT_SIZING: "off" }, 20);
    setSlider("Minimum position size", "0.08");
    expect(apply().disabled).toBe(true);
    fireEvent.click(reset("MIN_POSITION_PERCENT"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(apply().disabled).toBe(false);
  });
});
