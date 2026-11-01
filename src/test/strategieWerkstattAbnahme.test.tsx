import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { FleetEngine, PaperSlot } from "@/lib/desktopBridge";
import { abdeckungsLuecken, abweichungen, konzeptText, pruefschritte, zustaende, zustandsZeile } from "./uxKonzeptLeser";
import { SLOTS_LEER, haengt, karte, raeumeAuf, scheitert, werkstatt, type Bereich, type Szene } from "./werkstattKulisse";

/**
 * STW-0 (#4626), armed by STW-10 (#4636) — frontend half of the epic acceptance for the
 * strategy workshop (#4625, ADR-027).
 *
 * Bridge: `ai_trading_bot/tests/features/step_defs/test_epic_4625.py` reads this file and
 * keeps the "STW-N:" / "STW-B:" prefixes as its anchors.
 *
 * One `it` per check step (`A<n>-P<m>`) and per state and flow (`state:<state>/<flow>`) of the
 * UX concept, operated on the real Settings page against faked engine and shell
 * (`werkstattKulisse.tsx`). `covers every concept check` binds the marks to the concept: a mark
 * has a test, or an entry in OFFEN_LAUT_KONZEPT with a deviation line that names it.
 *
 * The components for the word check are collected by glob; the path is confirmed by the concept.
 */

vi.mock("@/lib/firebase", () => ({ auth: { currentUser: null } }));

const WORKSHOP_SOURCES = import.meta.glob<string>(
  "../console/desktop/strategyWorkshop/**/*.tsx",
  { eager: true, query: "?raw", import: "default" },
);

// #4630: the UX concept (#4628, "Komponentenschnitt") confirmed the path and named the
// views. One of them existing is not "the workshop exists" — all of them must.
const WORKSHOP_VIEWS = [
  "LiveStrategyAssignment.tsx", // #4630
  "AccountComparison.tsx", // #4633
  "StrategySimulationCompare.tsx", // #4635
  "StrategyList.tsx", // #4864
  "StrategyEditor.tsx", // #4873
  "PaperAccountSlots.tsx", // #4934
  "PaperSlotStrategy.tsx", // #4953
  "WorkshopStates.tsx", // #4864
];

function workshopSources(): [string, string][] {
  const entries = Object.entries(WORKSHOP_SOURCES);
  const missing = WORKSHOP_VIEWS.filter(
    (view) => !entries.some(([file]) => file.endsWith(`/${view}`)),
  );
  expect(missing, "workshop components missing — #4630/#4633/#4635").toEqual([]);
  return entries;
}

const KONZEPT = konzeptText();

/** Marks that an open flow or state is a deviation the concept itself names (plan #4636, 2.1). */
const OFFEN_LAUT_KONZEPT = [
  "A4-P1", "A4-P2", "A4-P3", "A4-P4", "A5-P4", "state:error/4", "state:no-keys/4", "state:no-keys/2", "state:engine-off/6",
];

/** Epic scenario 7 names these; each stands at the flow that carries it. */
const PFLICHT_ZUSTAENDE = [
  "state:empty/1", "state:empty/6", "state:loading/3", "state:error/1", "state:no-keys/3", "state:duplicate-account/2",
];

type Knoten = { name: string; tasks?: Knoten[] };
const testNamen = (k: Knoten): string[] => (k.tasks ? k.tasks.flatMap(testNamen) : [k.name]);

function konzeptLuecken(datei: Knoten): string[] {
  const text = konzeptText();
  return abdeckungsLuecken(
    [...pruefschritte(text), ...zustaende(text)],
    testNamen(datei),
    OFFEN_LAUT_KONZEPT,
    abweichungen(text),
  );
}

