# AAAgents: Autonomous Asset Management Agents

> **This file is included in the OSS snapshot and served to community users.**
> For the full internal cross-reference (Feature → Module → Code), see the private repo:
> `docs/0_strategy_and_roadmap/EDITIONS.md` and `docs/1_architecture_and_adr/MODULE_MAP.md`.

---

## Editions-Matrix (OSS ↔ Enterprise)

<!-- BEGIN gen_editions:matrix -->
> **GENERIERT — nicht von Hand editieren.** Quelle: die kanonische Matrix in
> `docs/0_strategy_and_roadmap/EDITIONS.md` §0. Neu erzeugen:
> `python scripts/gen_editions.py` (Drift bricht die CI, `scripts/test_gen_editions.py`).

| Fähigkeit | Community (OSS / Desktop) | Enterprise | Status |
| :--- | :--- | :--- | :--- |
| Runtime | Desktop-App (Win/macOS) · Docker-Compose self-hosted | GCP Cloud Run (managed, auto-scaling) | ✅ |
| Datenbank | SQLite (Desktop, auto-bootstrap) · lokales PostgreSQL (Docker self-hosted) | Cloud SQL | ✅ |
| Cache | lokales Redis (self-hosted) · keiner nötig (Desktop) | Cloud Memorystore Redis | ✅ |
| Auth | LocalMockAuth (IP-gebunden) | Firebase Auth (`verify_id_token`) | ✅ |
| Secret-Speicher | OS-Keychain (verschlüsselt, `core/keychain.py`, SEC-5/#1084) | GCP Secret Manager | ✅ |
| Marktdaten-Feed | Alpaca IEX (kostenlos) | Alpaca SIP + Databento | ✅ |
| VIX | synthetisch (SPY-Vol-Proxy) | echter CBOE-VIX (Polygon) | ✅ |
| Round Table | 14 Agenten (kanonische Liste: `docs/2_agentic_operations/AGENT_CATALOG.md`) | 14 Agenten + kuratierte Plugins | ✅ |
| LLM | Desktop: lokales Ollama · Dev/Cloud: Gemini | Gemini (voll) / kundeneigenes LLM | ✅ |
| Iron Dome (Risk) | identisch (voll) | identisch + governierte, admin-adjustierbare Limits (ADR-SEC-06) | ✅ |
| Trading Intelligence (IntelligentExit, `should_trade`) | vorhanden & aktiv (primärer Desktop-Exit-Pfad) | identisch | ✅ |
| Portfolio-Optimierung (cuFOLIO) | nicht verfügbar (`scripts/oss_exclude.txt`) | Institutional-only, dormant (`CUFOLIO_ENABLED`) | 🔨 |
| MiFID II WORM / RTS-Reporting | lokales JSON-Audit (SHA-256 Hash-Kette) | Cloud-SQL WORM + RTS 6; RTS 22-Export teilweise | ⚠️ |
| Multi-Tenancy | Single-Tenant | Multi-Tenant (Schema dormant) | ⚠️ |
| SEC-EDGAR-Fundamentals | gebundelter Public-Domain-Snapshot (Desktop+OSS) + Nightly-Refresh | identisch | ✅ |
| Makro-Regime-Daten (FRED) | Alpaca-ETF-Proxy (HYG/TLT/USO) **oder** user-eigener FRED-Key — KEIN geteilter Client-Key | server-seitig ein FRED-Key + Cache | 🟡 |
<!-- END gen_editions:matrix -->

---

### 👁️ Vision: The Future of Institutional Wealth Management

We believe the future of asset management does not lie in black-box algorithms, but in **explainable, autonomous AI agents** operating within a highly regulated framework.

AAAgents digitalizes the entire value chain of an institutional asset manager — from market analysis to trade execution. We do not view strict regulations (MiFID II, BaFin, DORA) as obstacles, but rather as our architectural foundation: *Compliance by Design*. Every decision made by the AI is transparent, reproducible, and explainable down to the individual data signal through a "Glass Box" approach.

---

### ⚙️ The Digital Value Chain

1. **VC-1 Research (Idea Factory):** Specialized AI agents analyze structured and unstructured market data and macroeconomic signals in real-time.
2. **VC-2 Portfolio Construction:** In a virtual "Round Table V2", 14 differently weighted agents evaluate investment ideas. A *Consensus Engine* consolidates signals (BUY > 0.65 / SELL < 0.35 / NO-TRADE between).
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

### 📦 Vollständige Feature-Matrix

Die kanonische, code-verifizierte Fähigkeits-Matrix steht **oben** (generierter Block, Quelle: §0 in `docs/0_strategy_and_roadmap/EDITIONS.md`). Die **vollständige** Enterprise-Feature-Matrix inkl. Detail-Tabellen und die 4-Tier-Paketierung führt der Master:

→ **[docs/0_strategy_and_roadmap/EDITIONS.md](../0_strategy_and_roadmap/EDITIONS.md)**

*(Die frühere hand-gepflegte Matrix hier wurde entfernt — sie driftete gegen den Master; die OSS-Projektion wird jetzt via `scripts/gen_editions.py` generiert, RULE-D6.)*

---

*Source of truth (internal): `docs/0_strategy_and_roadmap/EDITIONS.md` · OSS-Projektion generiert via `scripts/gen_editions.py`*

