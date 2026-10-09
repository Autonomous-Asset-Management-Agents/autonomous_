"""#3833 (G-7a) — Geld-Gate fuer den Strategie-Pfad: feste Lagen durch den ECHTEN Code.

Plan: ``docs/3833-geld-gate-szenario-fuer-den-strategie-pfad-dann/implementation_plan.md``.

Gemessen wird wie im Geld-Gate aus #3392 (``_geld_gate.py``) die Menge, die den Broker
erreicht (``client.submit_order``) — hier zusaetzlich der Datensatz des Tors (Halt-Prüfung
inklusive) und der verbrauchte Platz im Tagesbudget. Echt laufen:
``RLExecutionMixin._run_for_symbol_impl``, ``BaseStrategy._submit_order_safe``, der
``RiskManager`` (Sizer, Halt-Abfrage), der ``ComplianceGuardian`` (Regelwerk, Tagesbudget),
das ``OrderGateway`` und ``kill_switch.check_halt``. Ersetzt sind nur die Modell-Teilschritte
(Merkmale, Signal, Ausstiegsregel), der Broker-Client, die AI-Regeln, die Uhr und die
Halt-Quelle.

Ein Szenario faehrt entweder ``_run_for_symbol_impl`` (``einstieg="lauf"``) oder
``_submit_order_safe`` direkt (``einstieg="absendung"``) — Letzteres dort, wo eine Pruefung
davor die Lage schon abfinge (bei Halt verwirft ``evaluate_new_trade`` jede Seite) und die
Pruefung auf dem Absendeweg sonst ungemessen bliebe.

Aufruf zum Neuschreiben der Referenz (nur mit Begruendung im PR, der Diff IST das Geld):

    python tests/unit/_geld_gate_strategie.py --schreibe
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

_HIER = Path(__file__).resolve().parent
if str(_HIER) not in sys.path:
    sys.path.insert(0, str(_HIER))

import _geld_gate  # noqa: E402

REFERENZ = _AI_BOT / "tests" / "fixtures" / "geld_gate_strategie_referenz_3833.json"

DATENSATZ, HALT, BUDGET = "datensatz", "halt", "budget"
ASPEKTE = (DATENSATZ, HALT, BUDGET)
# Die fuenf Wege, auf denen ``_submit_order_safe`` den Broker erreicht.
ZWEIGE = ("MarketOrderRequest", "LimitOrderRequest", "kwargs", "simulation", "async")

SYMBOL = "GATE"
_STOP = {"triggered": True, "tier": "risk", "signal": "SELL"}


@dataclass(frozen=True)
class Szenario:
    name: str
    prueft: tuple  # welche Aspekte das Szenario abdeckt (Abdeckungs-Abgleich)
    einstieg: str = (
        "lauf"  # "lauf" = _run_for_symbol_impl, "absendung" = _submit_order_safe
    )
    client: str = "alpaca"  # alpaca | kwargs | simulation | async
    signal: str = "BUY"  # Ausgang von _evaluate_signal (nur "lauf")
    ausstieg: dict | None = None  # Ausgang von _check_exit (nur "lauf")
    gehalten: float = 0.0  # Bestand beim Broker
    einstand: float = 120.0
    seite: str = "buy"  # nur "absendung"
    menge: float = 10.0  # nur "absendung"
    schutz_exit: bool = False  # nur "absendung"
    halt: bool = False
    budget_erschoepft: bool = False
    equity: float = 100_000.0
    cash: float = 100_000.0
    preis: float = 100.0
    atr: float = 1.0
    config: dict = field(default_factory=dict)  # Attribute des Moduls ``config``
    laufzeit: dict = field(default_factory=dict)  # Attribute von ``get_config()``


SZENARIEN: tuple[Szenario, ...] = (
    # Der Normalfall: Modell sagt BUY, der echte Sizer bemisst, Markt-Order durchs Tor.
    Szenario("lauf_kauf_markt", (DATENSATZ, BUDGET)),
    Szenario("lauf_kauf_limit", (DATENSATZ,), config={"USE_LIMIT_ORDERS": True}),
    # Clients ohne ``order_data`` (Altclients) bekommen Schluesselwort-Argumente.
    Szenario("lauf_kauf_kwargs", (DATENSATZ,), client="kwargs"),
    # Der Stop-Loss der Default-Strategie: Risiko-Ausstieg aus dem Bestand.
    Szenario(
        "lauf_stop_loss",
        (DATENSATZ, BUDGET),
        signal="HOLD",
        ausstieg=_STOP,
        gehalten=10.0,
    ),
    # Ein Modell-Verkauf ohne Ausloeser — kein Schutz-Exit.
    Szenario("lauf_modell_verkauf", (DATENSATZ,), signal="SELL", gehalten=10.0),
    # Halt im Lauf: ``evaluate_new_trade`` (risk_vorpruefung.py::_vorpruefung_halt) verwirft jede Seite,
    # bevor ``_submit_order_safe`` erreicht wird — auch den Stop-Loss (BEFUND_PLAN.md).
    Szenario("lauf_kauf_bei_halt", (HALT,), halt=True),
    Szenario(
        "lauf_stop_loss_bei_halt",
        (HALT,),
        signal="HOLD",
        ausstieg=_STOP,
        gehalten=10.0,
        halt=True,
    ),
    Szenario(
        "lauf_modell_verkauf_bei_halt",
        (HALT,),
        signal="SELL",
        gehalten=10.0,
        halt=True,
    ),
    # Halt direkt am Absendeweg — ohne Risikopruefung davor: Kauf geblockt, Schutz-Exit frei.
    Szenario("absendung_kauf_bei_halt", (HALT,), einstieg="absendung", halt=True),
    Szenario(
        "absendung_stop_loss_bei_halt",
        (HALT, DATENSATZ),
        einstieg="absendung",
        seite="sell",
        gehalten=10.0,
        schutz_exit=True,
        halt=True,
    ),
    # Tagesbudget ausgeschoepft.
    Szenario(
        "absendung_kauf_budget_erschoepft",
        (BUDGET,),
        einstieg="absendung",
        budget_erschoepft=True,
    ),
    Szenario(
        "lauf_stop_loss_budget_erschoepft",
        (BUDGET,),
        signal="HOLD",
        ausstieg=_STOP,
        gehalten=10.0,
        budget_erschoepft=True,
    ),
    # Die beiden Wege am Tor vorbei (vertrag.toml, [broker_aufrufer.ausnahmen]).
    Szenario("lauf_kauf_simulation", (DATENSATZ, BUDGET), client="simulation"),
    # Der async-Zweig prueft den Halt nicht: festgehalten, nicht bewertet (BEFUND_PLAN.md).
    Szenario(
        "absendung_async_bei_halt",
        (HALT, DATENSATZ),
        einstieg="absendung",
        client="async",
        halt=True,
    ),
)


# ---------------------------------------------------------------------------
# Broker-Clients: die vier Formen, die ``_submit_order_safe`` unterscheidet
# ---------------------------------------------------------------------------


class _Konto:
    """Konto, Uhr und Bestand — allen Client-Formen gemeinsam."""

    def __init__(self, sz: Szenario, gesendet: list):
        self._sz = sz
        self.gesendet = gesendet

    def get_account(self):
        return SimpleNamespace(
            cash=self._sz.cash,
            equity=self._sz.equity,
            buying_power=self._sz.cash,
            daytrading_buying_power=self._sz.cash,
            pattern_day_trader=False,
        )

    def get_clock(self):
        return SimpleNamespace(is_open=True)

    def list_orders(self, status="open"):
        return []

    def get_all_positions(self):
        return []

    def get_open_position(self, symbol):
        if not self._sz.gehalten:
            return None
        return SimpleNamespace(qty=self._sz.gehalten, avg_entry_price=self._sz.einstand)

    def _merke(self, weg, seite, menge, tif=None, limit=None):
        self.gesendet.append(
            {
                "weg": weg,
                "seite": str(seite),
                "menge": round(float(menge), 6),
                "notional": round(float(menge) * self._sz.preis, 2),
                "tif": tif,
                "limit": limit,
            }
        )
        return SimpleNamespace(id=f"order-{len(self.gesendet)}")


class _AlpacaClient(_Konto):
    """Die produktive Form: ``submit_order(order_data)`` wie ``TradingClient``."""

    def submit_order(self, order_data):
        return self._merke(
            type(order_data).__name__,
            getattr(order_data.side, "value", order_data.side),
            order_data.qty,
            getattr(order_data.time_in_force, "value", order_data.time_in_force),
            getattr(order_data, "limit_price", None),
        )


class _KwargsClient(_Konto):
    def submit_order(self, symbol, qty, side, **rest):
        return self._merke(
            "kwargs", side, qty, rest.get("time_in_force"), rest.get("limit_price")
        )


class SimulationClient(_Konto):
    """Der Name traegt "Simulation" — daran erkennt ``_submit_order_safe`` den Sim-Zweig."""

    def submit_order(self, symbol, qty, side):
        return self._merke("simulation", side, qty)


class _AsyncClient(_Konto):
    """``submit_order`` als Koroutine — ``_submit_order_safe`` erkennt sie per
    ``inspect.iscoroutinefunction`` und ruft sie ohne Tor."""

    def __init__(self, sz: Szenario, gesendet: list):
        super().__init__(sz, gesendet)
        self.submit_order = AsyncMock(
            side_effect=lambda symbol, qty, side: self._merke("async", side, qty)
        )


_CLIENTS = {
    "alpaca": _AlpacaClient,
    "kwargs": _KwargsClient,
    "simulation": SimulationClient,
    "async": _AsyncClient,
}


# ---------------------------------------------------------------------------
# Die Strategie: echter Ausfuehrungs- und Absendepfad, Modell-Teilschritte fest
# ---------------------------------------------------------------------------


def _strategie(sz: Szenario, client, rm, guardian):
    import numpy as np
    import pandas as pd

    from core.strategies.base import BaseStrategy
    from core.strategies.rl_execution import RLExecutionMixin

    merkmale = pd.DataFrame([{"close": sz.preis, "atr_14d": sz.atr, "atr_14": sz.atr}])

    class _Strategie(RLExecutionMixin, BaseStrategy):
        async def run_for_symbol(self, symbol, ohlc_data, market_data, current_time):
            return await self._run_for_symbol_impl(
                symbol, ohlc_data, market_data, current_time
            )

        async def evaluate_for_symbol(self, *args, **kwargs):
            return None

        def _update_vix_from_market_data(self, market_data):
            pass

        def _calculate_conviction_score(self, features, pred, market_data):
            return 0.8

        def _generate_thought(self, *args, **kwargs):
            pass

    strategie = _Strategie(
        client=client,
        symbols=[SYMBOL],
        running_event=None,
        total_capital=sz.equity,
        risk_manager=rm,
        data_provider=MagicMock(),
        thought_callback=lambda text: None,
        compliance_guardian=guardian,
    )
    strategie.high_water_marks = {}
    strategie._entry_time = {}
    strategie.portfolio_manager = None
    strategie.trade_intelligence = None
    strategie._rl_model_version = "geld-gate"
    strategie._get_current_state = AsyncMock(
        return_value=(np.zeros(12, dtype=np.float32), merkmale, 0.55)
    )
    strategie._evaluate_signal = AsyncMock(
        return_value={"signal": sz.signal, "raw_rl_action": 1, "rl_action": 1}
    )
    strategie._check_exit = MagicMock(
        return_value=dict(sz.ausstieg or {"triggered": False, "signal": "HOLD"})
    )
    return strategie


def fahre(sz: Szenario) -> dict:
    """Ein Szenario durch den Strategie-Pfad. Liefert das Messergebnis als dict."""
    import config
    from core import kill_switch as ks_mod
    from core.compliance import ComplianceGuardian
    from core.gateway import fabrik
    from core.risk_manager import RiskManager

    gesendet: list = []
    datensaetze: list = []
    client = _CLIENTS[sz.client](sz, gesendet)

    with ExitStack() as st:
        for k, v in sz.config.items():
            st.enter_context(patch.object(config, k, v, create=True))
        for k, v in _geld_gate._laufzeit(sz).items():
            st.enter_context(patch.object(config.get_config(), k, v, create=True))
        # Die Halt-Quelle; ``check_halt`` bleibt echt und fragt sie.
        st.enter_context(
            patch.object(
                type(ks_mod.kill_switch),
                "is_halted",
                lambda self, user_id=None: sz.halt,
            )
        )
        st.enter_context(
            patch.object(fabrik, "record_gateway_decision", datensaetze.append)
        )

        rm = RiskManager(
            client=client, total_capital=sz.equity, clock=_geld_gate._uhr()
        )
        rm.ai_rules_singleton = MagicMock(get_rules=MagicMock(return_value=[]))
        guardian = ComplianceGuardian()
        guardian._budget_ablage = None  # nur im Arbeitsspeicher zaehlen
        guardian.daily_trades = guardian.max_daily_trades if sz.budget_erschoepft else 0
        vorher = guardian.daily_trades
        strategie = _strategie(sz, client, rm, guardian)

        if sz.einstieg == "lauf":
            ereignis = asyncio.run(
                strategie.run_for_symbol(
                    SYMBOL,
                    {
                        "open": sz.preis,
                        "high": sz.preis,
                        "low": sz.preis,
                        "close": sz.preis,
                        "volume": 1_000_000,
                    },
                    {"vix": 20.0, "regime": "Normal"},
                    datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc),
                )
            )
            ergebnis = getattr(ereignis, "action", None)
        else:
            ergebnis = asyncio.run(
                strategie._submit_order_safe(
                    SYMBOL,
                    sz.menge,
                    sz.seite,
                    expected_cost=sz.menge * sz.preis if sz.seite == "buy" else 0.0,
                    current_price=sz.preis,
                    held_qty=sz.gehalten,
                    is_protective_exit=sz.schutz_exit,
                )
            )

    return {
        "gesendet": gesendet,
        "datensatz": [
            d.reason_code.value + (":halt" if d.halted else "") for d in datensaetze
        ],
        "budget": guardian.daily_trades - vorher,
        "ergebnis": ergebnis,
    }


# ---------------------------------------------------------------------------
# Befunde gegen die Referenz
# ---------------------------------------------------------------------------

GELDWIRKUNG = "GELDWIRKUNG"
PFAD_WECHSEL = "PFAD-WECHSEL"
NEU = "NEU"


def messe_alle(szenarien=SZENARIEN) -> dict:
    return {sz.name: fahre(sz) for sz in szenarien}


def befunde(referenz: dict, ist: dict) -> list[tuple[str, str, str]]:
    """(Art, Szenario, Text) je Abweichung. Leere Liste = der Pfad ist unveraendert.

    GELDWIRKUNG: was den Broker erreicht, hat sich geaendert (Weg, Seite, Menge, Limit).
    PFAD-WECHSEL: dasselbe Geld, aber Datensatz, Budget oder Ausgang sind andere — etwa
    eine Halt-Pruefung, die jetzt an anderer Stelle greift.
    """
    aus = []
    for name, jetzt in ist.items():
        alt = referenz.get(name)
        if alt is None:
            aus.append((NEU, name, "Szenario ohne Referenz — Referenz neu schreiben."))
            continue
        if jetzt["gesendet"] != alt["gesendet"]:
            aus.append(
                (
                    GELDWIRKUNG,
                    name,
                    f"gesendet {alt['gesendet']} -> {jetzt['gesendet']}",
                )
            )
            continue
        for feld in ("datensatz", "budget", "ergebnis"):
            if jetzt[feld] != alt[feld]:
                aus.append(
                    (PFAD_WECHSEL, name, f"{feld} {alt[feld]!r} -> {jetzt[feld]!r}")
                )
    return aus


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
        for name, wert in gemessen.items():
            print(name, wert)