describe("Concept reader (#4636)", () => {
  it("reads all 38 check steps and the 22 state/flow pairs of the concept", () => {
    const text = konzeptText();
    expect(pruefschritte(text)).toHaveLength(38);
    expect(zustaende(text)).toHaveLength(22);
    expect(zustaende(text)).toContain("state:error/7");
    expect(zustaende(text)).toContain("state:loading/5");
    expect(zustaende(text)).not.toContain("state:loading/1");
    expect(abweichungen(text).some((z) => z.includes("A5-P4"))).toBe(true);
  });

  it("reads text and action of a state, per flow where the concept splits them", () => {
    const text = konzeptText();
    expect(zustandsZeile(text, "empty", 6)).toEqual({ text: "No paper account connected.", aktion: "Connect paper account" });
    expect(zustandsZeile(text, "empty", 1).aktion).toBe("Create strategy");
    expect(zustandsZeile(text, "error", 3)).toEqual({ text: "Could not load the data.", aktion: "Retry" });
    expect(zustandsZeile(text, "loading", 3).aktion).toBeNull();
  });

  it("a mark without test and without deviation is a gap", () => {
    expect(abdeckungsLuecken(["A1-P1"], [], [], [])).toEqual(["A1-P1: no test and no deviation"]);
  });

  it("a mark with two tests is a gap", () => {
    expect(abdeckungsLuecken(["A1-P1"], ["A1-P1: a", "A1-P1: b"], [], [])).toEqual(["A1-P1: 2 tests"]);
  });

  it("a test beside an open entry is a gap, the entry is stale", () => {
    expect(abdeckungsLuecken(["A1-P1"], ["A1-P1: a"], ["A1-P1"], ["A1-P1 open"])).toEqual(["A1-P1: test and open entry"]);
  });

  it("an open entry needs a deviation line naming the mark, and a settled line does not count", () => {
    expect(abdeckungsLuecken(["A1-P1"], [], ["A1-P1"], [])).toEqual(["A1-P1: no deviation line"]);
    expect(abdeckungsLuecken(["A1-P1"], [], ["A1-P1"], ["A1-P1 **Erledigt durch #1**"])).toEqual(["A1-P1: no deviation line"]);
    expect(abdeckungsLuecken(["A1-P1"], [], ["A1-P1"], ["A1-P1 open"])).toEqual([]);
  });

  it("an open entry the concept does not know is a gap", () => {
    expect(abdeckungsLuecken([], [], ["A9-P9"], ["A9-P9 open"])).toEqual(["A9-P9: not in the concept"]);
  });

  it("does not take A1-P1 for A1-P10", () => {
    expect(abdeckungsLuecken(["A1-P1"], [], ["A1-P1"], ["A1-P10 open"])).toEqual(["A1-P1: no deviation line"]);
  });
});

describe("Strategy workshop acceptance (STW-0, #4626)", () => {
  it("covers every concept check", ({ task }) => {
    expect(konzeptLuecken(task.file as Knoten)).toEqual([]);
  });

  it("the mandatory scope of the epic is never open", () => {
    const text = konzeptText();
    const pflicht = [...pruefschritte(text).filter((k) => !k.startsWith("A4-") && k !== "A5-P4"), ...PFLICHT_ZUSTAENDE];
    expect(pflicht).toHaveLength(39);
    expect(OFFEN_LAUT_KONZEPT.filter((k) => pflicht.includes(k))).toEqual([]);
  });

  // #4918: armed with the last of the three views — all of them exist and none carries a word
  // of the list (each view has its own neutrality test besides).
  it("STW-N: no workshop view shows a ranking, 'best', 'recommended' or an adopt button", () => {
    const banned = [/\bbest\b/i, /recommend/i, /\brank(ing)?\b/i, /\badopt\b/i];
    for (const [path, source] of workshopSources()) {
      for (const re of banned) {
        expect(source, `${path}: ${re}`).not.toMatch(re);
      }
    }
  });

  it("STW-B: create, simulate, compare and assign are operable end-to-end incl. states empty/loading/error/no keys/duplicate account", ({ task }) => {
    workshopSources();
    expect(konzeptLuecken(task.file as Knoten)).toEqual([]);
  });
});

const A = { name: "A", werte: { STOP_LOSS_PCT: "6.0" } };
const B = { name: "B", werte: { STOP_LOSS_PCT: "4.0" } };
const C = { name: "C", werte: { STOP_LOSS_PCT: "5.0" } };
const ZWEI = [A, B];
const DREI = [A, B, C];

/** The notice of a state with the text and action of the concept. */
async function zeigtZustand(bereich: Bereich, kennzeichen: string, ablauf: number) {
  const soll = zustandsZeile(KONZEPT, kennzeichen, ablauf);
  const hinweis = (await bereich.findByText(soll.text)).closest<HTMLElement>('[role="status"]');
  expect(hinweis).toHaveAttribute("data-state", kennzeichen);
  if (soll.aktion) expect(within(hinweis!).getByRole("button", { name: soll.aktion })).toBeEnabled();
}

const klick = (bereich: Bereich, name: string) => fireEvent.click(bereich.getByRole("button", { name }));
const eingabe = (feld: HTMLElement, value: string) => fireEvent.change(feld, { target: { value } });
const zeilen = (bereich: Bereich) => within(bereich.getByRole("table")).getAllByRole("row").slice(1) as HTMLTableRowElement[];
const ersteSpalte = (bereich: Bereich) => zeilen(bereich).map((z) => z.cells[0].textContent);

