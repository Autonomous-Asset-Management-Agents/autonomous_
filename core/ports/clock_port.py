from abc import ABC, abstractmethod
from datetime import datetime


class ClockPort(ABC):
    """
    Abstract port for providing the current system time.
    Isolates the core domain from direct dependencies on datetime.now() / time.time().
    """

    @abstractmethod
    def now(self) -> datetime:
        """Returns the current aware datetime."""
        pass  # pragma: no cover

    @abstractmethod
    def time(self) -> float:
        """Returns the current time in seconds since the Epoch."""
        pass  # pragma: no cover
