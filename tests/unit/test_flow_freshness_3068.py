# flake8: noqa
# rc4 live finding R4 (28.08.): the engine fetches cash-flows ONCE per curve rebuild and
# never again — a CSD that posts to Alpaca's activity ledger AFTER the rebuild (or a
# transient fetch miss) stays invisible until the next unrelated rebuild. On the real
# account the second 5,833 deposit (CSD 25.08.) was missing from the served flows:
# Net deposits +5.79k against an 11.9k book, and the settle jump read as +94% "return"
# (the +95.99% since-Aug-15 tile). Fix: every cached poll refetches the flows and
# compares a signature against the cache; a change evicts -> full rebuild (<=60s).

from __future__ import annotations

from core.engine.perf_metrics import flows_signature


def test_signature_is_stable_for_identical_flows():
    a = {"2026-08-24": 5793.0, "2026-08-25": 5753.0}
    b = {"2026-08-25": 5753.0, "2026-08-24": 5793.0}  # order must not matter
    assert flows_signature(a) == flows_signature(b)


def test_signature_changes_when_a_new_deposit_posts():
    before = {"2026-08-24": 5793.0}
    after = {"2026-08-24": 5793.0, "2026-08-25": 5753.0}  # CSD2 appears in the ledger
    assert flows_signature(before) != flows_signature(after)


def test_signature_changes_on_amount_correction():
    a = {"2026-08-24": 5793.0}
    b = {"2026-08-24": 5833.0}  # broker corrects/fee reclassifies
    assert flows_signature(a) != flows_signature(b)


def test_empty_and_none_are_equal_and_harmless():
    assert flows_signature({}) == flows_signature(None)
