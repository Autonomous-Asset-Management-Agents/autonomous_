# tests/unit/test_hwm_persist_flag_sweepable.py
# BORA-Paritaet: POSITION_EXIT_HWM_PERSIST_ENABLED bedeutet in beiden Editionen dasselbe.
#
# Gemessener Befund (25.08.), und er ist nicht der, den ich erwartet habe:
#
#   POSITION_EXIT_HWM_PERSIST_ENABLED=1
#     config.py      -> True     (Enterprise)
#     config.oss.py  -> False    (OSS / Desktop)
#
# Dieselbe Umgebungsvariable, derselbe Wert, entgegengesetzte Bedeutung. Ein Operator,
# der auf dem Desktop `=1` setzt, bekommt stillschweigend AUS.
#
# Ursache: `config.py` ist eine pydantic-`BaseSettings` (`RuntimeConfigState`,
# config.py:34). Pydantic liest die Umgebungsvariable selbst und parst sie als bool —
# "1"/"0"/"yes"/"on" inklusive. Der handgeschriebene Ausdruck
# `os.getenv(NAME, "False").lower() == "true"` liefert dort nur den Vorgabewert fuer den
# Fall, dass die Variable *fehlt*. `config.oss.py` hat keine pydantic-Schicht
# (`grep -c BaseSettings` = 0); dort IST der Ausdruck das Verhalten.
#
# Ausdruecklich NICHT der Anlass: "der Sweep koenne nicht zwei Zustaende messen". Das
# war meine Annahme und sie ist widerlegt — der Momentum-Sweep sweep-6f22a1bb5eea hat
# mit `[0, 1]` nachweislich zwei verschiedene Zustaende gemessen (52.576 gegen 45.040
# Orders), weil der Sweep-Pfad ueber die pydantic-Edition laeuft.
#
# Verhalten unveraendert: Default bleibt AUS, "true"/"True" lesen sich wie bisher.

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
_FLAG = "POSITION_EXIT_HWM_PERSIST_ENABLED"


def _load_config_fresh(name: str, datei: str):
    """Config-Modul frisch laden, damit der Wert gegen die aktuelle Umgebung entsteht."""
    spec = importlib.util.spec_from_file_location(name, _AI_BOT / datei)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(monkeypatch, datei: str, roh: str | None):
    if roh is None:
        monkeypatch.delenv(_FLAG, raising=False)
        marke = "unset"
    else:
        monkeypatch.setenv(_FLAG, roh)
        marke = roh.strip() or "leer"
    modul = _load_config_fresh(f"cfg_hwm_{datei.replace('.', '_')}_{marke}", datei)
    return getattr(modul.get_config(), _FLAG)


# ---------------------------------------------------------------------------
# Der eigentliche Prüfgegenstand: beide Editionen lesen denselben Wert gleich
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "roh, erwartet",
    [
        ("1", True),  # scheitert vor dem Fix in config.oss.py
        ("0", False),
        (" 1 ", True),  # scheitert vor dem Fix in config.oss.py
        ("yes", True),  # scheitert vor dem Fix in config.oss.py
        ("true", True),  # Regression: unverändert
        ("True", True),  # Regression: unverändert
        ("false", False),
        ("False", False),
    ],
)
def test_both_editions_agree(monkeypatch, roh, erwartet):
    ent = _read(monkeypatch, "config.py", roh)
    oss = _read(monkeypatch, "config.oss.py", roh)
    assert ent is erwartet, f"config.py liest {roh!r} als {ent!r}"
    assert oss is erwartet, f"config.oss.py liest {roh!r} als {oss!r}"
    assert ent is oss, f"Editionen widersprechen sich bei {roh!r}: {ent!r} vs {oss!r}"


def test_default_stays_off_in_both_editions(monkeypatch):
    """Die eigentliche Verhaltensgarantie dieses PRs.

    Die Änderung erweitert nur die Menge der verstandenen Eingaben — sie schaltet
    nichts ein. Der ausgelieferte Zustand bleibt AUS.
    """
    assert _read(monkeypatch, "config.py", None) is False
    assert _read(monkeypatch, "config.oss.py", None) is False


# ---------------------------------------------------------------------------
# Unsinnige Eingaben — hier dürfen die Editionen bewusst auseinandergehen
# ---------------------------------------------------------------------------


def test_enterprise_fails_closed_on_garbage(monkeypatch):
    """pydantic wirft bei nicht interpretierbaren Werten — laut und beim Start.

    Das ist die schärfere Haltung und bleibt unverändert: ein Tippfehler in der
    Umgebung fährt die Engine nicht mit einer stillen Fehlannahme hoch.
    """
    monkeypatch.setenv(_FLAG, "vielleicht")
    with pytest.raises(Exception) as exc:
        _load_config_fresh("cfg_hwm_ent_garbage", "config.py")
    assert "bool" in str(exc.value).lower()


def test_oss_falls_back_to_off_on_garbage(monkeypatch):
    """Die OSS-Edition hat keine Validierungsschicht und fällt auf AUS zurück.

    Bewusst nicht angeglichen: ein Absturz beim Desktop-Start wäre für einen
    Tippfehler in einer opt-in-Diagnoseeinstellung die härtere Folge. Der
    Unterschied ist damit dokumentiert statt unbemerkt.
    """
    assert _read(monkeypatch, "config.oss.py", "vielleicht") is False
