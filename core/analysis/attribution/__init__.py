"""
core.analysis.attribution — RTR-0 (#1947) per-agent attribution benchmark.

The GATE for every Round-Table activation (RTR-1..4): before any agent is
activated or re-weighted, this package produces its honest, net-of-cost,
out-of-sample baseline — standalone Spearman IC (+t, FDR-corrected),
hit-rate, leave-one-out consensus Sharpe contribution, vote correlation
matrix and a prove-it-or-kill-it truth table.

Design (plan #1947, Option C):
  * ``replay``          — deterministic offline vote replay over historical
                          daily bars (independent of the live loop and #2113).
  * ``metrics``         — the metric library; consensus replication calls the
                          REAL ``ConsensusEngine.aggregate`` (no re-derivation).
  * ``report``          — versioned gate artifact (Markdown + JSON).
  * ``source_capture``  — DORMANT #2113 online cross-check adapter.

OFFLINE ONLY: nothing in the product imports this package; it is read-only
against agents.py / consensus.py / core.ml.validation.harness (its first
consumer). Pure finance domain — no dev-tools imports (Airlock §5.7).
"""
