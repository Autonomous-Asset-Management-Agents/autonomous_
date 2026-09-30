# Implementation Plan — #3349 Earnings-Proximity-Guard (dark)

**Epic:** #2963 — Verhaltens-Audit der Paper-Verlustwoche 08/2026 (Defekt-Klasse *Entry-Time-Churn*)
**Sub-Issue:** #3349
**P-Level:** **P1** (Trading-Pfad-Verhaltensänderung) → `plan-approved`-Gate, danach Code-PR mit echtem TDD.
**Autor:** gorg4444 · Review: Antigravity/apeldorn

---

## 1. Problem & Evidenz

Live-Paper, laufende Analyse dieser Sitzung (read-only auf `aaagents.db`, `is_simulation=0`):

- 56 geschlossene Round-Trips (FIFO aus `decision_outcomes`, `voted_price` als Ein-/Ausstiegs-Proxy). **22 davon `−4%`-Loss-Cuts** (`triggered_by_stop=1`, `stop_type LIKE 'LOSS CUT%'`) — Summe **−96,7 pp**, der dominante Verlustpfad (Rotation +58 pp ist abgeschaltet, `ROTATION_EXIT_ENABLED` default False, `config.py:587`).
- EDGAR-quantifiziert (8-K Item 2.02 / 10-Q `filed`, Ticker→CIK aus `core/specialist/data/company_tickers_snapshot.json`):
  - **±3 Tage um eine Meldung: 3/22** — INCY (+1d), CNC (+1d), MPWR (+1d) = **17 %** des Loss-Cut-Schadens.
  - **±7 Tage: 4/22** (+ LITE −7d) = **24 %**.
  - Belegtes Muster (Web + EDGAR): **höchster Konsens im ganzen Datensatz** (0,906 / 0,911 / 0,916), Kauf **genau 1 Tag nach** dem Earnings-8-K, binnen 1–3 Tagen `~−5%` gecuttet → „sell-the-news"-Reversal (CNC-Beleg: 8-K 2.02 filed 2026-07-28, Kauf 07-29).

