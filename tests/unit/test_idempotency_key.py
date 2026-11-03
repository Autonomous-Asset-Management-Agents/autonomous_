"""#3387 (Epic #3367, ARC-E2) — der Idempotenz-Schluessel wird abgeleitet, nicht gewuerfelt.

Heute mintet der Order-Pfad an mehreren Stellen eine frische ``uuid4`` als
``client_order_id``. Die Wurzel liegt eine Ebene hoeher: ``core/cloud_logger.py:83``
gibt ``DecisionContext.client_order_id`` ein ``default_factory=lambda: str(uuid.uuid4())``
— jeder Kontext traegt von Geburt an einen Zufallswert. Nach einem Neustart ist er neu,
und damit kann der Broker eine Wiederholung nicht von einer zweiten Order unterscheiden.

Die gefaehrlichen Stellen sind die beiden Markt-Fallbacks (``order_executor.py:981`` und
``:1882``): sie schicken eine zweite Order fuer dasselbe Leg, mit zufaelligem Schluessel.

Die einzige Stelle, die heute bewusst ableitet, ist der HITL-Freigabepfad (``:2279``,
``f"hitl-{_approval_cid}"[:128]``). Diese Form muss **zeichengleich** erhalten bleiben:
Freigaben, die vor der Umstellung in der Warteschlange lagen, tragen ihre ``approval_id``
bereits — eine abweichende Ableitung erzeugte am Umstellungstag je Freigabe ein Duplikat.
"""

import re

import pytest

_ERLAUBT = re.compile(r"^[A-Za-z0-9._-]+$")


# ---------------------------------------------------------------------------
# Bestimmtheit
# ---------------------------------------------------------------------------


def test_wiederholung_erzeugt_denselben_schluessel():
    from core.idempotency import derive_client_order_id

    a = derive_client_order_id("d-4711", "entry", 0)
    b = derive_client_order_id("d-4711", "entry", 0)

    assert a == b


def test_neustart_rechnet_denselben_schluessel_neu_aus():
    """Nichts wird gelesen, nichts gespeichert — der Schluessel folgt aus der Entscheidung.

    Genau das ist der Punkt, an dem ein *gespeicherter* Schluessel bricht: die Wiederaufnahme
    haengt dann davon ab, dass der Schreibvorgang vor dem Absenden fertig war.
    """
    import importlib

    import core.idempotency as modul

    vorher = modul.derive_client_order_id("d-4711", "entry", 0)
    importlib.reload(modul)  # steht fuer den Prozessneustart
    nachher = modul.derive_client_order_id("d-4711", "entry", 0)

    assert vorher == nachher


# ---------------------------------------------------------------------------
# Unterscheidbarkeit
# ---------------------------------------------------------------------------


def test_markt_fallback_erhoeht_den_versuch():
    from core.idempotency import derive_client_order_id

    erst = derive_client_order_id("d-4711", "stop", 0)
    fallback = derive_client_order_id("d-4711", "stop", 1)

    assert erst != fallback


def test_legs_derselben_entscheidung_kollidieren_nicht():
    from core.idempotency import derive_client_order_id

    schluessel = {
        derive_client_order_id("d-4711", leg, 0)
        for leg in ("entry", "stop", "trim", "displacement", "panic", "breaker")
    }
    assert len(schluessel) == 6


def test_verschiedene_entscheidungen_kollidieren_nicht():
    from core.idempotency import derive_client_order_id

    assert derive_client_order_id("d-4711", "entry", 0) != derive_client_order_id(
        "d-4712", "entry", 0
    )


def test_das_leg_vokabular_ist_das_der_vertraege():
    """Kein zweites Vokabular: das Leg ist ein IntentKind aus #3378.

    Zwei Woerterbuecher fuer dieselbe Sache laufen auseinander, sobald eines erweitert
    wird — und der Reconciler (#3389) muesste dann beide kennen.
    """
    from typing import get_args

    from core.contracts import IntentKind
    from core.idempotency import derive_client_order_id

    for leg in get_args(IntentKind):
        assert derive_client_order_id("d-4711", leg, 0)


def test_unbekanntes_leg_wird_abgewiesen():
    from core.idempotency import derive_client_order_id

    with pytest.raises(ValueError):
        derive_client_order_id("d-4711", "kein-leg", 0)


# ---------------------------------------------------------------------------
# Broker-Grenzen
# ---------------------------------------------------------------------------


def test_der_schluessel_haelt_die_broker_grenze_ein():
    from core.idempotency import derive_client_order_id

    for decision_id in ("d-4711", "x" * 400, "mit leer und /slash#raute", "ä-ö-ü"):
        key = derive_client_order_id(decision_id, "entry", 0)
        assert 0 < len(key) <= 128, f"{decision_id!r} → {len(key)} Zeichen"
        assert _ERLAUBT.match(key), f"unzulaessige Zeichen in {key!r}"


def test_lange_entscheidungen_kollidieren_nicht_durch_kappung():
    """Kappen allein wuerde zwei lange, nur am Ende verschiedene IDs gleichmachen."""
    from core.idempotency import derive_client_order_id

    a = derive_client_order_id("x" * 200 + "A", "entry", 0)
    b = derive_client_order_id("x" * 200 + "B", "entry", 0)

    assert a != b


