"""#3823 (ARC-E6 G-2a) — die Ausstiegsrunde als Folge benannter Schritte.

Plan: ``docs/3823-*/implementation_plan.md`` §6 Schritte 2 und 3.

* **Größen** werden direkt gegen ``regeln.pruefe_groessen`` geprüft, nicht über
  ``test_groessen_gegen_den_code``: dort steht die Regel auf ``warnen`` und meldet nur
  eine Warnung (wie #3821).
* **Je Schritt ein Test** auf die neue Einheit — was sie liest, was sie zurückgibt und
  was sie dem Dirigenten überlässt. Was die Runde *im Ablauf* bewirkt, hält
  ``test_3823_ausstieg_charakterisierung.py`` fest.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.consensus_retention as retention_mod
import core.engine.trading_loop as tl
import core.report.lstm_panel_store as panel_mod
from core.engine.ausstieg_hebel import (
    AusstiegsHebelMixin,
    _AusstiegsLage,
    _Rotationsrunde,
)
from core.engine.trading_loop import TradingLoopMixin
from tests.architecture import regeln

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

_DATEI = "core/engine/trading_loop.py"
_HEBEL = "core/engine/ausstieg_hebel.py"
_DIRIGENT = "AusstiegsHebelMixin._run_deconcentration_and_rotation_exits"
#: Datei je Schritt: #4245 (H-2d) hat Dirigent, Vorbereitung und Rotationsrahmen zu
#: ihren Hebeln gezogen - die ganze Ausstiegsrunde liegt in ``ausstieg_hebel.py``.
_SCHRITTE = {
    "AusstiegsHebelMixin._ausstieg_vorbereiten": _HEBEL,
    "AusstiegsHebelMixin._rotation_ausstiege": _HEBEL,
    "AusstiegsHebelMixin._rotation_rangverlust": _HEBEL,
    "AusstiegsHebelMixin._rotation_ueberhang": _HEBEL,
    "AusstiegsHebelMixin._trim_ausstiege": _HEBEL,
}
_NOW = datetime(2026, 7, 27, 15, 0, 0, tzinfo=timezone.utc)


def _wurzel() -> Path:
    return Path(tl.__file__).resolve().parents[2]


# ── Größen (Plan §6 Schritt 2) ────────────────────────────────────────────────


def test_die_ausstiegsrunde_liegt_unter_der_funktionsschwelle():
    """Dirigent und Schritte melden nichts — weder zu lang noch einen veralteten Eintrag."""
    meldungen = regeln.pruefe_groessen(_wurzel(), regeln.lade_vertrag())
    eigene = [
        m
        for m in meldungen
        if _DIRIGENT in m
        or any(s in m for s in _SCHRITTE)
        or m.startswith(f"Groessen: {_DATEI} ")
        or _HEBEL in m
    ]
    assert not eigene, "\n".join(eigene)


def test_jeder_schritt_liegt_unter_der_funktionsschwelle():
    schwelle = regeln.lade_vertrag()["groessen"]["funktion_schwelle"]
    gemessen = {
        (b.datei, b.was): int(b.zusatz)
        for b in regeln.funktions_groessen(_wurzel(), "core/engine")
        if b.datei in (_DATEI, _HEBEL)
    }
    for name, datei in [(_DIRIGENT, _HEBEL), *_SCHRITTE.items()]:
        assert (datei, name) in gemessen, (datei, name)
        zeilen = gemessen[(datei, name)]
        assert zeilen <= schwelle, f"{name}: {zeilen} > {schwelle}"


def test_das_hebelmodul_bleibt_unter_der_dateischwelle():
    schwelle = regeln.lade_vertrag()["groessen"]["datei_schwelle"]
    zeilen = len((_wurzel() / _HEBEL).read_text(encoding="utf-8").splitlines())
    assert zeilen <= schwelle


def test_das_hebelmodul_liest_die_handelsschleife_nur_ueber_tl():
    """#4245 (H-2d): einziger Bezug ist der ``_tl``-Import am Dateiende (#4184 §3).

    Patch-Ziele wie ``tl.get_config`` wirken nur, wenn der Schritt sie zur Laufzeit am
    Kernmodul nachschlägt. Der Import steht hinter der Klasse, damit der Kreis Kern ↔
    Hebel in beiden Ladereihenfolgen trägt (``test_trading_loop_patch_ziele``).
    """
    zeilen = (_wurzel() / _HEBEL).read_text(encoding="utf-8").splitlines()
    importe = [
        (i, z)
        for i, z in enumerate(zeilen)
        if z.startswith(("import ", "from ")) and "trading_loop" in z
    ]
    assert [z.split("  #")[0] for _, z in importe] == [
        "from core.engine import trading_loop as _tl"
    ]
    klasse = next(i for i, z in enumerate(zeilen) if z.startswith("class Ausstiegs"))
    assert importe[0][0] > klasse


def test_ausstiegsrunde_liegt_in_ausstieg_hebel():
    """#4245 (H-2d): Dirigent und Schritte gehören dem Hebel-Mixin, nicht dem Kern."""
    for name in (
        "_run_deconcentration_and_rotation_exits",
        "_ausstieg_vorbereiten",
        "_rotation_ausstiege",
    ):
        assert name in AusstiegsHebelMixin.__dict__, name
        assert name not in TradingLoopMixin.__dict__, name


