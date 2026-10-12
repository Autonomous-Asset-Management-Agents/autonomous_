# Disclaimer — AAAgents Community Edition

**TL;DR:** This is open-source research software under the Apache 2.0
license. It defaults to paper trading. Running it does **not** require a
BaFin licence as long as you operate it for your own account.

## Legal posture

The software "autonomous_ trading solution" (distributed as AAAgents Community Edition) is open-source software created by Andreas Apeldorn and Georg Apeldorn, and maintained by Autonomous Asset Management Agents UG (haftungsbeschränkt). It is distributed under the Apache License 2.0 and is intended for paper-trading, research, and self-hosted personal use. No live-trading service or third-party asset management service is offered by the creators, the company, or the maintainers of this repository.

The creators and the company do **not** hold a BaFin authorisation under § 32 KWG / § 15 WpIG, and operating this software does not require one as long as it is run for the operator's own account.

MiFID II investment-firm obligations and DORA operational-resilience requirements apply to regulated financial entities. They do not apply to individuals running this software locally on their own behalf.

## Your responsibility

Users who deploy this software in any commercial, fiduciary, or
multi-user context — for example managing assets on behalf of a third
party — are **solely responsible** for obtaining the necessary licences
and complying with all applicable laws (including but not limited to
KWG, WpIG, MiFID II, DORA, GDPR, MaRisk, and BaFin guidance).

The hosted commercial platform at `aaagents.de` is a separate offering
operated under separate terms and is **not** governed by this disclaimer.

## No investment advice

Nothing in this repository, in the project's documentation, or in the
output of this software constitutes investment advice, a recommendation
to buy or sell any security, or a solicitation to enter into any
financial transaction.

## No warranty

The software is provided **"AS IS"** without warranty of any kind,
express or implied, including but not limited to warranties of
merchantability, fitness for a particular purpose, and
non-infringement. See the LICENSE file for the full Apache 2.0 terms.

The maintainers are not liable for any losses incurred from using this
software, whether in paper-trading or in any other context.

## Technical Scope & Accepted Limitations

The OSS Community Edition has the following **explicitly accepted technical limitations**.
These are architectural design decisions for this edition, not bugs:

- **Not suitable for HFT:** This system is designed for low-frequency trading strategies
  (signal intervals of minutes to hours). It is not suitable for High-Frequency Trading,
  latency-sensitive strategies, or sub-second order execution. No performance or
  latency guarantees are made.

- **Floating-point currency arithmetic:** This edition uses Python `float` for
  currency and order-size calculations. At low trading frequencies, the resulting
  rounding errors are negligible. For strategies requiring cent-exact precision,
  use the Enterprise Edition, which enforces `Decimal` arithmetic throughout.

- **Order latency & slippage:** Market orders are submitted asynchronously via the
  Alpaca API. Orders may be subject to broker-side latency, slippage, partial fills,
  or rejection. Pre-market orders will queue until market open and may fill at a
  significantly different price than at signal generation time.

- **No fill confirmation before state update:** The OSS edition records a trade
  immediately after order submission, not after fill confirmation. Partial fills
  or rejected orders may cause temporary discrepancies in the portfolio state display.

- **No Trademark License:** Nothing in this repository or the license agreement grants you any rights to use the company name "Autonomous Asset Management Agents UG (haftungsbeschränkt)", the software name "autonomous_ trading solution", the trade name "AAAgents", or the associated logos and designs. You must remove all original branding, logos, and trademarks if you distribute a modified version or a fork of this software.

- **Governing Law & Jurisdiction:** This disclaimer, the LICENSE agreement, and all legal disputes arising out of or in connection with the use of the "autonomous_ trading solution" (or AAAgents Community Edition) shall be governed exclusively by the laws of Germany. The exclusive place of jurisdiction for any disputes shall be Mainz, Germany.

---

*If you intend to operate this software in a regulated or commercial
context, consult a qualified legal advisor licensed in your
jurisdiction before doing so.*
