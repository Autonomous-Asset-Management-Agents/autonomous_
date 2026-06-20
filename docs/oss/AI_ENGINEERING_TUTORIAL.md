# Wie man mit KI komplexe Systeme baut
## Das vollständige AI-Engineering Tutorial — Die AAAgents Fallstudie

> Dieses Tutorial richtet sich an Entwickler, die mit KI-Assistenten mehr als einfache Skripte bauen wollen — an alle, die verteilte, regulierte, produktionsreife Systeme **gemeinsam mit autonomen Agenten** entwickeln. Es basiert auf der realen Entwicklungsgeschichte der **AAAgents Plattform** (Autonomous Asset Management Agents), einem algorithmischen Trading-System unter MiFID II Regulierung.

---

## Die vier Kompetenz-Säulen

Dieses Tutorial ist entlang von vier Säulen gegliedert, die sich in der Praxis als unverzichtbar erwiesen haben. Wer nur eine Säule beherrscht, wird scheitern.

| Säule | Kurzbeschreibung | Kernfrage |
|---|---|---|
| 🧠 **AI Fluency** | Verständnis von Modellverhalten, Bias & Governance | *Warum tut die KI das, was sie tut?* |
| 🔍 **Urteilskraft & Audit** | Halluzinationen erkennen, Workflows kritisch validieren | *Kann ich dem Output vertrauen?* |
| 🎼 **Orchestrierung & Vibe Coding** | Multi-Agenten-Systeme steuern, Anforderungen in Sprache definieren | *Wie bekomme ich das System zum Gehorsam?* |
| 🛡️ **Psychologische Sicherheit** | Sicherer Experimentierraum, Überwindung der Perfectionist Trap | *Darf ich hier Fehler machen?* |

---

## Phase 1: Inception & Prototyping — Der Kampf mit dem Kontext

### Was gebaut wurde

Die erste Version bestand aus PPO (Reinforcement Learning) und LSTM-Netzwerken, gekoppelt an einen monolithischen Python-Bot. Schnelles Prototyping war möglich — aber es entstanden „God-Files": `engine.py` wuchs auf über **2.650 Zeilen**, `strategies.py` auf **2.400 Zeilen**, mit zyklomatischer Komplexität von >130.

---

### 🧠 AI Fluency: Das Kontext-Fenster ist physikalisch begrenzt

KI-Modelle sind keine Datenbanken. Sie haben kein Langzeitgedächtnis. Alles, was sie „wissen", liegt in einem begrenzten **Context Window** (typischerweise 128k–1M Tokens). Wenn dein Prompt-Kontext zu groß wird, passiert Folgendes:

- **Silences statt Fehler:** Das Modell „vergisst" leise Code aus dem Anfang der Datei und überschreibt ihn mit neuem, inkonsistentem Code.
- **Intra-File-Konflikte:** In einer 2.600-Zeilen-Datei kann das LLM die Abhängigkeit zwischen Zeile 120 und Zeile 1.840 nicht halten.
- **Konfidenz-Täuschung:** Das Modell antwortet weiterhin mit hoher Konfidenz, auch wenn sein internes Bild der Datei bruchstückhaft ist.

> [!IMPORTANT]
> **Gesetz #1 der AI Fluency:** Modulare, kleine Dateien sind keine Stil-Frage. Sie sind eine **physikalische Notwendigkeit** für das Context Window. Jede Datei über 400 Zeilen ist ein Risiko für die KI-gestützte Entwicklung.

### 🔍 Urteilskraft: Woran erkennst du Context-Overflow?

Erkennungsmerkmale, wenn ein Modell „die Datei verloren hat":

1. **Neu-Initialisierung von Variablen**, die bereits existieren
2. **Import-Wiederholungen** für bereits importierte Module
3. **Plötzlich andere Namenskonventionen** (`camelCase` statt `snake_case`)
4. Die KI fragt dich: *„Kannst du mir nochmal den Anfang der Datei zeigen?"*

### 💡 Key Takeaway

> God-Files sind KI-Killer. Der spätere, schmerzhafte Refactor (Epic QUA-2 — Aufspaltung in 8 Module) war der Befreiungsschlag, der autonome Entwicklung erst wieder möglich machte.

---

## Phase 2: Die Sicherheits-Architektur — Compliance by Design

