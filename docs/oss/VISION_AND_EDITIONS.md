# AAAgents: Autonomous Asset Management Agents

> **This file is included in the OSS snapshot and served to community users.**
> For the full internal cross-reference (Feature → Module → Code), see the private repo:
> `docs/0_strategy_and_roadmap/value_chain/vc7_editions.md` and `docs/1_architecture_and_adr/MODULE_MAP.md`.

---

### 👁️ Vision: The Future of Institutional Wealth Management

We believe the future of asset management does not lie in black-box algorithms, but in **explainable, autonomous AI agents** operating within a highly regulated framework.

AAAgents digitalizes the entire value chain of an institutional asset manager — from market analysis to trade execution. We do not view strict regulations (MiFID II, BaFin, DORA) as obstacles, but rather as our architectural foundation: *Compliance by Design*. Every decision made by the AI is transparent, reproducible, and explainable down to the individual data signal through a "Glass Box" approach.

---

### ⚙️ The Digital Value Chain

1. **VC-1 Research (Idea Factory):** Specialized AI agents analyze structured and unstructured market data and macroeconomic signals in real-time.
2. **VC-2 Portfolio Construction:** In a virtual "Round Table V2", 9 differently weighted agents evaluate investment ideas. A *Consensus Engine* consolidates signals (BUY > 0.65 / SELL < 0.35 / NO-TRADE between).
3. **VC-3 Trading & Execution:** Deterministic, asynchronous execution of compliant orders via direct broker APIs.
4. **VC-4 Risk Management (Iron Dome):** Synchronous REST-based pre-trade gate — blocks non-compliant transactions in milliseconds.
5. **VC-5 Administration & Back-Office:** Shadow ledger, reconciliation, Cloud SQL persistence, ML model lifecycle.
6. **VC-6 Reporting & Auditing:** Senate Protocol persists exact agent vote records per trade (MiFID II / RTS 6 audit trail).

---

### 🧰 OSS Developer Tooling (PyPI)

Beyond the desktop app, we publish small, standalone, Apache-2.0 **developer-tooling packages** to PyPI for the AI-agent community:

- **`autonomous-audit`** — a tamper-evident, hash-chained decision-audit log plus a human-readable report for AI trading agents (Python standard library only). Try it: `uvx autonomous-audit demo`.

These are integrity/tooling utilities — **not** investment advice, a trading service, or a regulatory control. *(`autonomous-trading` is a reserved brand-namespace placeholder.)*

---

### 📦 Community vs. Enterprise — Complete Feature Matrix

> **Status labels:** ✅ Implemented · 🟡 Planned · 🔴 Disabled/Not available · ⚠️ Partial

#### Infrastructure & Deployment

| Feature | Community Edition (OSS) | Commercial Tiers (Pro, Professional, Enterprise) |
|---|---|---|
| Runtime | ✅ Native Desktop App (Electron) | ✅ GCP Cloud Run / AWS (BYOC / Self-hosted Cloud) |
| Database | ✅ SQLite (WAL) | ✅ Cloud SQL |
| Cache | ✅ Local Redis on 127.0.0.1 | ✅ Cloud Memorystore Redis |
| ML Model Source | ✅ GitHub Releases at boot | ✅ GCS Bucket Sync |
| ML Training Pipeline | 🔴 Local scripts only | ✅ Cloud Run Jobs (train_cloud.py) |
| Vertex AI Experiment Tracking | 🔴 Not available | ✅ Implemented |
| A/B Testing Framework | 🔴 Not available | ✅ Implemented (return/drawdown comparison) |
| Strategy Hot-Swap (live) | 🔴 Restart required | ✅ Live API switch with MiFID II audit log |
| Ops Alerting (Slack) | 🔴 Disabled | ✅ Kill-switch, ML errors, budget alerts |
| Shadow Mode (staging safety) | ✅ Paper trading default | ✅ SHADOW_MODE=true intercepts all orders |
| OpenTelemetry Tracing | 🔴 Disabled | ✅ GCP Cloud Trace + DB-insert spans |

#### Authentication & Secret Management