def test_die_handelsschleife_erbt_die_hebel():
    assert issubclass(TradingLoopMixin, AusstiegsHebelMixin)


# ── Gerüst ────────────────────────────────────────────────────────────────────


def _cfg(**kw):
    basis = {
        "ROTATION_EXIT_ENABLED": False,
        "DECONCENTRATION_TRIM_ENABLED": False,
        "ROTATION_PANEL_MAX_AGE_DAYS": 3,
        "SMART_EXIT_MIN_HOLD_DAYS": 5.0,
        "SMART_EXIT_EXIT_RANK_HYSTERESIS": 3.0,
        "MIN_ORDER_VALUE_USD": 1.0,
        "ROTATION_MAX_EXITS_PER_CYCLE": 0,
        "ROTATION_MAX_EXITS_PER_SESSION": 0,
        "TRIM_MAX_EXITS_PER_SESSION": 0,
        "BOOK_CAP_ENFORCEMENT_ENABLED": False,
    }
    basis.update(kw)
    return SimpleNamespace(**basis)


def _score(qty=10.0, price=500.0, days_held=10):
    return SimpleNamespace(
        qty=qty, current_price=price, days_held=days_held, avg_entry=100.0
    )


def _engine(scores, recs=()):
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    pm = SimpleNamespace(
        _position_scores=dict(scores),
        max_positions=10,
        refresh_positions=MagicMock(),
        record_trade=MagicMock(),
        get_rebalance_recommendations=MagicMock(return_value=list(recs)),
    )
    eng.active_strategy = SimpleNamespace(portfolio_manager=pm)
    eng._process_signal_event = AsyncMock()
    return eng, pm


def _lage(eng, cfg, *, rotation_on=False, trim_on=False, veto=None):
    return _AusstiegsLage(
        cfg=cfg,
        now=_NOW,
        rotation_on=rotation_on,
        trim_on=trim_on,
        strat=eng.active_strategy,
        pm=eng.active_strategy.portfolio_manager,
        consensus_retention_veto=veto or (lambda sym, kind, pm: False),
    )


@pytest.fixture
def panel(monkeypatch):
    def _panel(ranks):
        store = SimpleNamespace(
            latest_snapshot_date=lambda: _NOW.date(),
            cross_section_at=lambda now: {"_panel_present": 1.0},
        )
        monkeypatch.setattr(panel_mod, "get_store", lambda: store)
        monkeypatch.setattr(
            panel_mod,
            "cross_section_standing",
            lambda xsec, sym: ranks.get(sym, (None, None, None)),
        )

    return _panel


# ── Vorbereitung ──────────────────────────────────────────────────────────────


def test_vorbereiten_ohne_flag_ist_none(monkeypatch):
    monkeypatch.setattr(tl, "get_config", lambda: _cfg())
    eng, _ = _engine({})
    assert eng._ausstieg_vorbereiten(_NOW) is None


def test_vorbereiten_ohne_portfoliomanager_ist_none(monkeypatch):
    monkeypatch.setattr(tl, "get_config", lambda: _cfg(ROTATION_EXIT_ENABLED=True))
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    eng.active_strategy = SimpleNamespace(portfolio_manager=None)
    assert eng._ausstieg_vorbereiten(_NOW) is None


def test_vorbereiten_haelt_die_lage_fest(monkeypatch):
    cfg = _cfg(DECONCENTRATION_TRIM_ENABLED=True)
    monkeypatch.setattr(tl, "get_config", lambda: cfg)
    eng, pm = _engine({})

    lage = eng._ausstieg_vorbereiten(_NOW)

    assert lage.cfg is cfg
    assert lage.now == _NOW
    assert (lage.rotation_on, lage.trim_on) == (False, True)
    assert lage.strat is eng.active_strategy
    assert lage.pm is pm
    assert lage.consensus_retention_veto is retention_mod.consensus_retention_veto
    with pytest.raises(dataclasses.FrozenInstanceError):
        lage.trim_on = False


def test_vorbereiten_fragt_ohne_now_die_uhr(monkeypatch):
    monkeypatch.setattr(tl, "get_config", lambda: _cfg(ROTATION_EXIT_ENABLED=True))
    uhr = SimpleNamespace(clock_port=SimpleNamespace(now=lambda: _NOW))
    monkeypatch.setattr(tl.CompositionRoot, "get_instance", lambda: uhr)
    eng, _ = _engine({})
    assert eng._ausstieg_vorbereiten(None).now == _NOW


