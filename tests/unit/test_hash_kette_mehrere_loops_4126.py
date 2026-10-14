"""#4126 Teil 1a (Plan §10d): Die Hash-Kette bleibt geordnet, auch aus mehreren Loops.

Bisher sperrte ``LocalJSONAuditLogger`` nur je Event-Loop (``_per_loop_locks``). Zwischen dem
Setzen von ``prev_hash`` und dem Schreiben lag ein ``await``. Schrieben zwei Loops zugleich,
konnte die Zeile mit ``prev_hash = A`` vor Zeile A in der Datei landen. Die Kette war dann
gebrochen. Die Tor-Senke schreibt aus Worker-Threads, also aus mehreren Loops.

Der Test erzwingt die Ueberlappung: Das Schreiben jeder Zeile wird kurz verzoegert.
"""

import asyncio
import builtins
import hashlib
import json
import threading
import time

import pytest

from core.round_table import senate_log
from core.round_table.senate_log import LocalJSONAuditLogger

pytestmark = pytest.mark.vc0

_JE_SCHREIBER = 200


@pytest.fixture
def kettenverzeichnis(tmp_path, monkeypatch):
    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def langsames_schreiben(monkeypatch):
    echtes_open = builtins.open

    class _LangsameDatei:
        def __init__(self, datei):
            self._datei = datei

        def __enter__(self):
            self._datei.__enter__()
            return self

        def __exit__(self, *a):
            return self._datei.__exit__(*a)

        def write(self, text):
            time.sleep(0.001)
            return self._datei.write(text)

    def _open(pfad, modus="r", *args, **kwargs):
        datei = echtes_open(pfad, modus, *args, **kwargs)
        if "a" in modus and str(pfad).endswith(".jsonl"):
            return _LangsameDatei(datei)
        return datei

    monkeypatch.setattr(senate_log, "open", _open, raising=False)


def _schreiber(logger, name, fehler):
    async def _lauf():
        for i in range(_JE_SCHREIBER):
            await logger._write_to_hash_chain(
                {"event_type": "test", "von": name, "i": i}
            )

    try:
        asyncio.run(_lauf())
    except Exception as exc:  # noqa: BLE001 — im Test sichtbar machen
        fehler.append(exc)


def _zeilen(verzeichnis):
    dateien = sorted(verzeichnis.glob("audit_log_*.jsonl"))
    zeilen = []
    for datei in dateien:
        zeilen += [
            json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z
        ]
    return zeilen


def test_kette_bleibt_geordnet_bei_schreibern_aus_mehreren_loops(
    kettenverzeichnis, langsames_schreiben
):
    erster = LocalJSONAuditLogger()
    zweiter = (
        LocalJSONAuditLogger()
    )  # weitere Instanz wie eula_seal / trading_control_seal
    fehler = []
    faeden = [
        threading.Thread(target=_schreiber, args=(erster, "a", fehler)),
        threading.Thread(target=_schreiber, args=(erster, "b", fehler)),
        threading.Thread(target=_schreiber, args=(zweiter, "c", fehler)),
    ]
    for f in faeden:
        f.start()
    for f in faeden:
        f.join(timeout=120)

    assert not fehler
    zeilen = _zeilen(kettenverzeichnis)
    assert len(zeilen) == 3 * _JE_SCHREIBER

    for vorher, jetzt in zip(zeilen, zeilen[1:]):
        assert jetzt["prev_hash"] == vorher["hash"], "Kette in der Datei gebrochen"
    for zeile in zeilen:
        ohne_hash = {k: v for k, v in zeile.items() if k != "hash"}
        neu = hashlib.sha256(json.dumps(ohne_hash, sort_keys=True).encode()).hexdigest()
        assert neu == zeile["hash"]


def test_neue_instanz_setzt_die_kette_der_vorigen_fort(kettenverzeichnis):
    erster = LocalJSONAuditLogger()
    asyncio.run(erster._write_to_hash_chain({"event_type": "test", "i": 1}))

    zweiter = LocalJSONAuditLogger()
    asyncio.run(zweiter._write_to_hash_chain({"event_type": "test", "i": 2}))
    asyncio.run(erster._write_to_hash_chain({"event_type": "test", "i": 3}))

    zeilen = _zeilen(kettenverzeichnis)
    assert [z["i"] for z in zeilen] == [1, 2, 3]
    for vorher, jetzt in zip(zeilen, zeilen[1:]):
        assert jetzt["prev_hash"] == vorher["hash"]
