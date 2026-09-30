"""C9 (#2375): the /stock-history endpoint must validate the ticker shape so a crafted
symbol cannot traverse out of the data-provider cache dir (path traversal), matching the
/force-cycle SEC M7 whitelist. data_provider adds a basename defense-in-depth."""

import asyncio
from unittest.mock import patch

import pandas as pd

import core.engine.api_routes as api_routes


def test_stock_history_rejects_path_traversal_symbol():
    with patch.object(api_routes, "engine") as eng:
        r = asyncio.run(api_routes.get_stock_history(symbol="../../etc/passwd"))
    assert r["status"] == "error"
    assert "invalid" in r["message"].lower()
    eng.data_provider.get_data.assert_not_called()


def test_stock_history_rejects_separator_symbol():
    with patch.object(api_routes, "engine") as eng:
        r = asyncio.run(api_routes.get_stock_history(symbol="AB/CD"))
    assert r["status"] == "error"
    eng.data_provider.get_data.assert_not_called()


def test_stock_history_accepts_valid_symbol():
    with patch.object(api_routes, "engine") as eng:
        eng.data_provider.get_data.return_value = pd.DataFrame()
        r = asyncio.run(api_routes.get_stock_history(symbol="aapl"))
    assert r["symbol"] == "AAPL"
    eng.data_provider.get_data.assert_called_once()
