# 🤖 AAAgents — Local Multi-Agent Trading Client & Execution Utility
### Community Edition · Local-First · Open-Source (Apache 2.0)

[![OSS CI](https://github.com/Autonomous-Asset-Management-Agents/Dev-Enviroment/actions/workflows/oss-ci.yml/badge.svg)](https://github.com/Autonomous-Asset-Management-Agents/Dev-Enviroment/actions/workflows/oss-ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![MiFID II Inspired](https://img.shields.io/badge/Compliance-MiFID%20II%20Inspired-orange)](./docs/oss/ARCHITECTURE.md)
[![Status: Stable](https://img.shields.io/badge/Status-1.0.0-blue)](#)

**A decentralized, open-source software tool for automating and executing trading decisions on one's own account.**

AAAgents brings a powerful, operational trading and execution environment directly to your PC. The software runs fully locally on your own hardware and connects directly to your broker API. It serves as a tool for retail users and enterprises wishing to manage their own assets at their own discretion.

* **100% Decentralized & Private:** Your API keys and portfolio data remain in your local operating system keychain and your local SQLite database. No data is transmitted to us.
* **Operational Execution:** Once configured, the system executes real (or virtual) buy and sell orders fully automatically directly via your broker account.
* **No Financial Service:** We do not offer asset management, investment advice, or brokerage services. The operation, risk parameterization, and control of the software lie entirely in your responsibility.

---

## 🚀 Quick Start (Ready in 3 Steps)

For the Desktop App, you do **not** need any programming knowledge, Python, or Docker.

1. **Download:** Download the Windows installer directly:
   * ⬇️ [Download for Windows (autonomous_setup.exe)](https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases)
   * 🍏 **macOS:** no native installer yet — run from source (web console): **[macOS Quickstart](./docs/oss/macos-quickstart.md)**
2. **Install:** Run the setup and open the application **AAAgents**.
3. **Configure:**
   * **Paper Trading (Virtual Capital):** Enter your Alpaca Paper Trading keys to test the system risk-free with virtual orders.
   * **Live Trading (Real Capital):** Enter your Alpaca Live Trading keys. Your keys are securely stored locally, encrypted in the operating system keychain.
   * **Offline Mode:** Without keys, the voting engine runs in recommendation-only mode without sending orders to a broker.

---

## 🧠 Local Features of the Community Edition

* **Local AI (Ollama Integration):** Analyze news and sentiment fully locally on your graphics card (e.g., with Llama3 or Mistral) – completely free of charge and without third-party cloud providers.
* **9-Agent Consensus:** A local board composed of technical indicators, sentiment analysis, LSTMs, and Reinforcement Learning determines the signals.
* **Iron Dome Risk Control:** Integrated, configurable safety rules guarding against wash trades, excessive sector concentration, and uncontrolled trading behavior.

---

## 📊 Edition & Deployment Comparison

This table defines the exact scope of features across the **Desktop Edition**, **OSS Self-Hosted Edition**, and the **Enterprise Edition**. Detailed vision: [docs/oss/VISION_AND_EDITIONS.md](./docs/oss/VISION_AND_EDITIONS.md).

| Feature / Aspect | 🖥️ Desktop Edition (Local-First) | 🛠️ OSS Developer (Self-Hosted) | 🏢 Enterprise Edition (Cloud Native) |
|---|---|---|---|
| **Target Audience** | Retail Traders / Individual Users | Developers, Quants & Researchers | Asset Managers & Institutions |
| **Deployment** | Native Desktop App (Electron) | Docker Compose / Python CLI | GCP Cloud Run / Managed Kubernetes |
| **Prerequisites** | None (Zero-Dependency Installer) | Python 3.12 / Node.js / Docker | GCP / Cloud SQL / Firebase |
| **Architecture** | Electron + Embedded Python | FastAPI Backend + Vite Frontend | Cloud Run Microservices (Auto-scaling) |
| **Database** | SQLite (local, file-based) | SQLite (local, file-based) | Cloud SQL (PostgreSQL / AlloyDB) |
| **Authentication** | `LocalMockAuth` (127.0.0.1 bound) | `LocalMockAuth` (Single-Tenant) | Firebase Auth + Multi-Tenant Isolation |
| **State Management** | `LocalStateClient` (in-memory) | `LocalStateClient` (in-memory) | Redis Memorystore (persistent) |
| **Secrets Management** | OS Keychain (Credential Manager) | `.env.oss` / OS Keychain | GCP Secret Manager (per-User Encryption) |
| **Market Data Feed** | Alpaca IEX (Free Real-time) | Alpaca IEX / custom FEEDS | Alpaca SIP (Full US Market) + Databento |
| **Risk Gate (Iron Dome)** | ✅ Local Pre-Trade Rules | ✅ Local Pre-Trade Rules | ✅ Cloud-persistent + Ops Alerting |
| **Audit & Compliance** | `LocalJSONAuditLogger` (SHA-256) | Hash-Chained JSONL + `autonomous-audit` | SenateProtocol (WORM Cloud SQL + RTS 22) |
| **ML Model Source** | GitHub Releases (Boot Manifest) | GitHub Releases (Boot Manifest) | GCS Bucket Sync (Vertex AI) |
| **Extensibility** | Standard 9-Agent Board | Custom Python Plugins (`plugins/`) | Managed Enterprise Agent Pipelines |

---

## ⚙️ Operating Modes & Expectations

| Setup | Behavior |
|---|---|
| **Without Alpaca Keys** | **Offline Mode** — The engine starts, the 9 agents vote, but no orders are sent. Perfect for getting to know the software. |
| **Alpaca Paper Keys** | **Paper Trading Mode** (Default) — Orders are sent risk-free to the Alpaca sandbox environment. |
| `AAA_SIM_MODE=true` | **Quasi-Live Simulator** — Runs a full trading day in fast-forward using historical data. Replaces the live broker and data feed with dummy implementations (no API limits or market hour restrictions). |
| **Alpaca + POLYGON_API_KEY** | Adds real CBOE VIX volatility data. Without a key, the market regime index is estimated from the 60-day history of SPY. |
| **Alpaca + GEMINI_API_KEY** | **Full Sentiment Mode** — Activates GeminiSentimentAgent and NewsContextAgent. Without a key, the system runs in *Degraded Sentiment Mode* (7 out of 9 agents active). |

---

## 🛠️ `make` Commands (Docker Alternative)

If you prefer starting the software via Docker Compose:

```bash
make setup   # Generates .env.oss with secure secrets
make start   # Runs the setup and starts Docker Compose
make stop    # Stops all containers (data is preserved)
make logs    # Shows the backend logs
make reset   # Deletes all containers and local volumes
```

---

## 🔌 Adding Custom Agents (Plugin System)

The voting board can be extended. To do this, create a Python file in `plugins/round_table/my_strategy.py`:

```python
from core.round_table.base_agent import VotingAgent, VoteResult
from core.round_table.registry import register_agent

@register_agent("MyStrategyAgent")
class MyStrategyAgent(VotingAgent):
    default_weight: float = 15.0

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        # Score from 0.0 (Strong Sell) to 1.0 (Strong Buy)
        return VoteResult(
            agent_name=self.__class__.__name__,
            symbol=state["symbol"],
            score=0.6,
            weight=self.weight,
            reasoning="Example: Neutral-bullish signal."
        )
```

Activate plugins in your `.env.oss`:
```env
ALLOW_UNTRUSTED_PLUGINS=true
ROUND_TABLE_PLUGINS_DIR=./plugins/round_table
```

---

## 🛠️ Local Development (Running from Source)

If you wish to modify the code:

```bash
# 1. Create Python environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\activate

# 2. Install PyTorch (CPU version) in advance
pip install torch --index-url https://download.pytorch.org/whl/cpu

# 3. Install dependencies
pip install -r requirements.txt
pip install ./pandas-ta

# 4. Load default ML models
./scripts/setup_oss_models.sh

# 5. Start the app — the one-command wrapper runs the web UI + engine + read-only API together:
npm install
npm run desktop:dev
#    …or run the engine and web UI as two separate processes (handy for reading the engine log):
#    python -m core.engine &   # engine → http://localhost:8001
#    npm run dev               # web UI → http://localhost:5173 (proxies to the engine)
```

> First-time config (`bash setup.sh`) and the keyless "offline" vs. paper-trading run modes are
> covered step-by-step in the **[macOS Quickstart](./docs/oss/macos-quickstart.md)** — the same
> from-source flow applies on Linux.

---

## 📚 Documentation

| Document | Description |
|---|---|
| [**Setup Guide**](./docs/oss/README.md) | Step-by-step installation, ports, and troubleshooting |
| [Vision & Editions](./docs/oss/VISION_AND_EDITIONS.md) | Product roadmap and differences between editions |
| [Architecture](./docs/oss/ARCHITECTURE.md) | Bounded contexts, authentication details, and system boot |
| [Plugin Tutorial](./docs/oss/PLUGIN_TUTORIAL.md) | Programming custom analysis and trading agents |
| [Disclaimer](./DISCLAIMER.md) | Legal categorization, BaFin context, and liability exclusion |

---

## ⚠️ Important Risk Warning (Disclaimer)

Using automated trading systems carries significant risks. This software is provided by the developers under the Apache 2.0 license for decentralized personal use. The creators and the company *Autonomous Asset Management Agents UG (haftungsbeschränkt)* assume no liability for financial losses. Operating the software is solely at the user's own account and risk. Please read the full notice in [DISCLAIMER.md](./DISCLAIMER.md) before commissioning the software.

---

*Maintained by the AAAgents Community · [aaagents.de](https://aaagents.de) · [Releases](https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases)*
