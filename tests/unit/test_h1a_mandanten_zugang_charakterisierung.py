"""#4230 (ARC-E6 H-1a) — Charakterisierung des Mandanten-Zugangs vor dem Schnitt.

Plan: ``docs/4230-*/implementation_plan.md`` §6 Schritt 3. Netz fuer H-1d
(``mandanten_zugang.py``): ``get_active_tenant_clients``, ``_get_tenant_risk_manager``,
``_get_tenant_portfolio_manager`` und ``_broker_zugang_fuer``. Das Geld-Gate ersetzt genau
diese vier durch Mocks (``_geld_gate.py::fahre``); bis hierher fuhr sie kein Netz.

Die Tests halten fest, was der Code **heute** tut, nicht was er tun sollte. Nach dem Umzug
muessen sie unveraendert gruen sein; jede Abweichung ist eine Verhaltensaenderung.

Beobachtet wird an Naehten, die vom Ort der Funktionen unabhaengig sind: ``wallet_store``
und ``oauth_secrets`` an ihren Singletons, ``create_trading_client``, ``RiskManager`` und
``PortfolioManager`` in ihren Quellmodulen (die vier importieren sie funktionslokal), und am
Broker-Double. Kein Patch am Kern.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError

import config as _config
from core.secret_manager_utils import oauth_secrets
from core.user_wallet_store import wallet_store

pytestmark = [pytest.mark.unit, pytest.mark.vc3]


def _engine(api=None):
    from core.engine.base import BotEngine

    engine = BotEngine.__new__(BotEngine)
    engine.api = api
    return engine


def _konto(equity):
    client = MagicMock()
    client.get_account.return_value = SimpleNamespace(equity=equity)
    return client


def _api_fehler():
    http = MagicMock()
    http.response.status_code = 403
    return APIError(
        error=json.dumps({"code": 40310000, "message": "x"}), http_error=http
    )


def _mandanten(wallets, *, tokens=None, clients=(), papier=True):
    """``get_active_tenant_clients`` mit diesen Wallets; liefert Ergebnis und Fabrik-Aufrufe."""
    fabrik = MagicMock(side_effect=list(clients))
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                wallet_store, "get_active_wallets", AsyncMock(return_value=wallets)
            )
        )
        stack.enter_context(
            patch.object(
                oauth_secrets,
                "get_tokens",
                MagicMock(side_effect=lambda sid: (tokens or {}).get(sid)),
            )
        )
        stack.enter_context(patch("core.client_factory.create_trading_client", fabrik))
        stack.enter_context(patch.object(_config, "PAPER_TRADING", papier, create=True))
        ergebnis = asyncio.run(_engine().get_active_tenant_clients())
    return ergebnis, fabrik.call_args_list


# ── M1 lokale Schluessel ──────────────────────────────────────────────────────


@pytest.mark.parametrize("papier", [True, False])
def test_m1_lokale_schluessel_ergeben_einen_eintrag(papier):
    client = _konto("2500.5")
    limits = {"alpaca_keys": {"api_key": "k", "secret_key": "s"}, "max": 1}
    ergebnis, aufrufe = _mandanten(
        [{"user_id": "u1", "risk_limits": limits, "secret_manager_id": "sm-1"}],
        clients=[client],
        papier=papier,
    )
    assert ergebnis == [
        {"user_id": "u1", "client": client, "risk_limits": limits, "equity": 2500.5}
    ]
    assert [(a.args, a.kwargs) for a in aufrufe] == [
        ((), {"api_key": "k", "secret_key": "s", "paper": papier})
    ]


def test_m1_leeres_eigenkapital_wird_null():
    client = _konto(None)
    limits = {"alpaca_keys": {}}
    ergebnis, _ = _mandanten(
        [{"user_id": "u1", "risk_limits": limits}], clients=[client]
    )
    assert ergebnis[0]["equity"] == 0.0


# ── M2 OAuth ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("papier", [True, False])
def test_m2_oauth_token_ergibt_einen_eintrag(papier):
    client = _konto("1000")
    ergebnis, aufrufe = _mandanten(
        [{"user_id": "u2", "secret_manager_id": "sm-2"}],
        tokens={"sm-2": {"access_token": "tok"}},
        clients=[client],
        papier=papier,
    )
    assert ergebnis == [
        {"user_id": "u2", "client": client, "risk_limits": {}, "equity": 1000.0}
    ]
    assert [(a.args, a.kwargs) for a in aufrufe] == [
        ((), {"oauth_token": "tok", "paper": papier})
    ]


def test_m2_ohne_token_oder_secret_wird_ausgelassen(caplog):
    with caplog.at_level(logging.WARNING):
        ergebnis, aufrufe = _mandanten(
            [
                {"user_id": "ohne-secret"},
                {"user_id": "ohne-token", "secret_manager_id": "sm-x"},
                {"user_id": "falscher-token", "secret_manager_id": "sm-y"},
            ],
            tokens={"sm-y": {"refresh_token": "r"}},
        )
    assert ergebnis == [] and aufrufe == []
    warnungen = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert "No valid tokens found for active user ohne-token" in warnungen
    assert "No valid tokens found for active user falscher-token" in warnungen
    assert not any("ohne-secret" in w for w in warnungen)


# ── M3 Fehler lassen nur den einen Mandanten aus ──────────────────────────────


@pytest.mark.parametrize(
    "fehler", [_api_fehler(), RuntimeError("weg")], ids=["api", "andere"]
)
@pytest.mark.parametrize("zweig", ["lokal", "oauth"])
def test_m3_kontofehler_laesst_nur_diesen_mandanten_aus(fehler, zweig):
    kaputt = MagicMock()
    kaputt.get_account.side_effect = fehler
    gut = _konto("50")
    wallet = (
        {"user_id": "kaputt", "risk_limits": {"alpaca_keys": {}}}
        if zweig == "lokal"
        else {"user_id": "kaputt", "secret_manager_id": "sm-k"}
    )
    ergebnis, _ = _mandanten(
        [wallet, {"user_id": "gut", "secret_manager_id": "sm-g"}],
        tokens={"sm-k": {"access_token": "a"}, "sm-g": {"access_token": "b"}},
        clients=[kaputt, gut],
    )
    assert [(e["user_id"], e["equity"]) for e in ergebnis] == [("gut", 50.0)]


def test_m3_lokaler_fehler_faellt_nicht_auf_oauth_zurueck():
    kaputt = MagicMock()
    kaputt.get_account.side_effect = RuntimeError("weg")
    ergebnis, aufrufe = _mandanten(
        [
            {
                "user_id": "u",
                "risk_limits": {"alpaca_keys": {}},
                "secret_manager_id": "sm",
            }
        ],
        tokens={"sm": {"access_token": "a"}},
        clients=[kaputt],
    )
    assert ergebnis == [] and len(aufrufe) == 1


# ── M4 ein Manager je Nutzer ──────────────────────────────────────────────────


def test_m4_risk_manager_je_nutzer_einmal_dann_neuer_client():
    engine = _engine()
    with patch("core.risk_manager.RiskManager") as rm_cls:
        rm_cls.side_effect = lambda *a, **k: MagicMock(name="rm")
        erst = engine._get_tenant_risk_manager("u1", "c1", 1000.0)
        zweit = engine._get_tenant_risk_manager("u1", "c2", 9999.0)
        anderer = engine._get_tenant_risk_manager("u2", "c3", 50.0)
    assert erst is zweit and anderer is not erst
    assert [c.args[:2] for c in rm_cls.call_args_list] == [("c1", 1000.0), ("c3", 50.0)]
    assert all("clock" in c.kwargs for c in rm_cls.call_args_list)
    erst.reset_daily_limit.assert_called_once_with(1000.0)
    assert erst.client == "c2"


@pytest.mark.parametrize(
    ("voll", "erwartet"), [(False, 7), (True, 33)], ids=["standard", "volles_universum"]
)
def test_m4_portfolio_manager_je_nutzer(voll, erwartet):
    engine = _engine()
    cfg = _config.get_config()
    with ExitStack() as stack:
        stack.enter_context(patch.object(_config, "MAX_POSITIONS", 7, create=True))
        stack.enter_context(
            patch.object(cfg, "FULL_UNIVERSE_TRADING_ENABLED", voll, create=True)
        )
        stack.enter_context(
            patch.object(cfg, "FULL_UNIVERSE_MAX_POSITIONS", 33, create=True)
        )
        erst = engine._get_tenant_portfolio_manager("u1", "c1", 1000.0)
        with patch.object(
            erst, "update_total_capital", wraps=erst.update_total_capital
        ) as spy:
            zweit = engine._get_tenant_portfolio_manager("u1", "c2", 2000.0)
    assert erst is zweit
    assert erst.max_positions == erwartet
    assert erst.user_id == "u1"
    assert erst.client == "c2"
    assert erst.total_capital == 2000.0
    spy.assert_called_once_with(2000.0)
    assert engine._pm_restored == set()


# ── M5 Broker-Zugang fuer eine Freigabe ──────────────────────────────────────


def _zugang(engine, tenants):
    engine.get_active_tenant_clients = AsyncMock(return_value=tenants)
    return asyncio.run(engine._broker_zugang_fuer("u1"))


def test_m5_passender_mandant():
    t = {"user_id": "u1", "client": "c", "equity": 1.0}
    assert _zugang(_engine(api=_konto("5")), [{"user_id": "u0"}, t]) == (t, None)


def test_m5_mandanten_aufstellung_ohne_den_nutzer():
    engine = _engine(api=_konto("5"))
    assert _zugang(engine, [{"user_id": "u0"}]) == (None, "no_oauth_tenant")
    engine.api.get_account.assert_not_called()


def test_m5_ohne_mandanten_gilt_der_engine_zugang():
    api = _konto("12345.5")
    assert _zugang(_engine(api=api), []) == (
        {"user_id": "u1", "client": api, "equity": 12345.5},
        None,
    )


def test_m5_engine_zugang_mit_kontofehler_nimmt_default_equity():
    api = MagicMock()
    api.get_account.side_effect = RuntimeError("weg")
    cfg = _config.get_config()
    with patch.object(cfg, "DEFAULT_EQUITY", 777.0, create=True):
        tenant, grund = _zugang(_engine(api=api), [])
    assert grund is None and tenant["equity"] == 777.0


def test_m5_weder_mandant_noch_engine_zugang():
    assert _zugang(_engine(api=None), []) == (None, "no_broker_access")
