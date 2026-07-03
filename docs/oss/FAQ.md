# AAAgents — Frequently Asked Questions

> This FAQ is **technical product help, not financial or investment advice.** Trading involves risk of loss — see the [Risk Disclosure](https://aaagents.de/legal/risk-disclosure). For deep operational/setup issues see the [Troubleshooting Playbook](https://github.com/Autonomous-Asset-Management-Agents/Dev-Enviroment/blob/main/docs/oss/TROUBLESHOOTING.md).

<!-- Canonical FAQ source (GTM-1 T1): the in-app /support page renders this file. Edit here, not in two places. -->

## Getting started with Alpaca (your broker)

**What is Alpaca and why do I need it?**
AAAgents does not hold your money or place trades on its own servers — it connects to a **broker**. Alpaca is a commission-free, API-first broker. You bring your own Alpaca account; AAAgents sends orders to it on your behalf.

**Do I need to pay or fund anything to try it?**
No. Alpaca offers a free **Paper-Trading** account (virtual money, no funding, no identity check) — that is the default in AAAgents. You only need a funded **Live** account if and when you deliberately switch to real-money trading.

**How do I create an Alpaca account?**
Sign up at alpaca.markets. A **Paper** account is available instantly. A **Live** account requires identity verification (KYC) and funding, and availability depends on your country — check Alpaca's site for current eligibility.

**Where do I get my API keys?**
In the Alpaca dashboard you generate an **API Key ID** and a **Secret Key**. Paper and Live use **separate keys and endpoints** (`paper-api.alpaca.markets` vs `api.alpaca.markets`) — use the paper keys for paper trading.

**Where do I enter the keys, and are they safe?**
In the app's setup / Broker screen. Keys are stored in your **OS keychain** on your own machine. They are **never sent to AAAgents** — we run no central server and never see your credentials.

## Installing and running the app

**Is it free? Is it open source?**
Yes — the Community Edition is **free and open source**. You run it yourself; you can inspect, modify, and even **swap the AI models**.

**How do I install it?**
A **one-click installer** is coming with the official release. Today you can run it via the developer setup (clone + Docker, or the native desktop mode) — see the [README](https://github.com/Autonomous-Asset-Management-Agents/Dev-Enviroment/blob/main/docs/oss/README.md).

**What are the system requirements?**
A normal desktop/laptop. CPU is fine to start; the bundled models run **on your own machine** (on-prem). No cloud account required.

**Does it send my data anywhere?**
No. It is **self-hosted and decentralized** — your trades, logs, and API keys stay on your device. AAAgents (the company) collects none of it; under GDPR **you are the data controller** of your own instance.

## Paper vs. live trading (safety)

**Will it trade real money by default?**
**No.** The app runs **paper trading by default** (your Alpaca paper account, virtual money). Nothing touches real capital until you deliberately enable live.

**How do I go live with real money?**
Live trading requires a **deliberate, tamper-evident confirmation** inside the app (recorded on a tamper-proof audit chain). It cannot happen by accident or by a stray setting, and you explicitly accept that real losses are possible.

**Can I lose money?**
In **paper** mode: no (virtual). In **live** mode: **yes — you can lose real capital.** An autonomous system can be wrong; past performance is not indicative of future results. Only trade money you can afford to lose.

## Configuring the system

**Which AI models does it use? Can I change them?**
It ships a consensus engine of about a dozen classifiers (LSTM, GRU, PPO, XGBoost, and more). You can **bring your own models** — drop in your own classifiers, signal generators, or a private LLM as first-class voters. No vendor lock-in.

**Can I choose which stocks it trades?**
Yes — you define your **universe** (a few high-conviction names, a sector, or a broad index) and your **style** (risk caps, holding horizon, rebalancing cadence) in plain configuration.

**Do I keep control?**
Yes. Every decision is **inspectable, overridable, and reversible** — no black box. The default posture keeps a human in the loop for capital decisions.

**How realistic is the backtest / simulation?**
The Simulation page backtests the strategy over a historical period vs. the S&P 500. Be honest about its limits: **news & sentiment are simulated** (not a live news feed), the S&P 500 universe is **point-in-time where possible** but from a limited historical-membership set (survivorship is only partly mitigated — the app flags this), and **no paid data source** is used. Treat results as indicative, not a guarantee.

## Safety, risk and the kill-switch

**What is the kill-switch?**
A one-action emergency stop **outside** the trading pipeline: it halts all algorithms (and can cancel/flatten), and stays stopped until you manually reset it — no timed auto-resume.

**What risk limits are built in?**
Hard-coded, change-controlled limits including a daily-drawdown ceiling, concentration caps, and a synchronous pre-trade compliance gate that rejects non-compliant orders before any order is routed.

## Troubleshooting

**The engine starts but places no trades / a setup error / a crash on boot.**
See the deep [Troubleshooting Playbook](https://github.com/Autonomous-Asset-Management-Agents/Dev-Enviroment/blob/main/docs/oss/TROUBLESHOOTING.md) — it covers container/boot failures, Alembic, LLM/ML issues, SQLite (native desktop) errors, and OS-keychain problems.

## Help, support and security

**How do I get help or report a bug?**
Self-serve the docs/FAQ first; for questions use **GitHub Discussions**, for bugs open a **GitHub Issue**. The free edition is **best-effort community support** (no SLA). You can attach an in-app **diagnostics export** to a bug report.

**Security: how do I report a vulnerability, and what should I never share?**
Use the responsible-disclosure contact (`security@aaagents.de`). **Never share your Alpaca API keys** — no one from the team or community will ask for them. Diagnostic logs may contain secrets, so **review/redact before posting**.

**Is this investment advice?**
**No.** AAAgents is a **technology tool**, not a financial-services provider. It does not give investment advice or recommendations — you are solely responsible for your configuration and trades.
