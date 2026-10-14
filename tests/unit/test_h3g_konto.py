"""#4269 (H-3g+H-3h) — Konto, Halt und Liquidation wohnen in ``core/risk_konto.py``.

Plan: ``docs/4269-*/implementation_plan.md`` §5. Schnitt-Entscheidung #4185:
``docs/3738-arc-e6-gestalt/H3_SCHNITT_risk_manager.md`` §2/§3, Abschnitte H-3g und H-3h.

``_liquidate_through_gateway``, ``_halt``, ``update_account_equity`` und ``reset_daily_limit``
ziehen als ``KontoHaltMixin`` um. Der Kern-Import steht am Dateiende; ``CLOUD_LOGGING_AVAILABLE``
und ``cloud_log_risk_event`` bleiben Patch-Ziele am Modulobjekt ``core.risk_manager``.
Danach zerfällt ``update_account_equity`` in fünf benannte Schritte (H-3h); die
Charakterisierung hält vorher fest, was die Zerlegung nicht ändern darf.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc4]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
KONTO = PAKET / "core" / "risk_konto.py"
NUTZER = "nutzer-4269"

METHODEN = (
    "_liquidate_through_gateway",
    "_halt",
    "update_account_equity",
    "reset_daily_limit",
)


class _Uhr:
    def now(self, tz=None):
        import datetime

        return datetime.datetime(2026, 10, 8, 15, tzinfo=datetime.timezone.utc)


def _rm(client=None, halt=None):
    """Echter ``RiskManager`` mit lokalem Halt — der globale Kill-Switch bleibt unberührt.

    Tages-Limit 17.500 $ auf 100.000 $; der Portfolio-Stop ist aus, wenn nicht gesetzt.
    """
    from core.kill_switch import LokalerHalt
    from core.risk_manager import RiskManager

    rm = RiskManager(
        client if client is not None else MagicMock(),
        100_000.0,
        daily_drawdown_limit_percent=0.175,
        user_id=NUTZER,
        kill_switch=halt if halt is not None else LokalerHalt(),
        clock=_Uhr(),
    )
    rm.session_start_equity = 100_000.0
    rm.portfolio_stop_loss_pct = 0.0
    return rm


# ── H-3g: der Umzug ──────────────────────────────────────────────────────────


def test_konto_methoden_wohnen_im_mixin():
    from core.risk_konto import KontoHaltMixin
    from core.risk_manager import RiskManager

    for name in METHODEN:
        assert name in KontoHaltMixin.__dict__, f"{name} fehlt in KontoHaltMixin"
        assert name not in RiskManager.__dict__, f"{name} steht noch im Kern"
    assert issubclass(RiskManager, KontoHaltMixin)
    assert "__init__" not in KontoHaltMixin.__dict__


def test_kern_import_am_ende():
    baum = ast.parse(KONTO.read_text(encoding="utf-8"))
    letzte_klasse = max(
        i for i, k in enumerate(baum.body) if isinstance(k, ast.ClassDef)
    )
    kern = [
        i
        for i, k in enumerate(baum.body)
        if isinstance(k, ast.ImportFrom)
        and k.module == "core"
        and any(a.name == "risk_manager" and a.asname == "_rm" for a in k.names)
    ]
    assert kern, "kein `from core import risk_manager as _rm` in risk_konto.py"
    assert all(i > letzte_klasse for i in kern), "Kern-Import steht vor der Klasse"

    oben = set()
    for k in baum.body:
        if isinstance(k, ast.ImportFrom) and k.module:
            oben.add(k.module)
        elif isinstance(k, ast.Import):
            oben |= {a.name for a in k.names}
    verboten = {"core.engine.order_executor", "core.kill_switch", "core.gateway"}
    assert not (oben & verboten), f"später Import auf Modulebene: {oben & verboten}"


def test_patch_auf_kern_cloud_logging_trifft_konto():
    # Charakterisierung: vor dem Umzug geschrieben und dort grün. Fiele eine ``_rm.``-Lesestelle
    # weg, liefe der Patch still am Mixin vorbei und dieser Test würde rot.
    rm = _rm()
    attrappe = MagicMock()
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", True), patch(
        "core.risk_manager.cloud_log_risk_event", attrappe
    ):
        rm.update_account_equity(80_000.0)  # 20.000 $ > 17.500 $ Tages-Limit

    assert rm.trading_halted is True
    arten = [c.kwargs.get("event_type") for c in attrappe.call_args_list]
    assert "circuit_breaker" in arten, arten


# ── Charakterisierung vor der Zerlegung (H-3h): auf dem Umzug grün, danach unverändert ──


class _Spur:
    """Gemeinsame Reihenfolge von Halt und Broker-Aufrufen."""

    def __init__(self):
        self.ereignisse: list = []


def _aufzeichnender_halt(spur):
    from core.kill_switch import LokalerHalt

    class _Halt(LokalerHalt):
        def trip(self, reason, user_id=None, access_token=None, fail_closed=True):
            spur.ereignisse.append(("trip", reason))
            super().trip(reason, user_id, access_token, fail_closed)

    return _Halt()


class _Client:
    """Broker-Client mit zwei Positionen; jeder Aufruf landet in der Spur."""

    def __init__(self, spur):
        self._spur = spur

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _aufruf(*args, **kwargs):
            self._spur.ereignisse.append((name, args[0] if args else None))
            if name == "get_all_positions":
                return [
                    SimpleNamespace(symbol="AAPL", qty="10", qty_available="10"),
                    SimpleNamespace(symbol="MSFT", qty="5", qty_available="5"),
                ]
            return SimpleNamespace(id=f"order-{len(self._spur.ereignisse)}")

        return _aufruf


class _Logs(logging.Handler):
    """Logzeilen ab WARNING, die in ``risk_konto.py`` entstehen (Ort laut ``stacklevel``)."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.zeilen: list = []

    def emit(self, record):
        if Path(record.pathname).name == "risk_konto.py":
            self.zeilen.append((record.levelname, record.funcName, record.getMessage()))


