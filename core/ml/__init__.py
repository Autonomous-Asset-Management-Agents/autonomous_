"""Per-symbol / per-actor ML package (bundle → main fusion, stage S1).

Modules land here INCREMENTALLY and DORMANT: nothing in this package is wired
into the trading decision path until its walk-forward validation gate passes
(validate-before-activate). Keep this ``__init__`` import-free so a partial
landing never pulls in not-yet-ported modules.
"""
