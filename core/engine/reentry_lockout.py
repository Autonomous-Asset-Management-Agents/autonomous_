"""#3604 — re-entry lockout in TRADING DAYS after a full exit, persisted to disk.

Measured on the installed app's broker fills (2026-09-23, 444 fills, FIFO round trips):
re-entries 4–24 h after a full exit lost −1,769 $ (n=14) — the next-morning re-buy —
while the 4-hour minute brake (#2554/#2721) only covered the same-afternoon carousel.
Re-entries after more than one day were positive (+781 $, n=20). The lever is time,
not price (re-buys below the exit price lost just as much), so this is a TIME lockout:

- ``lockout_until(exit_time, days)``: the ET trading day ``days`` weekdays after the
  exit; a name is locked while ``trading_day_et(now) < until`` (1 ⇒ free again on the
  next trading day). Weekends are skipped; exchange holidays are not known here — a
  holiday extends the lockout by one day (conservative, documented in the plan).
- ``ReentryLockout``: the store. JSON file in the engine cache dir, written via a unique
  temp file + ``os.replace`` under a lock (a reader never sees a half-written file, and
  Windows cannot fail the swap on an open handle — same lesson as #3477/#3480).
- Fail-open: an unreadable file reads as "nothing locked" (WARNING with stack trace).
  A lockout can only DELAY a buy; it never blocks a sell and can never trap capital.
- No clock access here (architecture rule: the engine clock is injected by the caller),
  so a replay with a simulated ``now`` never sees a lockout from the future.

#3655 — slot hold. A stop-LOSS exit also holds the slot it freed for
``STOP_EXIT_SLOT_HOLD_DAYS`` trading days (entry field ``hold_until``): no NEW name may
take it, the sold name itself may return once its name lockout is over. Measured on the
installed app's fills (26.07.–23.09.2026, 38 full stop exits): the replacement bought
within a trading day lost −1,890 $ over 74 lots; the same name bought back a day or more
later made +1,446 $ over 9 lots; 14 sold names were rated BUY > 3,000 times afterwards
and never bought because the slot was taken. ``held_slots`` / ``reserved_slots`` are the
readers for the buy path (portfolio manager, cash-slot sizer).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Set
from zoneinfo import ZoneInfo

from config import get_config

logger = logging.getLogger(__name__)

_MARKET_TZ = ZoneInfo("America/New_York")
_CACHE_FILENAME = "reentry_lockout.json"
_FILE_LOCK = threading.Lock()


def trading_day_et(moment: datetime) -> date:
    """The ET calendar day a UTC instant belongs to (the NY trading-day boundary)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_MARKET_TZ).date()


def lockout_until(exit_time: datetime, days: int) -> Optional[date]:
    """First ET trading day on which the name may be bought again; ``None`` = never
    locked (``days <= 0``). Counts weekdays only."""
    days = int(days)
    if days <= 0:
        return None
    day = trading_day_et(exit_time)
    remaining = days
    while remaining > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:  # Mon..Fri
            remaining -= 1
    return day


def default_cache_path() -> Optional[str]:
    try:
        from core.report.financials_feed import DEFAULT_CACHE_DIR

        return os.path.join(DEFAULT_CACHE_DIR, _CACHE_FILENAME)
    except Exception:  # noqa: BLE001 — no cache location ⇒ in-memory only (fail-open)
        logger.warning(
            "ReentryLockout: no cache dir — lockouts will not survive a restart.",
            exc_info=True,
        )
        return None


