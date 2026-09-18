# UX-Test-Konzept — Desktop Operator Console (#1050)

> Sauberes, **ausführbares** End-to-End-Test-Konzept für die vier Kern-User-Journeys
> der Desktop-Konsole: **Onboarding · Support · Settings · Operativer Handel**.
> Es ersetzt das manuelle Durchklicken durch deterministische, in CI laufende Szenarien.

---

## 1. Ziel & Scope

Die Konsole (`/console`, React + Vite, im Electron-Shell `desktop/`) ist „one frontend for
all editions". Dieses Konzept testet die **User-Experience der Desktop-Edition** end-to-end:
echte Komponenten, echte Stores, echte Adapter und echte Polling-Hooks laufen unverändert —
**nur die eine Naht zum Electron-Shell und zur Engine wird gefälscht** (`window.aaagents` +
die HTTP-API). So testen wir Flows, nicht Implementierungsdetails.

**In-Scope:** die 4 Journeys + ihre Fehler-/Leerzustände, Navigation, Datenfluss.
**Out-of-Scope:** die Python-Engine selbst (eigene Test-Suite), echte Broker-/LLM-Calls,
Cloud-Multi-Tenant-Auth (Desktop hat keine Firebase-Auth).

## 2. Personas

| Persona | Ziel | Berührte Journeys |
|---|---|---|
| **Neuer Operator** (Erstinstallation) | App einrichten, loslegen | J1 Onboarding |
| **Täglicher Operator** | Buch beobachten, Engine fragen, sicher eingreifen | J2 Support, J3 Settings, J4 Handel |
| **Support/Betrieb** | Engine-Zustand prüfen, Not-Halt | J3 Settings (Lifecycle, Kill-switch) |

## 3. Test-Ebenen (Pyramide)

| Ebene | Runner | Was | Wo |
|---|---|---|---|
| **Unit/Component** | vitest + jsdom | einzelne Seiten/Bausteine, Randfälle | `src/test/console*.test.tsx` |
| **Journey / Integration-E2E** | vitest + Testing-Library | **komplette User-Journeys** durch den echten Komponentenbaum, Shell/Engine an der Naht gefälscht | `src/test/journeys/*.journey.test.tsx` ← *dieses Konzept* |
| **Browser-E2E** | Playwright (Chromium) | dieselben Szenarien im echten Build/Browser (Bridge injiziert, HTTP `page.route`) | `src/test/e2e/*.e2e.spec.ts` |

Die **Journey-Ebene** ist der ausführbare Kern (schnell, deterministisch, CI-grün heute).
Die **Browser-Ebene** ist die höhere Fidelität (echtes Bundle/Routing/CSS) für Smoke + Optik.

## 4. Umgebung & Ausführung

```bash
# Journey-/Component-/Unit-Tests (CI-Standard)
npm test                         # alle vitest-Suites
npx vitest run src/test/journeys # nur die Journeys

# Browser-E2E (separater Lauf, startet den Dev-Server selbst)
npm run test:e2e                 # Playwright (Chromium); braucht `npx playwright install chromium`
```

CI führt `npm test` aus (`.github/workflows/ci.yml`); die Playwright-Specs sind aus der
vitest-Glob ausgeschlossen (`vitest.config.ts`) und laufen separat.

## 5. Testdaten-Strategie

Ein **kanonischer Datensatz** wird von allen Ebenen geteilt, damit ein Szenario dieselben
Daten sieht — egal ob durch den React-Baum getrieben (vitest) oder auf der Leitung
abgefangen (Playwright `page.route`).

| Datei | Inhalt |
|---|---|
| `src/test/fixtures/consoleFixtures.ts` | **reine Daten** (kein `vi`): Portfolio, Round-Table, Equity-Kurve, Chat-Antworten, Alpaca-/Ollama-Ergebnisse, Engine-Logs, Beispiel-Keys. Shapes = exakt die Engine-DTOs aus `src/lib/api.ts`. |
| `src/test/fixtures/mockBridge.ts` | **`makeBridge(opts)`** — konfigurierbarer Fake des `window.aaagents`-Seams (Keychain, Alpaca-Validierung, Ollama-Provisioning, Engine-Lifecycle) + Emitter (`emitStatus`, `emitLog`) für Live-IPC-Updates. |

