// #4658 (Epic #4638): the LLM-QA funnel on the Decisions page — four stages with counts, each
// proposal with status and readable reason, and the list badge (age, status, active source).
// Data: GET /llm-vorauswahl/trichter (#4656, core/engine/routes/agenten.py).
import { act, render, screen, within } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import type { LlmTrichterResponse } from "@/lib/api";
import { FORBIDDEN } from "@/test/wordingGuard";

const fetchLlmTrichter = vi.fn<() => Promise<LlmTrichterResponse | null>>();
vi.mock("@/lib/api", () => ({ fetchLlmTrichter: () => fetchLlmTrichter() }));

import { LlmTrichter } from "../LlmTrichter";
import {
  ENDSTATUS_TEXT,
  GRUND_TEXT,
  LISTE_STATUS_TEXT,
  STUFEN_TEXT,
  STUFEN_TEXT_LISTE,
  QUELLE_TEXT,
  HINWEIS_TEXT,
  aktiveQuelle,
  grundText,
  statusTone,
  stufenText,
} from "@/console/shared/llmTrichterGruende";

// 30 → 25 → 5 → 4, as in the sub-issue's acceptance scenario.
const trichter = (over: Partial<LlmTrichterResponse> = {}): LlmTrichterResponse => ({
  status: "ok",
  lauf_id: "lauf-1",
  lauf_zeitpunkt_utc: "2026-10-22T14:00:00+00:00",
  liste_status: "frisch",
  liste_alter_s: 720,
  n_vorgeschlagen: 30,
  n_universum: 25,
  n_round_table: 5,
  n_iron_dome: 4,
  zaehlstufen: {},
  symbole: [
    { symbol: "NVDA", status: "zugelassen", grund: "executed" },
    { symbol: "AAPL", status: "abgelehnt_iron_dome", grund: "blocked:daily_limit" },
    { symbol: "MSFT", status: "abgelehnt_round_table", grund: "signal_hold" },
    { symbol: "TSLA", status: "noch_nicht_bewertet", grund: "keine_round_table_entscheidung" },
    { symbol: "XYZ1", status: "verworfen_vorab", grund: "nicht_im_universum" },
  ],
  ...over,
});

const LEER: LlmTrichterResponse = {
  status: "ok",
  lauf_id: null,
  lauf_zeitpunkt_utc: null,
  liste_status: "keiner",
  liste_alter_s: null,
  n_vorgeschlagen: 0,
  n_universum: 0,
  n_round_table: 0,
  n_iron_dome: 0,
  zaehlstufen: {},
  symbole: [],
};

afterEach(() => fetchLlmTrichter.mockReset());

// Let the mount fetch resolve and its state update commit before asserting absence.
const flush = async () => {
  await act(async () => {
    await Promise.resolve();
  });
  expect(fetchLlmTrichter).toHaveBeenCalled();
};

describe("LlmTrichter web search line (#4940)", () => {
  it("zeigt Web search no", async () => {
    fetchLlmTrichter.mockResolvedValue(trichter({ websuche: false }));
    render(<LlmTrichter />);
    expect((await screen.findByTestId("trichter-websuche")).textContent).toBe("Web search: no");
  });

  it("zeigt Web search yes", async () => {
    fetchLlmTrichter.mockResolvedValue(trichter({ websuche: true }));
    render(<LlmTrichter />);
    expect((await screen.findByTestId("trichter-websuche")).textContent).toBe("Web search: yes");
  });

  it("keine Zeile ohne Feld", async () => {
    fetchLlmTrichter.mockResolvedValue(trichter());
    render(<LlmTrichter />);
    await screen.findAllByTestId("trichter-stufe");
    expect(screen.queryByTestId("trichter-websuche")).toBeNull();
  });

  it("keine Zeile, wenn die Engine keine Aussage hat", async () => {
    fetchLlmTrichter.mockResolvedValue(trichter({ websuche: null }));
    render(<LlmTrichter />);
    await screen.findAllByTestId("trichter-stufe");
    expect(screen.queryByTestId("trichter-websuche")).toBeNull();
  });
});

