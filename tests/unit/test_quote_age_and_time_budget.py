"""#3381 (Epic #3366) — die Entscheidung kennt das Alter ihrer Grundlage.

Zwei Befunde mit derselben Wurzel:

1. ``_extract_ohlc_from_snapshot`` (trading_loop.py:331) liest ``latest_trade.price``
   und wirft ``latest_trade.timestamp`` weg. Danach ist nur noch ein ``float``
   unterwegs — ob er von vor zehn Sekunden oder von gestern stammt, ist nicht mehr
   feststellbar. Derselbe Preis speist Round Table, Sizing und Compliance.
2. Die drei Zeitgrenzen sind invertiert: Agent 60,0 s (round_table/runner.py:164) >
   Symbol 45,0 s (trading_loop.py:36), eine Zyklusgrenze gibt es nicht. Die innere
   Schicht gibt also nach der aeusseren auf und hinterlaesst einen Teilzyklus.

Die Tests laufen ohne externe Datenquelle: ``core/sim/data_client.py:89`` liefert mit
``_SimTrade``/``_SimSnapshot`` genau die Form, die der Live-Pfad erwartet.
"""

from datetime import datetime, timedelta, timezone

import pytest

from core.sim.data_client import _SimSnapshot


class _Bar:
    """Minimale daily_bar — die abgeschlossene Vortages-Referenz."""

    def __init__(self, close, ts=None):
        self.open = self.high = self.low = self.close = float(close)
        self.volume = 1_000.0
        self.timestamp = ts


def _snapshot(price, ts, bar_close=100.0):
    return _SimSnapshot(price, ts, _Bar(bar_close))


# ---------------------------------------------------------------------------
# 1 — das Alter verlaesst die Extraktionsstelle
# ---------------------------------------------------------------------------


def test_extraction_gibt_den_zeitstempel_der_preisquelle_zurueck():
    """Heute rot: die Funktion gibt bei :378 ein Zwei-Tupel zurueck."""
    from core.engine.trading_loop import _extract_ohlc_from_snapshot

    now = datetime(2026, 9, 16, 15, 30, tzinfo=timezone.utc)
    ohlc, price, quote_ts = _extract_ohlc_from_snapshot(_snapshot(185.5, now))

    assert price == pytest.approx(185.5)
    assert ohlc["close"] == pytest.approx(185.5)
    assert quote_ts is not None, (
        "Der Zeitstempel liegt im Snapshot vor (core/sim/data_client.py:96 setzt ihn, "
        "Alpaca liefert ihn am selben Objekt) und wird verworfen."
    )
    assert quote_ts.replace(tzinfo=timezone.utc) == now


def test_extraction_ohne_trade_meldet_das_alter_der_bar():
    """Der Fallback-Zweig (:380-390) gibt den GESTRIGEN close zurueck — mit dessen Alter."""
    from core.engine.trading_loop import _extract_ohlc_from_snapshot

    gestern = datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc)
    snap = _snapshot(1.0, gestern)
    snap.latest_trade = None
    snap.daily_bar = _Bar(152.0, ts=gestern)

    ohlc, price, quote_ts = _extract_ohlc_from_snapshot(snap)

    assert price == pytest.approx(152.0)
    assert quote_ts == gestern


def test_extraction_ohne_jede_quelle_hat_kein_alter():
    from core.engine.trading_loop import _extract_ohlc_from_snapshot

    snap = _snapshot(1.0, datetime(2026, 9, 16, tzinfo=timezone.utc))
    snap.latest_trade = None
    snap.daily_bar = None

    ohlc, price, quote_ts = _extract_ohlc_from_snapshot(snap)

    assert price == 0.0
    assert quote_ts is None


# ---------------------------------------------------------------------------
# 2 — Enthaltung statt Entscheidung auf altem Preis
# ---------------------------------------------------------------------------


def test_zu_altes_kursbild_gilt_als_stale_quote():
    from core.engine.time_budget import is_stale_quote

    now = datetime(2026, 9, 16, 15, 30, tzinfo=timezone.utc)
    alt = now - timedelta(hours=17)

    assert is_stale_quote(alt, now, 900.0) is True


def test_frisches_kursbild_ist_nicht_stale():
    from core.engine.time_budget import is_stale_quote

    now = datetime(2026, 9, 16, 15, 30, tzinfo=timezone.utc)
    assert is_stale_quote(now - timedelta(seconds=30), now, 900.0) is False


def test_fehlendes_alter_gilt_als_stale():
    """Kein Zeitstempel heisst 'Alter unbekannt' — die konservative Richtung ist Enthaltung."""
    from core.engine.time_budget import is_stale_quote

    now = datetime(2026, 9, 16, 15, 30, tzinfo=timezone.utc)
    assert is_stale_quote(None, now, 900.0) is True


def test_naive_zeitstempel_werden_nicht_zum_absturz():
    """Alpaca liefert tz-aware, der Sim-Pfad kann naiv liefern — beides muss vergleichbar sein."""
    from core.engine.time_budget import is_stale_quote

    now = datetime(2026, 9, 16, 15, 30, tzinfo=timezone.utc)
    naiv = datetime(2026, 9, 16, 15, 29, 30)

    assert is_stale_quote(naiv, now, 900.0) is False