Der kanonische Buch-Datensatz: 3 offene Positionen (AAPL +, NVDA −, MSFT +), Equity €105.000,
3 Round-Table-Verdikte (AAPL BUY / TSLA SELL / NVDA HOLD mit Gatekeeper-Veto), eine
6-Tage-Equity-Kurve vs. S&P.

## 6. Die vier Journeys (Given/When/Then)

### J1 · Onboarding — `onboarding.journey.test.tsx`
> Persona: Neuer Operator. Ziel: vom Erststart zur laufenden Konsole.

- **Gate** — *Given* eine bereits eingerichtete Installation (Keychain gefüllt) *When* die App startet *Then* wird der Wizard übersprungen und die Konsole geöffnet.
- **Happy Path (Ollama)** — Name → Alpaca live-validiert → lokales Ollama provisioniert (Fortschritt streamt) → „Launch" → Konsole erscheint.
- **Happy Path (Gemini)** — Cloud-Provider, Key gespeichert, erreicht den Launch-Schritt.
- **Guard** — „Continue" bleibt deaktiviert, bis das Pflichtfeld gefüllt ist.
- **Fehler: Alpaca lehnt ab** (HTTP 403) → Fehlermeldung, bleibt auf dem Schritt.
- **Fehler: Alpaca nicht erreichbar** (status 0) → Verbindungs-Hinweis.
- **Fehler: Ollama braucht manuelle Installation** → handlungsleitende Meldung, kein Fortschritt.

### J2 · Support — `support.journey.test.tsx`
> Persona: Täglicher Operator. Ziel: die laufende Engine im Klartext fragen.

- **Landing** — die Konsole öffnet auf der Chat-Support-Fläche mit Leerzustand.
- **Frage→Antwort** — Frage wird echoed + von der Engine beantwortet.
- **Multi-Turn** — Folgefrage; das vollständige Transkript bleibt erhalten (Store).
- **Engine offline** — saubere degradierte Zeile statt Absturz.

### J3 · Settings — `settings.journey.test.tsx`
> Persona: Support/Betrieb. Ziel: Engine-Zustand sehen & sicher steuern.

- **Status + Log-Replay** beim Öffnen.
- **Start** kippt den Status live über IPC auf „running"; Button-Disabled-States folgen dem Lifecycle.
- **Live-Log** — eine gestreamte Zeile erscheint sofort im Pane.
- **Engine-Fehler** mit Diagnose-Detail.
- *Hinweis:* die re-portierten **Sicherheitskontrollen** (Execution-Mode-Preference, **Emergency-Kill-switch → `POST /stop`**) sind auf Komponentenebene in `consoleSettings.test.tsx` abgedeckt; diese Journey besitzt den Lifecycle-Flow.

### J4 · Operativer Handel — `trading.journey.test.tsx`
> Persona: Täglicher Operator. Ziel: das Buch überwachen.

- **Positions** — das Engine-Buch fließt durch Polling→Adapter→Tabelle (AAPL/NVDA/MSFT).
- **Reports** — das jüngste Round-Table-Verdikt pro Symbol (AAPL/TSLA/NVDA) rendert.
- **Overview** — Equity & offene-Positions-Zahl aus demselben Buch.
- **Navigation** — das Buch bleibt konsistent beim Wechsel zwischen Flächen.
- **Warming up** — leere Engine-Antworten zeigen ehrliche Leerzustände, keine Null-Werte.
- *Deferred:* die **HITL Approve/Reject-Decision-Queue** ist auf `main` ein bewusster Stub (kein Engine-Endpoint — GAP2); sie kommt in diese Journey, sobald der Endpoint landet.

## 7. Funktionale Testfälle (Matrix)

Status: ✅ implementiert & grün · 🔵 Komponententest-Ebene · ⏳ deferred (Engine-Gap).

