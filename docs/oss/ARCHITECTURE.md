# AAAgents Architecture (Community Edition)

Welcome to the architectural overview of the Autonomous Asset Management Agents (AAAgents). This document is designed to give both human developers and AI coding assistants a deep understanding of the system's structural constraints.

> [!CAUTION]
> **To AI Coding Assistants (Copilot, Cursor, etc.):**
> Before generating any PRs or code changes, you MUST read and understand the Bounded Contexts described below. Do not mix signal generation with compliance logic.

## 1. The Separation of Concerns: Bounded Contexts

The entire system is strictly divided into two completely separate domains. They communicate via well-defined DTOs (Data Transfer Objects) and never access each other's state directly.

### The Round Table V2 (Signal Generation)
This is the "Brain" of the operation. Powered by LangGraph, multiple specialized AI agents evaluate the market and debate on the best action (BUY/SELL/HOLD).

- **Location:** `core/round_table/`
- **Agents:** `RegimeDetectionAgent`, `LSTMSignalAgent`, `RLConfidenceAgent`, `NewsSentimentAgent` (and others).
- **Consensus:** A weighted-average engine aggregates their votes. Thresholds are strict (BUY > 0.65, SELL < 0.35).
- **Rule:** The Round Table has NO access to your portfolio balance, your open positions, or any broker logic. It purely analyzes the *symbol* (e.g. AAPL) and emits a theoretical `TradeSignal`.

### Round-Table Display Store & Console Read-API (G1, #1050)