class ReentryLockout:
    """``{SYMBOL: {"until": "YYYY-MM-DD", "exited_at": iso}}`` on disk."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    # -- persistence ---------------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with _FILE_LOCK:
                with open(self.path, encoding="utf-8") as fh:
                    blob = json.load(fh)
            if isinstance(blob, dict):
                self._entries = {
                    str(k).upper(): v
                    for k, v in blob.items()
                    if isinstance(v, dict) and (v.get("until") or v.get("hold_until"))
                }
        except Exception:  # noqa: BLE001 — unreadable ⇒ nothing locked (fail-open)
            logger.warning(
                "ReentryLockout: file unreadable at %s — no lockouts (fail-open).",
                self.path,
                exc_info=True,
            )
            self._entries = {}

    def _save(self) -> None:
        if not self.path:
            return
        tmp = None
        try:
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=".reentry_lockout.", suffix=".tmp", dir=directory
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._entries, fh)
                fh.flush()
                os.fsync(fh.fileno())
            with _FILE_LOCK:
                os.replace(tmp, self.path)
            tmp = None
        except Exception:  # noqa: BLE001 — a cache write must never break the cycle
            logger.warning(
                "ReentryLockout: write failed at %s — lockout kept in memory only.",
                self.path,
                exc_info=True,
            )
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    logger.warning(
                        "ReentryLockout: temp file %s not removed.", tmp, exc_info=True
                    )

    # -- API -----------------------------------------------------------------
    @staticmethod
    def _date(value: Any) -> Optional[date]:
        try:
            return date.fromisoformat(str(value)) if value else None
        except (TypeError, ValueError):
            return None

    def _evict(self, now: datetime) -> None:
        today = trading_day_et(now)
        keep = {}
        for sym, entry in self._entries.items():
            ends = [
                d
                for d in (
                    self._date(entry.get("until")),
                    self._date(entry.get("hold_until")),
                )
                if d
            ]
            if ends and today < max(ends):
                keep[sym] = entry
        self._entries = keep

    def arm(
        self,
        symbols: Iterable[str],
        now: datetime,
        days: int,
        hold_days: int = 0,
        stop_exits: Iterable[str] = (),
    ) -> Set[str]:
        """Lock ``symbols`` from ``now`` for ``days`` trading days (#3604); the subset
        ``stop_exits`` additionally holds its freed slot for ``hold_days`` trading days
        (#3655). Returns the set of NAME-locked symbols."""
        self._load()
        until = lockout_until(now, days)
        hold_until = lockout_until(now, hold_days)
        if until is None and hold_until is None:
            return set()
        stops = {str(s or "").upper().strip() for s in stop_exits}
        changed = False
        for sym in symbols:
            sym = str(sym or "").upper().strip()
            if not sym:
                continue
            # #3670: MERGE with the existing entry instead of rebuilding it. The cycle
            # after a stop exit re-arms the same names via the vanished-position path
            # WITHOUT a stop set; rebuilding dropped ``hold_until`` and the slot hold
            # (#3655) never reached the buy path (measured in the sim, 25.09.2026).
            prev = self._entries.get(sym) or {}
            entry: Dict[str, Any] = {
                "exited_at": prev.get("exited_at") or now.isoformat()
            }
            until_s = self._latest(prev.get("until"), until)
            if until_s:
                entry["until"] = until_s
            hold_s = self._latest(
                prev.get("hold_until"), hold_until if sym in stops else None
            )
            if hold_s:
                entry["hold_until"] = hold_s
            if len(entry) == 1:
                continue  # neither locked nor held (e.g. days 0 and not a stop)
            self._entries[sym] = entry
            changed = True
        self._evict(now)
        if changed:
            self._save()
        return {s for s, e in self._entries.items() if e.get("until")}

    @staticmethod
    def _latest(existing: Optional[str], new: Optional[date]) -> Optional[str]:
        """ISO date of the later of an existing entry date and a newly computed one."""
        candidates = [d for d in (existing, new.isoformat() if new else None) if d]
        return max(candidates) if candidates else None

    def held_slots(
        self, now: datetime, held_symbols: Iterable[str], exclude: Optional[str] = None
    ) -> int:
        """#3655: number of slots still held at ``now`` — stop-exit entries whose
        ``hold_until`` lies ahead, whose name is not back in the book, and (for a
        returner asking about its own slot) not ``exclude``. A simulated past never
        sees a hold from the future."""
        self._load()
        today = trading_day_et(now)
        held = {str(s).upper() for s in held_symbols}
        exclude = str(exclude).upper() if exclude else None
        count = 0
        for sym, entry in self._entries.items():
            hold_until = self._date(entry.get("hold_until"))
            if hold_until is None or sym in held or sym == exclude:
                continue
            try:
                exited = datetime.fromisoformat(str(entry.get("exited_at")))
            except (TypeError, ValueError):
                continue
            if exited.tzinfo is None:
                exited = exited.replace(tzinfo=timezone.utc)
            if exited > now:
                continue
            if today < hold_until:
                count += 1
        return count

    def active(self, now: datetime) -> Set[str]:
        """Symbols locked at ``now``. A simulated past sees nothing from the future:
        an entry whose exit lies after ``now`` does not count."""
        self._load()
        today = trading_day_et(now)
        out = set()
        for sym, entry in self._entries.items():
            until = self._date(entry.get("until"))
            if until is None:
                continue  # slot-hold-only entry (#3655): the name itself is free
            try:
                exited = datetime.fromisoformat(str(entry.get("exited_at")))
            except (TypeError, ValueError):
                continue
            if exited.tzinfo is None:
                exited = exited.replace(tzinfo=timezone.utc)
            if exited > now:
                continue
            if today < until:
                out.add(sym)
        return out

    def entry(self, symbol: str) -> Optional[Dict[str, Any]]:
        self._load()
        return self._entries.get(str(symbol).upper())


# --- #3655: one store per process, readable from the buy path ----------------------
_SHARED: Optional[ReentryLockout] = None
_SHARED_LOCK = threading.Lock()


def shared_store() -> ReentryLockout:
    """The process-wide store (the loop arms it, the buy path reads it)."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = ReentryLockout(default_cache_path())
        return _SHARED


def reserved_slots(
    now: datetime, held_symbols: Iterable[str], exclude: Optional[str] = None
) -> int:
    """#3655: slots the buy path must treat as taken. ``STOP_EXIT_SLOT_HOLD_DAYS`` 0
    reads nothing (byte-identical); a store failure holds nothing (fail-open, WARNING
    with stack trace) — the hold can only delay a buy, never block a sell."""
    try:
        days = int(get_config().STOP_EXIT_SLOT_HOLD_DAYS or 0)
    except (TypeError, ValueError):
        logger.warning(
            "SlotHold: STOP_EXIT_SLOT_HOLD_DAYS unreadable -> 0", exc_info=True
        )
        return 0
    if days <= 0:
        return 0
    try:
        return int(shared_store().held_slots(now, held_symbols, exclude=exclude))
    except Exception:  # noqa: BLE001 - a side-store must never break a buy decision
        logger.warning(
            "SlotHold: store failed - no slot held this call (fail-open).",
            exc_info=True,
        )
        return 0
