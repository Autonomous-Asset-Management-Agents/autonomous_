import time
from datetime import datetime, timezone

from core.ports.clock_port import ClockPort


class SystemClock(ClockPort):
    def now(self) -> datetime:
        # #3668: die Engine-Uhr — unter SIM_MODE die Sim-Uhr, sonst datetime.now (identisch).
        from core.sim.clock import engine_now

        return engine_now(timezone.utc)

    def time(self) -> float:
        from core.sim.clock import engine_epoch

        return engine_epoch()
