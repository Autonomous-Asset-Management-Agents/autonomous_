# 🤖 AAAgents — Autonomous Asset Management Agents
### Community Edition · Local-First · Apache 2.0

[![OSS CI](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/actions/workflows/oss-ci.yml/badge.svg)](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/actions/workflows/oss-ci.yml)
[![Release](https://img.shields.io/github/v/release/Autonomous-Asset-Management-Agents/aaagents-oss?label=Release&color=blue)](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![MiFID II](https://img.shields.io/badge/Compliance-MiFID%20II%20by%20Design-orange)](./docs/oss/ARCHITECTURE.md)
[![GitHub Discussions](https://img.shields.io/github/discussions/Autonomous-Asset-Management-Agents/aaagents-oss)](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/discussions)

**An autonomous, regulation-aware AI trading platform you can run entirely on your own machine.**
No cloud subscription required. Runs on Docker. Paper-trading by default — no capital at risk until you explicitly configure live broker credentials.

> **Legal posture:** Research and educational software (Apache 2.0). No BaFin authorisation held. Operating for your own account requires no licence. See [DISCLAIMER.md](./DISCLAIMER.md) before deploying in any fiduciary or multi-user context.

---

## ⚡ Quick Start

**Prerequisites:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) running (🐋 whale icon visible in system tray) and a free [Alpaca Paper-Trading account](https://app.alpaca.markets).

```bash
# 1. Clone the repository
git clone https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss.git
cd aaagents-oss

# 2. Generate secrets and create .env.oss (macOS / Linux / Git Bash)
bash setup.sh
#   Windows PowerShell: powershell -ExecutionPolicy Bypass -File setup.ps1

# 3. Add your Alpaca Paper-Trading keys to .env.oss
#    ALPACA_API_KEY=...
#    ALPACA_SECRET_KEY=...

# 4. Start the stack
docker compose -f docker-compose.oss.yml up -d
```

🌐 **Dashboard:** `http://localhost` · **First start:** allow 3–5 minutes (image pull + model download + DB migration)

> **No build required.** Pre-built images are pulled automatically from GHCR:
> ```
> ghcr.io/autonomous-asset-management-agents/aaagents-backend:latest
> ghcr.io/autonomous-asset-management-agents/aaagents-public-api:latest
> ghcr.io/autonomous-asset-management-agents/aaagents-frontend:latest
> ```

> **Why Alpaca keys?** Without keys the system boots in **Offline Mode (Shadow Boot)** — all agents run, no orders execute. Add real Paper-Trading keys to `.env.oss` to activate order execution. See [setup details →](./docs/oss/README.md#schritt-2--umgebung-konfigurieren-ein-befehl)

---

## 🧠 What is AAAgents?

AAAgents maps the organisational structure of an institutional asset manager onto an autonomous software system. Each department becomes a strictly bounded software artefact:

| Layer | Component | Description |
|---|---|---|
| Research | **Round Table V2** | 9 specialised AI agents debate a symbol in parallel. A weighted `ConsensusEngine` aggregates votes (BUY > 0.65 / SELL < 0.35). |
| Risk & Compliance | **Iron Dome** | Every signal is audited before execution: PDT thresholds, VIX kill-switch, sector concentration limits, wash-trade detection. |
| Execution | **Broker API** | Deterministic, async order routing via Alpaca (paper-trading by default). |
| Reporting | **BORA Control Center** | React/TypeScript dashboard — live portfolio view, agent vote log, kill-switch panel. |

**Architecture invariant:** Round Table and Iron Dome are completely isolated. The Round Table has no access to account balances or open positions. The Iron Dome never overrides ML signals — it only enforces risk rules. This separation is enforced architecturally, not by convention.

---

## 🛠️ `make` Shortcuts

```bash
make setup   # Generate .env.oss with secure secrets (run once)
make start   # Setup (if needed) → docker compose up
make stop    # Stop all containers (data preserved)
make logs    # Tail backend logs
make reset   # Nuclear reset: remove containers + volumes
```

---

## 📚 Documentation

| Document | Description |
|---|---|
| [**Setup Guide**](./docs/oss/README.md) | Full step-by-step installation, port reference, troubleshooting |
| [Vision & Editions](./docs/oss/VISION_AND_EDITIONS.md) | AAAgents ecosystem, Community vs. Enterprise |
| [Architecture](./docs/oss/ARCHITECTURE.md) | Bounded contexts, plugin API, auth abstractions, engine bootstrap |
| [Troubleshooting](./docs/oss/TROUBLESHOOTING.md) | Startup failures, DB migrations, Iron Dome blocks, TLS proxy |
| [Plugin Tutorial](./docs/oss/PLUGIN_TUTORIAL.md) | Write and register your own strategy agent |
| [Contributing](./CONTRIBUTING.md) | PR workflow, coding standards, Archon Standard gate |
| [Security](./SECURITY.md) | Responsible disclosure policy |
| [Disclaimer](./DISCLAIMER.md) | Legal posture, BaFin positioning, liability |

For AI coding assistants: use `CLAUDE.md` (auto-loaded by Claude Code) as your entry point.

---

## 🔌 Plugin System — Add Your Own Strategy

The Round Table is fully extensible. A minimal plugin:

```python
# plugins/round_table/my_strategy.py
from core.round_table.base_agent import VotingAgent, VoteResult
from core.round_table.registry import register_agent

@register_agent("MyStrategyAgent")
class MyStrategyAgent(VotingAgent):
    default_weight: float = 15.0

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        # Return a score: 0.0 = Strong Sell | 1.0 = Strong Buy
        return VoteResult(
            agent_name=self.__class__.__name__,
            symbol=state["symbol"],
            score=0.6,
            weight=self.weight,
            reasoning="Example: neutral-bullish signal."
        )
```

Activate in `.env.oss`:
```env
ALLOW_UNTRUSTED_PLUGINS=true
ROUND_TABLE_PLUGINS_DIR=/app/app/plugins/round_table
```

> ⚠️ **`ALLOW_UNTRUSTED_PLUGINS=true` is effectively arbitrary code execution** as the container user. Every `.py` file in the plugin directory is imported at engine boot. Only enable if you wrote or fully reviewed each file yourself. Default is `false` (deny-by-default).

Full Plugin API: [docs/oss/PLUGIN_TUTORIAL.md](./docs/oss/PLUGIN_TUTORIAL.md)

---

## 🛠️ Development Setup (native, no Docker)

```bash
python -m venv .venv
source .venv/bin/activate          # Linux / macOS
# .\.venv\Scripts\activate         # Windows

# PyTorch CPU-only wheel first (avoids ~3 GB CUDA download)
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# Run unit tests
pytest tests/unit/ -v

# Code quality (mandatory before any PR)
black .
flake8 .
```

---

## 🤝 Community

- 💬 [GitHub Discussions](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/discussions) — questions, ideas, plugin showcase
- 🐛 [Issues](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/issues) — bug reports
- 📖 [Contributing Guide](./CONTRIBUTING.md) — how to submit PRs

---

## 📄 License

- **Source Code:** Apache 2.0 — see [LICENSE](./LICENSE)
- **Model Weights (PyTorch):** CC-BY-4.0 — see [LICENSE-MODELS](./LICENSE-MODELS)

---

*Maintained by the AAAgents Community · [aaagents.de](https://aaagents.de) · [Releases](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/releases)*
