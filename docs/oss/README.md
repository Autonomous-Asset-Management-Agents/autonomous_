# 🤖 AAAgents — Autonomous Asset Management Agents
### Community Edition · `ghcr.io/autonomous-asset-management-agents`

[![OSS CI](https://github.com/Autonomous-Asset-Management-Agents/autonomous_/actions/workflows/oss-ci.yml/badge.svg)](https://github.com/Autonomous-Asset-Management-Agents/autonomous_/actions/workflows/oss-ci.yml)
[![Release](https://img.shields.io/github/v/release/Autonomous-Asset-Management-Agents/autonomous_?label=Release&color=blue)](https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![MiFID II](https://img.shields.io/badge/Compliance-MiFID%20II%20by%20Design-orange)](./ARCHITECTURE.md)
[![GitHub Discussions](https://img.shields.io/github/discussions/Autonomous-Asset-Management-Agents/autonomous_)](https://github.com/Autonomous-Asset-Management-Agents/autonomous_/discussions)

> **The Open-Source, Regulation-Aware AI Trading Platform.**
> No cloud subscription required. Runs on Docker. Paper-trading by default — no capital at risk until you explicitly configure live broker credentials.

![AAAgents Console](./images/aaagents_console.png)

> [!NOTE]
> **Docker CLI compatibility:** This document uses `docker compose` (plugin syntax, Docker Desktop ≥ 4.x). Legacy installations using standalone `docker-compose` (V1) **≥ 1.25** also work (the `--env-file` flag was added in 1.25, Nov 2019) — replace `docker compose` with `docker-compose` in all commands. Docker Compose V1 itself was deprecated in June 2023; upgrade if possible.

> [!IMPORTANT]
> **Legal status:** AAAgents Community Edition is research and educational software (Apache 2.0). The system starts in paper-trading mode by default. The maintainers hold no BaFin licence under § 32 KWG / § 15 WpIG. Operating the system for one's own account requires no licence. Anyone managing third-party capital is solely responsible for ensuring the applicable regulatory compliance (KWG, WpIG, MiFID II, DORA). Full disclaimer: [DISCLAIMER.md](../../DISCLAIMER.md).

---

## 📐 What is AAAgents?

AAAgents maps the organisational structure of an institutional asset manager as an autonomous software system. Each department becomes a clearly bounded software artefact:

| Step | Domain | Technology |
|---|---|---|
| 1. Research | **Round Table V2** — 9 specialised AI agents evaluate market signals in parallel and consolidate their weighted votes via `ConsensusEngine` (BUY > 0.65 / SELL < 0.35) | LangGraph, PyTorch (PPO + LSTM), Redis L1 checkpointer |
| 2. Risk & Compliance | **Iron Dome** — Every signal is checked against hard rules before execution: PDT thresholds, VIX kill-switch, sector concentration limits, wash-trade detection | `ComplianceGuardian`, `RiskManager` |
| 3. Execution | **Broker API** — Deterministic, asynchronous order execution via Alpaca (paper-trading by default) | FastAPI, SQLAlchemy, Alembic |
| 4. Reporting | **AAAgents Console** — React/TypeScript dashboard with live portfolio view, agent voting log, and kill-switch | Docker image (nginx) |

> [!NOTE]
> **Bounded Context — architectural invariant:** Round Table and Iron Dome are fully isolated. The Round Table has no access to account balance or open positions. The Iron Dome never interferes with signal logic. Contributors must strictly maintain this separation. See [ARCHITECTURE.md](./ARCHITECTURE.md).

### ⚖️ Community Edition vs. Enterprise Edition

| Feature | Community Edition (this repository) | Enterprise Edition ([aaagents.de](https://aaagents.de)) |
|---|---|---|
| Deployment | Docker Compose (local-first) or Native Desktop | Google Cloud Run (managed SaaS) |
| Database | SQLite (local, auto-bootstrapped) or PostgreSQL 15 | AlloyDB AI (audit reasoning lake) |
| State / Cache | `LocalStateClient` (in-memory) or Redis | Redis (Memorystore) |
| LangGraph Checkpointer | `None` (Stateless) | `RedisSaver` (Memorystore) |
| Auth | `LocalMockAuth` — single-tenant, no login required | Firebase Authentication — multi-tenant |
| ML models | Pre-built and included in image | GCP Vertex AI MLOps + automated re-training |
| Secret management | `.env.oss` (local) | GCP Secret Manager + Workload Identity Federation |
| Audit Trail | `LocalJSONAuditLogger` (JSONL + SHA-256) | `SenateProtocol` (Redis + Cloud SQL) |
| Licence | Apache 2.0 | Commercial |

---

## ⚙️ Modes & Expectations

| Setup | Behavior |
|---|---|
| `.env.oss` only (no Alpaca keys) | **Offline Mode** — engine boots, all 9 agents vote, no orders execute. Useful for code exploration. |
| Alpaca paper-trading keys | **Paper Mode** (default) — orders route to Alpaca paper, real BUY/SELL signals. |
| Alpaca + `POLYGON_API_KEY` | Adds true CBOE VIX (without it, regime is derived from 60-day SPY volatility — same regime classes, slightly noisier signal). |
| Alpaca + `GEMINI_API_KEY` | **Full Sentiment Mode** — adds GeminiSentimentAgent and NewsContextAgent. Without it, the bot runs in **Degraded Sentiment Mode** (7/9 agents active). |

> **Without a model bundle release**, the engine still boots but the LSTM and RL agents vote neutral. See [TROUBLESHOOTING.md](./TROUBLESHOOTING.md#engine-running-but-no-trades-after-1-hour) if you don't see trades after market open.

---

## ✅ Prerequisites

AAAgents supports two deployment modes:

### Option A: Docker Compose (recommended for evaluation)

#### 1 · Git & Docker Desktop

| Operating System | Download |
|---|---|
| 🪟 Windows | [Git for Windows](https://git-scm.com/download/win) · [Docker Desktop](https://www.docker.com/products/docker-desktop/) |
| 🍎 macOS | [Git for macOS](https://git-scm.com/download/mac) · [Docker Desktop](https://www.docker.com/products/docker-desktop/) |

> [!IMPORTANT]
> Docker Desktop **must be started and fully initialised** before running the commands below. On Windows, the Docker whale icon (🐋) appears in the system tray after startup. On macOS it is in the menu bar. If the icon is not visible, start Docker Desktop manually and wait until the animation stops.

**Minimum resource configuration** (Docker Desktop → Settings → Resources):

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 4 cores | 6+ cores |
| RAM | 8 GB | 12 GB (with local LLM via Ollama) |
| Disk image | 20 GB | 30 GB |

### 2 · Access to the GitHub Container Registry (ghcr.io)

The stack consists of three images pulled automatically on the first `docker compose up`:

```
ghcr.io/autonomous-asset-management-agents/aaagents-backend:latest
ghcr.io/autonomous-asset-management-agents/aaagents-public-api:latest
ghcr.io/autonomous-asset-management-agents/aaagents-frontend:latest
```

> [!NOTE]
> If the images are public, no login is required. For private images, see [Authentication at ghcr.io](#-authentication-at-ghcrio-private-image) below.

### Option B: Native Desktop Mode (no Docker required)

Run the engine directly on your machine with Python — no Docker, PostgreSQL, or Redis needed.

**Requirements:**
- Python 3.12+ with `pip`
- Git

**Quick Start:**

```bash
git clone https://github.com/Autonomous-Asset-Management-Agents/autonomous_.git
cd autonomous_/ai_trading_bot
pip install -r requirements.oss.txt
```

Configure your environment (`.env.oss`):
```env
# Leave these empty or unset for native SQLite mode:
DATABASE_URL=
REDIS_URL=

# Required API keys:
GEMINI_API_KEY=your_gemini_key
ALPACA_API_KEY=your_alpaca_paper_key
ALPACA_SECRET_KEY=your_alpaca_paper_secret
```

Start the engine:
```bash
python -m core.engine
```

> [!NOTE]
> In native mode, SQLite is used as the database (`data/aaagents.db`) and `LocalStateClient` replaces Redis. No external services are required. See [TROUBLESHOOTING.md §6](./TROUBLESHOOTING.md#6-sqlite-specific-issues-native-desktop-mode) for common issues.

---

## 🚀 Installation (step-by-step)

### Step 1 — Clone the repository and open a terminal

```bash
git clone https://github.com/Autonomous-Asset-Management-Agents/autonomous_.git
cd autonomous_
```

### Step 2 — Configure the environment (one command)

Run the included setup script. It copies the template and generates all cryptographic secrets automatically:

```bash
# macOS / Linux / Git Bash (Windows):
bash setup.sh
```

```powershell
# Windows PowerShell (alternative):
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Or via `make`:

```bash
make setup
```

The script handles everything automatically:
- Copies `.env.oss.example` → `.env.oss`
- Generates `POSTGRES_PASSWORD` (cryptographically secure via `secrets.token_hex`)
- Generates `REDIS_PASSWORD` (cryptographically secure via `secrets.token_hex`)
- Generates `PROXY_ENGINE_SHARED_SECRET` (HMAC-SHA256 signing key between Public API and Backend Engine)
- Generates `ENGINE_API_KEY` (Engine REST API auth key)
- **Prints no secrets to stdout** (intentional)

> [!IMPORTANT]
> The script creates `.env.oss` **on the first run only**. Re-running it will not overwrite an existing `.env.oss`. To regenerate: `rm .env.oss && bash setup.sh`

**One manual step remains** (optional — to activate paper trading):

Without Alpaca keys the system starts in **Offline Mode (Shadow Boot)**: the backend container comes up fully, all agents run, no orders are executed. This is the recommended entry point for evaluation and algorithm testing.

To activate live paper trading, open the dashboard at `http://localhost` and use the **Broker Connection** widget — it appears automatically at the top of the dashboard when no broker is connected yet:

1. Enter your **Alpaca API Key** in the first field
2. Enter your **Alpaca Secret Key** in the second field
3. Click **Save Keys** — credentials are encrypted and stored in the local PostgreSQL database

> [!NOTE]
> Free Alpaca Paper Trading accounts: [app.alpaca.markets](https://app.alpaca.markets) — no real capital required. The Broker Connection widget disappears once the broker is successfully connected.
>
> **Advanced / headless setups:** As an alternative to the UI, you can also set keys directly in `.env.oss` before starting the stack:
> ```env
> ALPACA_API_KEY=your_alpaca_paper_key
> ALPACA_SECRET_KEY=your_alpaca_paper_secret
> ```

### Step 3 — Start the stack

```bash
make start
```

> **Note:** If you don't have `make` installed, you can use the fallback Docker command. The `--env-file .env.oss` flag is **required** because Docker Compose only auto-loads a file literally named `.env`:
> ```bash
> docker compose --env-file .env.oss -f docker-compose.oss.yml up -d
> ```

Docker Compose pulls all three images (approx. 2–5 minutes on first start depending on bandwidth, plus model download) and starts the services in the correct order: PostgreSQL → Redis → Backend → Public API → Frontend.

> [!NOTE]
> **Headless Boot:** Docker starts all services in the background ("detached mode"). **It will not automatically open your browser.** You must manually open your browser and navigate to `http://localhost` once the startup is complete.

**What happens internally at boot:**
1. `postgres` is monitored via health-check; the backend starts only once the DB accepts connections
2. `gcs_sync_on_start.py` downloads pre-trained PyTorch models (LSTM ~11 MB, RL agent ~9 MB) from GitHub Releases — SHA256 verified
3. The backend automatically runs `alembic upgrade head` (database migrations)
4. Shadow Boot pre-flight validates Redis, Alpaca, and Gemini connectivity — if any check fails, the container exits
5. The engine initialises the LangGraph workflow and waits for inputs

> [!NOTE]
> **Degraded mode:** If `gcs_sync_on_start.py` cannot download the model files (e.g. no internet access or repository access issues), the engine boots anyway. `LSTMSignalAgent` and `RLConfidenceAgent` vote with a neutral score of 0.5 — the system is operational but without ML signals. Check the backend logs for `gcs_sync` warnings in this case.

### Step 4 — Verify the startup

The first start can take **3–5 minutes** depending on bandwidth and hardware (image pull + model download ~21 MB, skipped if this release has no model manifest + DB migration). Follow the startup process:

```bash
docker compose --env-file .env.oss -f docker-compose.oss.yml logs -f backend
```

A successful start is indicated by:

```
[gcs_sync] ✅ OSS sync complete — 6/6 file(s) written to data/
INFO:     Uvicorn running on http://0.0.0.0:8001
INFO:     Application startup complete.
```

Stop the log output with `Ctrl+C` (the containers keep running).

---

## 🖥️ Verification

Once the stack is fully up, open your browser at:

**→ `http://localhost`**

The **AAAgents Console** (the AAAgents dashboard) should load. You will see the main overview with simulated portfolio data, the agent voting log, and the kill-switch panel.

> [!IMPORTANT]
> The frontend dashboard binds exclusively to `127.0.0.1:80` (loopback — no LAN access) for security reasons. This is intentional: the UI transmits session tokens and broker API headers that must not be exposed over unencrypted HTTP across the network. To access the dashboard from another device, see [TROUBLESHOOTING.md §5 — TLS Proxy](./TROUBLESHOOTING.md#5-exposing-the-dashboard-over-the-internet-ssh-tunnel-required).

**Service port overview:**

| Service | Host binding | Description |
|---|---|---|
| Frontend (AAAgents Console) | `127.0.0.1:80` → Container:8080 | Dashboard — loopback only, no LAN access |
| Public API | `127.0.0.1:8081` → Container:8080 | Auth proxy in front of the backend engine — loopback only |
| Backend Engine | `127.0.0.1:8001` → Container:8001 | FastAPI engine, health endpoint — loopback only |
| PostgreSQL | `127.0.0.1:5432` → Container:5432 | Loopback only, no LAN access |
| Redis | `127.0.0.1:6379` → Container:6379 | Loopback only, no LAN access |

> [!NOTE]
> **All five service ports bind to `127.0.0.1` only.** No service is reachable from the LAN or the public internet by default. The dashboard transmits session tokens and broker API headers that must not be exposed over unencrypted HTTP across the network. To expose the stack to other devices (e.g. a remote browser, a different machine on your home network), put the frontend behind a TLS reverse proxy (Caddy, Nginx, Traefik) and bind that proxy to `0.0.0.0`. See [TROUBLESHOOTING.md §5 — TLS Proxy](./TROUBLESHOOTING.md#5-exposing-the-dashboard-over-the-internet-ssh-tunnel-required).

---

## 🛠️ Troubleshooting

### Port 80 or 8081 is already in use

If `docker compose up` fails with `Bind for 0.0.0.0:8081 failed: port is already allocated`, another process is occupying the port. Identify and stop it:

```bash
# Windows (PowerShell)
netstat -ano | findstr :80

# macOS / Linux
lsof -i :80
```

### Check container status

```bash
docker compose --env-file .env.oss -f docker-compose.oss.yml ps
```

All services should show status `running (healthy)` or `running`. If a service shows `Exit` or `restarting`, the logs will reveal the cause:

```bash
docker compose --env-file .env.oss -f docker-compose.oss.yml logs backend
```

### Full system reset (nuclear option)

Removes all local containers, volumes, and paper-trading state. The Alpaca brokerage account is not affected.

```bash
# Via Makefile (with confirmation prompt):
make reset

# Or manually:
docker compose --env-file .env.oss -f docker-compose.oss.yml down -v
docker compose --env-file .env.oss -f docker-compose.oss.yml up -d
```

> [!IMPORTANT]
> The `-v` flag permanently deletes the PostgreSQL volumes. All locally stored trade history data will be lost.

Full diagnostic patterns (Alembic migration errors, Ollama connection, out-of-memory crashes, Iron Dome blocks): [TROUBLESHOOTING.md](./TROUBLESHOOTING.md).

---

## 🔐 Authentication at ghcr.io (private image)

If the images are configured as private, `docker compose up` will reject the pull with `unauthorized: unauthenticated`. In this case a one-time login with a GitHub Personal Access Token (PAT) is required.

**Step 1 — Create a PAT**

Navigate to:
`GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)`

Create a token with the **`read:packages`** scope only.

> [!IMPORTANT]
> Treat the token like a password. **Never** store it as plain text in scripts or `.env` files tracked by a Git repository. Use a password manager or secret manager of your choice.

**Step 2 — Log in**

```bash
echo "YOUR_GITHUB_PAT" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

`--password-stdin` prevents the token from appearing in the shell history. On success Docker outputs `Login Succeeded`. The login is cached locally.

**Step 3 — Start the stack**

Proceed with `docker compose --env-file .env.oss -f docker-compose.oss.yml up -d` from [Step 3](#step-3--start-the-stack).

---

## 🧩 Extensibility — Custom Strategy Agents

AAAgents supports a plugin system for custom voting agents. Each plugin agent participates in the Round Table and emits a score between `0.0` (strong sell) and `1.0` (strong buy).

```python
# plugins/round_table/my_strategy.py
from core.round_table.base_agent import VotingAgent, VoteResult
from core.round_table.registry import register_agent

@register_agent("MyStrategyAgent")
class MyStrategyAgent(VotingAgent):
    default_weight: float = 15.0

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        # Custom logic: RSI, sentiment, macro signals, etc.
        return VoteResult(
            agent_name=self.__class__.__name__,
            symbol=state["symbol"],
            score=0.6,   # 0.0 = Strong Sell | 1.0 = Strong Buy
            weight=self.weight,
            reasoning="Neutral-bullish: demo plugin."
        )
```

Enable plugin loading in `.env.oss`:

```env
ALLOW_UNTRUSTED_PLUGINS=true
ROUND_TABLE_PLUGINS_DIR=/app/app/plugins/round_table
```

> [!IMPORTANT]
> `ALLOW_UNTRUSTED_PLUGINS=true` enables dynamic code loading at engine boot time. Every `.py` file in the plugin directory is executed as the container user — this is effectively **arbitrary code execution**. Only enable this option if you have written or fully reviewed every plugin file yourself. The default is `false` (deny-by-default). Full plugin API reference: [PLUGIN_TUTORIAL.md](./PLUGIN_TUTORIAL.md).

---

## 📚 Further Documentation

| Document | Contents |
|---|---|
| [VISION_AND_EDITIONS.md](./VISION_AND_EDITIONS.md) | System vision, value chain, Community vs. Enterprise |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Bounded contexts, auth abstraction, ML models, plugin API, engine bootstrap |
| [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) | Operations handbook: startup errors, LLM, Iron Dome blocks, TLS proxy setup |
| [PLUGIN_TUTORIAL.md](./PLUGIN_TUTORIAL.md) | Step-by-step: writing and registering a custom voting agent |
| [CONTRIBUTING.md](../../CONTRIBUTING.md) | PR workflow, coding standards, Archon Standard gate |
| [SECURITY.md](../../SECURITY.md) | Responsible disclosure policy |
| [DISCLAIMER.md](../../DISCLAIMER.md) | Legal status, BaFin positioning, liability disclaimer |

---

## 🤝 Community

- 💬 [GitHub Discussions](https://github.com/Autonomous-Asset-Management-Agents/autonomous_/discussions) — questions, ideas, plugin showcase
- 🐛 [Issues](https://github.com/Autonomous-Asset-Management-Agents/autonomous_/issues) — bug reports
- 📖 [Contributing Guide](../../CONTRIBUTING.md) — pull request workflow

---

## 📄 Licence

- **Source code:** Apache 2.0 — see [LICENSE](../../LICENSE)
- **Model weights (PyTorch):** CC-BY-4.0 — see [LICENSE-MODELS](../../LICENSE-MODELS)

---

*Maintained by the AAAgents Community · [aaagents.de](https://aaagents.de) · [Releases](https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases)*
