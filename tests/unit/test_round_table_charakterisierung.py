"""#4274 (H-4a) — Charakterisierungsnetz fuer ``run_round_table``, vor dem Umbau H-4c bis H-4h.

Haelt das heutige Verhalten des Round-Table-Dirigenten fest: Signal, Senate-Session,
Zaehler, DecisionContext, Watchdog-Aufrufe und Log-Zeilen je Szenario
(``_round_table_charakterisierung.py``). Die Umzuege H-4c bis H-4h muessen danach gegen
die unveraenderte Referenz gruen sein.

Plan: ``docs/4274-*/implementation_plan.md``.

Referenz neu schreiben (nur auf unveraendertem ``core/``, der Diff ist der Befund):

    cd ai_trading_bot && python tests/unit/_round_table_charakterisierung.py --schreibe
"""

from __future__ import annotations

import dataclasses
import inspect
import socket
import textwrap
import types

import pytest

from tests.unit import _round_table_charakterisierung as netz

pytestmark = pytest.mark.h1


@pytest.mark.parametrize("sz", netz.SZENARIEN, ids=lambda s: s.name)
def test_runde_gleicht_der_referenz(sz):
    assert netz.REFERENZ.exists(), f"Referenz fehlt: {netz.REFERENZ}"
    referenz = netz.lade_referenz()
    assert sz.name in referenz, f"Referenz fehlt fuer Szenario {sz.name}"
    abweichungen = netz.befunde(referenz[sz.name], netz.fahre(sz))
    assert not abweichungen, f"[{sz.name}] erster Befund: " + "\n".join(
        abweichungen[:10]
    )


def test_alle_szenarien_haben_eine_referenz():
    namen = [sz.name for sz in netz.SZENARIEN]
    assert len(namen) == len(set(namen)), "Szenario-Name doppelt"
    assert set(netz.lade_referenz()) == set(namen)


@pytest.mark.parametrize(
    "sz", [s for s in netz.SZENARIEN if s.beobachtung_wirft], ids=lambda s: s.name
)
def test_werfende_beobachtung_aendert_das_verdikt_nicht(sz):
    """Szenario 12: Werfen die Zaehler, bleibt alles ausser den Zaehlern gleich."""
    zwilling = dataclasses.replace(
        sz, name=f"{sz.name}_zwilling", beobachtung_wirft=False
    )
    ist = netz.fahre(sz)
    ohne = netz.fahre(zwilling)
    assert ist.pop("zaehler") != ohne.pop("zaehler")
    assert netz.befunde(ohne, ist) == []


def test_netz_ist_deterministisch():
    laeufe = [netz.kanonisch(netz.messe_alle()) for _ in range(3)]
    assert laeufe[0] == laeufe[1] == laeufe[2]


# ── Gegenproben: ein falsch verdrahteter Dirigent muss auffallen ─────────────


def _manipulierter_schritt(
    *ersetzungen: tuple[str, str], name: str = "run_round_table"
):
    """Funktion ``name`` des Kerns mit gezielt ersetztem Quelltext, im Namensraum des Kerns.

    Jede Ersetzung muss genau einmal treffen — sonst waere die Gegenprobe still keine.
    """
    from core.round_table import runner

    quelltext = textwrap.dedent(inspect.getsource(getattr(runner, name)))
    for alt, neu in ersetzungen:
        anzahl = quelltext.count(alt)
        assert anzahl == 1, f"Ersetzung trifft {anzahl}-mal statt einmal: {alt!r}"
        quelltext = quelltext.replace(alt, neu)
    ablage: dict = {}
    exec(compile(quelltext, runner.__file__, "exec"), dict(vars(runner)), ablage)
    gebaut = ablage[name]
    # Globale des Kerns selbst, nicht einer Kopie: Patches aus ``fahre`` muessen greifen.
    return types.FunctionType(gebaut.__code__, vars(runner), gebaut.__name__)


# #4281 (H-4h): Phase 1 steht im Schritt ``_abstimmen``. Die Gegenproben ersetzen ihn am
# Kern und fahren den echten Dirigenten — das belegt zugleich, dass er den Schritt dort ruft.
def test_netz_ist_nicht_vakuoes_vertauschte_stimme(monkeypatch):
    from core.round_table import runner

    schritt = _manipulierter_schritt(
        (
            "_agent_name = _active_agents[i].__class__.__name__",
            "_agent_name = _active_agents[len(_active_agents) - 1 - i]"
            ".__class__.__name__",
        ),
        name="_abstimmen",
    )
    monkeypatch.setattr(runner, "_abstimmen", schritt)
    ist = netz.fahre("timeout_ml_stimme")
    assert netz.befunde(
        netz.lade_referenz()["timeout_ml_stimme"], ist
    ), "vertauschte Zuordnung Stimme→Agent blieb unbemerkt"


# Der Timeout-Zweig ohne Zaehler und ohne Watchdog-Bruecke.
_TIMEOUT_BUCHUNG = (
    "            try:\n"
    "                _bump_agent_failure(_agent_name)\n"
    "            except Exception as exc:  # noqa: BLE001 — observation never alters the flow\n"
    '                logger.warning("agent-failure counter failed: %s", exc, exc_info=True)\n'
    "            # Bridge: ML agent timeout → MLWatchdog escalation (60s→Slack, 300s→Kill)\n"
    "            if _agent_name in _ML_AGENT_NAMES and _ml_watchdog:\n"
    "                _ml_watchdog.record_error(_agent_name, result)\n"
)


def test_netz_ist_nicht_vakuoes_verschluckter_timeout(monkeypatch):
    from core.round_table import runner

    schritt = _manipulierter_schritt(
        (_TIMEOUT_BUCHUNG, "            pass\n"), name="_abstimmen"
    )
    monkeypatch.setattr(runner, "_abstimmen", schritt)
    befunde = netz.befunde(
        netz.lade_referenz()["timeout_ml_stimme"],
        netz.fahre("timeout_ml_stimme"),
    )
    assert any(b.startswith("zaehler.agent_vote_failures") for b in befunde), befunde
    assert any(b.startswith("watchdog") for b in befunde), befunde


def test_netzzugriff_laesst_das_netz_scheitern():
    def _verbindet(state):
        socket.create_connection(("192.0.2.1", 9), timeout=0.01)

    buy = netz.szenario("buy")
    sz = dataclasses.replace(
        buy,
        name="buy_mit_netzzugriff",
        stimmen={**buy.stimmen, "MomentumAgent": _verbindet},
    )
    with pytest.raises(AssertionError, match="Netzzugriff im Netz"):
        netz.fahre(sz)
