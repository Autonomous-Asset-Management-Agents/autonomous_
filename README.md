# 🤖 AAAgents — Local Multi-Agent Trading Client & Execution Utility
### Community Edition · Local-First · Open-Source (Apache 2.0)

[![OSS CI](https://github.com/Autonomous-Asset-Management-Agents/Dev-Enviroment/actions/workflows/oss-ci.yml/badge.svg)](https://github.com/Autonomous-Asset-Management-Agents/Dev-Enviroment/actions/workflows/oss-ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![MiFID II Inspired](https://img.shields.io/badge/Compliance-MiFID%20II%20Inspired-orange)](./docs/oss/ARCHITECTURE.md)
[![Status: Stable](https://img.shields.io/badge/Status-1.0.0-blue)](#)

**A decentralized, open-source software tool for automating and executing trading decisions on your own account.**

AAAgents brings a powerful, operational trading and execution environment directly to your PC. The software runs completely locally on your own hardware and connects directly to your broker API. It serves as a tool for private users and companies who want to manage their own assets at their own discretion.

* **100% Decentralized & Private:** Your API keys and portfolio data remain in your local operating system keychain and your local SQLite database. No data is transmitted to us.
* **Operational Execution:** Once configured, the system fully automatically executes real (or virtual) buy and sell orders directly via your broker account.
* **No Financial Services:** We do not offer asset management, investment advice, or broker services. The operation, risk parameterization, and control of the software are entirely your responsibility.

---

## 🚀 Quick Start (Ready in 3 Steps)

You need **no** programming skills, no Python, and no Docker for the Desktop App.

1. **Download:** Download the installer for Windows directly:
   * ⬇️ [Download for Windows (autonomous_setup.exe)](https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases/download/desktop-v0.9-beta/autonomous_setup.exe)
   * 🍏 Download for macOS (coming later)
2. **Install:** Start the setup and open the **AAAgents** application.
3. **Set Up:** 
   * **Paper Trading (Virtual Capital):** Enter your Alpaca Paper-Trading Keys to test the system risk-free with virtual orders.
   * **Live Trading (Real Capital):** Enter your Alpaca Live-Trading Keys. Your keys are securely encrypted and stored locally in the operating system keychain.
   * **Offline Mode:** Without keys, the voting engine runs in pure recommendation mode without sending orders to a broker.

---

## 🧠 Local Features of the Community Edition

* **Local AI (Ollama Integration):** Analyze news and sentiment completely locally on your GPU (e.g., with Llama3 or Mistral) – entirely free of charge and without third-party cloud providers.
* **9-Agent Consensus:** A local council of technical indicators, sentiment analysis, LSTMs, and reinforcement learning determines the signals.
* **Iron Dome Risk Control:** Integrated, configurable protection rules against wash trades, excessive sector concentration, and uncontrolled trading behavior.

---

## 📊 Community Edition vs. Enterprise

This table defines the exact scope of features of the Community Edition compared to the Enterprise version. Detailed vision: [docs/oss/VISION_AND_EDITIONS.md](./docs/oss/VISION_AND_EDITIONS.md).

| Feature | Community Edition (Open-Source) | Enterprise Edition |
|---|---|---|
| **Deployment** | Local as Desktop App / Docker Compose | GCP Cloud Run (Managed, Auto-Scaling) |
| **Authentication** | `LocalMockAuth` (Loopback/Private IP) | Firebase Auth + OIDC |
| **Database** | SQLite (local, file-based) | PostgreSQL / AlloyDB (Cloud SQL) |
| **State Management** | `LocalStateClient` (local in memory) | Redis Memorystore (persistent) |
| **Secret Management** | OS Keychain via `keyring` / `.env.oss` | GCP Secret Manager |
| **Tenancy** | Single-Tenant (Single User) | Multi-Tenant (Firebase UID Isolation) |
| **Data Feed** | Alpaca IEX (free real-time data) | Alpaca SIP (full US market data) |
| **Audit Trail** | `LocalJSONAuditLogger` (local, SHA-256) | SenateProtocol (Redis + Cloud SQL) |
| **MiFID II Export** | Pre-Trade Risk Gates (Iron Dome) | Automated RTS 22 Export (Roadmap) |
| **ML Model Source** | GitHub Releases (Boot Manifest) | GCS Bucket Sync (Vertex AI) |
| **HFT / Latency** | Not designed for HFT (Minutes/Hours) | Sub-second execution (Roadmap Phase 5) |

---

## ⚙️ Operating Modes & Expectations

| Setup | Behavior |
|---|---|
| **Without Alpaca Keys** | **Offline Mode** — The engine starts, the 9 agents vote, but no orders are sent. Perfect for getting to know the software. |
| **Alpaca Paper Keys** | **Paper-Trading Mode** (Default) — Orders are sent risk-free to the Alpaca Sandbox environment. |
| **Alpaca + POLYGON_API_KEY** | Adds real CBOE VIX volatility data. Without a key, the market regime index is estimated from the 60-day history of SPY. |
| **Alpaca + GEMINI_API_KEY** | **Full Sentiment Mode** — Activates GeminiSentimentAgent and NewsContextAgent. Without a key, the system runs in *Degraded Sentiment Mode* (7 out of 9 agents active). |

---

## 🛠️ `make` Commands (Docker Alternative)

If you prefer to start the software via Docker Compose:

```bash
make setup   # Generates .env.oss with secure secrets
make start   # Runs setup and starts Docker Compose
make stop    # Stops all containers (data is preserved)
make logs    # Shows the backend logs
make reset   # Deletes all containers and local volumes
```

---

## 🔌 Add Custom Agents (Plugin System)

The voting council can be extended. To do this, create a Python file in `plugins/round_table/my_strategy.py`:

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

## 🛠️ Local Development (Running from Source Code)

If you want to modify the code:

```bash
# 1. Create Python environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\activate

# 2. Pre-install PyTorch (CPU version)
pip install torch --index-url https://download.pytorch.org/whl/cpu

# 3. Install dependencies
pip install -r requirements.txt
pip install ./pandas-ta

# 4. Load standard ML models
./scripts/setup_oss_models.sh

# 5. Start desktop development mode (Frontend + Engine)
npm install
npm run desktop:dev
```

---

## 📚 Documentation

| Document | Description |
|---|---|
| [**Setup Guide**](./docs/oss/README.md) | Step-by-step installation, ports, and troubleshooting |
| [Vision & Editions](./docs/oss/VISION_AND_EDITIONS.md) | Product roadmap and differences between editions |
| [Architecture](./docs/oss/ARCHITECTURE.md) | Bounded contexts, authentication details, and system startup |
| [Plugin Tutorial](./docs/oss/PLUGIN_TUTORIAL.md) | Programming custom analysis and trading agents |
| [Disclaimer](./DISCLAIMER.md) | Legal classification, regulatory context, and disclaimer |

---

## ⚠️ Important Risk Notice (Disclaimer)

The use of automated trading systems involves significant risks. This software is provided by the developers under the Apache 2.0 license for decentralized personal use. The creators and the company *Autonomous Asset Management Agents UG (haftungsbeschränkt)* assume no liability for financial losses. The operation of the software is exclusively on the user's own account and at their own risk. Please read the full notice in [DISCLAIMER.md](./DISCLAIMER.md) before commissioning.

---

*Maintained by the AAAgents Community · [aaagents.de](https://aaagents.de) · [Releases](https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases)*