@pytest.fixture
def logs():
    from tests.unit._risk_netz import _GlobalerSpion

    handler = _Logs()
    wurzel = logging.getLogger()
    alte_stufe = wurzel.level
    wurzel.addHandler(handler)
    wurzel.setLevel(logging.INFO)
    # Der globale Kill-Switch bleibt unberührt, auch wenn das Tor ihn befragt.
    with patch("core.kill_switch.kill_switch", _GlobalerSpion()):
        try:
            yield handler
        finally:
            wurzel.removeHandler(handler)
            wurzel.setLevel(alte_stufe)


def test_breaker_setzt_halt_vor_der_ersten_order(logs):
    spur = _Spur()
    rm = _rm(client=_Client(spur), halt=_aufzeichnender_halt(spur))

    rm.update_account_equity(80_000.0)

    namen = [n for n, _ in spur.ereignisse]
    assert "trip" in namen and "submit_order" in namen, namen
    assert namen.index("trip") < namen.index("submit_order")
    orders = [r for n, r in spur.ereignisse if n == "submit_order"]
    assert [(o.symbol, str(o.side.value).lower()) for o in orders] == [
        ("AAPL", "sell"),
        ("MSFT", "sell"),
    ]
    assert rm.trading_halted is True
    assert rm.halt_trigger_count == 1


def test_liquidation_scheitert_halt_bleibt(logs):
    halt = _aufzeichnender_halt(_Spur())
    rm = _rm(halt=halt)
    rm._liquidate_through_gateway = MagicMock(side_effect=RuntimeError("Broker weg"))

    rm.update_account_equity(80_000.0)

    assert rm.trading_halted is True
    assert halt.is_halted(NUTZER) is True
    assert (
        "ERROR",
        "update_account_equity",
        "   Liquidation attempt failed: Broker weg",
    ) in logs.zeilen


def test_portfolio_stop_im_selben_aufruf_unterdrueckt_die_warnstufe(logs):
    # 12.000 $ Drawdown = 69 % des Tages-Limits (> 60 %) und 12 % unter Sitzungsstart (≥ 7 %).
    ohne_stop = _rm()
    ohne_stop.update_account_equity(88_000.0)
    assert (
        ohne_stop.trading_reduced is True
    )  # Gegenprobe: ohne Stop greift die Warnstufe

    mit_stop = _rm()
    mit_stop.portfolio_stop_loss_pct = 0.07
    attrappe = MagicMock()
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", True), patch(
        "core.risk_manager.cloud_log_risk_event", attrappe
    ):
        mit_stop.update_account_equity(88_000.0)

    assert mit_stop.trading_halted is True
    assert mit_stop.trading_reduced is False
    arten = [c.kwargs.get("event_type") for c in attrappe.call_args_list]
    assert arten == ["portfolio_stop_loss"]


