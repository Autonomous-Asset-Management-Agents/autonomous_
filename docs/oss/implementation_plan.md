# OSS Phase 1 — Scope Hardening & Frontend Remediation

Removes the dual key-entry vectors (dashboard UI + backend DB write) identified in the
post-mortem audit of the OSS Community Edition, and establishes `.env.oss` as the
exclusive credential source.

## Dual Design

| Aspekt | Option A: Formular entfernen (Gewählt) | Option B: Formular deaktivieren (Verworfen) |
|---|---|---|
| **Sicherheit** | Keine Credentials im Browser-State möglich | State existiert weiterhin, nur Button disabled |
| **Angriffsfläche** | Null — kein Input-Event, kein API-Call | Theoretisch via DevTools manipulierbar |
| **UX-Klarheit** | Explizite `.env.oss`-Anleitung sichtbar | Nur graues Formular → Nutzer verwirrt |
| **Code-Komplexität** | −86 Zeilen (Netto) | +4 Zeilen disabled-Attribute |
| **Wartung** | Kein toter Event-Handler im Codebase | Toter Handler bleibt, muss künftig mitgepflegt werden |

**Entscheidung:** Option A. Ein deaktiviertes Formular erfüllt die Sicherheitsanforderung nicht
(State lebt weiterhin im Browser). Die vollständige Entfernung ist die einzige Lösung, die
die README-Aussage "dashboard does not store or transmit API keys" als invariante Wahrheit verankert.

### Option A

Vollständige Entfernung des Formulars und aller zugehörigen State-Hooks, Imports und API-Calls aus
`BrokerConnectionWidget`. Ersatz durch ein statisches `.env.oss`-Instruktions-Panel.

### Option B

Deaktivierung des Formulars via `disabled`-Attribut und Hinzufügen eines Hinweis-Textes.
Formular-State, API-Imports und Handler-Funktion bleiben im Code erhalten.

### Trade-off

Option A löscht 86 Zeilen Code (netto), eliminiert die gesamte API-Aufruf-Kette vom Browser,
und erzeugt ein UX-Element das aktiv erklärt was zu tun ist. Option B hat geringeres
Diff-Risiko, löst aber das eigentliche Problem nicht.

## Proposed Changes

### Frontend — OSS Dashboard

#### [MODIFY] `src/components/BrokerConnectionWidget.tsx`

- Entfernung aller Input-Felder, State-Hooks und API-Calls
- Entfernung des toten `onConnectStart`-Props aus dem Interface
- Ersatz durch statisches `.env.oss`-Instruktions-Panel

## Verification Plan

### Automated Tests
- `Frontend Lint & Typings` CI-Gate muss grün sein (TypeScript-Kompilierung ohne Fehler)
- `Frontend` CI-Gate muss grün sein

### Manual Verification
- Dashboard öffnen: kein Eingabeformular für Alpaca-Keys mehr sichtbar
- "Not Connected"-Zustand zeigt `.env.oss`-Code-Block mit korrekten Variablennamen
