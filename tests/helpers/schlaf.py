"""`asyncio.sleep` für genau ein Modul ersetzen, nicht für den ganzen Prozess.

`patch("core.engine.trading_loop.asyncio.sleep", ...)` sieht modul-lokal aus, ist es aber
nicht: `trading_loop.asyncio` IST das Modul `asyncio`, der Patch ersetzt also `asyncio.sleep`
überall. Hintergrund-Tasks anderer Tests im selben xdist-Arbeiter schlafen dann über den
Mock, und Zählungen wie `assert_not_awaited()` werden zufällig rot (#4007, 04.10.2026).

`schlaf_nur_im_modul` tauscht stattdessen die Referenz `asyncio` IM Zielmodul gegen einen
Stellvertreter, der nur `sleep` ersetzt und alles andere an das echte `asyncio` weiterreicht.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Iterator
from unittest.mock import AsyncMock, patch


class _AsyncioMitSchlaf:
    def __init__(self, sleep: AsyncMock) -> None:
        self.sleep = sleep

    def __dir__(self):
        return dir(asyncio)

    def __repr__(self) -> str:
        return f"<_AsyncioMitSchlaf sleep={self.sleep!r}>"

    def __getattr__(self, name: str):
        return getattr(asyncio, name)


@contextlib.contextmanager
def schlaf_nur_im_modul(
    modul: str, mock: AsyncMock | None = None
) -> Iterator[AsyncMock]:
    """Ersetzt `asyncio.sleep` nur dort, wo `modul` es über `asyncio.sleep` aufruft."""
    schlaf = mock if mock is not None else AsyncMock()
    with patch(f"{modul}.asyncio", new=_AsyncioMitSchlaf(schlaf)):
        yield schlaf