# ── Lever A: Rotation ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_rotation_gibt_nur_ihre_eigenen_ausstiege_zurueck(panel):
    panel({"AAPL": (5.0, 400, 503), "MSFT": (5.0, 300, 503)})
    eng, _ = _engine({"AAPL": _score(), "MSFT": _score()})
    lage = _lage(eng, _cfg(), rotation_on=True)

    assert await eng._rotation_ausstiege(lage, {"MSFT"}) == {"AAPL"}


@pytest.mark.anyio
async def test_rotation_fragt_das_retention_gate_der_lage(panel):
    panel({"AAPL": (5.0, 400, 503)})
    eng, _ = _engine({"AAPL": _score()})
    gefragt = []

    def _veto(sym, kind, pm):
        gefragt.append((sym, kind))
        return True

    lage = _lage(eng, _cfg(), rotation_on=True, veto=_veto)
    assert await eng._rotation_ausstiege(lage, set()) == set()
    assert gefragt == [("AAPL", "rotation")]


@pytest.mark.anyio
async def test_rangverlust_zaehlt_die_rotationen_der_runde(panel):
    panel({"AAPL": (5.0, 400, 503), "MSFT": (5.0, 300, 503)})
    eng, _ = _engine({"AAPL": _score(), "MSFT": _score()})
    lage = _lage(eng, _cfg(), rotation_on=True)
    runde = _Rotationsrunde(
        xsec={},
        cross_section_standing=panel_mod.cross_section_standing,
        top_n=10,
        min_hold_days=5.0,
        hysteresis=3.0,
        rot_cap=1,
        session_cap=0,
    )
    exited: set = set()

    await eng._rotation_rangverlust(lage, runde, set(), exited)

    assert exited == {"AAPL"}
    assert runde.rot_exits == 1


@pytest.mark.anyio
async def test_ueberhang_nutzt_das_restbudget_der_runde(panel):
    panel({"S0": (5.0, 2, 503), "S1": (5.0, 3, 503), "S2": (5.0, 4, 503)})
    eng, pm = _engine({s: _score() for s in ("S0", "S1", "S2")})
    pm.max_positions = 1
    lage = _lage(eng, _cfg(), rotation_on=True)
    runde = _Rotationsrunde(
        xsec={},
        cross_section_standing=panel_mod.cross_section_standing,
        top_n=10,
        min_hold_days=5.0,
        hysteresis=3.0,
        rot_cap=2,
        session_cap=0,
        rot_exits=1,
    )
    exited: set = set()

    await eng._rotation_ueberhang(lage, runde, set(), exited)

    assert exited == {"S2"}  # Budget 2 - 1 = 1, schwächster Rang zuerst


# ── Lever B: Trim ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_trim_ueberspringt_die_uebergebene_acted_menge():
    eng, _ = _engine(
        {"AAPL": _score(), "MSFT": _score()},
        recs=[
            {
                "symbol": "AAPL",
                "action": "REDUCE",
                "market_value": 5000.0,
                "adjustment_value": -2500.0,
            },
            {
                "symbol": "MSFT",
                "action": "REDUCE",
                "market_value": 5000.0,
                "adjustment_value": -2500.0,
            },
        ],
    )
    lage = _lage(eng, _cfg(), trim_on=True)

    assert await eng._trim_ausstiege(lage, {"AAPL"}) == {"MSFT"}


# ── Dirigent ──────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_dirigent_rotation_vor_trim_mit_geteilter_acted_menge(monkeypatch):
    eng, _ = _engine({})
    aufrufe = []
    lage = _lage(eng, _cfg(), rotation_on=True, trim_on=True)
    monkeypatch.setattr(eng, "_ausstieg_vorbereiten", lambda now: lage, raising=False)

    rot = AsyncMock(
        side_effect=lambda la, acted, **kw: aufrufe.append(("rotation", la, set(acted)))
        or {"AAPL"}
    )
    trim = AsyncMock(
        side_effect=lambda la, acted, **kw: aufrufe.append(("trim", la, set(acted)))
        or {"MSFT"}
    )
    monkeypatch.setattr(eng, "_rotation_ausstiege", rot, raising=False)
    monkeypatch.setattr(eng, "_trim_ausstiege", trim, raising=False)

    out = await eng._run_deconcentration_and_rotation_exits({"NVDA"}, now=_NOW)

    assert out == {"AAPL", "MSFT"}
    assert aufrufe == [
        ("rotation", lage, {"NVDA"}),
        ("trim", lage, {"NVDA", "AAPL"}),
    ]


@pytest.mark.anyio
async def test_dirigent_ruft_nur_die_eingeschalteten_hebel(monkeypatch):
    eng, _ = _engine({})
    lage = _lage(eng, _cfg(), trim_on=True)
    monkeypatch.setattr(eng, "_ausstieg_vorbereiten", lambda now: lage, raising=False)
    rot = AsyncMock(return_value={"AAPL"})
    trim = AsyncMock(return_value=set())
    monkeypatch.setattr(eng, "_rotation_ausstiege", rot, raising=False)
    monkeypatch.setattr(eng, "_trim_ausstiege", trim, raising=False)

    assert await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW) == set()
    rot.assert_not_awaited()
    trim.assert_awaited_once()
