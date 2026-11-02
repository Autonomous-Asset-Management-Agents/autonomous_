"""`schlaf_nur_im_modul` ersetzt `asyncio.sleep` nur für EIN Modul, nicht prozessweit.

Anlass: `test_zyklus_schritte_4007.py` war auf main rot in drei von acht Läufen
(04.10.2026, z. B. „Expected mock to not have been awaited. Awaited 590 times").
Die Fixture patchte `core.engine.trading_loop.asyncio.sleep`. `trading_loop.asyncio` IST
das Modul `asyncio`, also ersetzte der Patch `asyncio.sleep` für den ganzen Prozess.
Hintergrund-Tasks anderer Tests im selben xdist-Arbeiter schliefen dann über den Mock,
und die Zählungen (`assert_not_awaited`, `assert_awaited_once_with`) stimmten nicht mehr.

Gegenprobe: `test_alter_weg_ersetzt_sleep_prozessweit` hält den Mechanismus fest.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import threading
from unittest.mock import AsyncMock, patch

import pytest

from tests.helpers.schlaf import schlaf_nur_im_modul

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

MODUL = "core.engine.trading_loop"


def _fremder_schlaf_ist(mock) -> bool:
    """Ruft asyncio.sleep in einem eigenen Thread mit eigenem Event-Loop auf, wie ein
    Hintergrund-Task eines anderen Tests, und meldet, ob dabei der Mock ankam."""
    vorher = mock.await_count
    t = threading.Thread(target=lambda: asyncio.run(asyncio.sleep(0)), daemon=True)
    t.start()
    t.join(5)
    return mock.await_count > vorher


def test_alter_weg_ersetzt_sleep_prozessweit():
    import core.engine.trading_loop  # noqa: F401

    with patch(f"{MODUL}.asyncio.sleep", new=AsyncMock()) as s:
        assert asyncio.sleep is s
        assert _fremder_schlaf_ist(s)


def test_helfer_ersetzt_nur_den_schlaf_des_moduls():
    import core.engine.trading_loop as tl

    with schlaf_nur_im_modul(MODUL) as s:
        assert tl.asyncio.sleep is s
        assert asyncio.sleep is not s
        assert not _fremder_schlaf_ist(s)
    assert tl.asyncio is asyncio


def test_helfer_reicht_den_rest_von_asyncio_durch():
    import core.engine.trading_loop as tl

    with schlaf_nur_im_modul(MODUL):
        assert tl.asyncio.wait_for is asyncio.wait_for
        assert tl.asyncio.TimeoutError is asyncio.TimeoutError


def test_der_helfer_zaehlt_die_schlafaufrufe_des_moduls():
    import core.engine.trading_loop as tl

    with schlaf_nur_im_modul(MODUL) as s:
        asyncio.run(tl.asyncio.sleep(7))
    s.assert_awaited_once_with(7)


def test_kein_test_ersetzt_den_schlaf_prozessweit():
    """Ratsche fuer alle Tests: `patch("<modul>.asyncio.sleep", …)` ersetzt asyncio.sleep fuer
    den ganzen Prozess. Nach #4007 kippte so auch `test_zyklus_schritte_4008.py` auf einem
    Fabrik-PR (#4123, „Awaited 118 times", 04.10.2026). Stattdessen `schlaf_nur_im_modul`.
    """
    tests = pathlib.Path(__file__).resolve().parents[1]
    muster = re.compile(r"[\"'][\w.]+\.asyncio\.sleep[\"']")
    treffer = [
        f"{p.relative_to(tests).as_posix()}:{text.count(chr(10), 0, m.start()) + 1}"
        for p in tests.rglob("*.py")
        # dieser Test und der Helfer nennen das alte Muster nur zur Erklaerung
        if p.name not in (pathlib.Path(__file__).name, "schlaf.py")
        for text in [p.read_text(encoding="utf-8", errors="replace")]
        for m in muster.finditer(text)
    ]
    assert not treffer, "prozessweiter Schlaf-Patch:\n" + "\n".join(treffer)


def test_zyklus_schritte_nutzen_den_helfer():
    """Ratsche für die Datei, die rot war: kein prozessweiter Schlaf-Patch mehr."""
    datei = pathlib.Path(__file__).with_name("test_zyklus_schritte_4007.py")
    text = datei.read_text(encoding="utf-8")
    assert not re.search(r"\"[\w.]+\.asyncio\.sleep\"", text)
    assert "schlaf_nur_im_modul" in text
