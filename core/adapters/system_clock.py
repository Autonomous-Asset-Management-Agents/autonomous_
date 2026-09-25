import time
from datetime import datetime, timezone

from core.ports.clock_port import ClockPort


class SystemClock(ClockPort):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)  # pragma: no cover

    def time(self) -> float:
        return time.time()  # pragma: no cover