/** `from` before `to` in the document. */
const stehtVor = (from: Element, to: Element) => from.compareDocumentPosition(to) & Node.DOCUMENT_POSITION_FOLLOWING;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-28T12:00:00Z"));
  vi.spyOn(console, "warn").mockImplementation(() => {});
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  raeumeAuf();
});

describe("Flow 1: create, duplicate, edit, delete a strategy", () => {
  async function editor(szene: Szene, knopf: string) {
    const umfeld = await werkstatt(szene);
    const k = await karte("Strategies");
    fireEvent.click((await k.findAllByRole("button", { name: knopf }))[0]);
    await k.findByLabelText("Buy threshold");
    return { ...umfeld, k };
  }
  const gespeichert = (anfragen: Awaited<ReturnType<typeof editor>>["anfragen"]) =>
    anfragen.find((a) => a.schluessel === "POST /api/strategies")?.body;

  it("A1-P1: without strategies the list shows empty, and Create strategy opens the editor", async () => {
    const { k } = await editor({ strategien: [] }, "Create strategy");
    expect(k.getByTestId("strategy-editor")).toBeInTheDocument();
  });

  it("A1-P2: the name is empty without a suggestion, Save strategy stays locked until one is typed", async () => {
    const { k } = await editor({ strategien: [A] }, "New strategy");
    const name = k.getByLabelText("Strategy name") as HTMLInputElement;
    expect(name.value).toBe("");
    expect(name).not.toHaveAttribute("placeholder");
    expect(k.getByRole("button", { name: "Save strategy" })).toBeDisabled();
    eingabe(name, "Wider stops");
    expect(k.getByRole("button", { name: "Save strategy" })).toBeEnabled();
  });

  it("A1-P3: From current settings takes the current values, the saved strategy is the last row", async () => {
    const { k, anfragen } = await editor({ strategien: ZWEI, aktuell: { STOP_LOSS_PCT: "5.5" } }, "New strategy");
    eingabe(k.getByLabelText("Strategy name"), "Wider stops");
    klick(k, "Save strategy");
    await k.findByText("Strategy saved. The change is recorded in the audit chain.");
    expect((gespeichert(anfragen)!.werte as Record<string, string>).STOP_LOSS_PCT).toBe("5.5");
    await waitFor(() => expect(ersteSpalte(k)).toEqual(["A", "B", "Wider stops"]));
  });

  it("A1-P4: Duplicate takes the values of the source, not its name", async () => {
    const { k, anfragen } = await editor({ strategien: [A, B], aktuell: { STOP_LOSS_PCT: "5.5" } }, "Duplicate");
    const name = k.getByLabelText("Strategy name") as HTMLInputElement;
    expect(name.value).toBe("");
    eingabe(name, "Copy");
    klick(k, "Save strategy");
    await k.findByText("Strategy saved. The change is recorded in the audit chain.");
    expect((gespeichert(anfragen)!.werte as Record<string, string>).STOP_LOSS_PCT).toBe("6.0");
  });

  it("A1-P5: with three strategies New strategy is locked and the limit text shows", async () => {
    await werkstatt({ strategien: DREI });
    const k = await karte("Strategies");
    expect(await k.findByRole("button", { name: "New strategy" })).toBeDisabled();
    expect(k.getByText("You have three strategies. Delete one to create another.")).toBeInTheDocument();
  });

  it("A1-P6: Delete asks first; an assigned strategy shows the hint instead", async () => {
    await werkstatt({ strategien: ZWEI, zuweisungen: [{ konto: "live", strategie: "A" }] });
    const k = await karte("Strategies");
    await k.findByText("Live account");
    fireEvent.click(k.getAllByRole("button", { name: "Delete" })[0]);
    expect(k.getByText("This strategy is assigned to an account. Assign another strategy to that account first.")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    fireEvent.click(k.getAllByRole("button", { name: "Delete" })[1]);
    const frage = await screen.findByRole("alertdialog", { name: "Delete this strategy?" });
    klick(within(frage), "Delete");
    await waitFor(() => expect(ersteSpalte(k)).toEqual(["A"]));
  });

  it("A1-P7: the fixed limits show and cannot be edited", async () => {
    const { k } = await editor({ strategien: [A] }, "New strategy");
    const grenzen = within(k.getByTestId("fixed-limits"));
    expect(grenzen.getByText("Fixed for all strategies")).toBeInTheDocument();
    expect(grenzen.queryAllByRole("textbox")).toEqual([]);
    expect(grenzen.queryAllByRole("slider")).toEqual([]);
    expect(grenzen.queryAllByRole("button")).toEqual([]);
  });

  it("state:empty/1: text and action of the concept", async () => {
    await werkstatt({ strategien: [] });
    await zeigtZustand(await karte("Strategies"), "empty", 1);
  });

  it("state:error/1: Retry reads the list again", async () => {
    let scharf = true;
    await werkstatt({
      routen: { "GET /api/strategies": () => (scharf ? { status: 500 } : { body: { strategien: [A], max_strategien: 3 } }) },
    });
    const k = await karte("Strategies");
    await zeigtZustand(k, "error", 1);
    scharf = false;
    klick(k, "Retry");
    await waitFor(() => expect(ersteSpalte(k)).toEqual(["A"]));
  });
});

describe("Flow 2: connect paper accounts", () => {
  const SLOT_AN: PaperSlot = { slot: "paper1", connected: true, accountMasked: "••••4321", engineStatus: "running" };
  const verbunden = (s: Partial<PaperSlot>) => [{ ...SLOT_AN, ...s }, SLOTS_LEER[1], SLOTS_LEER[2]];
  const platz = async (n: number) => within(await screen.findByRole("group", { name: `Paper account ${n}` }));

  async function keysEintragen(n: number) {
    const p = await platz(n);
    klick(p, "Connect");
    eingabe(p.getByLabelText("API key ID"), "PKTESTKEYID123456");
    eingabe(p.getByLabelText("Secret key"), "s3cr3t-very-private-value");
    klick(p, "Connect");
    return p;
  }

  it("A2-P1: three slots are visible, empty ones with Connect", async () => {
    await werkstatt();
    for (const n of [1, 2, 3]) expect((await platz(n)).getByRole("button", { name: "Connect" })).toBeInTheDocument();
  });

  it("A2-P2: valid keys lead to the masked account number and the engine status", async () => {
    const { shell } = await werkstatt();
    await keysEintragen(2);
    const p = await platz(2);
    expect(await p.findByText("••••4321")).toBeInTheDocument();
    expect(await p.findByText("Running")).toBeInTheDocument();
    expect(shell.connectPaperSlot).toHaveBeenCalledWith("paper2", "PKTESTKEYID123456", "s3cr3t-very-private-value");
  });

  // Built part only: the shell compares against the connected slots, not against the wizard keys or
  // a live account (deviations STW-6b #4951, A2-P3).
  it("A2-P3: the same account on a second slot shows duplicate-account, nothing is stored", async () => {
    const { shell } = await werkstatt({ shell: { connectPaperSlot: vi.fn().mockResolvedValue({ ok: false, code: "duplicate_account" }) } });
    await keysEintragen(2);
    const p = await platz(2);
    await p.findByRole("button", { name: "Use other keys" });
    expect(shell.listPaperSlots).toHaveBeenCalledTimes(1);
    expect(p.queryByText("••••4321")).toBeNull();
  });

  it("A2-P4: a stopped engine shows engine-off, Start engine starts this slot", async () => {
    const { shell } = await werkstatt({ slots: verbunden({ engineStatus: "stopped" }) });
    const p = await platz(1);
    fireEvent.click(await p.findByRole("button", { name: "Start engine" }));
    await waitFor(() => expect(shell.startPaperSlotEngine).toHaveBeenCalledWith("paper1"));
    expect(await p.findByText("Running")).toBeInTheDocument();
  });

  it("A2-P5: without enough memory the start shows low-memory before anything starts", async () => {
    const startPaperSlotEngine = vi.fn().mockResolvedValueOnce({ ok: false, code: "low_memory" }).mockResolvedValue({ ok: true });
    await werkstatt({ slots: verbunden({ engineStatus: "stopped" }), shell: { startPaperSlotEngine } });
    const p = await platz(1);
    fireEvent.click(await p.findByRole("button", { name: "Start engine" }));
    fireEvent.click(await p.findByRole("button", { name: "Start anyway" }));
    await waitFor(() => expect(startPaperSlotEngine).toHaveBeenLastCalledWith("paper1", { force: true }));
  });

  it("A2-P6: Disconnect asks first and then empties the slot", async () => {
    const { shell } = await werkstatt({ slots: verbunden({}) });
    const p = await platz(1);
    fireEvent.click(await p.findByRole("button", { name: "Disconnect" }));
    const frage = await screen.findByRole("alertdialog");
    expect(shell.disconnectPaperSlot).not.toHaveBeenCalled();
    klick(within(frage), "Disconnect");
    expect(await (await platz(1)).findByRole("button", { name: "Connect" })).toBeInTheDocument();
    expect(shell.disconnectPaperSlot).toHaveBeenCalledWith("paper1");
  });

  it("state:loading/2: the card shows loading until the shell answers", async () => {
    await werkstatt({ shell: { listPaperSlots: vi.fn(() => new Promise(() => {})) } });
    await zeigtZustand(await karte("Paper accounts"), "loading", 2);
  });

  it("state:error/2: an unreadable list shows error with Retry", async () => {
    await werkstatt({ routen: { "GET /api/strategies": scheitert(500) } });
    await zeigtZustand(await karte("Paper accounts"), "error", 2);
  });

  it("state:duplicate-account/2: Use other keys leaves the notice", async () => {
    await werkstatt({ shell: { connectPaperSlot: vi.fn().mockResolvedValue({ ok: false, code: "duplicate_account" }) } });
    const p = await keysEintragen(1);
    await zeigtZustand(p, "duplicate-account", 2);
    klick(p, "Use other keys");
    expect(p.queryByRole("button", { name: "Use other keys" })).toBeNull();
  });

  it("state:engine-off/2: text and action of the concept", async () => {
    await werkstatt({ slots: verbunden({ engineStatus: "stopped" }) });
    await zeigtZustand(await platz(1), "engine-off", 2);
  });

  it("state:low-memory/2: text, both actions, Cancel returns to engine-off", async () => {
    await werkstatt({
      slots: verbunden({ engineStatus: "stopped" }),
      shell: { startPaperSlotEngine: vi.fn().mockResolvedValue({ ok: false, code: "low_memory" }) },
    });
    const p = await platz(1);
    fireEvent.click(await p.findByRole("button", { name: "Start engine" }));
    await zeigtZustand(p, "low-memory", 2);
    klick(p, "Cancel");
    expect(await p.findByRole("button", { name: "Start engine" })).toBeInTheDocument();
  });
});

const KEINE_KEYS = { detail: "x", code: "simulation_keys_missing", params: { reason: "keine_keys" } };
const KEIN_KORPUS = { detail: "x", code: "simulation_data_missing", params: { reason: "kein_korpus" } };
const EDITION = { detail: "x", code: "simulation_edition_limit" };
const LAUF = "POST /api/strategy-sim/runs";

async function simulation(szene: Szene) {
  const umfeld = await werkstatt({ strategien: ZWEI, ...szene });
  const k = await karte("Simulate");
  klick(k, "Simulate");
  return { ...umfeld, k };
}

function zeitraum(k: Bereich, start: string, ende: string) {
  eingabe(k.getByLabelText("Start"), start);
  eingabe(k.getByLabelText("End"), ende);
}

async function starteSimulation(szene: Szene, namen = ["A"]) {
  const umfeld = await simulation(szene);
  zeitraum(umfeld.k, "2026-01-02", "2026-06-30");
  for (const n of namen) fireEvent.click(umfeld.k.getByRole("checkbox", { name: n }));
  klick(umfeld.k, "Run simulation");
  return umfeld;
}

describe("Flow 3: configure a simulation", () => {
  it("A3-P1: Run simulation is locked without a strategy or with an invalid period", async () => {
    const { k } = await simulation({});
    const lauf = k.getByRole("button", { name: "Run simulation" });
    expect(lauf).toBeDisabled();
    fireEvent.click(k.getByRole("checkbox", { name: "A" }));
    expect(lauf).toBeEnabled();
    zeitraum(k, "2026-06-30", "2026-01-02");
    expect(lauf).toBeDisabled();
  });

  it("A3-P2: a fourth strategy cannot be chosen", async () => {
    const { k } = await simulation({ strategien: [...DREI, { name: "D", werte: {} }] });
    for (const n of ["A", "B", "C"]) fireEvent.click(k.getByRole("checkbox", { name: n }));
    expect(k.getByRole("checkbox", { name: "D" })).toBeDisabled();
  });

  it("A3-P3: a start more than three years back cannot be chosen", async () => {
    const { k } = await simulation({});
    expect(k.getByLabelText("Start")).toHaveAttribute("min", "2023-10-28");
    fireEvent.click(k.getByRole("checkbox", { name: "A" }));
    eingabe(k.getByLabelText("Start"), "2023-10-27");
    expect(k.getByRole("button", { name: "Run simulation" })).toBeDisabled();
    eingabe(k.getByLabelText("Start"), "2023-10-28");
    expect(k.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  // Built part only: the state comes on a 403 of the route, not ahead of it (deviation STW-9c #4918, A3-P4).
  it("A3-P4: a 403 shows edition-limit, Shorten period sets the start to end minus 12 months", async () => {
    const { k } = await starteSimulation({ routen: { [LAUF]: scheitert(403, EDITION) } });
    fireEvent.click(await k.findByRole("button", { name: "Shorten period" }));
    expect(k.getByLabelText("Start")).toHaveValue("2025-06-30");
    expect(k.queryByRole("button", { name: "Shorten period" })).toBeNull();
  });

  it("A3-P5: without keys Connect keys brings Paper accounts into view", async () => {
    const { k, gesprungenZu } = await starteSimulation({ routen: { [LAUF]: scheitert(409, KEINE_KEYS) } });
    fireEvent.click(await k.findByRole("button", { name: "Connect keys" }));
    expect(gesprungenZu).toEqual(["paper-accounts"]);
  });

  it("state:loading/3: the run shows loading and locks a second start", async () => {
    const { k } = await starteSimulation({ routen: { [LAUF]: haengt } });
    await zeigtZustand(k, "loading", 3);
    expect(k.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  it("state:error/3: Retry starts the run again", async () => {
    const { k, anfragen } = await starteSimulation({ routen: { [LAUF]: scheitert(500) } });
    await zeigtZustand(k, "error", 3);
    klick(k, "Retry");
    await waitFor(() => expect(anfragen.filter((a) => a.schluessel === LAUF)).toHaveLength(2));
  });

  it("state:no-keys/3: text and action of the concept", async () => {
    const { k } = await starteSimulation({ routen: { [LAUF]: scheitert(409, KEINE_KEYS) } });
    await zeigtZustand(k, "no-keys", 3);
  });

  it("state:edition-limit/3: text and action of the concept", async () => {
    const { k } = await starteSimulation({ routen: { [LAUF]: scheitert(403, EDITION) } });
    await zeigtZustand(k, "edition-limit", 3);
  });

  // Flow 4 stands only as this notice (deviation STW-9c #4918); its check steps are open.
  it("a missing data set shows notice T21, no result and no Prepare data", async () => {
    const { k } = await starteSimulation({ routen: { [LAUF]: scheitert(409, KEIN_KORPUS) } });
    await k.findByText(/^Simulation data for this period is not on this computer yet\./);
    expect(k.queryByText("Simulation result")).toBeNull();
    expect(k.queryByRole("button", { name: "Prepare data" })).toBeNull();
  });
});

describe("Flow 5: simulation result", () => {
  const FERTIG = {
    run_id: "0123456789abcdef",
    status: "fertig",
    ergebnis: {
      zeitraum: { beginn: "2026-01-02", ende: "2026-06-30" },
      strategien: [
        { name: "C", twr_pct: 4.5, max_drawdown_pct: 3.0, spy_twr_pct: 6.0, spy_max_drawdown_pct: 5.0 },
        { name: "A", twr_pct: -2.0, max_drawdown_pct: 8.0, spy_twr_pct: 6.0, spy_max_drawdown_pct: 5.0 },
      ],
      unsicherheit: { pfade: 1, handelstage: 120 },
      nicht_abgebildet: [{ art: "survivorship", text: "x" }],
    },
  };
  const ERGEBNIS = "GET /api/strategy-sim/runs/*";

  async function ergebnis() {
    const umfeld = await starteSimulation({ strategien: DREI, routen: { [ERGEBNIS]: () => ({ body: FERTIG }) } }, ["A", "C"]);
    await umfeld.k.findByText("Simulation result");
    return umfeld;
  }

  it("A5-P1: mandatory note, period, uncertainty and the parts not modelled stand above the table", async () => {
    const { k } = await ergebnis();
    const texte = [/^Simulated or past performance/, /2026-01-02 – 2026-06-30/, /^Uncertainty: /, /^Not modelled: news, LLM agents, intraday stops\. Costs/];
    for (const text of texte) expect(stehtVor(k.getByText(text), k.getByRole("table"))).toBeTruthy();
  });

  it("A5-P2: rows keep creation order, only a header click sorts, aria-sort follows", async () => {
    const { k } = await ergebnis();
    expect(ersteSpalte(k)).toEqual(["C", "A"]);
    expect(k.getByRole("columnheader", { name: "TWR" })).toHaveAttribute("aria-sort", "none");
    klick(k, "TWR");
    expect(ersteSpalte(k)).toEqual(["A", "C"]);
    expect(k.getByRole("columnheader", { name: "TWR" })).toHaveAttribute("aria-sort", "ascending");
  });

  it("A5-P3: Restore creation order brings the creation order back", async () => {
    const { k } = await ergebnis();
    klick(k, "TWR");
    klick(k, "Restore creation order");
    expect(ersteSpalte(k)).toEqual(["C", "A"]);
    expect(k.getByRole("columnheader", { name: "TWR" })).toHaveAttribute("aria-sort", "none");
  });

  it("A5-P5: the section has no button and no link to adopt or assign", async () => {
    const { k } = await ergebnis();
    expect(k.queryAllByRole("link")).toEqual([]);
    const knoepfe = k.getAllByRole("button").map((b) => b.textContent);
    expect(knoepfe).toEqual(["Simulate", "TWR", "Max drawdown", "SPY (same period)", "Restore creation order", "Change setup"]);
  });

  it("state:loading/5: a running simulation shows loading until the result is there", async () => {
    const { k } = await starteSimulation({ routen: { [ERGEBNIS]: haengt } });
    await zeigtZustand(k, "loading", 5);
    expect(k.queryByText("Simulation result")).toBeNull();
  });

  it("state:error/5: a failed run shows error, no figures", async () => {
    const { k } = await starteSimulation({ routen: { [ERGEBNIS]: () => ({ body: { run_id: "x", status: "fehler" } }) } });
    await zeigtZustand(k, "error", 5);
    expect(k.queryByRole("table")).toBeNull();
  });
});

describe("Flow 6: compare accounts in operation", () => {
  const FLEET: FleetEngine[] = [
    { id: "live", kind: "live", port: 8001, status: "running" },
    { id: "paper1", kind: "paper", port: 8002, status: "running" },
    { id: "paper2", kind: "paper", port: 8003, status: "running" },
  ];
  const reihe = (tage: number) => {
    const t0 = Date.parse("2026-04-01T00:00:00Z");
    return Array.from({ length: tage }, (_, i) => ({
      date: new Date(t0 + i * 86_400_000).toISOString().slice(0, 10),
      equity: 1000 * 1.001 ** i,
    }));
  };
  const KURVE = { points: reihe(60), spy_points: reihe(60) };
  const AUSFALL = { points: [], spy_points: [], message: "fetch_failed" };

  async function vergleich(szene: Szene) {
    const umfeld = await werkstatt({ routen: { "GET /benchmark-equity": () => ({ body: KURVE }) }, ...szene });
    const k = await karte("Live and paper accounts");
    klick(k, "Live and paper accounts");
    return { ...umfeld, k };
  }

  it("A6-P1: live stands on top, the paper accounts follow in slot order", async () => {
    const { k } = await vergleich({ fleet: [FLEET[1], FLEET[0], FLEET[2]] });
    await k.findByRole("table");
    // The shell hands out the slots in their order (`engine-fleet.cjs`); the view keeps it.
    expect(ersteSpalte(k)).toEqual(["Live", "Paper account 1", "Paper account 2"]);
  });

  it("A6-P2: period, origin and mandatory note stand above the table", async () => {
    const { k } = await vergleich({ fleet: FLEET });
    await k.findByText(/2026-04-01 – 2026-05-30/);
    const texte = [/2026-04-01 – 2026-05-30/, /^Cash-flow adjusted, from account activities\.$/, /^Simulated or past performance/];
    for (const text of texte) expect(stehtVor(k.getByText(text), k.getByRole("table"))).toBeTruthy();
  });

  it("A6-P3: an unreachable account shows Unavailable, the other rows stay", async () => {
    const { k } = await vergleich({
      fleet: FLEET,
      routen: { "GET /benchmark-equity": ({ url }) => ({ body: url.port === "8003" ? AUSFALL : KURVE }) },
    });
    await k.findByText("Unavailable");
    expect(ersteSpalte(k)).toEqual(["Live", "Paper account 1", "Paper account 2"]);
    await waitFor(() => expect(zeilen(k)[0].textContent).toMatch(/%/));
  });

  it("A6-P4: without a paper account the view shows empty, the action leads to Paper accounts", async () => {
    const { k, gesprungenZu } = await vergleich({ fleet: [FLEET[0]] });
    fireEvent.click(await k.findByRole("button", { name: "Connect paper account" }));
    expect(gesprungenZu).toEqual(["paper-accounts"]);
  });

  it("A6-P5: no button and no link leads to the live assignment", async () => {
    const { k } = await vergleich({ fleet: FLEET });
    await k.findByText(/2026-04-01 – 2026-05-30/);
    expect(k.queryAllByRole("link")).toEqual([]);
    const knoepfe = k.getAllByRole("button").map((b) => b.textContent);
    expect(knoepfe).toEqual(["Live and paper accounts", "TWR", "Max drawdown", "SPY (same period)", "Restore creation order"]);
  });

  it("state:empty/6: text and action of the concept", async () => {
    const { k } = await vergleich({ fleet: null });
    await zeigtZustand(k, "empty", 6);
  });

  it("state:loading/6: the view shows loading until the shell names the accounts", async () => {
    const { k } = await vergleich({ shell: { getEngineFleet: vi.fn(() => new Promise(() => {})) } });
    await zeigtZustand(k, "loading", 6);
  });

  it("state:error/6: an unreadable list shows error with Retry", async () => {
    await werkstatt({ routen: { "GET /api/strategies": scheitert(500) } });
    await zeigtZustand(await karte("Live and paper accounts"), "error", 6);
  });
});

describe("Flow 7: live assignment", () => {
  const TITEL = "Assign strategy to the live account?";

  async function modal(szene: Szene) {
    const umfeld = await werkstatt({ strategien: ZWEI, ...szene });
    const k = await karte("Live account strategy");
    eingabe(await k.findByRole("combobox", { name: "Strategy for the live account" }), "B");
    klick(k, "Review changes");
    return { ...umfeld, k, dialog: within(await screen.findByRole("dialog", { name: TITEL })) };
  }

  it("A7-P1: the card is reachable only in Settings → Trading", async () => {
    await werkstatt({ strategien: ZWEI });
    await karte("Live account strategy");
    for (const reiter of ["trading-agents", "system", "notifications", "about", "legal"]) {
      window.location.hash = reiter;
      fireEvent(window, new HashChangeEvent("hashchange"));
      await waitFor(() => expect(screen.queryByText("Live account strategy")).toBeNull());
    }
  });

  it("A7-P2: Review changes opens the modal with the diff of the chosen strategy", async () => {
    const { dialog } = await modal({ aktuell: { STOP_LOSS_PCT: "7.0" } });
    expect(dialog.getByText("7.0").parentElement).toHaveTextContent("7.0→4.0");
    expect(dialog.getByText("The change is recorded with timestamp, previous value and new value.")).toBeInTheDocument();
  });

  it("A7-P3: Change and restart is locked until the live checkbox is set", async () => {
    const { dialog } = await modal({ live: true });
    expect(dialog.getByRole("button", { name: "Change and restart" })).toBeDisabled();
    fireEvent.click(dialog.getByRole("checkbox"));
    expect(dialog.getByRole("button", { name: "Change and restart" })).toBeEnabled();
  });

  it("A7-P4: confirming records before it persists before it restarts", async () => {
    const { dialog, protokoll, shell } = await modal({ live: true });
    fireEvent.click(dialog.getByRole("checkbox"));
    klick(dialog, "Change and restart");
    await waitFor(() => expect(protokoll).toEqual(["record", "persist", "restart"]));
    expect(shell.restartEngine).toHaveBeenCalledWith(expect.any(String), { goLive: true });
  });

  it("A7-P5: Cancel writes nothing and closes the modal", async () => {
    const { dialog, protokoll } = await modal({});
    klick(dialog, "Cancel");
    expect(screen.queryByRole("dialog", { name: TITEL })).toBeNull();
    expect(protokoll).toEqual([]);
  });

  it("A7-P6: with HITL off Review changes is locked and the hint shows", async () => {
    await werkstatt({ strategien: ZWEI, hitl: false });
    const k = await karte("Live account strategy");
    eingabe(await k.findByRole("combobox", { name: "Strategy for the live account" }), "B");
    expect(k.getByRole("button", { name: "Review changes" })).toBeDisabled();
    expect(k.getByText("Live assignment requires human approval of orders to be on.")).toBeInTheDocument();
  });

  it("state:loading/7: the card shows loading until the engine answers", async () => {
    await werkstatt({ routen: { "GET /api/strategies": haengt } });
    await zeigtZustand(await karte("Live account strategy"), "loading", 7);
  });

  it("state:error/7: an unreadable list shows error with Retry", async () => {
    await werkstatt({ routen: { "GET /api/strategies": scheitert(500) } });
    await zeigtZustand(await karte("Live account strategy"), "error", 7);
  });
});
