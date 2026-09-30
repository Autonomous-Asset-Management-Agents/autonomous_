# ai_trading_bot.core.sim module

import os

__all__ = ["is_sim_mode"]


def is_sim_mode() -> bool:
    """Is this process an offline Sim-Day run? (#2632)

    ONE definition for every component that has to behave differently offline, so a new
    consumer cannot drift from the others (the third copy of this check was the point at
    which duplicating it stopped being acceptable).

    Two channels, either is sufficient:
      * ``AAA_SIM_MODE=true`` in the environment — the sim CLI exports it **before** config
        is imported (``core/sim/__main__.py``), so env is the earliest reliable signal;
      * ``config.SIM_MODE`` — the resolved value, for callers that run after config import.

    Read at CALL time (never frozen into a module constant) and **fail-safe to LIVE**: if
    config cannot be read we return False, because every caller uses this to *disable*
    something (a watchdog, a network fetch). An unreadable config must never silently
    switch a live component off.
    """
    if str(os.environ.get("AAA_SIM_MODE", "")).strip().lower() == "true":
        return True
    try:
        import config

        return bool(getattr(config.get_config(), "SIM_MODE", False))
    except Exception:  # noqa: BLE001 — fail-safe direction is LIVE
        return False
