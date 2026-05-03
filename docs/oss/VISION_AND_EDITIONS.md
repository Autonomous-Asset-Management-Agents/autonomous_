# AAAgents: Autonomous Asset Management Agents

### 👁️ Vision: The Future of Institutional Wealth Management
We believe the future of asset management does not lie in black-box algorithms, but in **explainable, autonomous AI agents** operating within a highly regulated framework. 

AAAgents digitalizes the entire value chain of an institutional asset manager—from market analysis to trade execution. We do not view strict regulations (MiFID II, BaFin, DORA) as obstacles, but rather as our architectural foundation: *Compliance by Design*. Our ultimate goal is absolute auditability. Every decision made by the AI is transparent, reproducible, and explainable down to the individual data signal through a "Glass Box" approach.

---

### ⚙️ The Digital Value Chain
The system is built as a modular multi-agent network, mapping the traditional departments of a financial institution into software artifacts:

1. **Research (Idea Factory):** Specialized AI agents analyze structured and unstructured market data, news, and macroeconomic signals in real-time.
2. **Portfolio Construction:** In a virtual "Investment Board", differently weighted models discuss and evaluate investment ideas in parallel. A mathematical *Consensus Engine* consolidates these signals into concrete allocation proposals.
3. **Risk Management (Iron Dome):** Before any order leaves the system, it must pass through the *Compliance Guardian*. This filter blocks transactions in milliseconds if they violate exposure limits, wash-trading rules, or daily loss limits.
4. **Trading & Execution:** Deterministic, asynchronous execution of compliant orders via direct broker APIs.
5. **Reporting & Auditing:** The system provides end clients and auditors with deep insights into the portfolio structure and the SHAP values of the AI models (Explainable AI) via operational dashboards.

---

### 🛠️ Technology & Architecture
Powering this value chain is an asynchronous, event-driven tech stack optimized for latency and resilience:
* **Orchestration:** Python 3.12, FastAPI, and LangGraph for the deterministic routing of agent workflows (Round Table V2).
* **Deep Learning:** Utilization of PPO (Reinforcement Learning) and LSTM networks for signal generation.
* **Speed & Persistence:** Redis as an L1 checkpointer for millisecond latency; asynchronous database access via SQLAlchemy.
* **Frontend:** BORA Control Center — pre-built React/Vite dashboard, shipped as a Docker image.

---

### 📦 Editions: Community vs. Enterprise

To enable a fast start for quants, developers, and researchers, we have separated our architecture into two distinct versions. This repository contains the **AAAgents Community Edition**.

#### 🌍 Community Edition (Local-First OSS)
The open-source version is designed as a transparent sandbox, giving developers full control over the core engine.
* **Local Infrastructure:** Runs entirely localized via Docker Compose with standard PostgreSQL and Redis.
* **Offline & Safe by Default:** Boots with simulated API keys in an offline mode ("Shadow Boot") to protect capital—ideal for paper trading and algorithm testing.
* **Core Engine Access:** Full source code access to the LangGraph routing, trading logic, and the "Iron Dome" compliance filter.
* **Pre-Built UI:** The BORA Control Center (Dashboard) is shipped as a ready-to-use Docker image, running securely and privacy-compliant via a GDPR loopback (`127.0.0.1`).

#### 🏢 Enterprise Edition (Cloud-Native SaaS)
The commercial version (available at *[aaagents.de](https://aaagents.de)*) is engineered for institutional scaling and B2B multi-tenancy.
* **MLOps:** Direct integration with Google Vertex AI for model training, registry, and fully automated re-training pipelines.
* **Reasoning Lake:** Audit-proof storage of the agents' entire "thought processes" in AlloyDB AI for RAG-based, unlimited reporting.
* **DevSecOps & Zero-Trust:** Secured by Workload Identity Federation (WIF) and the GCP Secret Manager.
* **Multi-Tenancy:** Firebase Authentication for secure account and tenant management across institutional boundaries.
