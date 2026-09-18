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


def _as_date(value) -> date:
    """Coerce a date / datetime / pandas Timestamp / ``YYYY-MM-DD`` string to ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date") and callable(value.date):  # pandas Timestamp
        return value.date()
    return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()


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
    """tz-aware virtual clock over one or more trading days' [start, end] windows.

    Multi-day (#2693): the clock replays the SAME intraday window on each of a list of trading
    days, rolling over when a day's window ends. The day list is handed in via
    :meth:`set_trading_days` **after** the engine boots, because it is derived from the corpus the
    data client loads — the calendar is therefore data-derived (holidays included) rather than
    guessed from weekdays. Until it is set the clock is single-day, byte-identical to before, which
    is what keeps ``--days 1`` and every existing sim test unchanged.

    Why multi-day at all: on a single day with one bar per symbol, ``equity = cash + Σ qty·price``,
    so selling at the mark and holding at the mark are worth the same — an exit only moves value
    between the summands. The consequence of an exit lands on LATER days, so a one-day window can
    compare mechanics but never exit *policy* (measured in #2692: Δ 0.00 between two genuinely
    different parameter sets).
    """

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
        self._window = (start_t, end_t)
        self.start_time = datetime.combine(d, start_t, tzinfo=_ET)
        self.end_time = datetime.combine(d, end_t, tzinfo=_ET)
        self.current_time = self.start_time
        # Multi-day state. `_days` holds the remaining days AFTER the current one, so an unset
        # calendar and a single-day calendar are the same thing: nothing left to roll to.
        self._first_day = d
        self._days: list = []
        self._days_completed = 1
        self._n_days = 1
        # Optional per-day callback, set by the runner (panel seed + equity point). Kept as a
        # hook so the clock stays free of trading concerns and the trading loop stays free of
        # sim bookkeeping.
        self.on_new_day = None
        logger.info(
            "SimClock: replaying %s window %s→%s cadence %s.",
            d,
            self.start_time.strftime("%H:%M"),
            self.end_time.strftime("%H:%M"),
            self.cadence,
        )

    # ---- multi-day calendar --------------------------------------------------
    def set_trading_days(self, days) -> int:
        """Replay EXACTLY these trading days, in order; returns how many will run.

        The clock **repositions** to ``days[0]``. The caller (``runner._select_replay_days``) owns
        the selection — it knows the corpus calendar and whether an explicit ``--date`` anchors the
        start. Letting the clock keep its own default instead was wrong and the first multi-day run
        proved it: with no ``--date`` the clock defaults to *yesterday*, which the corpus does not
        carry, so the selected days were all "before the start", nothing was queued, and the run
        silently replayed one empty day.

        Must be called BEFORE positions are seeded — the seed prices off ``start_time``.

        Idempotent, and fail-soft: an empty or unusable list leaves the clock single-day (today's
        behaviour) rather than producing a zero-day run, which would be silent and
        indistinguishable from "nothing happened".
        """
        try:
            ordered = sorted({_as_date(x) for x in (days or [])})
        except Exception:  # noqa: BLE001 — a bad calendar must not kill the run
            logger.warning(
                "SimClock: unusable trading-day list %r — staying single-day.", days
            )
            return 1
        if not ordered:
            return self._n_days

        first, rest = ordered[0], ordered[1:]
        if first != self._first_day:
            start_t, end_t = self._window
            self._first_day = first
            self.start_time = datetime.combine(first, start_t, tzinfo=_ET)
            self.end_time = datetime.combine(first, end_t, tzinfo=_ET)
            self.current_time = self.start_time
            logger.info("SimClock: repositioned to the first replay day %s.", first)
        self._days = list(rest)
        self._days_completed = 1
        self._n_days = 1 + len(rest)
        if self._n_days > 1:
            logger.info(
                "SimClock: multi-day replay — %d trading days, %s → %s.",
                self._n_days,
                first,
                rest[-1],
            )
        return self._n_days

    @property
    def n_days(self) -> int:
        """Total days this clock will replay (1 when single-day)."""
        return self._n_days

    @property
    def days_completed(self) -> int:
        return self._days_completed

    def advance(self) -> bool:
        """Step one cadence. Returns True if this step rolled over into a NEW trading day.

        The caller uses the return value for per-day bookkeeping (re-seed the rank panel for the
        new day, record the closing equity point). Without that, day 2+ would vote on day 1's
        cross-section — which would freeze the ranking and silently disable the rotation the
        multi-day run exists to observe.
        """
        self.current_time += self.cadence
        if self.current_time <= self.end_time or not self._days:
            logger.debug("SimClock advanced to %s", self.current_time)
            return False

        next_day = self._days.pop(0)
        start_t, end_t = self._window
        self.start_time = datetime.combine(next_day, start_t, tzinfo=_ET)
        self.end_time = datetime.combine(next_day, end_t, tzinfo=_ET)
        self.current_time = (
            self.start_time
        )  # restart the window, never continue past 16:00
        self._days_completed += 1
        logger.info(
            "SimClock: day %d/%d — rolling over to %s.",
            self._days_completed,
            self._n_days,
            next_day,
        )
        callback = self.on_new_day
        if callback is not None:
            try:
                callback(next_day)
            except Exception:  # noqa: BLE001 — bookkeeping must not kill the run
                logger.warning(
                    "SimClock: on_new_day hook failed for %s — continuing.",
                    next_day,
                    exc_info=True,
                )
        return True

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
