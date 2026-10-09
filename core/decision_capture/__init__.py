"""#2113 Decision-Outcome-Capture (Epic #1913 MLR) — owner-only, local, no egress.

One durable ``decision_outcomes`` row per Round-Table decision, joinable per
``decision_id``: Decision-Kern + Eval-Inputs + Execution-Outcome + Lineage
(labels follow in Increment 2). Everything here is PURE OBSERVATION behind
``DECISION_CAPTURE_ENABLED`` (default OFF) — fail-safe, never on the trading
path, edition-neutral (BORA: no GCP imports, config via ``config.get_config()``).
"""