### Was gebaut wurde

Harte Failsafes: ein **Automated Kill-Switch** (löscht alle Orders bei GCP-Latenzen >2000ms) und der **Iron Dome** — ein deterministisches Pre-Trade-Kontrollsystem nach MiFID II.

---

### 🧠 AI Fluency: Bias ist kein Bug, er ist strukturell

Im Round Table V2 stimmen 9 Agenten über jedes Handelssignal ab. Die Gewichte der Agenten sahen initial so aus:

```python
# Initiale, naive Gewichtung (FALSCH)
DrawdownGuardAgent   weight = 10.0
MomentumAgent        weight = 10.0
NewsSentimentAgent   weight = 10.0
LSTMSignalAgent      weight = 10.0
# ... alle gleich
```

Das Problem: **Gleichgewichtung ist kein Neutralität-Versprechen, sondern eine Bias-Entscheidung.** Wenn du einem News-Sentiment-Agenten (der auf einem LLM basiert und halluzinieren kann) dasselbe Gewicht gibst wie einem deterministischen LSTM-Modell (das auf historischen Daten trainiert wurde), sagst du damit implizit: *„Ich vertraue halluzinierbarem LLM-Output genauso wie validierten ML-Modellen."*

Das Produktiv-System löst das durch **explizite, begründete Gewichts-Asymmetrie:**

```
RegimeDetectionAgent    weight = 25.00  # Deterministisch, marktstrukturell
LSTMSignalAgent         weight = 25.00  # Trainiertes ML-Modell
RLConfidenceAgent       weight = 25.00  # RL-basiert, backtested
NewsSentimentAgent      weight = 25.00  # LLM (Gemini) — maximale Kontrolle durch Gatekeeper
DrawdownGuardAgent      weight =  0.60  # Veto-Kraft, aber geringe Grundgewichtung
SpecialistAlphaAgent    weight =  0.00  # GLOBAL DISABLED — kein valides Backtesting
```

> [!IMPORTANT]
> **Gesetz #1 der AI Governance:** Jedes Gewicht, jeder Threshold und jede Konfiguration einer KI ist eine **ethische Entscheidung**. Dokumentiere, warum ein Agent deaktiviert oder schwach gewichtet ist — nicht nur wie.

### 🔍 Urteilskraft: Der Gatekeeper außerhalb des Modells

Die wichtigste Regel im AAAgents-System: Der Iron Dome (`gatekeeper.py`) läuft **nach** dem LLM, **außerhalb** seiner Kontrolle. Das LLM kann kein Signal „durch den Gatekeeper mogeln":

```
1. 9 Agenten stimmen ab (inkl. Gemini LLM)
2. ConsensusEngine aggregiert → float [0.0, 1.0]
3. ComplianceGatekeeper.check() → VETO oder APPROVED
   ├── PDT-Regel: ≥3 Day Trades in 5 Tagen → VETO
   ├── Konzentrations-Limit: >25% Portfolio in einem Symbol → VETO
   └── Kill-Switch aktiv → VETO
4. Nur bei APPROVED → SignalEvent wird erzeugt
```

Das Modell „weiß" nicht, ob sein Signal vetoed wird. Es kann auch nicht dagegen argumentieren. Das ist by design.

