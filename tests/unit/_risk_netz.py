"""#4263 (H-3a) — Netz für Konto, Halt, Liquidation und Vorprüfung des RiskManager.

Plan: ``docs/4263-*/implementation_plan.md`` §2. Gemessen wird, was den Broker-Client
erreicht, was der Halt anzeigt, welche Risiko-Ereignisse gemeldet werden und welcher Span
entsteht. Echt laufen: ``RiskManager``, ``LokalerHalt``, das Tor (``gateway_for``,
``liquidate_positions``, ``OrderGateway``) mit seinem späten Import. Ersetzt sind nur der
Broker-Client, die Uhr, der globale Kill-Switch (Spion) und die Beobachtungs-Senken am
Modulobjekt ``core.risk_manager`` — so übersteht das Netz die Umzüge H-3c und H-3g.

Aufruf zum Neuschreiben der Referenz (nur mit Begründung im PR):

    python tests/unit/_risk_netz.py --schreibe
"""

from __future__ import annotations

import enum
import importlib
import json
import logging
import re
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

REFERENZ = _AI_BOT / "tests" / "fixtures" / "risk_netz_referenz_4263.json"

#: Entscheidung §3: die Namen, die das Netz patchen darf — am Modulobjekt
#: ``core.risk_manager``, das Klassenattribut ``AILearnedRules.get_rules`` und den globalen
#: Kill-Switch am Modulobjekt ``core.kill_switch``. Nichts unter ``core.engine`` und
#: ``core.gateway``: H-1h (#4237) zieht das Tor um.
ERLAUBTE_PATCH_ZIELE = frozenset(
    {
        "core.risk_manager.tracer",
        "core.risk_manager.CLOUD_LOGGING_AVAILABLE",
        "core.risk_manager.cloud_log_risk_event",
        "core.ai_rules.AILearnedRules.get_rules",
        "core.kill_switch.kill_switch",
    }
)

#: Was das Netz tatsächlich patcht, als ``(Objektpfad, Name)``. Der Wächter prüft es gegen
#: die Tabelle und dass der Treiber an keiner anderen Stelle patcht.
PATCH_ZIELE = (
    ("core.risk_manager", "tracer"),
    ("core.risk_manager", "CLOUD_LOGGING_AVAILABLE"),
    ("core.risk_manager", "cloud_log_risk_event"),
    ("core.ai_rules.AILearnedRules", "get_rules"),
    ("core.kill_switch", "kill_switch"),
)

START = 100_000.0  # Tages- und Sitzungsstart; Tages-Limit 17,5 % = 17.500 $
NUTZER = "netz"
T0 = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)

#: Zwei gehaltene Positionen. MSFT hat 1 Stück in einer offenen Order gebunden:
#: ``qty_available`` hat beim Schließen Vorrang (``liquidation.py::_menge``).
POSITIONEN = (
    ("AAPL", "10", "10"),
    ("MSFT", "5", "4"),
)

#: Die Funktionen, deren Aufruf das Netz mitschneidet (``sys.setprofile``, kein Patch).
BEOBACHTET = frozenset(
    {
        "update_account_equity",
        "_liquidate_through_gateway",
        "liquidate_positions",
        "reset_daily_limit",
        "evaluate_new_trade",
        "resolve_vix",
        "submit_with_result",
    }
)

#: Logzeilen, die zum Verhalten gehören — nach Funktionsname, nicht nach Datei, damit ein
#: Umzug in ein Mixin sie nicht verliert.
_LOG_FUNKTIONEN = frozenset(
    {
        "update_account_equity",
        "_liquidate_through_gateway",
        "reset_daily_limit",
        "evaluate_new_trade",
        "resolve_vix",
        "liquidate_positions",
        "trip",
    }
)
_TOR_FUNKTION = "record_gateway_decision"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass(frozen=True)
class Szenario:
    """Ein Szenario: Aufbau und eine Folge von Aufrufen.

    Schritte: ``("konto", eigenkapital, allow_unlock, stunden_seit_t0)``,
    ``("reset", eigenkapital)``, ``("pruefe", symbol, seite, market_data)``.
    """

    name: str
    schritte: tuple
    zweige: tuple = ()
    portfolio_stop: float = 0.07
    positionsabfrage: bool = True
    regeln: tuple = ()
    halt_vorher: bool = False


_BREAKER = ("konto", 80_000.0, True, 0)  # 20.000 $ Drawdown > 17.500 $ Tages-Limit
_REGEL_BLOCK = {
    "id": "netz-1",
    "trigger": {"side": "buy"},
    "action": "block_trade",
    "reason": "Netz H-3a",
    "status": "active",
}

