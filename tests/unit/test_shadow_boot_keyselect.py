"""INC-1 (P0) — live pre-flight Alpaca key-selection regression guard.

RCA (docs/reviews/live-trading-rca-2026-07-18.md): on a LIVE desktop boot the
shadow-boot pre-flight read the *account-agnostic* aliases ``config.API_KEY`` /
``config.API_SECRET`` / ``config.BASE_URL``. In the desktop edition those aliases
hold the PAPER credentials (``PK…``) while the *selected* account is exposed via
``config.ALPACA_API_KEY`` / ``config.ALPACA_SECRET_KEY`` / ``config.ALPACA_BASE_URL``
(swapped to the live slots by ``_select_alpaca_account``). The pre-flight therefore
sent the PAPER key to the LIVE endpoint → HTTP 401 → the live boot silently degraded
to paper.

These tests pin the fix: ``_check_alpaca`` MUST use the account-SELECTED
``config.ALPACA_*`` values so the live key reaches the live endpoint. A source-level
parity assertion covers the OSS variant (``shadow_boot.oss.py``), whose renamed copy
ships as the desktop ``shadow_boot.py``.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import config
from scripts import shadow_boot


def _distinct_paper_vs_live_config():
    """Patch config so PAPER aliases and LIVE (selected) creds are DISTINCT."""
    return (
        patch("config.API_KEY", "PK-PAPER"),
        patch("config.API_SECRET", "PK-PAPER-SECRET"),
        patch("config.BASE_URL", "https://paper-api.alpaca.markets"),
        patch("config.ALPACA_API_KEY", "AK-LIVE"),
        patch("config.ALPACA_SECRET_KEY", "SK-LIVE"),
        patch("config.ALPACA_BASE_URL", "https://api.alpaca.markets"),
    )


def test_check_alpaca_sends_live_creds_to_live_endpoint():
    """The pre-flight must authenticate with the SELECTED (live) key against the live URL."""
    # §5.2: AsyncMock for the async httpx client — never a hand-rolled `async def` stub.
    # The AsyncMock IS the async context manager (its __aenter__ returns itself) and .get is awaitable.
    resp = MagicMock(status_code=200, text="{}")
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = resp

    patches = _distinct_paper_vs_live_config()
    for p in patches:
        p.start()
    try:
        # httpx.AsyncClient() is called (not awaited) → a plain Mock returning our async client.
        with patch("httpx.AsyncClient", return_value=client):
            result = asyncio.run(shadow_boot._check_alpaca())
    finally:
        for p in patches:
            p.stop()

    assert result.ok is True
    client.get.assert_awaited_once()
    url = client.get.await_args.args[0]
    headers = client.get.await_args.kwargs["headers"]
    assert url == "https://api.alpaca.markets/v2/account"
    assert headers["APCA-API-KEY-ID"] == "AK-LIVE"
    assert headers["APCA-API-SECRET-KEY"] == "SK-LIVE"
    # Never the paper key / paper endpoint on a live boot (the exact RCA failure).
    assert "PK-PAPER" not in headers.values()
    assert "PK-PAPER-SECRET" not in headers.values()
    assert "paper-api" not in url


def _check_alpaca_config_attrs(path: Path) -> set[str]:
    """Return the set of ``config.<ATTR>`` names read inside ``_check_alpaca``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
        and n.name == "_check_alpaca"
    )
    attrs: set[str] = set()
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "config"
        ):
            attrs.add(node.attr)
    return attrs


def test_enterprise_check_alpaca_uses_selected_alpaca_creds():
    path = Path(shadow_boot.__file__)
    attrs = _check_alpaca_config_attrs(path)
    assert {"ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_BASE_URL"} <= attrs
    assert not ({"API_KEY", "API_SECRET", "BASE_URL"} & attrs), (
        "shadow_boot.py::_check_alpaca must read the account-SELECTED config.ALPACA_* "
        f"values, never the paper aliases. Found: {attrs}"
    )


def test_oss_check_alpaca_uses_selected_alpaca_creds():
    """Source-parity: the OSS variant (shipped as desktop shadow_boot.py) too."""
    oss_path = Path(shadow_boot.__file__).with_name("shadow_boot.oss.py")
    attrs = _check_alpaca_config_attrs(oss_path)
    assert {"ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_BASE_URL"} <= attrs
    assert not ({"API_KEY", "API_SECRET", "BASE_URL"} & attrs), (
        "shadow_boot.oss.py::_check_alpaca must read config.ALPACA_API_KEY (not "
        f"config.API_KEY). Found: {attrs}"
    )