**Abgrenzung (ehrlich):** Earnings erklärt **nicht** die Mehrheit der Loss-Cuts (~76 % liegen 8–36 Tage von jeder Meldung entfernt, mitten im Quartal → getrennter Hebel „Stop-Breite", eigenes Sub-Issue). Dieser Guard adressiert gezielt den **sauber abgegrenzten ~1/5-Anteil**.

**Ursachen-Verifikation im Code:** Es existiert **kein** Earnings-Bewusstsein — kein `days_to_earnings`, kein Blackout in `core/risk_manager.py` oder Round Table; „earnings" kommt nur als Fundamental-Kennzahl vor (`core/round_table/agents.py:2227`, PEG). Der Round Table behandelt einen frisch auf Earnings gestiegenen Titel wie jeden anderen — Momentum/LSTM **bevorzugen** ihn sogar (jüngste Stärke), daher ist der Konsens ausgerechnet am Reversal-Punkt maximal.

---

## 2. Soll-Verhalten

Eine **BUY-only Einstiegs-Sperre** im Melde-Fenster einer NEUEN Position:

- **POST** (exakt): kein neuer BUY innerhalb `POST_DAYS` Kalendertagen **nach** dem letzten 8-K Item 2.02 (Fallback 10-Q `filed`) des Symbols.
- **PRE** (heuristisch, default aus): kein neuer BUY innerhalb `PRE_DAYS` Tagen **vor** dem *geschätzten* nächsten Meldedatum (= letztes Meldedatum + Median-Kadenz der letzten Berichte). EDGAR kennt kein künftiges Datum → bewusst heuristisch, default `0`.

**Nie betroffen:** SELL, Risiko-Exits (Stop-Loss, DrawdownGuard, Trailing), Rebalance/Trim. Der Guard ist ausschließlich ein Gate für die *Eröffnung* neuer Positionen.

**Fail-Closed bei Netzwerkfehlern:** Können EDGAR-Daten aufgrund von Netzwerk-/Parsefehlern nicht geladen werden, erfolgt ein **Veto** (`blocked:earnings_data_missing`), da das Risiko unbemessbar ist. Liefert EDGAR jedoch erfolgreich *keine* Berichte (z.B. für ETFs), wird *kein* Veto ausgelöst.

---

## 3. Architektur & Dual Design

### Dual Design Abwägung (Archon-Requirement)

**Option A: EDGAR-Parsing als Engine-Guard (Gewählte Lösung)**
- *Ansatz:* Lokales Parsing von 8-K (Item 2.02) Dokumenten über den bestehenden SEC-Feed (`sec_fundamentals.py`).
- *Vorteile:* Keine neuen API-Keys nötig, keine Abhängigkeit von Drittanbietern (Finnhub/FMP), BaFin-Compliance bleibt intakt (keine Paid-APIs). 100% deterministisch simulierbar.
- *Nachteile:* Das zukünftige "PRE"-Fenster kann nur grob über historische Cadenzen geschätzt werden, da EDGAR keinen Vorwärtskalender bietet.

**Option B: Externe Earnings-Calendar API (Finnhub / FMP)**
- *Ansatz:* Integration einer externen API, die exakte historische und zukünftige (PRE) Earnings-Daten liefert.
- *Vorteile:* Das PRE-Fenster wäre absolut präzise, kein Kadenz-Schätzen nötig.
- *Nachteile:* Führt eine neue externe Abhängigkeit ein, erfordert API-Keys, bricht potenziell die Offline-Fähigkeit für Backtests.

**Trade-offs & Fail-Open vs. Fail-Closed Diskussion:**
Wir wählen **Option A**, da die System-Architektur (Offline-Fähigkeit, keine neuen Keys in OSS) Vorrang vor der absoluten Präzision des (ohnehin per Default deaktivierten) PRE-Fensters hat. 

Bezüglich der **Fail-Open vs. Fail-Closed** Semantik bei fehlenden EDGAR-Daten:
- *Die Warnung des Audits:* Da es sich um einen Risiko-Guard handelt, der hohe Verluste (-4% Loss-Cuts) verhindern soll, wäre "Fail-Closed" (Blockiere den Kauf, wenn Daten fehlen) eigentlich sicherer.
- *Die Entscheidung:* Wir modifizieren das Verhalten für diesen spezifischen Guard zu **Fail-Closed**, falls ein Datenabruf-Fehler (z.B. API down) vorliegt. Das bedeutet: Können die EDGAR-Daten für ein Symbol nicht geladen oder geparst werden, wird ein *neuer BUY* aus Sicherheitsgründen blockiert (`execution_outcome='blocked:earnings_data_missing'`). Dies verhindert blinde Käufe in potenziell riskanten Zeiträumen und respektiert die Iron-Dome-Prinzipien besser als Fail-Open. (Ein reiner "Keine Daten auf EDGAR gemeldet"-Zustand bei erfolgreichem API-Call bleibt weiterhin gültig/no-veto).

---

### 3.1 Neues Modul `core/engine/earnings_guard.py`
Reine, seiteneffektfreie Funktion nach Vorbild `_minutes_since_market_open` (`portfolio_context.py:54`):

```
def earnings_block_reason(
    report_dates: Sequence[date],     # bekannte Meldedaten (8-K 2.02 bevorzugt), aufsteigend
    now: date,                        # engine_now(...).date() — NIE wall-clock
    post_days: int, pre_days: int,
    next_estimate: Optional[date],    # geschätztes nächstes Datum (PRE); None ⇒ PRE inaktiv
) -> Optional[str]:                   # Grund-String oder None (kein Veto = fail-open)
```

- POST: `0 <= (now - last_report).days <= post_days` → `"earnings +Nd (8-K 2.02 <date>)"`.
- PRE: `next_estimate and 0 <= (next_estimate - now).days <= pre_days` → `"earnings -Nd (est <date>)"`.
- Erfolgreicher Abruf, aber keine Daten (z.B. ETF) ⇒ `None` (Kein Veto).
- Fehlschlag beim Datenabruf (API/Network Error) ⇒ `"earnings_data_missing"` (Fail-Closed).

### 3.2 Daten: EDGAR-Meldedaten (erweitert #3145)
`core/report/sec_fundamentals.py` liest heute nur `_ANNUAL_FORMS` (`:33`) und liefert `filing_date` (`:162`). **Erweiterung:** eine Funktion `earnings_report_dates(cik) -> list[date]` aus der Submissions-`recent`-Liste:
- `form == "8-K"` **und** `"2.02" in items` (Results of Operations = Earnings-Release) → exaktes Datum.
- Fallback `form in {"10-Q","10-K","20-F"}` `filed`, falls kein 8-K/2.02 im Fenster.
- Kadenz-Schätzung: `next_estimate = last + median(diff(report_dates))` (nur für PRE).

**Caching:** Meldedaten je Symbol in einer Tages-Cache-Datei/Tabelle (Refresh 1×/Tag). Netzabruf strikt via bestehendem EDGAR-Client (User-Agent Pflicht), Rate-Limit ≤10 req/s. Offline (`REDIS_URL=""`/Desktop) ⇒ Cache-only, sonst fail-open.

### 3.3 Verdrahtung (Naht)
Der Guard reiht sich in die **BUY-Einstiegs-Gate-Familie** ein, dort wo `NO_BUY_OPENING_MINUTES`/`GLOBAL_BUY_COOLDOWN_MINUTES` greifen (Frequenz-Guards, `trading_settings.py:161-163`). Konkret der Punkt im Round-Table-/Order-Pfad, an dem eine neue Eröffnung freigegeben wird (`core/round_table/runner.py` BUY-Zweig bzw. `order_executor`). Bei Veto: BUY unterdrückt, `decision_outcomes.execution_outcome='blocked:earnings'`, `execution_reason=<Grund>`. **Integrationstest** stellt sicher, dass der Default-Pfad den Guard wirklich konsultiert (wire-the-seams, vgl. `feedback_wire_the_seams_test_integration`).

### 3.4 Settings + WORM
Registrierung in `core/trading_settings.py::_SETTINGS` (`:106`) — erbt automatisch die SHA-256-WORM-Kette (`audit-chain.cjs`, Header `:24-28`) und den `/api/trading-settings`-Endpoint. Einfügen im Block „Frequenz-Guards" (`:159`):

```
_b("EARNINGS_GUARD_ENABLED", False),                 # dark → byte-identisch
_i("EARNINGS_GUARD_POST_DAYS", 2, 0, 10),            # Kalendertage NACH Meldung
_i("EARNINGS_GUARD_PRE_DAYS", 0, 0, 10),             # vor gesch. nächster Meldung (0=aus)
```

Defaults gespiegelt in `config.py` **und** `config.oss.py` (BORA-Parität, `feedback_bora_stringent`). Dark-Default ⇒ byte-identisch, LOW-Risk-Merge möglich.

---

## 4. Change Impact Analysis (CIA — WoW §1.4, verbindlich)

| Dimension | Bewertung |
|---|---|
| **Radius** | `core/engine/earnings_guard.py` (neu), `core/report/sec_fundamentals.py` (+`earnings_report_dates`), Round-Table-BUY-Naht (`runner.py`/`order_executor`), `core/trading_settings.py` (+3 Settings), `config.py`+`config.oss.py` (+3 Defaults). **SELL-/Risiko-Exit-Pfade unberührt.** Enterprise+OSS. |
| **Severity** | **LOW dark** (Default OFF ⇒ byte-identisch, Guard-Funktion nicht aufgerufen bzw. gibt `None`). **MEDIUM aktiviert**: reduziert Kaufzahl; Nebenwirkung = auch *gute* Post-Earnings-Käufe können gesperrt werden (Präzision vs. Schutz) → erst Paper/Prod messen. |
| **Rollback** | Flag OFF ⇒ sofort byte-identisch; PR-Revert. **Keine Schema-Migration**, wenn `execution_outcome='blocked:earnings'` (String, kein neues Feld) wiederverwendet wird. Wird doch eine Spalte gewünscht: additive Alembic-Migration (Desktop `create_all()` bootstrappt selbst). |
| **Compliance** | EDGAR frei, kein Key, **keine Kundendaten** (BaFin, `project_training_data_own_only_bafin`). Keine Anlageberatung/Risikoprofile (`feedback_no_risk_profiles_neutral_settings_only`) — neutrale Betriebseinstellung. US-Filer-Coverage vollständig fürs Universum; ETFs (QQQ/IWM) haben keine Earnings ⇒ Guard no-op. |

---

## 5. TDD (Red → Green, Schicht + Naht)

Neue Tests (unit, `AsyncMock` für async-Nähte, `feedback` §5.2):
1. `test_earnings_guard.py::test_post_window_blocks` — Report 2026-07-28, POST_DAYS=2 → BUY 07-29 vetoed, BUY 07-31 (3d) erlaubt.
2. `::test_pre_window_blocks` — next_estimate gesetzt, PRE_DAYS=3 → BUY 3d davor vetoed, 4d davor erlaubt.
3. `::test_no_data_no_veto` — erfolgreicher API Call, leere report_dates + kein estimate → `None` (nie Veto).
3b. `::test_network_error_fails_closed` — Exception beim EDGAR Abruf → Veto (`blocked:earnings_data_missing`).
4. `::test_disabled_byte_identical` — ENABLED=False ⇒ Naht ruft Guard nicht bzw. Kaufverhalten identisch.
5. `::test_uses_engine_clock_not_wallclock` — `now` kommt aus `engine_now`, nicht `datetime.now()` (Sim-Determinismus, die Sim-Clock-Lehre).
6. **Integration** `test_buy_path_consults_earnings_guard` — Default-BUY-Pfad ruft den Guard und respektiert das Veto (Naht existiert nicht nur, sie ist verdrahtet).
7. `test_sec_fundamentals::test_earnings_report_dates` — 8-K/2.02 wird als Earnings erkannt, 5.02-only-8-K nicht; 10-Q-Fallback.

---

## 6. Gherkin (CIA-Akzeptanz)

```gherkin
Scenario: Kauf am Tag nach einem Earnings-Bericht wird gesperrt
  Given ein 8-K Item 2.02 fuer AAPL mit filed=2026-07-28
  And EARNINGS_GUARD_ENABLED=true und EARNINGS_GUARD_POST_DAYS=2
  When der Round Table AAPL am 2026-07-29 eroeffnen will
  Then wird der BUY vetoed mit execution_outcome="blocked:earnings"
  And ein bestehender AAPL-Risiko-Exit feuert unveraendert

Scenario: Fail-Closed bei API/Netzwerkfehler
  Given ein Verbindungsfehler zu EDGAR (offline/Parsefehler) für XYZ
  When der Round Table XYZ eroeffnen will
  Then wird der BUY aus Sicherheitsgründen blockiert (blocked:earnings_data_missing)

Scenario: Kein Veto wenn API antwortet aber keine Berichte vorhanden sind
  Given EDGAR meldet erfolgreich 0 Berichte für XYZ (z.B. ETF oder neues Listing)
  When der Round Table XYZ eroeffnen will
  Then feuert der Guard NICHT und der BUY laeuft normal

Scenario: Guard aus ⇒ byte-identisch
  Given EARNINGS_GUARD_ENABLED=false
  Then ist das Kaufverhalten identisch zum Stand ohne Guard
```

---

## 7. Doku-Pflichten (nach Merge)

- `FEATURE_FLAGS.md` **regenerieren** (`scripts/gen_feature_flags.py` vom Repo-Root — nie handeditieren, `reference_feature_flags_generated`).
- Default-Settings-Übersicht aktualisieren (verbindliche Pre-Merge-Prüfung).
- Walkthrough-Fragment `docs/walkthroughs/PR_3349_earnings_guard.md`.
- `docs/llms.txt` Cross-Domain-Eintrag falls nötig.

---

## 8. Offene Entscheidung (im Review zu bestätigen)

- **PRE-Fenster-Genauigkeit:** Bis ein echter Vorwärts-Earnings-Kalender existiert, ist `next_estimate` nur kadenz-geschätzt → `PRE_DAYS` default `0`. Ein Folge-Sub-Issue könnte einen freien Vorwärtskalender ergänzen (Finnhub-Free/FMP-Free als Kandidaten — externe Keys, gegen No-Paid-Regel zu prüfen).
- **Epic-Heimat #2963** vs. #3086: als #2963 gewählt (Entry-Timing-Defekt der Verlustwoche); re-homebar, falls der Owner #3086 bevorzugt.