describe("LlmTrichter (#4658)", () => {
  it("zeigt vier Stufen", async () => {
    fetchLlmTrichter.mockResolvedValue(trichter());
    render(<LlmTrichter />);
    const stufen = await screen.findAllByTestId("trichter-stufe");
    expect(stufen).toHaveLength(4);
    expect(stufen.map((s) => s.textContent)).toEqual([
      `30${STUFEN_TEXT.vorgeschlagen}`,
      `25${STUFEN_TEXT.universum}`,
      `5${STUFEN_TEXT.round_table}`,
      `4${STUFEN_TEXT.iron_dome}`,
    ]);
  });

  it("zeigt Grund je Symbol", async () => {
    fetchLlmTrichter.mockResolvedValue(trichter());
    render(<LlmTrichter />);
    const row = (sym: string) => within(screen.getByTestId(`trichter-zeile-${sym}`));
    await screen.findByTestId("trichter-zeile-NVDA");
    expect(row("NVDA").getByText(ENDSTATUS_TEXT.zugelassen)).toBeTruthy();
    expect(row("NVDA").getByText(GRUND_TEXT.executed)).toBeTruthy();
    expect(row("AAPL").getByText(ENDSTATUS_TEXT.abgelehnt_iron_dome)).toBeTruthy();
    expect(row("AAPL").getByText(GRUND_TEXT["blocked:daily_limit"])).toBeTruthy();
    expect(row("MSFT").getByText(GRUND_TEXT.signal_hold)).toBeTruthy();
    expect(row("TSLA").getByText(GRUND_TEXT.keine_round_table_entscheidung)).toBeTruthy();
    expect(row("XYZ1").getByText(ENDSTATUS_TEXT.verworfen_vorab)).toBeTruthy();
    expect(row("XYZ1").getByText(GRUND_TEXT.nicht_im_universum)).toBeTruthy();
  });

  it("zeigt einen unbekannten Grund wörtlich statt ihn zu erfinden", () => {
    expect(grundText("Gatekeeper: conviction below floor")).toBe(
      "Gatekeeper: conviction below floor",
    );
  });

  it("zeigt Listenstatus abgelaufen mit aktiver Quelle", async () => {
    fetchLlmTrichter.mockResolvedValue(
      trichter({ liste_status: "abgelaufen", liste_alter_s: 3 * 3600 }),
    );
    render(<LlmTrichter />);
    const badge = await screen.findByTestId("trichter-liste");
    expect(badge.textContent).toContain("abgelaufen — LSTM-Ranking aktiv");
    expect(badge.textContent).toContain(QUELLE_TEXT.lstm);
    expect(badge.textContent).toContain("3 h");
  });

  it("zeigt Listenstatus frisch und veraltet mit der LLM-Vorauswahl als Quelle", async () => {
    fetchLlmTrichter.mockResolvedValue(trichter());
    const { unmount } = render(<LlmTrichter />);
    let badge = await screen.findByTestId("trichter-liste");
    expect(badge.textContent).toContain("frisch");
    expect(badge.textContent).toContain(QUELLE_TEXT.llm);
    expect(badge.textContent).toContain("12 min");
    unmount();

    fetchLlmTrichter.mockResolvedValue(trichter({ liste_status: "veraltet_aber_gueltig" }));
    render(<LlmTrichter />);
    badge = await screen.findByTestId("trichter-liste");
    expect(badge.textContent).toContain("veraltet, noch gültig");
    expect(badge.textContent).toContain(QUELLE_TEXT.llm);
  });

  it("verborgen bei Quelle lstm", async () => {
    // The route answers an empty funnel (no run, liste_status 'keiner') while the LLM pre-selection
    // has never run — i.e. the ranking source is the LSTM panel.
    fetchLlmTrichter.mockResolvedValue(LEER);
    const { container } = render(<LlmTrichter />);
    await flush();
    expect(container.innerHTML).toBe("");
  });

  it("verborgen, wenn die Route nicht antwortet", async () => {
    fetchLlmTrichter.mockResolvedValue(null);
    const { container } = render(<LlmTrichter />);
    await flush();
    expect(container.innerHTML).toBe("");
  });
});

// #4861: the user's own S&P-500 list as ranking source — no LLM, no expiry.
describe("LlmTrichter mit eigener Liste (#4861)", () => {
  const liste = (over: Partial<LlmTrichterResponse> = {}) =>
    trichter({
      quelle: "liste",
      hinweis: null,
      lauf_id: "liste",
      liste_status: "dauerhaft",
      liste_alter_s: null,
      ...over,
    });

  it("zeigt die erste Stufe als 'aus der eigenen Liste' und die Quelle", async () => {
    fetchLlmTrichter.mockResolvedValue(liste());
    render(<LlmTrichter />);
    const stufen = await screen.findAllByTestId("trichter-stufe");
    expect(stufen[0].textContent).toBe("30aus der eigenen Liste");
    expect(stufen[1].textContent).toBe(`25${STUFEN_TEXT.universum}`);
    const badge = screen.getByTestId("trichter-liste");
    expect(badge.textContent).toContain("gilt bis zur nächsten Änderung");
    expect(badge.textContent).toContain("Eigene Liste");
    expect(badge.querySelector(".bg-\\[\\#00c27a\\]")).not.toBeNull();
    expect(screen.queryByTestId("trichter-hinweis")).toBeNull();
  });

  it("zeigt den Hinweis, wenn er gesetzt ist", async () => {
    fetchLlmTrichter.mockResolvedValue(liste({ hinweis: "liste_heutiges_wissen" }));
    render(<LlmTrichter />);
    const hinweis = await screen.findByTestId("trichter-hinweis");
    expect(hinweis.textContent).toContain(
      "Die Liste wurde mit dem Wissen von heute zusammengestellt.",
    );
  });

  it("Helfer: Ton, Quelle und Stufentext", () => {
    expect(statusTone("dauerhaft")).toBe("on");
    expect(statusTone("frisch")).toBe("on");
    expect(statusTone("veraltet_aber_gueltig")).toBe("neutral");
    expect(statusTone("keiner")).toBe("off");
    expect(aktiveQuelle("dauerhaft")).toBe("Eigene Liste");
    expect(aktiveQuelle("keiner")).toBe(QUELLE_TEXT.lstm);
    expect(stufenText("liste").vorgeschlagen).toBe("aus der eigenen Liste");
    expect(stufenText("llm")).toBe(STUFEN_TEXT);
    expect(stufenText(undefined)).toBe(STUFEN_TEXT);
  });
});

describe("LlmTrichter wording guard (#4658)", () => {
  it("alle neuen Texte ergeben 0 Treffer auf FORBIDDEN", async () => {
    const tabellen = [
      ENDSTATUS_TEXT,
      GRUND_TEXT,
      LISTE_STATUS_TEXT,
      STUFEN_TEXT,
      STUFEN_TEXT_LISTE,
      QUELLE_TEXT,
      HINWEIS_TEXT,
    ];
    const texte = tabellen.flatMap((t) => Object.values(t));
    expect(texte.length).toBeGreaterThan(0);
    for (const t of texte) expect(t.match(FORBIDDEN), t).toBeNull();

    fetchLlmTrichter.mockResolvedValue(trichter());
    const { container } = render(<LlmTrichter />);
    await screen.findByTestId("trichter-liste");
    expect(container.textContent?.match(FORBIDDEN) ?? null).toBeNull();
  });
});
