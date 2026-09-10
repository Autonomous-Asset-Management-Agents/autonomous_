"""#2548 C1 — the primary engine boot seam must route through the client factory under SIM_MODE.

`api_routes._init_trading_clients` is where `engine.api` / `engine.data_api` are constructed on the
desktop/uvicorn boot path. In #2544 it still built a real TradingClient + StockHistoricalDataClient
+ a real get_account() network probe directly — so under AAA_SIM_MODE=true the VirtualLiveBroker was
NEVER injected there and real Alpaca was touched (fail-closed breached). This locks in that under
SIM_MODE the seam uses the factory (sim clients) and never constructs a real client / calls the probe.
"""


def _boom(*a, **k):
    raise AssertionError(
        "a real Alpaca client was constructed on the boot seam under SIM_MODE"
    )


def test_boot_seam_routes_through_factory_under_sim_mode(monkeypatch):
    from core.engine import api_routes

    monkeypatch.setattr(api_routes.config, "SIM_MODE", True, raising=False)
    # Factory returns sim sentinels (the real factory is exercised elsewhere).
    monkeypatch.setattr(
        api_routes, "create_trading_client", lambda *a, **k: "SIM_BROKER"
    )
    monkeypatch.setattr(api_routes, "create_data_client", lambda *a, **k: "SIM_DATA")
    # Real constructors + probe must never be reached under SIM_MODE.
    monkeypatch.setattr(api_routes, "TradingClient", _boom)
    monkeypatch.setattr(api_routes, "StockHistoricalDataClient", _boom)

    captured = {}

    class _StubEngine:
        def __init__(self, trading_client=None, data_client=None):
            captured["t"] = trading_client
            captured["d"] = data_client

    monkeypatch.setattr(api_routes, "BotEngine", _StubEngine)

    api_routes._init_trading_clients()

    assert (
        captured["t"] == "SIM_BROKER"
    ), "engine.api must be the sim broker under SIM_MODE"
    assert (
        captured["d"] == "SIM_DATA"
    ), "engine.data_api must be the sim data client under SIM_MODE"


def test_boot_seam_sim_mode_ignores_missing_alpaca_key(monkeypatch):
    """Under SIM_MODE the sim clients are built even with no ALPACA_API_KEY (the offline point)."""
    from core.engine import api_routes

    monkeypatch.setattr(api_routes.config, "SIM_MODE", True, raising=False)
    monkeypatch.setattr(api_routes.config, "ALPACA_API_KEY", None, raising=False)
    monkeypatch.setattr(
        api_routes, "create_trading_client", lambda *a, **k: "SIM_BROKER"
    )
    monkeypatch.setattr(api_routes, "create_data_client", lambda *a, **k: "SIM_DATA")
    monkeypatch.setattr(api_routes, "TradingClient", _boom)
    monkeypatch.setattr(api_routes, "StockHistoricalDataClient", _boom)

    captured = {}
    monkeypatch.setattr(
        api_routes,
        "BotEngine",
        lambda trading_client=None, data_client=None: captured.update(
            t=trading_client, d=data_client
        )
        or object(),
    )

    api_routes._init_trading_clients()
    assert captured["t"] == "SIM_BROKER"
    assert captured["d"] == "SIM_DATA"