SZENARIEN: tuple[Szenario, ...] = (
    # (1) 10 % unter Sitzungsstart >= 7 %; 10.000 $ Drawdown < 60 % des Limits.
    Szenario(
        "portfolio_stop",
        (("konto", 90_000.0, True, 0),),
        zweige=("update_account_equity",),
    ),
    # (2) Portfolio-Stop aus: nur der Tages-Breaker, Liquidation durch das echte Tor.
    Szenario(
        "tages_limit",
        (_BREAKER,),
        zweige=(
            "update_account_equity",
            "_liquidate_through_gateway",
            "liquidate_positions",
            "submit_with_result",
        ),
        portfolio_stop=0.0,
    ),
    # (2b) Der Broker kennt get_all_positions nicht: nichts wird geschlossen.
    Szenario(
        "tages_limit_ohne_positionsabfrage",
        (_BREAKER,),
        zweige=("update_account_equity", "_liquidate_through_gateway"),
        portfolio_stop=0.0,
        positionsabfrage=False,
    ),
    # (3) 12.000 $ = 69 % des Limits -> Warnstufe; 8.000 $ = 46 % -> zurück.
    Szenario(
        "warnstufe",
        (("konto", 88_000.0, True, 0), ("konto", 92_000.0, True, 0)),
        zweige=("update_account_equity",),
        portfolio_stop=0.0,
    ),
    # (4a) 5.000 $ <= 50 % von 17.500 $ -> frei.
    Szenario(
        "erholung_frei",
        (_BREAKER, ("konto", 95_000.0, True, 0)),
        zweige=("update_account_equity",),
        portfolio_stop=0.0,
    ),
    # (4b) Dieselbe Lage, Live-Pfad ohne Entsperren.
    Szenario(
        "erholung_gesperrt",
        (_BREAKER, ("konto", 95_000.0, False, 0)),
        zweige=("update_account_equity",),
        portfolio_stop=0.0,
    ),
    # (4c) 3 h nach dem Halt gilt 30 % = 5.250 $: 7.000 $ entsperren nicht mehr
    # (bei 50 % = 8.750 $ hätten sie entsperrt).
    Szenario(
        "erholung_nach_3h",
        (_BREAKER, ("konto", 93_000.0, True, 3)),
        zweige=("update_account_equity",),
        portfolio_stop=0.0,
    ),
    # (4c) 5 h nach dem Halt gilt 20 % = 3.500 $: 5.000 $ entsperren nicht mehr
    # (bei 30 % = 5.250 $ hätten sie entsperrt).
    Szenario(
        "erholung_nach_5h",
        (_BREAKER, ("konto", 95_000.0, True, 5)),
        zweige=("update_account_equity",),
        portfolio_stop=0.0,
    ),
    # (4d) Nach einem Portfolio-Stop: volle Erholung entsperrt nicht.
    Szenario(
        "erholung_nach_portfolio_stop",
        (("konto", 90_000.0, True, 0), ("konto", START, True, 0)),
        zweige=("update_account_equity",),
    ),
    # (5) Neues Tages-Limit nach dem Breaker; dann ``None`` -> 0.0 mit WARNING.
    Szenario(
        "reset_daily_limit",
        (_BREAKER, ("reset", 90_000.0), ("reset", None)),
        zweige=("update_account_equity", "reset_daily_limit"),
        portfolio_stop=0.0,
    ),
    # (6) Ohne VIX: Kauf abgelehnt, Verkauf frei (Regelschleife mit VIX 0.0).
    Szenario(
        "vix_unbestaetigt",
        (("pruefe", "AAPL", "buy", {}), ("pruefe", "AAPL", "sell", {})),
        zweige=("evaluate_new_trade", "resolve_vix"),
        regeln=(_REGEL_BLOCK,),
    ),
    # (7) Regel blockiert den Kauf; der Verkauf passt nicht auf die Regel.
    Szenario(
        "ki_regel_blockiert",
        (
            ("pruefe", "AAPL", "buy", {"vix": 20.0}),
            ("pruefe", "AAPL", "sell", {"vix": 20.0}),
        ),
        zweige=("evaluate_new_trade", "resolve_vix"),
        regeln=(_REGEL_BLOCK,),
    ),
    # (8) Eigener Halt gesetzt: Ablehnung vor dem VIX und vor der Regelschleife.
    Szenario(
        "halt_ausgang",
        (("pruefe", "AAPL", "buy", {"vix": 20.0}),),
        zweige=("evaluate_new_trade",),
        regeln=(_REGEL_BLOCK,),
        halt_vorher=True,
    ),
)


