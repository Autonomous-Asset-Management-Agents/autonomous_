"""#3487 (ARC-E2.11) — Halt und Tagesbudget ueberleben den harten Neustart (Epic #3367).

**Gegen ``main`` ohne #3486 ROT, und das ist der Auftrag.** Auf dem Desktop lebten Halt und
Tagesbudget bis #3449 Schritt 5 nur im Arbeitsspeicher: Der neu gestartete Prozess wusste von
beidem nichts. Gruen wird die Abnahme mit PR #3486.

Plan: ``docs/3487-abnahme-halt-budget/implementation_plan.md`` (PR #3492, ``plan-approved``).
Abweichung vom Plan, beim Bauen gefunden: Das Tagesbudget prueft nicht das Tor, sondern der
Executor **vor** der Absendung (``ComplianceGuardian.check_trade``, nach ``check_order``). Der
Prozess der Kette tut darum genau das, in derselben Reihenfolge —
``tests/chain/_halt_und_budget.py``.
"""

from __future__ import annotations

import sqlite3

from pytest_bdd import given, scenarios, then, when

from tests.chain.chaos import ChaosVorrichtung

scenarios("../halt_und_budget_neustart.feature")

UHR = "2026-09-16 09:35"
SEED = 7
_MODUL = "tests.chain._halt_und_budget"

#: Der Tagesdeckel dieser Abnahme — klein, damit das Szenario nicht an der Produktionszahl
#: haengt (ADR-C04) und schnell laeuft.
_DECKEL = "3"


def _lauf(vorrichtung, **umgebung):
    lauf = vorrichtung.lauf(
        modul=_MODUL,
        umgebung_zusatz={"COMPLIANCE_MAX_DAILY_TRADES": _DECKEL, **umgebung},
    )
    return lauf


def _ereignisse(vorrichtung, art):
    return [e for e in vorrichtung.beobachtung() if e.get("ereignis") == art]


@given(
    "ein Engine-Prozess hat einen Trip ausgeloest und ist hart gestorben",
    target_fixture="vorrichtung",
)
def _trip_und_tod(tmp_path):
    v = ChaosVorrichtung(tmp_path, seed=SEED, uhr=UHR)
    erster = _lauf(
        v, KETTE_TRIP="Portfolio stop loss (7.1%)", KETTE_NACH_TRIP_TOETEN="1"
    )
    assert not erster.sauber_beendet, (
        "Der Prozess ist nicht hart gestorben — dann misst dieses Szenario nichts.\n"
        + erster.ausgabe[-1500:]
    )
    return v


@given("die Ablage traegt einen Halt ohne Trip-Satz", target_fixture="vorrichtung")
def _halt_ohne_satz(tmp_path):
    from core.state.sqlite_adapter import _SCHEMA

    v = ChaosVorrichtung(tmp_path, seed=SEED, uhr=UHR)
    with sqlite3.connect(tmp_path / "engine_state.db") as db:
        db.executescript(_SCHEMA)
        db.execute(
            "INSERT INTO engine_state(key, value, expires_at) VALUES ('system_halted', "
            "'true', NULL)"
        )
    return v


@given(
    "ein Engine-Prozess hat alle erlaubten Trades des Tages bis auf einen verbraucht",
    target_fixture="vorrichtung",
)
def _budget_fast_verbraucht(tmp_path):
    v = ChaosVorrichtung(tmp_path, seed=SEED, uhr=UHR)
    erster = _lauf(v, KETTE_BUDGET_VERBRAUCHEN=str(int(_DECKEL) - 1))
    assert erster.rueckgabecode == 0, erster.ausgabe[-1500:]
    return v


@when("der Prozess neu startet und einen Einstieg versucht")
def _neustart_ein_einstieg(vorrichtung):
    lauf = _lauf(vorrichtung, KETTE_EINSTIEGE="1")
    assert lauf.rueckgabecode == 0, lauf.ausgabe[-1500:]


@when("die Engine ihre Startpruefung durchlaeuft und einen Einstieg versucht")
def _startpruefung_und_einstieg(vorrichtung):
    lauf = _lauf(vorrichtung, KETTE_STARTPRUEFUNG="1", KETTE_EINSTIEGE="1")
    assert lauf.rueckgabecode == 0, lauf.ausgabe[-1500:]


@when("der Prozess neu startet und zwei Einstiege versucht")
def _neustart_zwei_einstiege(vorrichtung):
    lauf = _lauf(vorrichtung, KETTE_EINSTIEGE="2")
    assert lauf.rueckgabecode == 0, lauf.ausgabe[-1500:]


@then("geht keine Order zum Broker")
def _keine_order(vorrichtung):
    orders = vorrichtung.broker_orders()
    assert not orders, (
        f"Der Broker haelt {len(orders)} Order(s), obwohl der Handel angehalten sein "
        "muesste. Der Halt hat den Neustart nicht ueberlebt — auf dem Desktop lebte er bis "
        "#3449 Schritt 5 nur im Arbeitsspeicher, und der Start raeumte einen Halt ohne "
        "Trip im Speicher (#2467 FIX 4c)."
    )


@then("der Einstieg wurde wegen des Halts abgewiesen")
def _wegen_halt(vorrichtung):
    assert _ereignisse(vorrichtung, "einstieg_halt"), vorrichtung.beobachtung()


@then("geht genau eine Order zum Broker")
def _genau_eine(vorrichtung):
    orders = vorrichtung.broker_orders()
    assert len(orders) == 1, (
        f"Der Broker haelt {len(orders)} Orders; erlaubt war nach dem Neustart noch genau "
        "einer. Das verbrauchte Tagesbudget hat den Neustart nicht ueberlebt."
    )


@then("der zweite Einstieg wurde wegen des Tagesbudgets abgewiesen")
def _wegen_budget(vorrichtung):
    assert (
        len(_ereignisse(vorrichtung, "einstieg_budget")) == 1
    ), vorrichtung.beobachtung()
