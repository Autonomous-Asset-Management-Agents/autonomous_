# aaagents-oss — Autonomous Asset Management Agents (Community Edition)

[![OSS CI](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/actions/workflows/oss-ci.yml/badge.svg)](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/actions/workflows/oss-ci.yml)
[![Release](https://img.shields.io/github/v/release/Autonomous-Asset-Management-Agents/aaagents-oss?label=Release&color=blue)](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![GitHub Discussions](https://img.shields.io/github/discussions/Autonomous-Asset-Management-Agents/aaagents-oss)](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/discussions)

**An autonomous AI trading platform you can run entirely on your own machine.** No cloud subscription, no API keys required to start — just Docker.

> **Legal posture:** This is a research and educational project. Paper-trading by default. No BaFin licence held. See [SECURITY.md](./SECURITY.md) before running with real funds.

---

## ⚡ Quick Start — 3 Commands

```bash
# Option A — via aaagents CLI (recommended)
pip install aaagents
aaagents install   # interactive setup wizard
aaagents start     # spins up the full aaagents-oss stack

# Option B — via Docker Compose directly
cp .env.oss.example .env.oss
docker compose -f docker-compose.oss.yml up -d
```

🌐 **Dashboard available at:** `http://localhost` after startup.

> **No build required.** Pre-built images are pulled automatically from GHCR:
> ```
> ghcr.io/autonomous-asset-management-agents/bora-backend:latest
> ghcr.io/autonomous-asset-management-agents/bora-public-api:latest
> ghcr.io/autonomous-asset-management-agents/bora-frontend:latest
> ```

### 📦 Default ML Models (Community Baseline)

On first start the backend container reads `data/models_manifest.json` and pulls 6 model files (~25 MB total) from the [`models-v1.0`](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/releases/tag/models-v1.0) GitHub Release. SHA256 is verified before each file is written. Without these, two of the nine Round Table voting agents (`LSTMSignalAgent` and `RLConfidenceAgent`, weight 0.40 each) fall back to a neutral 0.5 — the system runs but the ML half is silent.

| File | Purpose |
|---|---|
| `lstm_model_v2.pth` | LSTM 5-day-return predictor (input_dim=34, hidden_dim=128, 3 layers, sequence_length=60) |
| `rl_agent_v5.zip` | RecurrentPPO RL agent (sb3-contrib `MlpLstmPolicy`) |
| `scaler_x_v2.pkl`, `scaler_y_v2.pkl` | StandardScalers for input features and return target |
| `model_metadata_v2.json` | Feature list + LSTM hyper-parameters |
| `rl_stats_v5.pkl` | VecNormalize stats matching the RL training environment |

You can also pre-populate `data/` manually via `bash scripts/setup_oss_models.sh` (same logic, runs before container start). Failures during sync are non-blocking — the engine boots regardless and falls back to neutral voting until model files arrive.

> ⚠️ **No performance guarantee. Read this first.**
>
> These default models are a single internal paper-trading snapshot from
> 2026-Q1, intended as a baseline so the ML voting agents are not silent
> on first install — **not as a trading recommendation.** Sample is n=1
> forward run on a single broker (Alpaca paper) over a single market
> regime; this is **not a backtest**, not a Sharpe-graded result, and not
> a walk-forward validation. Past performance does not guarantee future
> results. Models age — re-evaluate every 3 months.
>
> Methodology of the reference figure (kept for transparency; do not treat
> as marketing): 2026-01-14 → 2026-04-30, paper account on Alpaca,
> portfolio equity rose from $100k to ≈$113.9k while SPY total return in
> the same window was +3.58% (yfinance close-to-close). For production
> use, validate against your own walk-forward backtest and/or retrain.
> See [SECURITY.md](./SECURITY.md) for legal posture (paper-trading,
> self-hosted personal use; no BaFin licence held).

---

## 🧠 What is aaagents-oss?

**aaagents-oss** is the Community Edition of the Autonomous Asset Management Agents platform. It ships with:

| Feature | Description |
|---|---|
| **Round Table V2** | 9-agent LangGraph consensus framework (LSTM, RL, News, Market Regime, Compliance…) |
| **Iron Dome** | MiFID II-inspired risk management: VIX kill-switch, position limits, wash-trade detection |
| **Plugin Architecture** | Add custom strategy agents via `StockSpecialistRegistry` — no core changes needed |
| **Control Center** | React/TypeScript dashboard — live portfolio view, agent votes, kill-switch |
| **Local-First** | PostgreSQL + Redis, fully containerized — no GCP, no Firebase required |

---

## 📚 Documentation

| Document | Description |
|---|---|
| [Architecture](./docs/oss/ARCHITECTURE.md) | System design, plugin architecture, separation of concerns |
| [Troubleshooting](./docs/oss/TROUBLESHOOTING.md) | Common Docker, network, and startup issues |
| [Contributing](./CONTRIBUTING.md) | How to add features, open issues, write plugins |
| [Security](./SECURITY.md) | Responsible disclosure policy |

For AI agents and LLMs: use `CLAUDE.md` (loaded automatically by Claude Code) as your entry point.

---

## 🔌 Plugin Tutorial (Add Your Own Strategy)

The aaagents-oss agent system is fully extensible. A minimal plugin:

```python
# plugins/my_strategy.py
from core.round_table.base_agent import BaseVotingAgent

class MyStrategyAgent(BaseVotingAgent):
    name = "MyStrategy"
    default_weight = 10.0

    async def analyze(self, symbol: str, context: dict) -> float:
        # Return a score between 0.0 (strong sell) and 1.0 (strong buy)
        return 0.6
```

Mount the plugin and register it in `.env.oss`:
```env
ROUND_TABLE_PLUGINS_DIR=/app/plugins
ALLOW_UNTRUSTED_PLUGINS=true
```

See [docs/oss/ARCHITECTURE.md](./docs/oss/ARCHITECTURE.md) for the full Plugin API.

---

## 🛠️ Development Setup

```bash
# Backend (in repo root)
python -m venv venv
source venv/bin/activate          # Linux/Mac
# .\venv\Scripts\activate         # Windows

pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# Run tests
pytest tests/unit/ -v
```

**Code quality (mandatory before PRs):**
```bash
black .
flake8 .
```

---

## 🤝 Community

- 💬 [GitHub Discussions](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/discussions) — questions, ideas, show & tell
- 🐛 [Issues](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/issues) — bug reports
- 📖 [Contributing Guide](./CONTRIBUTING.md) — how to submit PRs

---

## 📄 License

Apache 2.0 — see [LICENSE](./LICENSE).
Model weights are licensed separately under CC-BY-4.0 — see [LICENSE-MODELS](./LICENSE-MODELS).

---

*Maintained by the AAAgents Community | [aaagents-oss v0.1.0-beta](https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss/releases/tag/v0.1.0-beta)*