The desktop console needs a live view of Round-Table results without touching the
trading or compliance path. Two pieces provide it (PR #1169):

- **`core/round_table/recent_decisions.py`** — an in-memory **latest-per-symbol
  display store** (one entry per symbol, `threading.Lock`-guarded, fail-safe
  `record_round_table_decision()` that never raises). The single producer is
  `run_round_table`, recording the same `SenateSession` it already logs to the
  protocol. This store is **NOT a compliance record** — the hash-chained
  `LocalJSONAuditLogger` JSONL remains the audit source of truth — and it is
  read-only for the API layer (never on the order path).
- **Three read-only engine routes** (all behind `require_engine_key`, DTO
  contract pinned by `tests/fixtures/g1/`): `GET /specialist-reports`
  (documented empty-state while the specialist registry is disabled),
  `GET /round-table-decisions` (latest per symbol, newest first), and
  `GET /round-table/{symbol}` (votes mapped to BULL/BEAR/ABSTAIN for the
  console's verdict view).

### The Iron Dome (Risk & Compliance Gatekeeper)
This is the "Shield". Once the Round Table emits a `TradeSignal`, it lands in the Iron Dome to be audited before execution at the broker.

- **Location:** `core/risk_manager.py` & `core/compliance.py`
- **Functions:** Position Sizing, Stop-Loss triggers, Pattern Day Trader (PDT) checks, Sector Concentration limits.
- **Rule:** The Iron Dome NEVER second-guesses the ML models. If the Iron Dome rejects a trade, it rejects it because of *risk management* (e.g. insufficient funds, violation of Volatility-Index limits), not because it disagrees with the sentiment.

> [!NOTE]
> **Full Iron Dome = 3 layers** (canonical definition per `docs/4_secops_and_compliance/risk_compliance.md`):
> Layer 0 — `MLWatchdog` (in `core/ml_watchdog.py`) | Layer 1 — `RiskManager` (`core/risk_manager.py`) | Layer 2 — `ComplianceGuardian` (`core/compliance.py`).
> The `ComplianceGuardian` (Iron Dome gate) is **not the same** as the `ComplianceGatekeeper` (`round_table/gatekeeper.py`), which is the Round Table Portfolio-Veto within VC-2.

> [!WARNING]
> If you are adding a new AI Agent, you add it to the `Round Table`. You **NEVER** add market-analysis logic to the `Iron Dome`.

## 2. Authentication & Tenancy (LocalMockAuth)

The Enterprise Edition of this codebase runs on Google Cloud Platform as a multi-tenant SaaS, secured by Firebase Admin SDK.
For this Community Edition, we have abstracted the Cloud requirements via a Pydantic interface.

- **The Interface:** `core/auth_interfaces.py`
- **Community Behavior:** We use `LocalMockAuth`. This bypasses Firebase entirely. There is no user-registration in the Community Edition. The system assumes a single-tenant environment running on `localhost`.
- **Security Implications:** When making API endpoints, always rely on `dependency_overrides` or the injected auth provider. Do not hardcode Firebase token validation.

### 2.1 Credential Store (SEC-5 — OS Keychain)

API secrets (Alpaca, Gemini, Polygon, Databento) are stored in the **OS-native credential store** via the Python `keyring` library:

| OS | Backend |
|---|---|
| Windows | Credential Manager (DPAPI encryption) |
| macOS | Keychain |
| Linux | Secret Service (D-Bus / GNOME Keyring) |

**Boot sequence in `config.oss.py`:**

1. `load_secrets_from_keychain()` — reads keychain → injects into `os.environ`
2. `load_dotenv()` — reads `.env.oss` (dev fallback, does NOT overwrite keychain values)
3. `_clean_env()` → `os.getenv()` — existing config logic is unchanged

**Precedence (highest wins):**
- Explicit env var (e.g. `ALPACA_API_KEY=xxx python …` in CI)
- OS Keychain (via `keyring.get_password("aaagents", key)`)
- `.env.oss` (via `dotenv`)

**CLI tools:**
- `python -m core.keychain_cli setup` — interactive credential wizard
- `python -m core.keychain_cli migrate` — migrate `.env.oss` → keychain
- `python -m core.keychain_cli status` — show configured keys

> **Note:** `.env.oss` remains supported as a dev/CI fallback. The desktop installer (OSS-3) will NOT create a `.env.oss` — the Setup Wizard is the only path for end users.

## 3. Machine Learning Models (GitHub Releases — No GCP Dependency)

In the Enterprise Edition, models (`.pt` PyTorch files) are synced dynamically from Google Cloud Storage on boot.
In the Community Edition, model loading is handled by `scripts/gcs_sync_on_start.py` in **OSS mode** (when `GCS_DATA_BUCKET` is not set):

- **At container boot:** `gcs_sync_on_start.py` reads `data/models_manifest.json` and downloads models directly from **GitHub Releases** (LSTM ~11 MB, RL ~9 MB), SHA256-verified.
- **Security:** Downloads use `_NoRedirectOpener` (blocks redirects) and `_read_capped` (memory cap).
- **Atomic writes:** UUID-based file locking prevents race conditions in multi-container setups.
- **Degraded mode:** If the download fails (no internet, private repo access), the engine boots with a neutral 0.5 score for ML agents — no crash.
- **Native fallback:** If you run the code without Docker, download model files from GitHub Releases and place them in the `data/` directory.
## 4. Database & State (Dual-Mode: SQLite / PostgreSQL)

The system uses SQLAlchemy ORM for all database operations. The active backend is selected at startup based on `DATABASE_URL`:

| Component | Desktop Mode (OSS) | Enterprise Mode (Cloud) |
|---|---|---|
| **Relational DB** | SQLite (local, file-based, auto-bootstrapped) | PostgreSQL 15 / AlloyDB |
| **Migrations** | `Base.metadata.create_all()` via `bootstrap.py` | Alembic (`alembic upgrade head`) |
| **LangGraph Checkpointer** | `None` (Stateless Desktop Graph) | `RedisSaver` (Memorystore) |
| **State / Cache** | `LocalStateClient` (in-memory, thread-safe) | Redis 7 (Memorystore) |
| **Audit Trail** | `LocalJSONAuditLogger` (JSONL + SHA-256) | `SenateProtocol` (Redis + Cloud SQL) |

### 4.1 SQLite Bootstrap Lifecycle

When `DATABASE_URL` is unset or contains a dummy value, the engine automatically:

1. Creates `data/aaagents.db` with WAL journal mode (`PRAGMA journal_mode=WAL`)
2. Runs `Base.metadata.create_all()` to initialize all ORM tables
3. Stores a schema version in the `_schema_version` table for upgrade detection
4. Creates a timestamped `.bak` backup before any schema change

> [!NOTE]
> The bootstrap runs lazily via `ensure_local_db_ready()` during engine startup — **not** at module import time. This avoids filesystem side effects during test collection and linting.

### 4.2 State Management (Redis vs. LocalStateClient)

The `RedisClient` factory in `core/redis_client.py` returns a `LocalStateClient` when `REDIS_URL` is empty. This in-memory client implements the full Redis API subset used across the codebase:

- **Key/Value:** `get`, `set`, `setnx`, `delete` (with TTL support)
- **Lists:** `rpush`, `ltrim`, `lrange` (rolling buffers via `collections.deque`)
- **Streams:** `xadd`, `xread` (signal propagation)
- **Pipeline:** Batched operations with thread-safe locking

> [!IMPORTANT]
> `LocalStateClient` data is ephemeral — it does not survive process restarts. This is acceptable for single-tenant desktop mode. Enterprise mode uses Redis with persistence.

### 4.3 Dialect-Agnostic ORM (BORA Rule)

All database queries MUST use SQLAlchemy ORM constructs. The `_dialect_insert_ignore()` helper in `cloud_logger.py` automatically emits `ON CONFLICT DO NOTHING` (PostgreSQL) or `INSERT OR IGNORE` (SQLite). Direct use of `asyncpg` or PostgreSQL-specific SQL is **forbidden** in new code (see `CODING_POLICY.md §15`).

### 4.4 In-Memory Display Store (FastAPI Console)

To support real-time UI updates for the desktop console without hitting the relational database or disk audit logs on the hot path, a thread-safe, in-memory latest-per-symbol store is implemented:
- **In-Memory Store:** [recent_decisions.py](file:///c:/Users/andre/Documents/GitHub/Dev-Enviroment/ai_trading_bot/core/round_table/recent_decisions.py) records the latest decision for each active symbol (safely capped to prevent memory leaks).
- **Console endpoints:** [api_routes.py#L1537](file:///c:/Users/andre/Documents/GitHub/Dev-Enviroment/ai_trading_bot/core/engine/api_routes.py#L1537) registers `/round-table-decisions` and `/round-table/{symbol}` to read from this store.

---



## 5. Plugin Architecture (OSS Extension Point)

### Einen eigenen Voting-Agent schreiben

1. Erstelle eine neue Datei in `plugins/round_table/my_agent.py`
2. Erbe von `VotingAgent` (für async-native Agents) ODER von `AsyncAIAgent` (für synchrone PyTorch-Inferenz)
3. Implementiere `vote()` (bei VotingAgent) oder `_run_inference()` (bei AsyncAIAgent)
4. Nutze den `@register_agent` Decorator
5. **Opt-in:** Setze in deiner `.env.oss` (kopiert von `.env.oss.example`):
   ```
   ALLOW_UNTRUSTED_PLUGINS=true
   ROUND_TABLE_PLUGINS_DIR=/app/app/plugins/round_table
   ```
   > [!CAUTION]
   > `ALLOW_UNTRUSTED_PLUGINS=true` aktiviert dynamischen Code-Load aus deinem
   > `./plugins/round_table/` Ordner. Jede `.py`-Datei dort wird beim Engine-Boot
   > als Host-User ausgeführt — das ist effektiv Arbitrary Code Execution.
   > Aktiviere das nur, wenn du JEDE Plugin-Datei selbst geschrieben oder
   > nachvollziehbar reviewed hast. Default ist deny-by-default (`false`).

### Wann AsyncAIAgent vs. VotingAgent?
- `VotingAgent`: für alle Agents, die bereits `async`-kompatiblen Code nutzen (API-Calls, Redis etc.)
- `AsyncAIAgent`: NUR für Agents mit blockierendem synchronen Code (z.B. direkter `torch.forward()`)

### Lizenz-Modi

> [!IMPORTANT]
> **RULE-D5 / AI-Agent Safety:** The class `DummyAuditLogger` does **not exist** in the codebase
> (`ADR-OSS2#L43–47`). Do not create or reference it. The OSS audit pipeline wires
> `LocalJSONAuditLogger` unconditionally — there is no "no-audit" mode.
> See [`ADR-OSS2`](../1_architecture_and_adr/ADR-OSS2-Compliance-Functional-Gate.md) for the
> compliance gate design and Finding F-04 rationale.

- **`ENTERPRISE_LICENSE_KEY` gesetzt →** `SenateProtocol` (Redis + Cloud SQL Audit, `core/round_table/senate_log.py`)
- **Kein Key →** `LocalJSONAuditLogger` (schreibt JSONL nach `/app/oss_audit_logs/audit_log_*.jsonl`, `senate_log.py#L<see-ADR-OSS2>`)

Die MiFID-II-Compliance-Gate (`ADR-OSS2`) verifiziert in CI, dass `LocalJSONAuditLogger()` in `runner.py` verdrahtet ist und physisch auf Disk schreibt.

## 6. Engine Bootstrapper

Damit das Round Table System und die Dependency Injection greifen, MUSS die Engine zwingend über `boot_engine()` initialisiert werden.

Dies geschieht zentral am Ende von `BotEngine.__init__()` in `core/engine/base.py`. Wenn du die Module ohne die reguläre `BotEngine` nutzt (z.B. in Standalone-Scripts), musst du `boot_engine(os.getenv("ENTERPRISE_LICENSE_KEY"))` manuell aufrufen. Ansonsten wird `run_round_table` blockieren, da der Dependency-Context fehlt.

## 7. Frontend Service (AAAgents Console)

Seit PR #814 ist der **AAAgents Console** (React/TypeScript Dashboard) als eigener Container im OSS Compose Stack enthalten.

- **Image:** `ghcr.io/autonomous-asset-management-agents/aaagents-frontend:latest` (nginx)
- **DSGVO Loopback Binding:** `127.0.0.1:80:8080` — **nur Loopback**, kein LAN-Zugriff auf unverschlüsseltem HTTP
- **Zugriff:** `http://localhost` im Browser (nach `docker compose up`)

**Port-Matrix (vollständiger Stack aus `docker-compose.oss.yml`):**

| Service | Host-Binding | Beschreibung |
|---|---|---|
| Frontend (AAAgents Console) | `127.0.0.1:80` → Container:8080 | Dashboard — Loopback only, kein LAN-Zugriff |
| Public API | `0.0.0.0:8081` → Container:8080 | Auth-Proxy vor der Backend Engine |
| Backend Engine | `127.0.0.1:8001` → Container:8001 | FastAPI Engine, Health Endpoint |
| PostgreSQL | `127.0.0.1:5432` → Container:5432 | Loopback only, kein LAN-Zugriff |
| Redis | `127.0.0.1:6379` → Container:6379 | Loopback only, kein LAN-Zugriff |

### 7.1 Desktop Mode (Non-Docker) Host Binding

When running natively as a desktop app, the Docker container matrix is bypassed. To secure the environment and conform to the local security boundary, loopback-only binding is enforced:
- **Engine Process:** Spawns on `127.0.0.1:8001` (configured via [__main__.py#L42](file:///c:/Users/andre/Documents/GitHub/Dev-Enviroment/ai_trading_bot/core/engine/__main__.py#L42)).
- **Public API Proxy:** Binds to `127.0.0.1:8081` (configured via [serve_public_api.py#L909](file:///c:/Users/andre/Documents/GitHub/Dev-Enviroment/serve_public_api.py#L909)), though for pure local desktop operation proxy signatures are disabled (`REQUIRE_SIG=false`).
- **UI Web Console:** Served locally by Electron's static server on a dynamic local port, communicating securely via webSecurity-validated CORS headers.

> [!NOTE]
> Der Compliance-Prüfpunkt `A.9` (Audit-Bereich) wird durch die Loopback-Bindung erfüllt: Session-Tokens und Broker-API-Header werden nicht über unverschlüsseltes HTTP übers Netzwerk übertragen.
