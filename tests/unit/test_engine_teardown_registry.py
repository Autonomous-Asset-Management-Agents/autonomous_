"""#3438 — der Engine-Teardown haelt den Specialist-Thread an.

Grundlage ist die RCA ``docs/6_runbooks/RCA_2026_09_17_ENGINE_TEARDOWN_LEAKS_SPECIALIST_THREAD.md``
(gemergt mit #3439). Kurzfassung des Befunds:

* ``stop_strategy()`` (``base.py:1112``) haelt Latenz-Watchdog, Strategie-, Monitor- und
  Tagesbericht-Thread an. Die ``specialist_registry`` steht nicht in der Liste.
* ``StockSpecialistRegistry.stop()`` hatte **keinen Produktionsaufrufer** — der einzige
  Treffer im Bestand war das Docstring-Beispiel in ``specialist_registry.py:140``.
* ``stop()`` konnte seine Zusage ohnehin nicht halten: Das Shutdown-Event wird nur
  *zwischen* Symbolen geprueft, und ``_refresh_symbol`` wartete unbegrenzt auf
  ``agent.research()`` — sechs Netzabrufe parallel, ohne Zeitlimit. Steckt der Thread
  dort, verstreicht der ``join(timeout=15)`` wirkungslos und ``stop()`` kehrt zurueck,
  als haette es gestoppt.

Die Reihenfolge der Behebung ist zwingend: **ohne das Zeitlimit ist alles Weitere
wirkungslos**, weil ``stop()`` sonst weiterhin nur so tut, als haette es gestoppt.

Diese Datei prueft die Zusagen von aussen — ueber lebende Threads und Rueckkehrzeiten,
nicht ueber Aufrufzaehler an Attrappen. Ein Aufrufzaehler wuerde gruen, sobald jemand
``stop()`` *ruft*; die Frage ist aber, ob der Thread danach **weg** ist.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

THREAD_NAME = "SpecialistRegistryThread"

#: Grosszuegig: Der Plan warnt ausdruecklich davor, ein zu knappes Limit zu waehlen —
#: ein abgebrochener Bericht zeigt "degraded" statt Inhalt. Hier wird nur geprueft, dass
#: ueberhaupt eines greift, nicht wie kurz es ist.
STOP_OBERGRENZE_S = 30.0


def _lebende_registry_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith(THREAD_NAME)]


# ---------------------------------------------------------------------------
# Schicht 1 — stop() haelt seine Zusage, auch bei haengender Auffrischung
# ---------------------------------------------------------------------------


@pytest.mark.mutates_global_state
def test_stop_kehrt_zurueck_obwohl_eine_auffrischung_haengt() -> None:
    """Szenario: Eine haengende Auffrischung blockiert den Stopp nicht.

    Der Kern des Befunds. Ein Netzaufruf, der nicht antwortet, darf den Thread nicht
    unkuendbar machen — sonst ist ``stop()`` eine Behauptung.
    """
    import asyncio

    from core.specialist_registry import StockSpecialistRegistry

    haengt = threading.Event()

    async def _haengt_fuer_immer(*_a, **_kw):
        # Simuliert den Fall, den `agent.research()` heute offenlaesst: sechs parallele
        # Netzabrufe ohne umschliessendes Zeitlimit. `*_a, **_kw` ist der Grund fuer die
        # AsyncMock-Vorgabe (CODING_POLICY §9): Bekommt `research()` spaeter ein
        # Argument, bricht die Attrappe nicht.
        haengt.set()
        await asyncio.sleep(3600)

    registry = StockSpecialistRegistry(symbols=["AAPL"], gemini_api_key="")
    agent = MagicMock()
    agent.research = AsyncMock(side_effect=_haengt_fuer_immer)
    registry._agents["AAPL"] = agent
    registry._report_only = False

    registry.start()
    try:
        assert haengt.wait(timeout=20), (
            "Die Auffrischung wurde nie erreicht — dann prueft dieser Test nichts. "
            "Vorrichtung, nicht Befund."
        )
        beginn = time.monotonic()
        registry.stop()
        gedauert = time.monotonic() - beginn
    finally:
        registry._shutdown.set()

    assert gedauert < STOP_OBERGRENZE_S, (
        f"stop() brauchte {gedauert:.1f}s. Das Shutdown-Event wird nur ZWISCHEN "
        "Symbolen geprueft, und die Auffrischung selbst hat kein Zeitlimit — der "
        "join() verstreicht wirkungslos (#3438, Schicht 1)."
    )
    assert not _lebende_registry_threads(), (
        f"Nach stop() leben noch {len(_lebende_registry_threads())} Registry-Threads. "
        "stop() hat zurueckgegeben, als haette es gestoppt."
    )


async def test_zeitlimit_laesst_den_letzten_bekannten_bericht_stehen(
    monkeypatch,
) -> None:
    """Szenario: Eine haengende Auffrischung wird abgebrochen und benannt gemeldet.

    Der Zeitlimit-Zweig ist neuer Code und war damit zunaechst ungeprueft — waehrend
    die drei bestehenden Tests, die den ERFOLGSPFAD pruefen, einen schweren Fehler
    meiner ersten Fassung gefangen haben: Der Erfolgspfad lag dort hinter einem
    ``return`` und war unerreichbar. Kein Bericht waere je gespeichert worden.

    Geprueft wird hier die Zusage des Plans: abbrechen, **benannt** melden, und den
    letzten bekannten Stand stehen lassen. Ein halber Bericht waere schlechter als ein
    alter.
    """
    import asyncio

    from core import specialist_registry as sr

    monkeypatch.setattr(sr, "REFRESH_SYMBOL_TIMEOUT_SECONDS", 0.05)

    registry = sr.StockSpecialistRegistry(symbols=["AAPL"], gemini_api_key="")
    registry._report_only = False

    alter_bericht = MagicMock(name="alter Bericht")
    registry._reports["AAPL"] = alter_bericht

    async def _zu_langsam(*_a, **_kw):
        await asyncio.sleep(5)

    agent = MagicMock()
    agent.research = AsyncMock(side_effect=_zu_langsam)
    registry._agents["AAPL"] = agent

    await registry._refresh_symbol("AAPL")

    assert registry._reports["AAPL"] is alter_bericht, (
        "Der letzte bekannte Bericht wurde durch den Abbruch verworfen. Ein Zeitlimit "
        "darf Wissen nicht loeschen, nur das Warten beenden."
    )
    marke = registry.refresh_failures().get("AAPL")
    assert marke and marke.get("reason") == "timeout", (
        f"Der Abbruch wurde nicht benannt gemeldet: {marke!r}. Ein stiller Abbruch ist "
        "von einem erfolgreichen Lauf nicht zu unterscheiden (#3438)."
    )


# ---------------------------------------------------------------------------
# Schicht 2 — stop_strategy() haelt die Registry an
# ---------------------------------------------------------------------------


@pytest.mark.mutates_global_state
def test_stop_strategy_haelt_auch_den_specialist_thread_an(monkeypatch) -> None:
    """Szenario: Stop stoppt auch den Specialist-Thread.

    Der fuehrende Gegentest des Tickets. Er scheitert heute inhaltlich: die Registry
    steht schlicht nicht in der Thread-Liste von ``stop_strategy()`` (``base.py:1129``).
    """
    import config as cfg

    monkeypatch.setattr(cfg, "GEMINI_API_KEY", "valid-test-key")

    # Die schweren Abhaengigkeiten werden ersetzt (bewaehrtes Muster aus
    # test_engine_base.py:67-79), der Auto-Start unterbunden. Die **Registry** wird
    # ausdruecklich NICHT ersetzt — sie ist der Gegenstand. Sie wegzumocken waere
    # Option B des Plans, die verworfen wurde, weil sie den Produktionsdefekt
    # dauerhaft verdeckt haette.
    with patch(
        "core.engine.base.HistoricalDataProvider", return_value=MagicMock()
    ), patch("core.engine.base.NewsProcessor", return_value=MagicMock()), patch(
        "core.engine.base.MarketRegimeModel", return_value=MagicMock()
    ), patch(
        "core.engine.base.AIMarketScanner", return_value=MagicMock()
    ), patch(
        "core.engine.base.AILearningEngine", return_value=MagicMock()
    ), patch(
        "core.engine.base.threading.Timer"
    ):
        from core.engine.base import BotEngine

        engine = BotEngine(
            clock=type(
                "MockClock",
                (),
                {
                    "now": lambda self: __import__("datetime").datetime(
                        2025, 1, 1, tzinfo=__import__("datetime").timezone.utc
                    ),
                    "time": lambda self: 1735732800.0,
                },
            )(),
            trade_intelligence=MagicMock(),
            trading_client=MagicMock(),
            data_client=MagicMock(),
        )

    registry = getattr(engine, "specialist_registry", None)
    if registry is None:
        pytest.skip(
            "Die Registry ist in diesem Boot nicht aktiv (Flag aus) — dann gibt es "
            "nichts anzuhalten. Kein Befund, sondern eine andere Konfiguration."
        )

    registry.start()
    try:
        assert (
            _lebende_registry_threads()
        ), "Der Registry-Thread laeuft nicht — dann prueft dieser Test nichts."
        engine.is_simulation = True  # erzwingt den Teardown-Pfad in base.py:1113-1118
        engine.stop_strategy()
    finally:
        registry._shutdown.set()
        if registry._refresh_thread:
            registry._refresh_thread.join(timeout=5)

    lebende = _lebende_registry_threads()
    assert not lebende, (
        f"Nach stop_strategy() leben noch {len(lebende)} Registry-Threads: "
        f"{[t.name for t in lebende]}\n"
        "stop_strategy() (base.py:1129) haelt Latenz-Watchdog, Strategie-, Monitor- und "
        "Tagesbericht-Thread an — die specialist_registry steht nicht in der Liste. Wer "
        "den Handel stoppt, hat weiterhin einen Prozessteil, der externe APIs abfragt "
        "und Kontingente verbraucht (#3438)."
    )


# ---------------------------------------------------------------------------
# Schicht 4 — der Waechter gegen eine erneute Asymmetrie
# ---------------------------------------------------------------------------


def test_jeder_thread_start_im_engine_boot_hat_einen_stopp() -> None:
    """Szenario: Ein neuer Start ohne Stopp faellt auf.

    Die Ursache war nicht der Thread, sondern die **Asymmetrie**: #2408 hat den
    Registry-Start in den Boot-Pfad gehaengt und ``stop_strategy()`` nicht erweitert.
    Nichts hat das bemerkt. Dieser Waechter bemerkt es.

    Geprueft wird ueber den AST, nicht ueber Textsuche: Eine Textsuche faende auch die
    Erwaehnung in einem Kommentar oder Docstring — genau diese Falle steckt heute im
    CH-2-Zaehler von #3366 (siehe #3447).
    """
    import ast
    from pathlib import Path

    quelle = Path(__file__).resolve().parents[2] / "core" / "engine" / "base.py"
    baum = ast.parse(quelle.read_text(encoding="utf-8"))

    def _self_attribut(knoten) -> str | None:
        """``self.<name>`` → ``<name>``, sonst ``None``."""
        if isinstance(knoten, ast.Attribute) and isinstance(knoten.value, ast.Name):
            if knoten.value.id == "self":
                return knoten.attr
        return None

    # Was wird im Boot gestartet? `self.<name>.start()`
    gestartet: set[str] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Attribute):
            if knoten.func.attr == "start":
                name = _self_attribut(knoten.func.value)
                if name:
                    gestartet.add(name)

    # Ein **anonym** gestarteter Thread — `threading.Thread(...).start()`, ohne dass
    # das Ergebnis irgendwo landet — ist schlimmer als ein vergessener: Er ist nicht
    # einmal auffindbar. Genau so waren `SpecialistWarmup` und `StallMonitorThread`
    # gebaut, und genau deshalb hat die RCA sie uebersehen: Sie nannte einen Thread,
    # es waren drei. Gemessen ueber ein Testlauf-Zaehlwerk, nicht erschlossen.
    anonym: list[str] = []
    for knoten in ast.walk(baum):
        if not (
            isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Attribute)
        ):
            continue
        if knoten.func.attr != "start":
            continue
        # `Thread(...).start()` statt `self.<attr>.start()` → das Objekt hat keinen Namen
        if isinstance(knoten.func.value, ast.Call):
            ziel = knoten.func.value.func
            name = (
                ziel.attr
                if isinstance(ziel, ast.Attribute)
                else getattr(ziel, "id", "")
            )
            if name == "Thread":
                anonym.append(f"Zeile {knoten.lineno}")

    assert not anonym, (
        "In core/engine/base.py wird ein Thread gestartet, ohne ihn irgendwo "
        f"festzuhalten: {anonym}\n"
        "Ein anonymer Thread kann nicht angehalten und nicht abgewartet werden — er "
        "ist im Teardown gar nicht auffindbar. Er gehoert an ein Attribut (#3438)."
    )

    # Was kommt im Teardown ueberhaupt vor? Die Pruefung ist bewusst NICHT auf
    # `.stop()` verengt: Die Thread-Attribute werden ueber eine Liste gejoint
    # (base.py:1129-1145), nicht einzeln gestoppt. Verlangt wird nur, dass der
    # Teardown die Sache **kennt** — das ist die Symmetrie, die #2408 gebrochen hat.
    teardown = next(
        (
            k
            for k in ast.walk(baum)
            if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))
            and k.name == "stop_strategy"
        ),
        None,
    )
    assert teardown is not None, (
        "stop_strategy() ist in core/engine/base.py nicht mehr zu finden — dann prueft "
        "dieser Waechter nichts. Vorrichtung, nicht Befund."
    )
    im_teardown = {
        name for k in ast.walk(teardown) if (name := _self_attribut(k)) is not None
    }
    # `getattr(self, "daily_report_thread", None)` (base.py:1132) ist syntaktisch kein
    # Attributzugriff, sondern ein Aufruf mit einem String. Ohne diesen Zweig meldete
    # der Waechter eine Stelle als vergessen, die sehr wohl aufgeraeumt wird — ein
    # Fehlalarm, und ein Waechter mit Fehlalarmen wird abgeschaltet.
    for k in ast.walk(teardown):
        if (
            isinstance(k, ast.Call)
            and isinstance(k.func, ast.Name)
            and k.func.id == "getattr"
            and len(k.args) >= 2
            and isinstance(k.args[0], ast.Name)
            and k.args[0].id == "self"
            and isinstance(k.args[1], ast.Constant)
            and isinstance(k.args[1].value, str)
        ):
            im_teardown.add(k.args[1].value)

    vergessen = sorted(gestartet - im_teardown)
    assert not vergessen, (
        "In core/engine/base.py wird im Boot gestartet, kommt im Teardown aber nicht "
        f"vor: {vergessen}\n"
        "Wer einen Hintergrund-Thread startet, muss im selben Change den Stopp "
        "anfuegen. Genau diese Asymmetrie ist mit #2408 entstanden und bis #3438 "
        "unbemerkt geblieben (RCA §4)."
    )
