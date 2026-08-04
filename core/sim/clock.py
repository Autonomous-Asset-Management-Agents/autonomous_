"""Virtual market clock for the Alpaca Sim-Day (#2548 M7).

Replays a REAL chosen day + window (``SIM_DATE`` + ``SIM_WINDOW`` "09:30-16:00 ET") at the
configured cadence (``SIM_CADENCE`` "1m"/"15m"/"1h"), **tz-aware** (market tz America/New_York)
so comparisons against alpaca's tz-aware bar timestamps never TypeError. Shared as a process
singleton so the broker, data client and fill engine all read the same timeline.
"""

import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Optional

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - zoneinfo always present on 3.12+
    from datetime import timezone

    _ET = timezone.utc

try:
    from config import get_config
except ImportError:  # pragma: no cover
    from ai_trading_bot.config import get_config

logger = logging.getLogger(__name__)


def _parse_date(sim_date: str) -> date:
    """Parse ``YYYY-MM-DD``; empty → the most recent past weekday (Mon-Fri)."""
    if sim_date:
        return datetime.strptime(sim_date.strip(), "%Y-%m-%d").date()
    d = datetime.now(_ET).date() - timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d -= timedelta(days=1)
    return d


def _parse_window(sim_window: str):
    """Parse ``"09:30-16:00 ET"`` → (start_time, end_time). Fail-soft to 09:30-16:00."""
    try:
        core = sim_window.replace("ET", "").strip()
        start_s, end_s = [p.strip() for p in core.split("-")]
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
        return time(sh, sm), time(eh, em)
    except Exception:
        logger.warning(
            "SimClock: unparseable SIM_WINDOW %r — defaulting 09:30-16:00.", sim_window
        )
        return time(9, 30), time(16, 0)


def _parse_cadence(sim_cadence: str) -> timedelta:
    """Parse ``"1m"``/``"30s"``/``"1h"`` → timedelta. Fail-soft to 1 minute."""
    m = re.fullmatch(r"\s*(\d+)\s*([smh])\s*", str(sim_cadence).lower())
    if not m:
        return timedelta(minutes=1)
    n, unit = int(m.group(1)), m.group(2)
    return {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
    }[unit]


class SimClock:
    """tz-aware virtual clock over a single day's [start, end] window."""

    def __init__(self, sim_date=None, sim_window=None, sim_cadence=None):
        cfg = get_config()
        d = _parse_date(
            sim_date if sim_date is not None else getattr(cfg, "SIM_DATE", "")
        )
        start_t, end_t = _parse_window(
            sim_window
            if sim_window is not None
            else getattr(cfg, "SIM_WINDOW", "09:30-16:00 ET")
        )
        self.cadence = _parse_cadence(
            sim_cadence
            if sim_cadence is not None
            else getattr(cfg, "SIM_CADENCE", "1m")
        )
        self.start_time = datetime.combine(d, start_t, tzinfo=_ET)
        self.end_time = datetime.combine(d, end_t, tzinfo=_ET)
        self.current_time = self.start_time
        logger.info(
            "SimClock: replaying %s window %s→%s cadence %s.",
            d,
            self.start_time.strftime("%H:%M"),
            self.end_time.strftime("%H:%M"),
            self.cadence,
        )

    def advance(self) -> None:
        self.current_time += self.cadence
        logger.debug("SimClock advanced to %s", self.current_time)

    @property
    def is_open(self) -> bool:
        return self.start_time <= self.current_time <= self.end_time


_shared_clock: Optional[SimClock] = None


def get_sim_clock() -> SimClock:
    """Return the process-singleton SimClock (built from config on first use)."""
    global _shared_clock
    if _shared_clock is None:
        _shared_clock = SimClock()
    return _shared_clock


def reset_sim_clock() -> None:
    """Drop the singleton so the next get_sim_clock() rebuilds from current config (runner/tests)."""
    global _shared_clock
    _shared_clock = None


def engine_now(tz=None):
    """The engine's "current time" — wall-clock ``datetime.now(tz)`` normally, but the virtual
    sim-clock time under SIM_MODE, so the strategy analyses AS OF the sim day (not today).

    #2548 M7-extension: the hot analysis path (trading_loop calendar-day check, market scanner
    date, get_data ``current_date``) reads its "now" through this helper. **Byte-identical to
    ``datetime.now(tz)`` when SIM_MODE is off** — the sim branch is only taken with AAA_SIM_MODE=true.
    """
    if getattr(get_config(), "SIM_MODE", False):
        t = get_sim_clock().current_time
        if tz is None:
            return t
        return t.replace(tzinfo=tz) if t.tzinfo is None else t.astimezone(tz)
    return datetime.now(tz)
