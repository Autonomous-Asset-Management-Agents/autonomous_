"""#3386 (Epic #3367, ARC-E2) — Vertraege an der Uebergabe H4: vom Broker in die Buecher.

Heute endet der Rueckweg im Nichts. Die Broker-Order-ID wird am Kontext gesetzt
(``order_executor.py:1720`` und ``:1783``, ``context.alpaca_order_id = str(order.id)``)
und das Modell fuehrt das Feld (``cloud_logger.py:162``) — aber der Schreibpfad wirft es
an der Persistenzgrenze weg: ``cloud_logger.py:496`` filtert auf
``model.__table__.columns.keys()``, und der Kommentar darueber nennt ``alpaca_order_id``
und ``client_order_id`` ausdruecklich als die verworfenen Schluessel. Zu einem Fill gibt
es damit keinen Datensatz, der ihn mit der Entscheidung verbindet.

Der vorhandene Abgleich kennt nur ein flaches Tupel (``core/reconciliation.py:26``,
``ReconciliationBreak`` mit ``order_id``/``symbol``/``break_type``) — ohne Lauf-Identitaet,
ohne Zeitpunkt und ohne Gegenueberstellung der beiden Seiten. Man kann einem solchen
Befund nicht ansehen, was verglichen wurde.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

_JETZT = datetime(2026, 9, 16, 15, 30, tzinfo=timezone.utc)


def _fill(**kw):
    from core.contracts import FillEvent

    felder = dict(
        fill_id="f-1",
        broker_order_id="b-99",
        client_order_id="entry-0-d-4711",
        decision_id="d-4711",
        symbol="AAPL",
        side="buy",
        filled_qty=1.5,
        price=185.5,
        filled_at=_JETZT,
    )
    felder.update(kw)
    return FillEvent(**felder)


# ---------------------------------------------------------------------------
# FillEvent — Pflichtfelder
# ---------------------------------------------------------------------------


def test_fill_ohne_broker_order_id_wird_abgelehnt():
    with pytest.raises(ValidationError) as exc:
        _fill(broker_order_id="")
    assert "broker_order_id" in str(exc.value)


def test_fill_ohne_decision_id_wird_abgelehnt():
    """Ohne Entscheidung ist ein Fill eine Bewegung ohne Urheber."""
    with pytest.raises(ValidationError) as exc:
        _fill(decision_id="")
    assert "decision_id" in str(exc.value)


def test_fill_ohne_fill_id_wird_abgelehnt():
    with pytest.raises(ValidationError) as exc:
        _fill(fill_id="")
    assert "fill_id" in str(exc.value)


def test_fill_verlangt_eine_positive_menge():
    with pytest.raises(ValidationError):
        _fill(filled_qty=0.0)
    with pytest.raises(ValidationError):
        _fill(filled_qty=-1.0)


def test_fill_verlangt_einen_positiven_preis():
    with pytest.raises(ValidationError):
        _fill(price=0.0)


def test_fill_haelt_bruchstuecke():
    """Bruchteilige Positionen bleiben — Owner-Entscheidung, kein Rundungsfall."""
    assert _fill(filled_qty=0.017).filled_qty == pytest.approx(0.017)


def test_fill_kennt_nur_kauf_und_verkauf():
    with pytest.raises(ValidationError):
        _fill(side="hold")


def test_fill_weist_unbekannte_felder_ab():
    with pytest.raises(ValidationError):
        _fill(schattenfeld="x")


def test_fill_ist_unveraenderlich():
    """Ein Fill ist eine Tatsache, kein Zwischenstand."""
    f = _fill()
    with pytest.raises(ValidationError):
        f.filled_qty = 2.0


# ---------------------------------------------------------------------------
# FillEvent — Teilausfuehrungen
# ---------------------------------------------------------------------------


def test_teilausfuehrungen_bleiben_unterscheidbar():
    erst = _fill(fill_id="f-1", filled_qty=0.5)
    zweit = _fill(fill_id="f-2", filled_qty=1.0)

    assert erst.broker_order_id == zweit.broker_order_id
    assert erst.fill_id != zweit.fill_id
    assert erst != zweit


def test_zwei_gleiche_fills_sind_gleich():
    """Wertgleichheit, damit ein doppelt eingetroffenes Ereignis erkennbar ist."""
    assert _fill() == _fill()


# ---------------------------------------------------------------------------
# ReconciliationRecord — geschlossene Liste der Abweichungsarten
# ---------------------------------------------------------------------------


def _bruch(**kw):
    from core.contracts import ReconciliationBreak

    felder = dict(
        kind="position_mismatch",
        symbol="AAPL",
        broker_side="qty=3.0",
        engine_side="qty=2.0",
    )
    felder.update(kw)
    return ReconciliationBreak(**felder)


def test_unbekannte_abweichungsart_wird_abgelehnt():
    with pytest.raises(ValidationError):
        _bruch(kind="irgendwas")


def test_die_heutigen_zwei_arten_bleiben_gueltig():
    """core/reconciliation.py:31 kennt genau diese zwei — sie duerfen nicht wegfallen."""
    for art in ("orphaned_order", "position_mismatch"):
        assert _bruch(kind=art).kind == art


def test_ein_bruch_stellt_beide_seiten_gegenueber():
    """Der heutige NamedTuple kann das nicht — man sieht dem Befund nicht an, was verglichen wurde."""
    b = _bruch(broker_side="qty=3.0", engine_side="qty=2.0")
    assert b.broker_side == "qty=3.0"
    assert b.engine_side == "qty=2.0"


def test_record_haelt_lauf_identitaet_und_zeitpunkt():
    from core.contracts import ReconciliationRecord

    r = ReconciliationRecord(
        run_id="r-1",
        started_at=_JETZT,
        finished_at=_JETZT,
        breaks=(),
    )
    assert r.run_id == "r-1"
    assert r.started_at == _JETZT
    assert r.clean is True


def test_record_mit_abweichung_ist_nicht_sauber():
    from core.contracts import ReconciliationRecord

    r = ReconciliationRecord(
        run_id="r-1",
        started_at=_JETZT,
        finished_at=_JETZT,
        breaks=(_bruch(),),
    )
    assert r.clean is False


def test_record_ohne_lauf_identitaet_wird_abgelehnt():
    from core.contracts import ReconciliationRecord

    with pytest.raises(ValidationError):
        ReconciliationRecord(
            run_id="", started_at=_JETZT, finished_at=_JETZT, breaks=()
        )


def test_record_ist_unveraenderlich():
    from core.contracts import ReconciliationRecord

    r = ReconciliationRecord(
        run_id="r-1", started_at=_JETZT, finished_at=_JETZT, breaks=()
    )
    with pytest.raises(ValidationError):
        r.run_id = "r-2"


# ---------------------------------------------------------------------------
# Editionsneutral (BORA)
# ---------------------------------------------------------------------------


def test_die_vertraege_lesen_keine_konfiguration():
    """Wie #3378: gleiches Verhalten in beiden Editionen.

    Geprueft werden **Importe und Aufrufe**, nicht Woerter im Text — der Docstring von
    ``order_intent.py`` erwaehnt Alpaca gerade, um zu sagen, dass der Vertrag KEIN
    Alpaca-Typ ist. Eine Wortsuche wuerde genau diesen Satz als Verstoss lesen.
    """
    import ast
    from pathlib import Path

    verzeichnis = Path(__file__).resolve().parents[2] / "core" / "contracts"
    verbotene_module = ("config", "alpaca", "redis", "sqlalchemy", "google")
    verbotene_aufrufe = ("get_config", "getenv")

    for datei in sorted(verzeichnis.glob("*.py")):
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.Import):
                quellen = [a.name for a in knoten.names]
            elif isinstance(knoten, ast.ImportFrom):
                quellen = [knoten.module or ""]
            elif isinstance(knoten, ast.Call):
                name = getattr(knoten.func, "id", None) or getattr(
                    knoten.func, "attr", ""
                )
                assert (
                    name not in verbotene_aufrufe
                ), f"{datei.name} ruft {name}() — ein Vertrag darf keine Konfiguration lesen"
                continue
            else:
                continue
            for quelle in quellen:
                wurzel = quelle.split(".")[0]
                assert (
                    wurzel not in verbotene_module
                ), f"{datei.name} importiert {quelle!r} — das bindet den Vertrag an eine Edition"


def test_serialisierung_ist_stabil():
    f = _fill()
    assert f.model_dump() == _fill().model_dump()
    assert f.model_dump()["broker_order_id"] == "b-99"