| Feature | Community Edition (OSS) | Commercial Tiers (Pro, Professional, Enterprise) |
|---|---|---|
| Auth Provider | ✅ LocalMockAuth (loopback-bound) | ✅ FirebaseAuth (cryptographic JWT) |
| OAuth Token Storage | ✅ OS Keychain via keyring | ✅ GCP Secret Manager |
| Per-User Alpaca Credentials | 🔴 .env.oss only | ✅ Firebase-UID-scoped, 3 secrets per user |
| Secret Revoke (GDPR) | 🔴 Not available | ✅ Implemented (audit trail preserved) |
| Multi-Tenancy | 🔴 Single-tenant | ⚠️ DB schema dormant (tenant_id columns) |

#### Market Data & Data Quality

| Feature | Community Edition (OSS) | Commercial Tiers (Pro, Professional, Enterprise) |
|---|---|---|
| Alpaca Data Feed | ✅ IEX (free, paper trading) | ✅ SIP (full US market aggregator) |
| VIX Source | ✅ Synthetic (60-day SPY volatility proxy) | ✅ Real CBOE VIX via Polygon.io |
| Polygon Fundamentals | ✅ Optional (graceful fallback) | ✅ fetch_fundamentals() for all symbols |
| Databento Historical Data | ✅ Optional (key configured if available) | ✅ Primary source for backtesting |
| News Sentiment | ⚠️ Active if GEMINI_API_KEY set, else weight=0 | ✅ Full quota + Redis cache (5 min TTL) |
| AI Market Scanner | 🔴 Not available | ✅ Gemini-based strategy-selector scoring |
| Multi-Broker Support (IBKR etc.) | 🔴 Alpaca only | 🟡 Planned (Phase 5) |

#### AI Round Table (9 Agents)

| Agent | Weight | Community Edition (OSS) | Enterprise Edition |
|---|---|---|---|
| DrawdownGuardAgent | 0.60 | ✅ Always active | ✅ Always active |
| SpecialistAlphaAgent | 0.55 (config-gated; default 0.0) | ⚠️ Dormant by default — `SPECIALIST_ALPHA_WEIGHT=0.0` (`agents.py#L33`) | ✅ Active with data pipeline |
| RegimeDetectionAgent | 0.50 | ✅ Always active | ✅ Always active |
| MomentumAgent | 0.45 | ✅ Always active | ✅ Always active |
| VIXAwareRiskAgent | 0.45 | ✅ Volume proxy | ✅ Real CBOE VIX |
| LSTMSignalAgent | 0.40 | ✅ If model loaded, else weight=0 | ✅ GCS-synced, auto-versioned |
| RLConfidenceAgent | 0.40 | ✅ If model loaded, else weight=0 | ✅ GCS-synced, auto-versioned |
| NewsSentimentAgent | 0.35 | ⚠️ weight=0 without GEMINI_API_KEY | ✅ Active with full quota |
| PatternRecognitionAgent | 0.30 | ✅ Always active (pure math) | ✅ Always active |
| **Plugin Agents** | Variable | ✅ Community plugins via ROUND_TABLE_PLUGINS_DIR | ✅ Curated enterprise plugins |

> **Consensus thresholds (both editions):** BUY > 0.65 · SELL < 0.35 · NO-TRADE between

#### Risk Management — Iron Dome (identical in both editions)

| Feature | Status |
|---|---|
| MLWatchdog (60s alert, 300s safe halt) | ✅ Both editions |
| RiskManager (ADR-R01 to R10, VIX scaling, portfolio stop-loss 7%) | ✅ Both editions |
| ComplianceGuardian (5-step pre-trade gate, MiFID II field check) | ✅ Both editions |
| Wash-Trade Detection (60s window) | ✅ Both editions |
| Kill Switch (mass-cancel, Redis-backed) | ✅ Both editions |
| Max Order Value Gate (10,000 EUR) | ✅ Both editions |
| Daily Trade Limit (50 trades/day) | ✅ Both editions |

#### Advanced Trading Intelligence (Enterprise only)