> [!WARNING]
> **Anti-Pattern:** Wenn du einem LLM erlaubst, Compliance-Entscheidungen zu treffen (*„Ist dieser Trade regulatorisch korrekt?"*), bist du abhängig von seiner aktuellen Halluzinationsrate — und die ist nie null.

### 🛡️ Psychologische Sicherheit: Fail-Closed als Befreiung

Der Kill-Switch ist kein Zeichen von Schwäche — er ist das, was dir erlaubt, **mutig neue Features zu deployen**. Weil du weißt: wenn etwas fundamental schiefgeht, friert das System ein, anstatt Kapital zu verbrennen.

> *Der sichere Experimentierraum entsteht nicht durch Fehlerlosigkeit, sondern durch das Vertrauen in den Fallschirm.*

---

## Phase 3: CI/CD & Agentic Governance — Das Ende des „Agentic Drift"

### Was gebaut wurde

GKE-basierte ARC-Runner (saubere Container-Starts, 0 State Leakage), OpenTelemetry Tracing-Loops und strenge CI/CD-Gates. Der absolute Game-Changer: **Epic AUT-6 — AI-as-Judge**.

---

### 🧠 AI Fluency: Warum autonome Agenten driften

Mit zunehmender Autonomie entwickeln KI-Agenten toxische Muster:

**„Requirement Skipping"** — die KI programmiert schnell, lässt aber leise Lücken aus der ursprünglichen Planung. Sie meldet „fertig", obwohl 2 von 5 Anforderungen fehlen. Sie tut das nicht böswillig — sie optimiert auf die Nähe zum User-Prompt, nicht auf die Vollständigkeit eines 20-seitigen Plans.

**„Watermelon Reporting"** — außen grün (lokale Tests laufen), innen rot (Produktion crasht). Dieses Muster führte zu einem 3,5-Wochen-Outage in Staging (`RCA_2026_04_11_WATERMELON_EFFECT.md`).

> [!CAUTION]
> **Das gefährlichste Verhalten eines Agenten ist nicht der offensichtliche Fehler — es ist der stille, selbstbewusst formulierte Fehler, der wie Erfolg klingt.**

### 🔍 Urteilskraft: Der AI-as-Judge Gate

Die Antwort auf Agentic Drift war radikal: **Lass eine KI die andere KI prüfen.** In der GitHub Actions Pipeline läuft ein Gemini-Modell als deterministischer Auditor:

```yaml
# .github/workflows/ci.yml (vereinfacht)
- name: AI Judge — Archon Compliance Check
  run: |
    python .agent/scripts/ai_judge.py \
      --plan docs/current_task/implementation_plan.md \
      --diff ${{ github.event.pull_request.diff_url }} \
      --max-tokens 8192 \
      --fail-on-missing-requirements
```

Der AI Judge vergleicht **semantisch**:
- Was hat der Coding-Agent in `implementation_plan.md` versprochen?
- Was steht tatsächlich im Diff?
- Gibt es Anforderungen im Plan, die keine korrespondierende Code-Änderung haben?

Wenn der Judge eine Lücke findet, blockiert er den Merge. Der Mensch muss reviewen.

**Architecture-as-a-Prompt (Dual Design):**

```markdown
# implementation_plan.md — Dual Design Format

## Proposed Changes

### [MODIFY] gatekeeper.py — ComplianceGatekeeper
- Add PDT-check: if day_trades >= 3 in rolling 5-day window → VETO
- Evidence Anchor: `gatekeeper.py` L.47-89
- Test: `tests/unit/test_gatekeeper_pdt.py::test_pdt_blocks_on_third_trade`

### [NEW] tests/unit/test_gatekeeper_pdt.py
- Must cover: 0, 1, 2 trades (PASS) and 3+ trades (VETO)
```

Das `implementation_plan.md` ist keine Doku für Menschen mehr. Es ist der **Ground Truth Prompt** für die CI/CD-Pipeline.

### 🎼 Vibe Coding: Der Agent Deterministic Loop

Das obligatorische Workflow-Gesetz für jeden Agenten:

```
PLAN  → implementation_plan.md mit Evidence Anchors
  ↓
CODE  → Nur die Dateien, die im Plan beschrieben sind
  ↓
TEST  → pytest + flake8 lokal — KEIN Commit bei Rot
  ↓
ITERATE → Bei Fehler: lesen → fixen → testen (nie pushen bei Rot)
  ↓
COMMIT → Nur bei 100% grün, mit Walkthrough-Dokumentation
```

> [!IMPORTANT]
> **Gesetz #1 des Vibe Coding:** Natürliche Sprache definiert das **Was**. Der deterministische Loop erzwingt das **Wie**. Ohne den Loop wird Vibe Coding zu Chaos Coding.

### 🛡️ Psychologische Sicherheit: Checklisten befreien von Perfektionismus

Die Perfectionist Trap sieht so aus: *„Ich fange nicht an zu committen, bis ich sicher bin, dass alles perfekt ist."* Das Ergebnis: nichts wird committed, Angst vor dem Push wächst.

Der Deterministic Loop schneidet diese Spirale durch:

- **Du musst nicht wissen, ob der Code gut ist** — der Linter sagt es dir.
- **Du musst nicht raten, ob der Test grünt** — du führst ihn aus.
- **Du musst nicht ahnen, ob der AI Judge zustimmt** — du siehst es im PR.

> *Die Checkliste ersetzt das innere Urteil. Das innere Urteil erzeugt Angst. Die Checkliste erzeugt Fortschritt.*

---

## Phase 4: LangGraph & Asynchrone Autonomie — Die KI als Orchester

### Was gebaut wurde

Migration auf **LangGraph** (Asynchronous Orchestration). Round Table V2: 9 spezialisierte Agenten stimmen asynchron ab. P99-Latenz sank von >1400ms auf **28ms** — Faktor 50.

---

### 🎼 Orchestrierung: Wie ein Multi-Agenten-System entsteht

Das System wurde durch Vibe Coding definiert. Der initiale Prompt, der die gesamte Architektur auslöste:

> *„Baue ein demokratisches Abstimmungssystem, in dem 9 spezialisierte KI-Agenten pro Aktien-Symbol diskutieren und abstimmen. Jeder Agent hat eine Stimme zwischen 0.0 (Strong Sell) und 1.0 (Strong Buy). Ein Compliance-Gatekeeper hat Vetorecht. Das Ergebnis muss MiFID II auditierbar sein."*

Aus dieser einen Anforderung in natürlicher Sprache entstanden:
- `core/round_table/agents.py` (9 Implementierungen)
- `core/round_table/consensus.py` (Gewichtete Aggregation)
- `core/round_table/gatekeeper.py` (Compliance Veto)
- `core/orchestration/graph.py` (LangGraph State Machine)

```python
# graph.py — Die State Machine, definiert in ~40 Zeilen
from langgraph.graph import StateGraph, START, END

workflow = StateGraph(SymbolEvalState)
workflow.add_node("fetch_context", fetch_market_context)
workflow.add_node("run_strategy", run_round_table_node)
workflow.add_node("process_signal", process_signal_node)

workflow.add_edge(START, "fetch_context")
workflow.add_edge("fetch_context", "run_strategy")
workflow.add_edge("run_strategy", "process_signal")
workflow.add_edge("process_signal", END)

graph = workflow.compile(checkpointer=redis_checkpointer)
```

Das ist Vibe Coding in seiner reinsten Form: **Die Architektur liest sich wie natürliche Sprache.**

### 🧠 AI Fluency: Multi-Agent > Single Model

Warum 9 Agenten besser sind als ein großes Modell:

| Eigenschaft | Single LLM | Multi-Agent Round Table |
|---|---|---|
| **Halluzination** | Hoch — ein Fehler beeinflusst alles | Niedrig — Fehler wird von anderen Agenten überstimmt |
| **Nachvollziehbarkeit** | „Das Modell hat BUY gesagt" | Jeder Agent liefert `reasoning` (MiFID II Art. 13) |
| **Parallelisierung** | Sequenziell | `asyncio.gather(*votes)` — alle 9 gleichzeitig |
| **Ausfallsicherheit** | Single Point of Failure | Ausgefallener Agent meldet `weight=0.0` — System läuft weiter |
| **Update-Risiko** | Model-Update bricht alles | Ein Agent kann ausgetauscht werden, ohne das System zu stoppen |

### 🔍 Urteilskraft: MLSecOps — der KI-Cache als Angriffsfläche

Ein subtiles Sicherheitsproblem, das KI-Systeme fast exklusiv betrifft: **korrumpierte ML-Caches**.

Das System nutzte ursprünglich `pickle` zum Speichern von Modell-Zuständen. Ein Angreifer, der Zugang zum GCS-Bucket hat, könnte eine manipulierte `.pkl`-Datei einschleusen und damit **beliebigen Python-Code beim Laden des Modells ausführen** (Remote Code Execution).

Die Lösung: Migration zu `safetensors`:

```python
# VORHER — Kritische Schwachstelle
import pickle
model = pickle.load(open("model.pkl", "rb"))  # RCE möglich

# NACHHER — MLSecOps Standard
from safetensors.torch import load_file
model_weights = load_file("model.safetensors")  # Nur Tensor-Daten, kein Code
```

> [!CAUTION]
> **AI-spezifische Sicherheitslücke:** Jede KI-Anwendung, die Modell-Dateien aus externen Quellen lädt, ist anfällig für RCE durch korrumpierte Serialisierung. `pickle`, `joblib`, und `numpy.load(allow_pickle=True)` sind niemals sicher bei fremden Dateien.

### 🛡️ Psychologische Sicherheit: Stateful Serverless

Der Redis-Checkpointer in LangGraph löst ein tiefes psychologisches Problem bei der KI-Entwicklung: **die Angst vor dem Container-Absturz**.

Ohne Persistenz gilt: stirbt der Container mid-task, ist der gesamte Gedankengang verloren. Mit Redis-Checkpointing:

```python
# langgraph.checkpoint.redis.RedisSaver
# Bei Container-Absturz: State wird aus Redis restored
checkpointer = RedisSaver.from_conn_string(os.environ["REDIS_URL"])
graph = workflow.compile(checkpointer=checkpointer)
```

> *Du kannst einen laufenden Agenten mutig in Produktion deployen, wenn du weißt: sein aktueller Gedankengang überlebt den Neustart.*

---

## Phase 5: Open Source & Enterprise Separation — Market Readiness

### Was gebaut wurde

Trennung in **AAAgents Community Edition** (Local-First, Docker Compose, Shadow Boot) und Enterprise Edition (Vertex AI, AlloyDB, Firebase). Bereinigung aller proprietären Konfigurationen durch KI-gestützte Housekeeping-Agenten.

---

### 🔍 Urteilskraft: Physical Evidence Anchors

Die größte Versuchung bei KI-gestützter Entwicklung: der Aussage eines Agenten vertrauen, ohne sie zu verifizieren.

> *„Das Repo ist jetzt Open-Source-Ready."*

Glaube dieser Aussage nicht. Baue stattdessen Pipeline-Checks, die es **physisch beweisen**:

```python
# .agent/scripts/archon_linter.py (vereinfacht)
FORBIDDEN_PATTERNS = [
    r"GEMINI_PRO_API_KEY\s*=\s*['\"]AI[a-zA-Z0-9_-]+",  # Hardcoded API Keys
    r"aaa-production-\w+\.europe-west3",                   # GCP Project IDs
    r"projects/aaa-enterprise/",                           # Proprietary GCP paths
    r"AlloyDB|VertexAI|FirebaseAdmin",                     # Enterprise-only imports
]

def audit_file(filepath: str) -> List[str]:
    violations = []
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, content):
            violations.append(f"{filepath}: LEAK detected — {pattern}")
    return violations
```

**Evidence Anchors** sind konkrete, maschinenverifizierte Beweise:
- ✅ `grep -r "aaa-enterprise" . | wc -l` → `0` (beweist: kein proprietärer Pfad)
- ✅ `pytest tests/oss/test_shadow_boot.py -v` → `PASSED` (beweist: Community Edition startet)
- ❌ *„Ich habe alle proprietären Pfade entfernt"* (kein Beweis)

### 🎼 Vibe Coding: Systeme in natürlicher Sprache definieren

Der Shadow Boot wurde durch diesen Vibe Coding-Prompt spezifiziert:

> *„Erstelle einen Boot-Modus, in dem das System startet und Daten verarbeitet, aber keine echten Orders platziert. Wenn ein essentieller Service (Redis, Postgres) fehlt, soll das System in diesen Modus fallen statt zu crashen. Der Nutzer soll klar sehen, dass er im Shadow-Modus ist."*

Das Ergebnis war `shadow_boot.oss.py` — ein vollständiger Fallback-Boot-Prozess. Der Prompt enthielt **kein einziges Wort Python**. Er beschrieb nur das **Verhalten**.

> [!TIP]
> **Vibe Coding Best Practice:** Beschreibe **Verhalten und Grenzen** (was das System tut, was es niemals tut, wie es auf Fehler reagiert) — nicht die Implementierung. Lass den Agenten die Implementierung wählen.

### 🧠 AI Fluency: Governance über Prompt Engineering hinaus

Prompt Engineering ist das *Sprechen* mit einem Modell. AI Governance ist das *Einschränken* des Modells. Beides ist nötig, aber keins ersetzt das andere.

```
Prompt Engineering   → Bessere Inputs → Bessere Outputs
AI Governance        → Kontrollierte Boundaries → Sichere Outputs
```

Im AAAgents-System sind beide Ebenen aktiv:

| Ebene | Mechanismus | Beispiel |
|---|---|---|
| **Prompt** | `SymbolEvalState` Contract | Agenten bekommen nur skalare OHLC-Daten |
| **Architektur** | Iron Dome Gatekeeper | Veto außerhalb des LLM-Kontrollflusses |
| **CI/CD** | AI Judge, Archon Linter | Semantische Plan-Diff-Prüfung |
| **Recht** | MiFID II `reasoning` Field | Audit-Trail für jeden Trade-Entscheid |
| **Ops** | Kill-Switch (>2000ms Latenz) | Deterministisches Einfrieren |

---

## Die vier Säulen: Checklisten für die Praxis

### 🧠 AI Fluency — Lernpfad

- [ ] Verstehe das Context Window deines Modells (Tokens, nicht Zeilen)
- [ ] Kenne die Halluzinations-Trigger: Gewichts-Asymmetrie, veraltete Trainingsdaten, Konfidenz ≠ Korrektheit
- [ ] Trenne `LLM-generierte Entscheidungen` (probabilistisch) von `determinierten Regeln` (garantiert)
- [ ] Dokumentiere jede Gewichts- und Threshold-Entscheidung als ethische Aussage
- [ ] Lerne die Unterschiede: Prompt Engineering → AI Governance → MLSecOps

### 🔍 Urteilskraft & Audit — Lernpfad

- [ ] Baue Physical Evidence Anchors in jeden Deploy-Prozess ein
- [ ] Implementiere einen AI Judge in deiner CI-Pipeline (Diff vs. Plan)
- [ ] Schreibe `implementation_plan.md` so, dass eine KI ihn als Prüfschema nutzen kann
- [ ] Erkenne Watermelon Reporting: grüne Tests lokal, roter Stand in Prod
- [ ] Hinterfrage jede Aussage ohne Datei-Zeilen-Nachweis

### 🎼 Orchestrierung & Vibe Coding — Lernpfad

- [ ] Definiere System-Anforderungen in natürlicher Sprache (Verhalten, Grenzen, Fehlerreaktion)
- [ ] Nutze LangGraph oder ähnliche State Machines für nachvollziehbare Agenten-Flows
- [ ] Baue Multi-Agenten-Systeme mit klaren `TypedDict`-Kontrakten zwischen Knoten
- [ ] Erzwinge den Agent Deterministic Loop für jeden Code-Commit
- [ ] Trenne Orchestrierung (Workflow) von Implementierung (Code)

### 🛡️ Psychologische Sicherheit — Lernpfad

- [ ] Baue einen Fallschirm, bevor du springst: Kill-Switch, Shadow Boot, Fail-Closed
- [ ] Ersetze das innere Urteil durch externe Checklisten (Linter, Tests, AI Judge)
- [ ] Führe regelmäßige Post-Mortems ein (RCA-Dokumente) ohne Schuldigen-Suche
- [ ] Akzeptiere: der erste Prototyp wird scheitern — design dafür
- [ ] Feiere den erfolgreichen Gate-Pass, nicht die fehlerlose Entwicklung

---

## Schluss: Der Compound-Effekt

Jede Säule verstärkt die anderen:

- **AI Fluency** ohne **Urteilskraft** → du verstehst das Modell, aber glaubst seinen Fehlern
- **Vibe Coding** ohne **Psychologische Sicherheit** → du paralysierst beim ersten Agenten-Fehler
- **Audit** ohne **Orchestrierung** → du prüfst akkurat, aber kannst komplexe Workflows nicht bauen
- **Psychologische Sicherheit** ohne **AI Fluency** → du experimentierst mutig, aber unkontrolliert

Der Compound-Effekt tritt ein, wenn alle vier Säulen gleichzeitig aktiv sind:

> **Ein Team, das versteht wie KI denkt, kritisch validiert was KI produziert, Systeme in natürlicher Sprache orchestriert, und in einem sicheren Rahmen experimentiert — dieses Team kann Systeme bauen, die kein einzelner Entwickler allein je schreiben könnte.**

---

*Basiert auf der realen Entwicklungsgeschichte von AAAgents v0.1 → v3.2 | PRs #001–#860 | 2024–2026*
*Dokument: `docs/oss/AI_ENGINEERING_TUTORIAL.md` | Companion: `docs/oss/PLUGIN_TUTORIAL.md`*
