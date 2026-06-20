# 🤖 AAAgents — Local Multi-Agent Trading Client & Execution Utility
### Community Edition · Local-First · Open-Source (Apache 2.0)

[![OSS CI](https://github.com/Autonomous-Asset-Management-Agents/Dev-Enviroment/actions/workflows/oss-ci.yml/badge.svg)](https://github.com/Autonomous-Asset-Management-Agents/Dev-Enviroment/actions/workflows/oss-ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![MiFID II Inspired](https://img.shields.io/badge/Compliance-MiFID%20II%20Inspired-orange)](./docs/oss/ARCHITECTURE.md)
[![Status: Stable](https://img.shields.io/badge/Status-1.0.0-blue)](#)

**Ein dezentrales, quelloffenes Software-Werkzeug zur Automatisierung und Ausführung von Handelsentscheidungen auf eigene Rechnung.**

AAAgents bringt eine leistungsstarke, operative Handels- und Ausführungsumgebung direkt auf Ihren PC. Die Software läuft vollständig lokal auf Ihrer eigenen Hardware und verbindet sich direkt mit Ihrer Broker-API. Sie dient als Hilfsmittel für Privatanwender und Unternehmen, die eigene Vermögenswerte in eigenem Ermessen verwalten möchten.

* **100% Dezentral & Privat:** Ihre API-Schlüssel und Portfoliodaten verbleiben in Ihrem lokalen Betriebssystem-Schlüsselbund und Ihrer lokalen SQLite-Datenbank. Es werden keine Daten an uns übertragen.
* **Operative Ausführung:** Das System führt nach der Konfiguration vollautomatisch echte (oder virtuelle) Kauf- und Verkaufsaufträge direkt über Ihr Broker-Konto aus.
* **Keine Finanzdienstleistung:** Wir bieten keine Vermögensverwaltung, Anlageberatung oder Broker-Dienste an. Der Betrieb, die Risikoparametrisierung und die Kontrolle der Software liegen vollständig in Ihrer Verantwortung.

---

## 🚀 Schnellstart (In 3 Schritten startklar)

Für die Desktop-App benötigen Sie **keine** Programmierkenntnisse, kein Python und kein Docker.

1. **Herunterladen:** Laden Sie den Installer für Windows direkt herunter:
   * ⬇️ [Download für Windows (autonomous_setup.exe)](https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases/latest/download/autonomous_setup.exe)
   * 🍏 Download für macOS (folgt später)
2. **Installieren:** Starten Sie das Setup und öffnen Sie die Anwendung **AAAgents**.
3. **Einrichten:** 
   * **Paper Trading (Virtuelles Kapital):** Tragen Sie Ihre Alpaca Paper-Trading Keys ein, um das System risikofrei mit virtuellen Orders zu testen.
   * **Live Trading (Echtes Kapital):** Tragen Sie Ihre Alpaca Live-Trading Keys ein. Ihre Schlüssel werden lokal sicher verschlüsselt im Betriebssystem-Schlüsselbund gespeichert.
   * **Offline-Modus:** Ohne Schlüssel läuft die Abstimmungs-Engine im reinen Empfehlungsmodus, ohne Orders an einen Broker zu senden.

---

## 🧠 Lokale Features der Community Edition

* **Lokale KI (Ollama Integration):** Analysieren Sie Nachrichten und Sentiment vollständig lokal auf Ihrer Grafikkarte (z.B. mit Llama3 oder Mistral) – komplett kostenfrei und ohne Cloud-Drittanbieter.
* **9-Agenten-Konsensus:** Ein lokaler Rat aus technischen Indikatoren, Sentiment-Analysen, LSTMs und Reinforcement Learning bestimmt die Signale.
* **Iron Dome Risikokontrolle:** Integrierte, konfigurierbare Schutzregeln gegen Wash-Trades, zu hohe Branchenkonzentration und unkontrolliertes Handelsverhalten.

---

## 📊 Community Edition vs. Enterprise

Diese Tabelle definiert den genauen Leistungsumfang der Community Edition im Vergleich zur Enterprise-Version. Detaillierte Vision: [docs/oss/VISION_AND_EDITIONS.md](./docs/oss/VISION_AND_EDITIONS.md).

| Feature | Community Edition (Open-Source) | Enterprise Edition |
|---|---|---|
| **Bereitstellung** | Lokal als Desktop-App / Docker Compose | GCP Cloud Run (Managed, Auto-Scaling) |
| **Authentifizierung** | `LocalMockAuth` (Loopback/Private IP) | Firebase Auth + OIDC |
| **Datenbank** | SQLite (lokal, dateibasiert) | PostgreSQL / AlloyDB (Cloud SQL) |
| **Zustandsverwaltung** | `LocalStateClient` (lokal im Arbeitsspeicher) | Redis Memorystore (persistent) |
| **Geheimnisverwaltung** | OS Keychain via `keyring` / `.env.oss` | GCP Secret Manager |
| **Mandantenfähigkeit** | Single-Tenant (Einzelnutzer) | Multi-Tenant (Firebase UID Isolation) |
| **Datenzufuhr** | Alpaca IEX (kostenfreie Echtzeitdaten) | Alpaca SIP (vollständige US-Marktdaten) |
| **Audit Trail** | `LocalJSONAuditLogger` (lokal, SHA-256) | SenateProtocol (Redis + Cloud SQL) |
| **MiFID II Export** | Pre-Trade Risikogates (Iron Dome) | Automatisierter RTS 22 Export (Roadmap) |
| **ML-Modellquelle** | GitHub Releases (Boot-Manifest) | GCS Bucket Sync (Vertex AI) |
| **HFT / Latenz** | Nicht für HFT ausgelegt (Minuten/Stunden) | Sub-Sekunden-Ausführung (Roadmap Phase 5) |

---

## ⚙️ Betriebsmodi & Erwartungen

| Setup | Verhalten |
|---|---|
| **Ohne Alpaca-Keys** | **Offline-Modus** — Die Engine startet, die 9 Agenten stimmen ab, aber es werden keine Orders gesendet. Perfekt zum Kennenlernen der Software. |
| **Alpaca Paper Keys** | **Paper-Trading-Modus** (Standard) — Orders werden risikofrei an die Alpaca Sandbox-Umgebung gesendet. |
| **Alpaca + POLYGON_API_KEY** | Fügt echte CBOE VIX Volatilitätsdaten hinzu. Ohne Key wird der Marktregime-Index aus der 60-Tage-Historie von SPY geschätzt. |
| **Alpaca + GEMINI_API_KEY** | **Full Sentiment Mode** — Aktiviert GeminiSentimentAgent und NewsContextAgent. Ohne Key läuft das System im *Degraded Sentiment Mode* (7 von 9 Agenten aktiv). |

---

## 🛠️ `make` Befehle (Docker-Alternative)

Falls Sie die Software lieber über Docker Compose starten möchten:

```bash
make setup   # Erzeugt .env.oss mit sicheren Geheimnissen
make start   # Führt das Setup aus und startet Docker Compose
make stop    # Stoppt alle Container (Daten bleiben erhalten)
make logs    # Zeigt die Backend-Logs an
make reset   # Löscht alle Container und lokalen Volumes
```

---

## 🔌 Eigene Agenten hinzufügen (Plugin-System)

Das Abstimmungsgremium kann erweitert werden. Erstellen Sie dazu eine Python-Datei in `plugins/round_table/my_strategy.py`:

```python
from core.round_table.base_agent import VotingAgent, VoteResult
from core.round_table.registry import register_agent

@register_agent("MyStrategyAgent")
class MyStrategyAgent(VotingAgent):
    default_weight: float = 15.0

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        # Score von 0.0 (Strong Sell) bis 1.0 (Strong Buy)
        return VoteResult(
            agent_name=self.__class__.__name__,
            symbol=state["symbol"],
            score=0.6,
            weight=self.weight,
            reasoning="Beispiel: Neutral-bullisches Signal."
        )
```

Aktivieren Sie Plugins in Ihrer `.env.oss`:
```env
ALLOW_UNTRUSTED_PLUGINS=true
ROUND_TABLE_PLUGINS_DIR=./plugins/round_table
```

---

## 🛠️ Lokale Entwicklung (Ausführung aus dem Quellcode)

Wenn Sie den Code modifizieren möchten:

```bash
# 1. Python-Umgebung erstellen
python -m venv .venv
source .venv/bin/activate  # Unter Windows: .\.venv\Scripts\activate

# 2. PyTorch (CPU-Version) vorab installieren
pip install torch --index-url https://download.pytorch.org/whl/cpu

# 3. Abhängigkeiten installieren
pip install -r requirements.txt
pip install ./pandas-ta

# 4. Standard-ML-Modelle laden
./scripts/setup_oss_models.sh

# 5. Desktop-Entwicklungsmodus starten (Frontend + Engine)
npm install
npm run desktop:dev
```

---

## 📚 Dokumentation

| Dokument | Beschreibung |
|---|---|
| [**Setup Guide**](./docs/oss/README.md) | Schritt-für-Schritt Installation, Ports und Fehlerbehebung |
| [Vision & Editions](./docs/oss/VISION_AND_EDITIONS.md) | Produkt-Roadmap und Unterschiede zwischen den Editionen |
| [Architecture](./docs/oss/ARCHITECTURE.md) | Bounded Contexts, Authentifizierungsdetails und Systemstart |
| [Plugin Tutorial](./docs/oss/PLUGIN_TUTORIAL.md) | Programmierung eigener Analyse- und Trading-Agenten |
| [Disclaimer](./DISCLAIMER.md) | Rechtliche Einordnung, BaFin-Kontext und Haftungsausschluss |

---

## ⚠️ Wichtiger Risikohinweis (Haftungsausschluss)

Die Nutzung von automatisierten Handelssystemen birgt erhebliche Risiken. Diese Software wird von den Entwicklern unter der Apache 2.0-Lizenz zur dezentralen Eigennutzung bereitgestellt. Die Ersteller und die Gesellschaft *Autonomous Asset Management Agents UG (haftungsbeschränkt)* übernehmen keinerlei Haftung für finanzielle Verluste. Der Betrieb der Software erfolgt ausschließlich auf eigene Rechnung und eigenes Risiko des Anwenders. Bitte lesen Sie vor Inbetriebnahme die vollständigen Hinweise in [DISCLAIMER.md](./DISCLAIMER.md).

---

*Unterhalten von der AAAgents Community · [aaagents.de](https://aaagents.de) · [Releases](https://github.com/Autonomous-Asset-Management-Agents/Dev-Enviroment/releases)*
