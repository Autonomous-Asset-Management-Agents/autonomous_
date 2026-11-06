# language: de
@epic-5169
Funktionalität: [Epic] KONTEN — Alle Konten unter Trading keys, Strategie je Konto an einer Stelle (#5169)
  e2e: Alle vier Konten mit maskierter Nummer unter Trading keys, Strategie je Konto an einer Stelle

  Zweck-Kennzahl: Konten in der Kachel „Trading keys" mit maskierter Kontonummer — vorher 0 von 2 (Datei fehlt; `accountMasked`/`maskAccount` in TradingKeysCard.tsx und BrokerKeysCard.tsx: 0 Treffer, gemessen 05.11.2026), Ziel 4 von 4 (Live „—" mit Hinweis bei Keys von vor dem Update zählt als erfüllt) (Messung: `npx vitest run src/test/tradingKeysCard.test.tsx`)

  Diese Abnahme ist beim Anlegen rot und bleibt es, solange das Epic offen ist.
  Sie wird grün, wenn alle Sub-Issues von #5169 umgesetzt sind — und
  erst dann darf der xfail-Marker in den Schrittdefinitionen fallen.

  Szenario: Alle Konten an einer Stelle
  Angenommen Paper-Keys aus dem Setup, Live-Keys und ein verbundener Paper account 2
  Wenn der Betreiber Settings → System öffnet
  Dann zeigt "Trading keys" Paper account 1, Paper account 2, Paper account 3 und Live
  Und jedes verbundene Konto zeigt seine maskierte Kontonummer

  Szenario: Live-Nummer nach dem Speichern
  Wenn der Betreiber gültige Live-Keys prüft und speichert
  Dann zeigt der Bereich Live die maskierte Nummer des Live-Kontos
  Und die volle Nummer erreicht die Console nicht

  Szenario: Live-Keys aus der Zeit vor dem Update
  Angenommen Live-Keys, die vor diesem Epic gespeichert wurden
  Dann zeigt der Bereich Live "—" und "Re-enter the live keys to show the account number."

  Szenario: Engine aus
  Angenommen die Haupt-Engine läuft nicht
  Wenn der Betreiber Settings → System öffnet
  Dann zeigt "Trading keys" alle Konten mit Nummer und gespeicherten Keys

  Szenario: Strategien aus
  Angenommen Strategien sind aus
  Dann sind Paper account 2 und 3 unter Trading keys sichtbar, aber nicht bedienbar
  Und ein Link führt zu "Enable strategies" auf Trading → Strategies

  Szenario: Live-Wechsel aus dem Bereich Live
  Angenommen die Haupt-Engine handelt Paper account 1 und Live-Keys sind gespeichert
  Wenn der Betreiber im Bereich Live "ENABLE LIVE TRADING" wählt
  Dann öffnet derselbe Freigabe-Dialog wie über die Seitenleiste
  Und die Aktion trägt die kritische Pill wie das Referenz-Control in der Seitenleiste

  Szenario: Zurück zu Paper
  Angenommen die Haupt-Engine handelt Live
  Wenn der Betreiber im Bereich Paper account 1 "Switch main engine to paper" wählt
  Dann läuft der heutige Wechsel zurück zu Paper

  Szenario: Strategie je Konto an einer Stelle
  Wenn der Betreiber Trading → Strategies öffnet
  Dann zeigt "Strategy per account" eine Zeile je Konto (Live, Paper 1–3)
  Und Live wird über den heutigen Prüfdialog zugewiesen, Paper über die Platz-Zuweisung
  Und Paper account 1 zeigt "Set in the setup"
  Und es gibt keine zweite Karte für die Live-Zuweisung

  Szenario: Dialoge nach dem Design-Guide
  Wenn ein Dialog auf Trading → Strategies offen ist
  Dann tragen alle Knöpfe die .btn-Gestaltung der Konsole mit weißer Schrift
