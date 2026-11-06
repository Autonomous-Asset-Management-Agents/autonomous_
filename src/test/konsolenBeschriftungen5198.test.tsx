/**
 * #5198 — zwei Beschriftungen in der Desktop-Konsole, Owner-Beanstandung 06.11.2026.
 *
 * 1. Der Menüeintrag "Strategies" (Settings → Trading → Strategies, seit #5193 in
 *    Sidebar.tsx) trägt keinen Reifegrad-Marker, obwohl der Nachbareintrag
 *    "Notifications" ihn längst trägt. Owner wörtlich: "hinter den strategies button im
 *    menü (settings - trading - strategies) auch ein BETA label schreiben, so wie bei
 *    'notifications'".
 *
 *    Erster Anlauf dieses PR hat die Karten-Eyebrow in StrategyList.tsx markiert statt den
 *    Menüeintrag — die Analyse stand auf einem veralteten Checkout VOR #5193, in dem es
 *    den Menüeintrag noch nicht gab. Antigravity hat das zurückgewiesen (FINDING-01).
 *    Der Marker sitzt jetzt dort, wo er verlangt war, und mit derselben Darstellung wie
 *    bei Notifications.
 *
 * 2. Overview.tsx trug fest verdrahtet "Portfolio · Live · Paper trading" — Live UND Paper
 *    gleichzeitig, unabhängig vom tatsächlichen Zustand. Als Zustandsanzeige nicht nur
 *    überflüssig, sondern falsch; der echte Zustand steht daneben (Broker-Pille, Markt-
 *    StatusDot) und im Sidebar-Fuß.
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { readFileSync } from "node:fs";

vi.mock("../lib/api", () => ({
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchHealth: vi.fn().mockResolvedValue(null),
  fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
  getHitlPolicy: vi.fn().mockResolvedValue({ HITL_AUTONOMOUS_UNLIMITED: true }),
}));
vi.mock("../lib/killSwitch", () => ({
  resetKillSwitchAndResume: vi.fn().mockResolvedValue({ message: "reset ok" }),
}));

import { Sidebar } from "../console/desktop/Sidebar";
import { useStore } from "../console/store/useStore";

const oeffneTrading = () => {
  window.location.hash = "trading";
  useStore.setState({ desktopPage: "settings" });
  render(<Sidebar />);
};

describe("#5198 der Menüeintrag Strategies ist als BETA erkennbar", () => {
  beforeEach(() => {
    window.location.hash = "";
  });

  it("der Strategies-Knopf trägt den Marker", () => {
    oeffneTrading();
    const knopf = screen.getByRole("button", { name: "Strategies" });
    expect(knopf.textContent).toContain("BETA");
  });

  it("der zugängliche Name bleibt wörtlich 'Strategies'", () => {
    // Der Marker ist aria-hidden, wie bei Notifications. Sonst reisst der Vertrag in
    // consoleSidebar.test.tsx, der den Knopf per getByRole(..., { name: "Strategies" })
    // sucht, und ein Screenreader liest "Strategies BETA" statt des Seitennamens.
    oeffneTrading();
    const knopf = screen.getByRole("button", { name: "Strategies" });
    const marker = [...knopf.querySelectorAll("span")].find((s) => s.textContent === "BETA");
    expect(marker).toBeTruthy();
    expect(marker?.getAttribute("aria-hidden")).toBe("true");
  });

  it("dieselbe Darstellung wie bei Notifications", () => {
    oeffneTrading();
    const strategien = screen.getByRole("button", { name: "Strategies" });
    const hinweise = screen.getByRole("button", { name: "Notifications" });
    const klasse = (knopf: HTMLElement) =>
      [...knopf.querySelectorAll("span")].find((s) => s.textContent === "BETA")?.className;
    expect(klasse(strategien)).toBe(klasse(hinweise));
  });

  it("die Nachbareinträge tragen keinen Marker", () => {
    oeffneTrading();
    for (const name of ["Controls", "Agents", "System", "About", "Legal"]) {
      expect(screen.getByRole("button", { name }).textContent).not.toContain("BETA");
    }
  });
});

describe("#5198 Dashboard ohne bedeutungslose Eyebrow", () => {
  // Quelltext-Vertrag: Overview rendert erst mit einem Dutzend Mocks (Engine-Status, Audit,
  // Round Table, Broker). Für den Nachweis "diese Zeile ist weg" ist das der ehrlichere
  // Schnitt — er prüft genau die Behauptung und nichts daneben.
  const quelle = readFileSync("src/console/desktop/pages/Overview.tsx", "utf8");

  it("die fest verdrahtete Zeile ist entfernt", () => {
    expect(quelle).not.toMatch(/Portfolio\s*·\s*Live\s*·\s*Paper trading/);
  });

  it("die echten Zustandsanzeigen bleiben", () => {
    // Broker-Pille und Markt-StatusDot sind die Anzeigen, die den Zustand WIRKLICH messen.
    expect(quelle).toMatch(/brokerLabel\s*&&\s*<span className="pill">/);
    expect(quelle).toMatch(/Market\s*·\s*\{marketPillLabel\(marketOpen\)\}/);
  });

  it("die Begrüßung ist das erste Element im Kopfbereich", () => {
    const kopf = quelle.slice(quelle.indexOf("{/* Hero header */}"));
    const begruessung = kopf.indexOf("greeting(userName)");
    const h1 = kopf.indexOf("<h1");
    expect(begruessung).toBeGreaterThan(-1);
    expect(h1).toBeGreaterThan(-1);
    // Kein .eyebrow mehr VOR der Begrüßung im Kopfbereich.
    expect(kopf.slice(0, h1)).not.toMatch(/className="eyebrow/);
  });
});

describe("#5198 die Karte wurde nicht nebenbei mitmarkiert", () => {
  // FINDING-01: der erste Anlauf hing eine .pill an die Karten-Eyebrow. Das war der falsche
  // Ort und widersprach der eigenen Vorgabe im Issue ("nicht als eigene Pille"). Dieser
  // Vertrag haelt den Rueckbau fest - und schuetzt zwei fremde Verträge, die den Textinhalt
  // des .eyebrow-Elements auf genau den Kartennamen binden (strategyList.test.tsx,
  // settingsTradingSubnav.test.tsx).
  const karte = readFileSync(
    "src/console/desktop/strategyWorkshop/StrategyList.tsx",
    "utf8",
  );

  it("StrategyList traegt keinen eigenen Marker", () => {
    expect(karte).not.toMatch(/console\.beta/);
    expect(karte).not.toMatch(/>BETA</);
  });
});