# ---------------------------------------------------------------------------
# Außenwelt
# ---------------------------------------------------------------------------


class _Uhr:
    """``ClockPort`` mit fest eingestellter Zeit."""

    def __init__(self):
        self.jetzt = T0

    def now(self, tz=None):
        return self.jetzt


class _GlobalerSpion:
    """Steht für den globalen Kill-Switch und merkt sich jede Berührung."""

    def __init__(self):
        self.trips = 0
        self.resets = 0

    def trip(self, reason, user_id=None, access_token=None, fail_closed=True):
        self.trips += 1

    def reset(self, user_id=None):
        self.resets += 1

    def is_halted(self, user_id=None):
        return False

    def check_halt(self, user_id=None):
        return None


class _Client:
    """Broker-Client: liefert zwei Positionen und schreibt jeden Aufruf mit."""

    def __init__(self, positionsabfrage: bool):
        self.aufrufe: list = []
        self._positionsabfrage = positionsabfrage

    def __getattr__(self, name):
        if name.startswith("_") or (
            name == "get_all_positions" and not self._positionsabfrage
        ):
            raise AttributeError(name)

        def _aufruf(*args, **kwargs):
            self.aufrufe.append((name, args, kwargs))
            if name == "get_all_positions":
                return [
                    SimpleNamespace(symbol=s, qty=q, qty_available=v)
                    for s, q, v in POSITIONEN
                ]
            return SimpleNamespace(id=f"order-{len(self.aufrufe)}")

        return _aufruf


