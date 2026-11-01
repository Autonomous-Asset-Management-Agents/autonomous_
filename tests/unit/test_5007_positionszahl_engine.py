# #5007 — Die Console rechnet mit der Positionszahl, die die Engine wirksam nutzt.
# Plan: docs/5007-positionszahl-kommt-von-der-engine-statt-aus-fes/implementation_plan.md
#
# Wirksam ist je nach FULL_UNIVERSE_TRADING_ENABLED die 20 oder MAX_POSITIONS
# (core/risk_manager.py::effective_max_positions). ``current_shape`` kennt den Schalter
# nicht; deshalb liefert GET /api/trading-settings die Zahl aus effective_max_positions.

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from core.portfolio_shape import DEFAULT_POSITIONS

KONSOLE = Path(__file__).resolve().parents[3] / "src" / "console" / "desktop"


@pytest.fixture
def client():
    from core.engine import api_routes

    api_routes.app.dependency_overrides[api_routes.require_engine_key] = lambda: None
    yield TestClient(api_routes.app)
    api_routes.app.dependency_overrides.clear()


def _konfig(monkeypatch, **werte):
    import config

    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(**werte))


@pytest.mark.vc1
def test_get_nennt_bei_schalter_an_die_full_universe_zahl(client, monkeypatch):
    _konfig(
        monkeypatch,
        FULL_UNIVERSE_TRADING_ENABLED=True,
        FULL_UNIVERSE_MAX_POSITIONS=20,
        MAX_POSITIONS=10,
    )
    assert client.get("/api/trading-settings").json()["effective_positions"] == 20


@pytest.mark.vc1
def test_get_nennt_bei_schalter_aus_max_positions(client, monkeypatch):
    # current_shape() lieferte hier die 15; gehandelt wird mit 10.
    _konfig(
        monkeypatch,
        FULL_UNIVERSE_TRADING_ENABLED=False,
        FULL_UNIVERSE_MAX_POSITIONS=15,
        MAX_POSITIONS=10,
    )
    assert client.get("/api/trading-settings").json()["effective_positions"] == 10


@pytest.mark.vc1
def test_portfolio_default_ist_in_der_console_gleich():
    karte = (KONSOLE / "TradingControlsCard.tsx").read_text(encoding="utf-8")
    register = (KONSOLE / "tradingSettingsDefaults.ts").read_text(encoding="utf-8")

    konstante = re.search(r"const DEFAULT_POSITIONS = (\d+);", karte)
    eintrag = re.search(
        r'key: "FULL_UNIVERSE_MAX_POSITIONS".*?default: "(\d+)"', register
    )

    assert konstante and int(konstante.group(1)) == DEFAULT_POSITIONS
    assert eintrag and int(eintrag.group(1)) == DEFAULT_POSITIONS


@pytest.mark.vc1
@pytest.mark.parametrize(
    "datei", ["AdvancedTradingSection.tsx", "strategyWorkshop/StrategyEditor.tsx"]
)
def test_querfeld_pruefung_hat_keinen_festen_rueckfall(datei):
    quelle = (KONSOLE / datei).read_text(encoding="utf-8")
    assert "useState(10)" not in quelle
    assert "FULL_UNIVERSE_MAX_POSITIONS" not in quelle
