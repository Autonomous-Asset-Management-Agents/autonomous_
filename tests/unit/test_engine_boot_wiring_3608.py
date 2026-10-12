# flake8: noqa
"""#3608: die Engine startete auf main nicht mehr.

Der C3-Smoke-Test des Windows-Builds (0.5.1-rc1, 23.09.) fand die Engine 180 s lang ungesund; die
/health-Antwort nannte den Grund:

    TypeError: BotEngine.__init__() missing 2 required keyword-only arguments:
               'clock' and 'trade_intelligence'

Die Clock-Injection (#3561/#3563/#3564) machte beide Argumente verpflichtend, aber
`_init_trading_clients()` in api_routes.py — der Bootpfad, über den Desktop und Cloud Run die
Engine bauen — übergab sie nicht. Beide Zweige waren betroffen: der SIM_MODE-Zweig und der
reguläre.

Die Bausteine liefert der CompositionRoot (`clock_port`, `trade_intelligence`) — laut eigener
Beschreibung „the ONLY place where Editionsweichen evaluated" werden. Dieser Test hält fest, dass
der Bootpfad sie auch dort holt.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _boot(sim_mode: bool):
    """Führt _init_trading_clients() aus und gibt die kwargs zurück, mit denen BotEngine gebaut wurde."""
    import os

    import core.engine.api_routes as api_routes
    from core.composition.root import CompositionRoot

    old_engine = api_routes.engine
    old_env = dict(os.environ)
    try:
        CompositionRoot.reset()
        root = CompositionRoot.get_instance()

        with patch("core.engine.api_routes.BotEngine") as engine_cls, patch(
            "core.engine.api_routes.create_trading_client", return_value=MagicMock()
        ), patch(
            "core.engine.api_routes.create_data_client", return_value=MagicMock()
        ), patch.object(
            __import__("config"), "SIM_MODE", sim_mode
        ):
            from core.engine.api_routes import _init_trading_clients

            _init_trading_clients()

        assert engine_cls.call_count == 1, "BotEngine wurde nicht genau einmal gebaut"
        return engine_cls.call_args.kwargs, root
    finally:
        api_routes.engine = old_engine
        os.environ.clear()
        os.environ.update(old_env)
        CompositionRoot.reset()


@pytest.mark.vc0
@pytest.mark.mutates_global_state
@pytest.mark.parametrize("sim_mode", [True, False])
def test_boot_passes_clock_and_trade_intelligence(sim_mode):
    """Ohne beide Argumente wirft BotEngine einen TypeError und die Engine wird nie gesund."""
    kwargs, root = _boot(sim_mode)
    assert "clock" in kwargs, "clock fehlt — die Engine startet nicht"
    assert (
        "trade_intelligence" in kwargs
    ), "trade_intelligence fehlt — die Engine startet nicht"


@pytest.mark.vc0
@pytest.mark.mutates_global_state
@pytest.mark.parametrize("sim_mode", [True, False])
def test_boot_takes_them_from_the_composition_root(sim_mode):
    """Nicht irgendeine Uhr: die des CompositionRoot, der die Editionsweichen auswertet."""
    kwargs, root = _boot(sim_mode)
    assert kwargs["clock"] is root.clock_port
    assert kwargs["trade_intelligence"] is root.trade_intelligence