class _Mitschnitt(logging.Handler):
    """Logzeilen der beobachteten Funktionen, ohne UUID."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.zeilen: list = []
        self.tor: list = []

    def emit(self, record):
        if record.funcName == _TOR_FUNKTION:
            decision_id, approved, grund, halted, _detail = record.args
            self.tor.append(
                {
                    "decision_id": _ohne_uuid(decision_id),
                    "freigegeben": bool(approved),
                    "grund": grund,
                    "gehalten": bool(halted),
                }
            )
        elif record.funcName in _LOG_FUNKTIONEN and record.levelno >= logging.WARNING:
            self.zeilen.append(
                [record.levelname, record.funcName, _ohne_uuid(record.getMessage())]
            )


def _ohne_uuid(text) -> str:
    return _UUID.sub("<uuid4>", str(text))


# ---------------------------------------------------------------------------
# Normalisierung
# ---------------------------------------------------------------------------


def _wert(v):
    """JSON-fester, adressfreier Wert."""
    if isinstance(v, enum.Enum):
        return _wert(v.value)
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, (str, int, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {str(k): _wert(x) for k, x in sorted(v.items(), key=lambda kv: str(kv))}
    if isinstance(v, (list, tuple)):
        return [_wert(x) for x in v]
    return type(v).__name__


_ORDER_FELDER = ("symbol", "side", "qty", "type", "time_in_force")


def _order(req) -> dict:
    aus = {f: _wert(getattr(req, f, None)) for f in _ORDER_FELDER}
    if aus["qty"] is not None:
        aus["qty"] = float(aus["qty"])
    # Abgeleitet aus ``breaker-<uuid4>`` + Symbol: gemessen wird, dass er gesetzt ist.
    aus["client_order_id"] = (
        "abgeleitet" if getattr(req, "client_order_id", None) else None
    )
    aus["art"] = type(req).__name__
    return aus


def _broker(client: _Client, ab: int) -> list:
    aus = []
    for name, args, kwargs in client.aufrufe[ab:]:
        if name == "submit_order":
            req = args[0] if args else kwargs.get("order_data")
            aus.append({"methode": name, "order": _order(req)})
        else:
            aus.append({"methode": name, "args": [_wert(a) for a in args]})
    return aus


_ZUSTAND = (
    "trading_halted",
    "trading_reduced",
    "halt_trigger_count",
    "unlock_recovery_percent",
    "daily_drawdown_limit",
    "_portfolio_stop_triggered",
    "portfolio_stop_loss_pct",
)


def _zustand(rm) -> dict:
    aus = {k: _wert(getattr(rm, k)) for k in _ZUSTAND}
    aus["last_halt_time"] = (
        None if rm.last_halt_time is None else rm.last_halt_time.isoformat()
    )
    return aus


# ---------------------------------------------------------------------------
# Fahren
# ---------------------------------------------------------------------------


def baue(sz: Szenario):
    """Echter ``RiskManager`` mit eigenem Halt, gefälschtem Client und fester Uhr."""
    from core.kill_switch import LokalerHalt
    from core.risk_manager import RiskManager

    client = _Client(sz.positionsabfrage)
    halt = LokalerHalt()
    rm = RiskManager(
        client,
        START,
        daily_drawdown_limit_percent=0.175,
        user_id=NUTZER,
        kill_switch=halt,
        clock=_Uhr(),
    )
    rm.session_start_equity = START
    rm.portfolio_stop_loss_pct = sz.portfolio_stop
    if sz.halt_vorher:
        halt.trip(reason="Netz H-3a: vorher gesetzt", user_id=NUTZER)
    return rm, client, halt


def _ziel(pfad: str):
    """``"a.b.C"`` -> Modul ``a.b`` bzw. Klasse ``C`` darin."""
    try:
        return importlib.import_module(pfad)
    except ModuleNotFoundError:
        modul, _, name = pfad.rpartition(".")
        return getattr(importlib.import_module(modul), name)


@contextmanager
def _aufrufe_mitschneiden(gelaufen: dict):
    def _profil(frame, ereignis, _arg):
        if ereignis == "call" and frame.f_code.co_name in BEOBACHTET:
            name = frame.f_code.co_name
            gelaufen[name] = gelaufen.get(name, 0) + 1

    vorher = sys.getprofile()
    sys.setprofile(_profil)
    try:
        yield
    finally:
        sys.setprofile(vorher)


def _vorwaermen() -> None:
    """Erstimporte und Singleton-Aufbau vor der Messung: ihre einmaligen Logzeilen
    gehören nicht ins Netz (sonst wäre der erste Lauf anders als der dritte)."""
    import core.engine.order_executor  # noqa: F401 — später Import im Breaker
    import core.gateway  # noqa: F401
    from core.ai_rules import AILearnedRules

    AILearnedRules()


def fahre(sz: Szenario) -> dict:
    """Ein Szenario fahren. Liefert das Messergebnis als dict."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    _vorwaermen()
    exporter = InMemorySpanExporter()
    anbieter = TracerProvider()
    anbieter.add_span_processor(SimpleSpanProcessor(exporter))
    ereignisse: list = []
    spion = _GlobalerSpion()

    def _risiko_ereignis(*args, **kwargs):
        ereignisse.append(
            {"event_type": kwargs.get("event_type"), "severity": kwargs.get("severity")}
        )

    werte = {
        ("core.risk_manager", "tracer"): anbieter.get_tracer("core.risk_manager"),
        ("core.risk_manager", "CLOUD_LOGGING_AVAILABLE"): True,
        ("core.risk_manager", "cloud_log_risk_event"): _risiko_ereignis,
        ("core.ai_rules.AILearnedRules", "get_rules"): (
            lambda self, _r=[dict(r) for r in sz.regeln]: [dict(r) for r in _r]
        ),
        ("core.kill_switch", "kill_switch"): spion,
    }

    rm, client, halt = baue(sz)
    mitschnitt = _Mitschnitt()
    wurzel = logging.getLogger()
    alte_stufe = wurzel.level
    gelaufen: dict = {}
    schritte: list = []

    with ExitStack() as st:
        for ziel in PATCH_ZIELE:
            st.enter_context(patch.object(_ziel(ziel[0]), ziel[1], werte[ziel]))
        wurzel.addHandler(mitschnitt)
        wurzel.setLevel(logging.INFO)
        st.callback(wurzel.setLevel, alte_stufe)
        st.callback(wurzel.removeHandler, mitschnitt)
        st.enter_context(_aufrufe_mitschneiden(gelaufen))

        for schritt in sz.schritte:
            ab_client = len(client.aufrufe)
            ab_ereignis = len(ereignisse)
            ab_log = len(mitschnitt.zeilen)
            ab_tor = len(mitschnitt.tor)
            exporter.clear()
            ergebnis = None
            art = schritt[0]
            if art == "konto":
                _, eigenkapital, entsperren, stunden = schritt
                rm.clock.jetzt = T0 + timedelta(hours=stunden)
                rm.update_account_equity(eigenkapital, allow_unlock=entsperren)
                aufruf = (
                    f"update_account_equity({eigenkapital}, {entsperren}) +{stunden}h"
                )
            elif art == "reset":
                rm.reset_daily_limit(schritt[1])
                aufruf = f"reset_daily_limit({schritt[1]})"
            else:
                _, symbol, seite, markt = schritt
                ergebnis = _wert(rm.evaluate_new_trade(symbol, seite, dict(markt), 3.0))
                aufruf = f"evaluate_new_trade({symbol}, {seite}, {markt})"
            schritte.append(
                {
                    "aufruf": aufruf,
                    "ergebnis": ergebnis,
                    "broker": _broker(client, ab_client),
                    "tor": mitschnitt.tor[ab_tor:],
                    "ereignisse": ereignisse[ab_ereignis:],
                    "log": mitschnitt.zeilen[ab_log:],
                    "zustand": _zustand(rm),
                    "eigener_halt": halt.is_halted(NUTZER),
                    "spans": [
                        {"name": s.name, "attribute": _wert(dict(s.attributes))}
                        for s in exporter.get_finished_spans()
                    ],
                }
            )

    return {
        "schritte": schritte,
        "globaler_halt": {"trips": spion.trips, "resets": spion.resets},
        "gelaufen": {k: gelaufen[k] for k in sorted(gelaufen)},
    }