def test_grenze_null_schaltet_die_pruefung_ab():
    """Rollback ohne Revert (Plan §6): eine nicht-positive Grenze ist das Aus."""
    from core.engine.time_budget import is_stale_quote

    now = datetime(2026, 9, 16, 15, 30, tzinfo=timezone.utc)
    assert is_stale_quote(now - timedelta(days=3), now, 0.0) is False
    assert is_stale_quote(None, now, 0.0) is False


# ---------------------------------------------------------------------------
# 3 — Staffelung Agent < Symbol < Zyklus
# ---------------------------------------------------------------------------


def test_geladene_zeitgrenzen_sind_gestaffelt():
    """Heute rot: 60,0 s Agent gegen 45,0 s Symbol, keine Zyklusgrenze."""
    from config import RuntimeConfigState

    cfg = RuntimeConfigState()
    assert (
        cfg.AGENT_VOTE_TIMEOUT_SECONDS
        < cfg.SYMBOL_EVAL_TIMEOUT_SECONDS
        < cfg.CYCLE_TIMEOUT_SECONDS
    )


def test_verdrehte_grenzen_werden_geklemmt_und_gemeldet(caplog):
    """Nicht still korrigieren: jede Klemmung schreibt eine WARNING."""
    from core.engine.time_budget import ordered_time_budget

    with caplog.at_level("WARNING"):
        agent_s, symbol_s, cycle_s = ordered_time_budget(60.0, 45.0, 30.0)

    assert agent_s < symbol_s < cycle_s
    assert agent_s == pytest.approx(60.0), "Die innerste Grenze bleibt der Anker"
    assert any("SYMBOL_EVAL_TIMEOUT_SECONDS" in r.message for r in caplog.records)
    assert any("CYCLE_TIMEOUT_SECONDS" in r.message for r in caplog.records)


def test_geordnete_grenzen_bleiben_unveraendert_und_still(caplog):
    from core.engine.time_budget import ordered_time_budget

    with caplog.at_level("WARNING"):
        budget = ordered_time_budget(60.0, 120.0, 1800.0)

    assert budget == (60.0, 120.0, 1800.0)
    assert not caplog.records


# ---------------------------------------------------------------------------
# 4 — die eigentliche Absicherung: eine Enthaltung ist kein Zyklusabbruch
# ---------------------------------------------------------------------------


def test_ein_veraltetes_symbol_blockiert_den_zyklus_nicht():
    """Der naheliegende Fehlerweg ist, aus einer Symbol-Enthaltung einen Abbruch zu machen.

    Geprueft wird die Zusammensetzung, die im Zyklus an der Extraktionsstelle steht:
    Alter ermitteln, gegen die Grenze halten, betroffenes Symbol auslassen.
    """
    from core.engine.time_budget import is_stale_quote
    from core.engine.trading_loop import _extract_ohlc_from_snapshot

    now = datetime(2026, 9, 16, 15, 30, tzinfo=timezone.utc)
    universum = {
        "FRISCH": _snapshot(185.5, now - timedelta(seconds=12)),
        "ALT": _snapshot(42.0, now - timedelta(hours=9)),
    }

    entschieden, enthalten = [], []
    for symbol, snap in universum.items():
        _ohlc, _price, quote_ts = _extract_ohlc_from_snapshot(snap)
        if is_stale_quote(quote_ts, now, 900.0):
            enthalten.append(symbol)
            continue
        entschieden.append(symbol)

    assert entschieden == ["FRISCH"]
    assert enthalten == ["ALT"]


def test_die_enthaltung_ueberspringt_nur_das_symbol():
    """Quellbeleg: das Tor endet in `continue`, nicht in `break` oder `return`.

    Die Zusammensetzung oben kann nicht zeigen, was der Zyklus mit dem Befund macht.
    Ein `break` an dieser Stelle wuerde jedes folgende Symbol mit abraeumen — der Test
    haelt die Stelle deshalb am Quelltext fest.
    """
    import ast
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "core" / "engine" / "trading_loop.py"
    ).read_text(encoding="utf-8")

    tore = [
        node
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.If)
        and any(
            isinstance(call.func, ast.Name) and call.func.id == "is_stale_quote"
            for call in ast.walk(node.test)
            if isinstance(call, ast.Call)
        )
    ]
    assert len(tore) == 1, f"Genau ein stale_quote-Tor erwartet, gefunden: {len(tore)}"

    koerper = list(ast.walk(tore[0]))
    assert any(
        isinstance(n, ast.Continue) for n in koerper
    ), "Das stale_quote-Tor endet nicht in `continue`."
    for knoten, name in (
        (ast.Break, "break"),
        (ast.Return, "return"),
        (ast.Raise, "raise"),
    ):
        assert not any(isinstance(n, knoten) for n in koerper), (
            f"Das stale_quote-Tor enthaelt `{name}` — eine Enthaltung darf nur das "
            "betroffene Symbol auslassen, nicht den Zyklus beenden."
        )


def test_nicht_positive_grenzen_fallen_auf_die_standardstaffelung_zurueck():
    from core.engine.time_budget import ordered_time_budget

    agent_s, symbol_s, cycle_s = ordered_time_budget(0.0, -1.0, 0.0)
    assert agent_s < symbol_s < cycle_s
    assert agent_s > 0.0