def test_ohne_allow_unlock_bleibt_der_halt(logs):
    gesperrt = _rm()
    gesperrt.update_account_equity(80_000.0)
    gesperrt.update_account_equity(100_000.0, allow_unlock=False)
    assert gesperrt.trading_halted is True

    frei = _rm()
    frei.update_account_equity(80_000.0)
    frei.update_account_equity(100_000.0)  # Gegenprobe: Drawdown 0 ≤ Erholungsschwelle
    assert frei.trading_halted is False


def test_logzeilen_tragen_den_namen_des_dirigenten(logs):
    stop = _rm()
    stop.portfolio_stop_loss_pct = 0.07
    stop.update_account_equity(90_000.0)  # Portfolio-Stop: CRITICAL

    warn = _rm()
    warn.update_account_equity(88_000.0)  # Warnstufe: WARNING

    breaker = _rm()
    breaker._liquidate_through_gateway = MagicMock(side_effect=RuntimeError("weg"))
    # 20.000 $ = 114 % des Limits: im selben Aufruf erst die Warnstufe (WARNING), dann der
    # Breaker (2 × CRITICAL) und die gescheiterte Liquidation (ERROR).
    breaker.update_account_equity(80_000.0)

    stufen = [stufe for stufe, _, _ in logs.zeilen]
    assert stufen == [
        "CRITICAL",
        "WARNING",
        "WARNING",
        "CRITICAL",
        "CRITICAL",
        "ERROR",
    ], stufen
    assert {f for _, f, _ in logs.zeilen} == {"update_account_equity"}


# ── H-3h: benannte Schritte ──────────────────────────────────────────────────

SCHRITTE = (
    "_konto_portfolio_stop",
    "_konto_drawdown",
    "_konto_warnstufe",
    "_konto_breaker",
    "_konto_erholung",
)


def _mixin_methoden() -> dict[str, ast.FunctionDef]:
    baum = ast.parse(KONTO.read_text(encoding="utf-8"))
    klasse = next(
        k
        for k in baum.body
        if isinstance(k, ast.ClassDef) and k.name == "KontoHaltMixin"
    )
    return {m.name: m for m in klasse.body if isinstance(m, ast.FunctionDef)}


def test_schritte_existieren():
    from core.risk_konto import KontoHaltMixin

    fehlt = [s for s in SCHRITTE if s not in KontoHaltMixin.__dict__]
    assert not fehlt, f"Schritte fehlen in KontoHaltMixin: {fehlt}"


def test_kein_schritt_ueber_150():
    methoden = _mixin_methoden()
    assert set(SCHRITTE) <= set(methoden), sorted(set(SCHRITTE) - set(methoden))
    zu_lang = {}
    for name, m in methoden.items():
        start = min([m.lineno, *(d.lineno for d in m.decorator_list)])
        zeilen = m.end_lineno - start + 1
        if zeilen > 150:
            zu_lang[name] = zeilen
    assert not zu_lang, f"über 150 Zeilen: {zu_lang}"


def test_allow_unlock_nur_im_dirigenten():
    methoden = _mixin_methoden()
    assert "_konto_erholung" in methoden, "Schritt _konto_erholung fehlt"
    fremd = sorted(
        name
        for name, m in methoden.items()
        if name != "update_account_equity"
        and any(
            (isinstance(k, ast.Name) and k.id == "allow_unlock")
            or (isinstance(k, ast.arg) and k.arg == "allow_unlock")
            for k in ast.walk(m)
        )
    )
    assert not fremd, f"allow_unlock außerhalb des Dirigenten: {fremd}"

    dirigent = methoden["update_account_equity"]
    bedingungen = [
        ast.unparse(k.test) for k in ast.walk(dirigent) if isinstance(k, ast.If)
    ]
    assert "self.trading_halted and allow_unlock" in bedingungen, bedingungen


def test_logs_der_schritte_mit_stacklevel():
    methoden = _mixin_methoden()
    schritte = {n: m for n, m in methoden.items() if n.startswith("_konto_")}
    assert set(schritte) == set(SCHRITTE), sorted(schritte)

    ohne = []
    for name, m in schritte.items():
        for k in ast.walk(m):
            if (
                isinstance(k, ast.Call)
                and isinstance(k.func, ast.Attribute)
                and isinstance(k.func.value, ast.Name)
                and k.func.value.id == "logging"
            ):
                stufe = next(
                    (w.value for w in k.keywords if w.arg == "stacklevel"), None
                )
                if not (isinstance(stufe, ast.Constant) and stufe.value == 2):
                    ohne.append(f"{name}:{k.lineno} logging.{k.func.attr}")
    assert not ohne, f"Logaufruf ohne stacklevel=2: {ohne}"