def messwerte(ergebnis: dict) -> int:
    """Zahl der Messwerte eines Szenarios (Leerlauf-Wächter)."""
    return sum(
        len(s["broker"])
        + len(s["ereignisse"])
        + len(s["spans"])
        + len(s["log"])
        + (s["ergebnis"] is not None)
        for s in ergebnis["schritte"]
    )


# ---------------------------------------------------------------------------
# Befunde gegen die Referenz
# ---------------------------------------------------------------------------

NEU = "NEU"
FEHLT = "FEHLT"
ABWEICHUNG = "ABWEICHUNG"


def messe_alle(szenarien=SZENARIEN) -> dict:
    return {sz.name: fahre(sz) for sz in szenarien}


def befunde(referenz: dict, ist: dict) -> list[tuple[str, str, str]]:
    """(Art, Szenario, Text) je Abweichung. Leere Liste = Verhalten unverändert."""
    aus = []
    for name in sorted(set(referenz) - set(ist)):
        aus.append((FEHLT, name, "Referenz ohne gemessenes Szenario."))
    for name, jetzt in ist.items():
        alt = referenz.get(name)
        if alt is None:
            aus.append((NEU, name, "Szenario ohne Referenz — Referenz neu schreiben."))
            continue
        for feld in sorted(set(alt) | set(jetzt)):
            if feld == "schritte":
                continue
            if alt.get(feld) != jetzt.get(feld):
                aus.append(
                    (ABWEICHUNG, name, f"{feld}: {alt.get(feld)} -> {jetzt.get(feld)}")
                )
        alte, neue = alt.get("schritte", []), jetzt.get("schritte", [])
        if len(alte) != len(neue):
            aus.append((ABWEICHUNG, name, f"Schritte {len(alte)} -> {len(neue)}"))
        for i, (a, n) in enumerate(zip(alte, neue)):
            for feld in sorted(set(a) | set(n)):
                if a.get(feld) != n.get(feld):
                    aus.append(
                        (
                            ABWEICHUNG,
                            name,
                            f"Schritt {i} {feld}: "
                            f"{json.dumps(a.get(feld), sort_keys=True, ensure_ascii=False)}"
                            f" -> "
                            f"{json.dumps(n.get(feld), sort_keys=True, ensure_ascii=False)}",
                        )
                    )
    return aus


def veraendere_eine_menge(ist: dict) -> bool:
    """Gegenprobe: die Menge der ersten Breaker-Order um ein Stück verändern."""
    for schritt in ist["tages_limit"]["schritte"]:
        for aufruf in schritt["broker"]:
            if aufruf["methode"] == "submit_order":
                aufruf["order"]["qty"] += 1
                return True
    return False


def veraendere_den_halt(ist: dict) -> bool:
    """Gegenprobe: der Halt nach dem Tages-Limit stünde auf „nicht gehalten"."""
    zustand = ist["tages_limit"]["schritte"][-1]["zustand"]
    if zustand["trading_halted"] is not True:
        return False
    zustand["trading_halted"] = False
    return True


def lade_referenz() -> dict:
    return json.loads(REFERENZ.read_text(encoding="utf-8"))


def schreibe_referenz(ist: dict) -> None:
    REFERENZ.write_text(
        json.dumps(ist, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    gemessen = messe_alle()
    if "--schreibe" in sys.argv:
        schreibe_referenz(gemessen)
        print(f"geschrieben: {REFERENZ}")
    else:
        print(json.dumps(gemessen, indent=2, sort_keys=True, ensure_ascii=False))