| ID | Journey | Vorbedingung | Schritte | Testdaten | Erwartet | Status |
|----|---------|--------------|----------|-----------|----------|--------|
| **OB-01** | Onboarding | Keychain gefüllt | App start | `makeBridge({hasKeychain:true})` | Wizard übersprungen, Konsole offen | ✅ |
| **OB-02** | Onboarding | Erststart | Name→Alpaca→Ollama→Launch | valid Alpaca, `ollamaSuccess` | Konsole erscheint | ✅ |
| **OB-03** | Onboarding | Erststart | …→Gemini-Key→ | `sampleKeys.geminiKey` | erreicht Launch | ✅ |
| **OB-04** | Onboarding | Welcome-Schritt | Feld leer | — | „Continue" disabled→enabled | ✅ |
| **OB-05** | Onboarding | Alpaca-Schritt | falsche Keys | `alpacaRejected` (403) | Fehler, kein Advance | ✅ |
| **OB-06** | Onboarding | Alpaca-Schritt | offline | `alpacaUnreachable` (0) | „Couldn't reach Alpaca" | ✅ |
| **OB-07** | Onboarding | LLM-Schritt | Ollama fehlt | `ollamaNeedsManual` | Install-Hinweis, kein Advance | ✅ |
| **SUP-01** | Support | Konsole offen | — | — | Chat-Leerzustand sichtbar | ✅ |
| **SUP-02** | Support | Engine läuft | Frage senden | `chat.reply` | Echo + Antwort | ✅ |
| **SUP-03** | Support | nach 1. Antwort | Folgefrage | `chat.followUpReply` | Transkript (4 Msgs) erhalten | ✅ |
| **SUP-04** | Support | Engine offline | Frage senden | `sendChat→null` | degradierte Zeile | ✅ |
| **SET-01** | Settings | Engine stopped | Settings öffnen | `engineLogs` | Status + Log-Replay | ✅ |
| **SET-02** | Settings | Engine stopped | Start klicken | IPC-Emit `running` | Status flippt, Buttons folgen | ✅ |
| **SET-03** | Settings | Engine running | Log-Stream | `emitLog(...)` | Zeile erscheint live | ✅ |
| **SET-04** | Settings | Engine running | Fehler-Emit | `emitStatus(error,detail)` | Fehler + Diagnose | ✅ |
| **SET-05** | Settings | Engine running | Kill-switch arm→confirm | `api.stop` | `POST /stop`, „Engine halted" | 🔵 `consoleSettings` |
| **SET-06** | Settings | — | Execution-Mode wählen | localStorage | Preference persistiert | 🔵 `consoleSettings` |
| **TRD-01** | Handel | Buch vorhanden | →Positions | `portfolioSummary` | 3 Zeilen, „3 positions" | ✅ |
| **TRD-02** | Handel | Verdikte vorhanden | →Reports | `roundTableDecisions` | AAPL/TSLA/NVDA, „3 decisions" | ✅ |
| **TRD-03** | Handel | Buch vorhanden | →Overview | `portfolioSummary` | Equity €105.000 | ✅ |
| **TRD-04** | Handel | Buch vorhanden | Nav P→R→P | kanonisch | konsistentes Buch | ✅ |
| **TRD-05** | Handel | Engine warming up | →Positions/Reports | `portfolioEmpty`/`roundTableEmpty` | ehrliche Leerzustände | ✅ |
| **TRD-06** | Handel | HITL-Decision offen | approve/reject | — | (Engine-Endpoint fehlt) | ⏳ GAP2 |

## 8. Browser-E2E (Playwright-Ebene)

Dieselben Szenarien im echten Chromium gegen den Dev-Server. Die Konsole wird über einen
injizierten `window.aaagents`-Bridge (`page.addInitScript`) erreichbar gemacht und die Engine
per `page.route` abgefangen — Harness + Referenz-Specs unter `src/test/e2e/`. Lauf:
`npm run test:e2e`. (Setzt den Desktop-Login-Bypass voraus, siehe `fix/desktop-console-login-wall`.)

## 9. Pflege

- Neue Engine-Felder → zuerst die Fixture in `consoleFixtures.ts` erweitern (eine Quelle).
- Neuer Screen / re-portierte UX → die betroffene Journey ergänzen, nicht duplizieren.
- HITL-Endpoint landet → TRD-06 aktivieren + J4 erweitern.
