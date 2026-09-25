# core/engine/loop_counters.py
# ADR-OBS-01 / PR B — loop liveness counters (PURE OBSERVATION)
#
# The trading/monitor loops call ``_bump_loop_counter`` on the hot path to bump a
# monotone counter on the engine (_cycles_completed / _scans_completed /
# _high_latency_cycles). It swallows EVERY error so a counter failure can NEVER
# raise into — or change the control flow of — the loop (§ SAFETY INVARIANT).
#
# Lives in its own leaf module (not base.py) so the loop mixins can import it
# without a circular import back into BotEngine.


def _bump_loop_counter(engine, attr: str, delta: int = 1) -> None:
    """Fail-safe monotone loop-counter bump — swallows every error."""
    try:
        setattr(engine, attr, getattr(engine, attr, 0) + delta)
    except Exception:  # noqa: BLE001 — a broken counter must never break a loop
        pass