def test_leere_entscheidung_wird_abgewiesen():
    """Ein Schluessel ohne Entscheidung waere kein Idempotenz-Schluessel."""
    from core.idempotency import derive_client_order_id

    with pytest.raises(ValueError):
        derive_client_order_id("", "entry", 0)


def test_negativer_versuch_wird_abgewiesen():
    from core.idempotency import derive_client_order_id

    with pytest.raises(ValueError):
        derive_client_order_id("d-4711", "entry", -1)


# ---------------------------------------------------------------------------
# Der naechste Versuch — der gefaehrliche Fall aus dem Issue
# ---------------------------------------------------------------------------


def test_naechster_versuch_behaelt_den_entscheidungsanteil():
    """Markt-Fallback: dieselbe Entscheidung, dasselbe Leg, Versuch um eins hoeher.

    Genau hier lag der Schaden: order_executor.py:981 und :1882 schicken eine ZWEITE
    Order fuer dasselbe Leg mit `str(uuid.uuid4())` — der Broker kann sie nicht als
    Wiederholung erkennen.
    """
    from core.idempotency import derive_client_order_id, next_attempt

    erst = derive_client_order_id("d-4711", "stop", 0)
    zweit = next_attempt(erst)

    assert zweit == derive_client_order_id("d-4711", "stop", 1)
    assert zweit != erst
    assert "d-4711" in zweit


def test_naechster_versuch_ist_bestimmt():
    from core.idempotency import derive_client_order_id, next_attempt

    erst = derive_client_order_id("d-4711", "entry", 0)
    assert next_attempt(erst) == next_attempt(erst)


def test_naechster_versuch_zaehlt_weiter():
    from core.idempotency import derive_client_order_id, next_attempt

    k = derive_client_order_id("d-4711", "entry", 0)
    assert next_attempt(next_attempt(k)) == derive_client_order_id("d-4711", "entry", 2)


def test_naechster_versuch_haelt_die_broker_grenze_ein():
    from core.idempotency import derive_client_order_id, next_attempt

    k = derive_client_order_id("x" * 400, "entry", 0)
    assert len(next_attempt(k)) <= 128


def test_naechster_versuch_einer_alten_form_bleibt_unterscheidbar():
    """Bestands-Schluessel (HITL-Freigabe, alte UUID) sind nicht strukturiert.

    Auch dann muss der Fallback bestimmt und vom Original unterscheidbar sein — der
    Entscheidungsanteil ist aus so einem Schluessel aber nicht rueckgewinnbar.
    """
    from core.idempotency import next_attempt

    for alt in ("hitl-abc-123", "9f1c2b7e-0000-4444-8888-0123456789ab"):
        neu = next_attempt(alt)
        assert neu != alt
        assert neu == next_attempt(alt)
        assert len(neu) <= 128
        assert _ERLAUBT.match(neu)


def test_naechster_versuch_verlangt_einen_schluessel():
    from core.idempotency import next_attempt

    with pytest.raises(ValueError):
        next_attempt("")


# ---------------------------------------------------------------------------
# Die Freigabeform bleibt zeichengleich
# ---------------------------------------------------------------------------


def test_die_freigabeform_bleibt_zeichengleich():
    """Muss ``f"hitl-{approval_id}"[:128]`` aus order_executor.py:2279 exakt reproduzieren."""
    from core.idempotency import derive_approval_client_order_id

    for approval_id in ("abc-123", "x" * 200, "7"):
        assert (
            derive_approval_client_order_id(approval_id) == f"hitl-{approval_id}"[:128]
        )


def test_die_freigabeform_verlangt_eine_id():
    from core.idempotency import derive_approval_client_order_id

    with pytest.raises(ValueError):
        derive_approval_client_order_id("")


# ---------------------------------------------------------------------------
# Die Wurzel: der Kontext wuerfelt nicht mehr
# ---------------------------------------------------------------------------


def test_decisioncontext_wuerfelt_keine_client_order_id_mehr():
    """cloud_logger.py:83 war die Hauptquelle des Zufalls, nicht der else-Zweig im Executor."""
    from core.cloud_logger import DecisionContext

    a = DecisionContext(decision_id="d-4711")
    b = DecisionContext(decision_id="d-4711")

    assert a.client_order_id == b.client_order_id, (
        "Zwei Kontexte derselben Entscheidung tragen verschiedene client_order_id — "
        "damit kann der Broker eine Wiederholung nicht erkennen."
    )


def test_decisioncontext_leitet_aus_der_decision_id_ab():
    from core.cloud_logger import DecisionContext
    from core.idempotency import derive_client_order_id

    ctx = DecisionContext(decision_id="d-4711")

    assert ctx.client_order_id == derive_client_order_id("d-4711", "entry", 0)


def test_ein_ausdruecklich_gesetzter_schluessel_bleibt_unangetastet():
    """Der HITL-Pfad setzt den Schluessel selbst — die Ableitung darf ihn nicht ueberschreiben."""
    from core.cloud_logger import DecisionContext

    ctx = DecisionContext(decision_id="d-4711", client_order_id="hitl-abc")

    assert ctx.client_order_id == "hitl-abc"