| Feature | Community Edition (OSS) | Commercial Tiers (Pro, Professional, Enterprise) |
|---|---|---|
| TradeIntelligence (adaptive per-symbol tracking) | 🔴 Not available | ✅ Win rate, profit factor, history |
| Self-Tuning Parameters | 🔴 Not available | ✅ Adaptive based on symbol history |
| Agent Attribution | 🔴 Not available | ✅ Which agent drove the trade |
| Pattern Detection | 🔴 Not available | ✅ FOMO, revenge trading, pattern learning |
| IntelligentExit | 🔴 Not available | ✅ Multi-factor exit score (5 dimensions) |
| RuleValidator | 🔴 Not available | ✅ Walk-Forward + Monte Carlo validation |
| Portfolio Optimization (cuFOLIO — Mean-CVaR allocation) | 🔴 Not available | 🔨 Enterprise Edition only — planned/dormant (#1905) |

#### Audit Logging

| Feature | Community Edition (OSS) | Commercial Tiers (Pro, Professional, Enterprise) |
|---|---|---|
| Round Table Logger | ✅ LocalJSONAuditLogger (JSONL + SHA-256) | ✅ SenateProtocol (Redis Streams + Cloud SQL) |
| WORM Audit Log (Cloud SQL) | 🔴 Local JSONL fallback | ✅ mifid_decision_log table (immutable) |
| Decision Reasoning Trace | 🔴 Not available | ✅ Decision table with reasoning_summary |
| AI Transparency Log | 🔴 Not available | ✅ AIThought table (agent reasoning JSON) |

#### Scope Boundaries — Not in Either Edition

| Feature | Status | Reason |
|---|---|---|
| High-Frequency Trading (< 1s) | 🔴 Not supported in OSS · 🟡 Planned Enterprise Phase 6+ | Architecture redesign required |
| Multi-Broker (IBKR etc.) | 🔴 Not supported | 🟡 Planned Enterprise Phase 5 |
| Third-party asset management (fiduciary) | 🔴 Not in scope | Separate regulatory license required |
| BaFin/FCA direct filing | 🔴 Not in scope | Not a regulated entity — tooling only |
| Local LLM (Ollama) | ✅ Shipped — vendor-independent picker: local Ollama (Mistral/Llama) + BYO-key cloud (Gemini/OpenAI/Anthropic) | ✅ Same picker |

---

### 🏛️ MiFID II Compliance Foundation

> **Context:** MiFID II obligations apply to regulated investment firms, not to individuals trading their own accounts. The Enterprise Edition provides this infrastructure for clients operating as regulated entities (e.g., family offices, licensed portfolio managers).

#### Implemented (both editions where noted)

| Requirement | Basis | Status |
|---|---|---|
| Pre-Trade Gate — 5-step | Art. 17 MiFID II | ✅ Both editions |
| MiFID II field validation | Art. 25 + RTS 22 | ✅ Both editions |
| Wash-Trade Detection | MAR Art. 12 | ✅ Both editions |
| WORM Audit Log (5 years) | Art. 16 | ✅ Enterprise |
| Decision Reasoning Trace | Art. 25 (Know-Your-Algorithm) | ✅ Enterprise |
| AI Transparency Log | EU AI Act Art. 13 + MiFID II Art. 25 | ✅ Enterprise |
| RTS 6: Strategy-Swap Audit | RTS 6 Art. 7 | ✅ Enterprise |
| RTS 6: Force-Cycle Audit | RTS 6 Art. 7 | ✅ Enterprise |

#### Open Points (Planned)

| Gap | Basis | Priority |
|---|---|---|
| `GET /api/v1/audit/export` — structured export for date range | Art. 16 + RTS 22 Art. 3 | P1 |
| `RTS22TransactionReport` formatter (CSV/JSON) | RTS 22 Art. 3 | P1 |
| NCA format (ISO 20022 XML) | RTS 22 Annex I | P2 |
| LEI field in MifidDecisionLog | RTS 22 Field 7 | P2 |
| ISIN mapping (symbol → ISIN via Polygon) | RTS 22 Field 41 | P2 |

---

*Last updated: 2026-06-12 | Source of truth (internal): `docs/0_strategy_and_roadmap/value_chain/vc7_editions.md`*
