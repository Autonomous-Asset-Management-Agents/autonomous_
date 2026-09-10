# Task #1803 (GTM-1) — Universal Spot/US-Equity-only enforcement (CFDs structurally excluded)

**Status:** implemented (TDD)
**Branch:** `r6/25-1803-spot-equity-guard`
**Epic:** GTM-1 · **Type:** additive, fail-closed defense-in-depth guard

---

## 1. Problem / Goal

Add a **fail-closed instrument-type guard** to the Iron Dome compliance gate that rejects any
order whose instrument is NOT a **spot US equity / ETF**. It must reject CFDs, options, crypto,
futures, forex, and any leveraged/derivative instrument.

This control is **UNIVERSAL**: it applies to ALL tiers identically. It is **NOT tier-gated** and
does **NOT** depend on the entitlement layer (#1800). It is pure defense-in-depth: the trading
universe is already US-equity-only (`data_provider._get_alpaca_symbols` fetches only
`AssetClass.US_EQUITY`), but there must be an explicit, tested guard that **fail-closes** if a
non-equity instrument ever reaches the order path.

## 2. Where asset class is known at guard time (investigation result)

- `data_provider.py:578` builds the tradable universe with `asset_class=AssetClass.US_EQUITY`
  (Alpaca `GetAssetsRequest`) — the sole existing US-equity signal, but it is a *fetch-time*
  filter, not a per-order signal.
- The order dict that reaches `ComplianceGuardian.check_order(order)` (built in
  `order_executor.py` at the two `check_order` call sites, ~:587 and ~:1250) contains
  `symbol / side / quantity / price / strategy_id / timestamp / user_id` — **no `asset_class`
  field today.**
- Alpaca's `AssetClass` enum has exactly four members: `us_equity`, `us_option`, `crypto`,
  `crypto_perp`. Forex / CFD / futures are not even Alpaca asset classes — if such a symbol ever
  appears it is a foreign/mis-routed instrument that must be rejected on shape.

**Decision — dual-signal, fail-closed classification (no network call at guard time):**

1. **Explicit field (future-proof):** if the order carries an `asset_class` (or `asset_type`)
   field, it MUST equal the US-equity marker (`us_equity`, case-insensitive). Any other value →
   reject. This lets a future broker-asset lookup positively assert the class without changing
   the guard.
2. **Symbol shape (always applied):** the symbol MUST match the canonical US-equity ticker shape
   already used for defense-in-depth at `api_routes.py:2263` — *starts with a letter, then only
   `A–Z`/`.`/`-`, length 1–10, and NO digits.* This deterministically rejects:
   - crypto / forex pairs — contain `/` (`BTC/USD`, `EUR/USD`),
   - OCC option symbols — long, embed digits (`AAPL240119C00150000`),
   - futures / CFD-style codes — contain digits or disallowed separators.

The guard performs **no network / broker call** — it must be synchronous, deterministic, and
never able to fail-open due to an API error. Confirmation is *positive*: US-equity shape must be
proven, otherwise reject.

**Fail-closed rule:** if the asset class cannot be *positively confirmed* as US equity — missing
/ non-string symbol, symbol shape not equity-like, or an explicit `asset_class` present but not
`us_equity` — the guard **REJECTS** (returns `False`), it never allows on doubt.

## 3. Design + exact insertion point

New private helper `ComplianceGuardian._is_spot_us_equity(order) -> bool` in
`core/compliance.py`. New reject reason code **`non_spot_us_equity`**.

Insert as a **new gate in `check_order`**, immediately after the restricted-list gate (Gate 1)
and before the MiFID-fields gate — so an instrument-type reject is decided as early as possible
and audited exactly once via the existing single-audit `finally` block (BUG-AI-101 / #1237).
The reason string embeds the symbol (human trail); the machine `reason_code` is the bounded
`non_spot_us_equity` code fed to the fail-safe `_bump_compliance` counter (PR A.2).

```
check_order:
  Gate 1  restricted_symbol
  Gate 1b non_spot_us_equity   <-- NEW (universal, fail-closed)
  Gate 2  missing_mifid_fields
  Gate 3  wash_trade
  Gate 4  max_order_value
```

Ordering rationale: it sits after the restricted-list check (a named blocklist hit is a more
specific, higher-signal reject and should win) but before MiFID/wash/risk — a non-equity
instrument should never even be evaluated for those. It reuses the existing audit + counter
plumbing, so it is exactly-once audited and adds one bounded machine reason code.

The Iron Dome / risk-manager / kill-switch semantics are untouched — this is a pure additional
NO-GO branch inside the existing `check_order` try-block.

## 4. Acceptance scenarios (Gherkin)

```gherkin
Feature: Universal spot US-equity-only order guard (#1803)

  Background:
    Given a ComplianceGuardian on the order path

  Scenario: A normal US-equity order passes the instrument guard
    When an order for symbol "AAPL" is checked
    Then the instrument-type guard does not reject it
    And the order is approved (all other checks passing)

  Scenario: An OCC-style option symbol is rejected
    When an order for symbol "AAPL240119C00150000" is checked
    Then the order is rejected
    And the reject reason code is "non_spot_us_equity"

  Scenario: A crypto pair is rejected
    When an order for symbol "BTC/USD" is checked
    Then the order is rejected
    And the reject reason code is "non_spot_us_equity"

  Scenario: A forex / CFD-style instrument is rejected
    When an order for symbol "EUR/USD" is checked
    Then the order is rejected
    And the reject reason code is "non_spot_us_equity"

  Scenario: Fail-closed on unknown / unresolvable asset class
    When an order whose symbol is missing (or asset_class is not us_equity) is checked
    Then the order is rejected
    And the reject reason code is "non_spot_us_equity"

  Scenario: An explicit non-equity asset_class field is rejected even with an equity-shaped symbol
    When an order for symbol "AAPL" with asset_class "crypto" is checked
    Then the order is rejected
    And the reject reason code is "non_spot_us_equity"
```

## 5. Impact analysis

**Files changed**
- `core/compliance.py` — add `_is_spot_us_equity` helper + one new gate in `check_order`
  (new reason code `non_spot_us_equity`). Additive; no existing branch altered.
- `tests/unit/test_compliance.py` — new tests mirroring the existing gate-test style.
- `docs/superpowers/specs/2026-07-07-1803-spot-equity-guard.md` — this spec.

**Existing tests that could break — and why they do NOT**
- All existing `check_order` tests use `symbol="AAPL"` (or `SCAM_TOKEN`, checked by Gate 1
  *before* the new gate). `AAPL` is equity-shaped → passes the new gate. `SCAM_TOKEN` contains
  `_`, which is not in the allowed `[A-Z.-]` set, but it is rejected by Gate 1 (restricted list)
  which runs first, so its reason code stays `restricted_symbol`. Verified: the restricted-list
  gate precedes the new gate, so `test_restricted_symbol` /
  `test_rejected_order_is_audited_exactly_once` are unaffected.
- Wash-trade / risk / MiFID tests all use `AAPL` → unaffected.
- No change to `check_trade`, `_check_risk_limits`, kill-switch, or risk-manager.

**Non-goals**
- No broker asset lookup / network call at guard time.
- No entitlement / tier logic (that is #1800; this guard is universal).
- No change to the data-provider universe.
